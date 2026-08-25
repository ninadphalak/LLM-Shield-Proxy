"""Edge-Level Agent Identity Enforcer Middleware.

Extracts and cryptographically validates DPoP proofs and Workload Identity (JWT) tokens.
Maps identities in O(1) time and enforces per-tenant or global ingress barriers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Request

from llm_shield_proxy.core.config import agent_identity_ctx, settings

logger = logging.getLogger(__name__)

# Global state for coalescing fetches
_jwks_clients: Dict[str, jwt.PyJWKClient] = {}
_jwks_locks = defaultdict(threading.Lock)

async def _get_signing_key(issuer: str, token: str):
    def _fetch():
        with _jwks_locks[issuer]:
            if issuer not in _jwks_clients:
                jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
                _jwks_clients[issuer] = jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
            return _jwks_clients[issuer].get_signing_key_from_jwt(token)
    return await asyncio.to_thread(_fetch)

async def verify_agent_identity(request: Request, tenant_policy: Optional[Dict[str, Any]] = None) -> None:
    """FastAPI Dependency for Agent Identity Enforcement.

    1. Checks if enforcement is globally enabled or per-tenant enabled.
    2. Validates DPoP and Workload Identity JWTs.
    3. Drops connection (401) immediately if invalid.
    4. Injects agent_identity_claim into request.state.
    """
    enforce_identity = "off"

    # Check tenant policy first, fallback to global setting
    if tenant_policy and "agent_identity_enforcer" in tenant_policy:
        enforce_identity = tenant_policy["agent_identity_enforcer"]
    else:
        enforce_identity = settings.AGENT_IDENTITY_ENFORCER

    # handle old bools for backward compatibility
    if enforce_identity is True:
        enforce_identity = "strict"
    elif enforce_identity is False:
        enforce_identity = "off"

    if enforce_identity == "off":
        request.state.agent_identity_claim = None
        return

    # Extract Workload Identity Token
    auth_header = request.headers.get("Authorization")
    dpop_header = request.headers.get("DPoP")

    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Agent Identity Enforcer active."
        )

    if not dpop_header:
        raise HTTPException(
            status_code=401,
            detail="Missing DPoP proof. Agent Identity Enforcer active."
        )

    token = auth_header[7:].strip()

    try:
        # Step 1: Decode unverified headers to find the issuer
        unverified_claims = jwt.decode(token, options={"verify_signature": False})

        issuer = unverified_claims.get("iss")
        if not issuer:
            raise jwt.InvalidIssuerError("Missing issuer claim")

        # Strict Default-Closed Allowlist
        allowed_issuers = tenant_policy.get("allowed_issuers", []) if tenant_policy else getattr(settings, "ALLOWED_ISSUERS", [])
        if not allowed_issuers or issuer not in allowed_issuers:
            raise jwt.InvalidIssuerError("Unauthorized issuer")

        allowed_audiences = tenant_policy.get("allowed_audiences", []) if tenant_policy else getattr(settings, "ALLOWED_AUDIENCES", [])

        # Finding 1: Check audience before fetching JWKS over network
        if allowed_audiences and unverified_claims.get("aud") not in allowed_audiences:
            raise jwt.InvalidAudienceError("Unauthorized audience")

        # Step 2: Fetch JWKS (cached) and get signing key non-blockingly
        signing_key = await _get_signing_key(issuer, token)

        # Step 3: Verify the token signature and claims
        options_dict = {
            "verify_exp": True,
            "verify_aud": bool(allowed_audiences),
        }

        # Finding 2: Offload CPU-bound PyJWT signature verification to avoid loop starvation
        decoded_token = await asyncio.to_thread(
            jwt.decode,
            token,
            signing_key.key,
            algorithms=["RS256", "ES256", "PS256"],
            audience=allowed_audiences if allowed_audiences else None,
            options=options_dict
        )

        # Step 4: Validate DPoP signature and claims (RFC 9449)
        dpop_unverified_header = jwt.get_unverified_header(dpop_header)
        jwk_dict = dpop_unverified_header.get("jwk")
        if not jwk_dict:
            raise jwt.InvalidTokenError("Missing jwk in DPoP header")

        dpop_jwk = jwt.PyJWK(jwk_dict)

        # Offload DPoP signature verification as well
        dpop_claims = await asyncio.to_thread(
            jwt.decode,
            dpop_header,
            dpop_jwk.key,
            algorithms=["RS256", "ES256", "PS256"],
            options={"verify_exp": True, "leeway": 60}
        )

        # DPoP Expiry and Freshness with clock skew (60 seconds leeway)
        iat = dpop_claims.get("iat")
        if not iat:
            raise jwt.InvalidTokenError("DPoP proof missing iat")

        now = time.time()
        if iat > now + 60:
            raise jwt.InvalidTokenError("DPoP proof iat is in the future")
        if now - iat > 360:
            raise jwt.InvalidTokenError("DPoP proof expired (older than 5 minutes)")

        # Finding 3: 3-Tier Replay validation (Strict vs Lenient vs None)
        expected_htu = str(request.url.replace(query=""))
        is_dpop_valid = True
        dpop_error_msg = ""

        if dpop_claims.get("htm") != request.method:
            is_dpop_valid = False
            dpop_error_msg = "DPoP htm mismatch"
        elif dpop_claims.get("htu") != expected_htu:
            is_dpop_valid = False
            dpop_error_msg = "DPoP htu mismatch"

        if not is_dpop_valid:
            if enforce_identity == "strict":
                raise jwt.InvalidTokenError(dpop_error_msg)
            elif enforce_identity == "lenient":
                logger.warning(f"DPoP Validation Warning (lenient mode): {dpop_error_msg}")
                # For prometheus metric and admin review telemetry (and tests)
                request.state.dpop_warning = dpop_error_msg

        # Ensure the DPoP token's thumbprint matches the access token's 'cnf' (Confirmation) claim
        cnf = decoded_token.get("cnf", {})
        expected_jkt = cnf.get("jkt")
        if not expected_jkt:
            raise jwt.InvalidTokenError("Missing cnf.jkt in access token")

        actual_jkt = dpop_jwk.thumbprint
        if actual_jkt != expected_jkt:
            raise jwt.InvalidTokenError("DPoP jkt thumbprint mismatch")

        # O(1) Identity Mapping and injection
        identity_claim = decoded_token.get("sub", "unknown_agent")
        request.state.agent_identity_claim = identity_claim
        agent_identity_ctx.set(identity_claim)

    except Exception as e:
        logger.warning(f"Agent Identity Enforcer blocked request: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Cryptographic verification failed. Invalid identity proof."
        )
