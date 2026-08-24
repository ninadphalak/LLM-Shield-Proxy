"""Edge-Level Agent Identity Enforcer Middleware.

Extracts and cryptographically validates DPoP proofs and Workload Identity (JWT) tokens.
Maps identities in O(1) time and enforces per-tenant or global ingress barriers.
"""

from __future__ import annotations

import logging
import time
import asyncio
from functools import lru_cache
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Request

from llm_shield_proxy.core.config import request_policy_ctx, settings, agent_identity_ctx

logger = logging.getLogger(__name__)

# O(1) Memory Cache for JWKS to guarantee < 1ms validation
# In a real environment, this would fetch from an OIDC provider like Google/Azure
@lru_cache(maxsize=128)
def fetch_jwks(issuer: str) -> jwt.PyJWKClient:
    """Heavily cached JWKS client factory."""
    # Note: For production this URL must be dynamically resolved or configured.
    jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
    return jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)

async def verify_agent_identity(request: Request, tenant_policy: Optional[Dict[str, Any]] = None) -> None:
    """FastAPI Dependency for Agent Identity Enforcement.
    
    1. Checks if enforcement is globally enabled or per-tenant enabled.
    2. Validates DPoP and Workload Identity JWTs.
    3. Drops connection (401) immediately if invalid.
    4. Injects agent_identity_claim into request.state.
    """
    enforce_identity = False

    # Check tenant policy first, fallback to global setting
    if tenant_policy and tenant_policy.get("agent_identity_enforcer"):
        enforce_identity = True
    elif settings.AGENT_IDENTITY_ENFORCER:
        enforce_identity = True

    if not enforce_identity:
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
        allowed_issuers = tenant_policy.get("allowed_issuers", []) if tenant_policy else getattr(settings, "ALLOWED_ISSUERS", [])
        if not issuer or (allowed_issuers and issuer not in allowed_issuers):
            raise jwt.InvalidIssuerError("Missing or unauthorized issuer claim")

        # Step 2: Fetch JWKS (cached) and get signing key non-blockingly
        jwks_client = fetch_jwks(issuer)
        signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)

        # Step 3: Verify the token signature and claims
        allowed_audiences = tenant_policy.get("allowed_audiences", []) if tenant_policy else getattr(settings, "ALLOWED_AUDIENCES", [])
        
        options_dict = {
            "verify_exp": True,
            "verify_aud": bool(allowed_audiences),
        }
        
        decoded_token = jwt.decode(
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
        dpop_claims = jwt.decode(
            dpop_header,
            dpop_jwk.key,
            algorithms=["RS256", "ES256", "PS256"],
            options={"verify_exp": False} # typically 'iat' is used for DPoP freshness
        )
        
        if not dpop_claims.get("htu") or not dpop_claims.get("htm"):
            raise jwt.InvalidTokenError("Malformed DPoP proof")
            
        iat = dpop_claims.get("iat")
        if not iat or time.time() - iat > 300: # 5 min freshness window
            raise jwt.InvalidTokenError("DPoP proof expired or missing iat")
            
        # Ensure the DPoP token's thumbprint matches the access token's 'cnf' (Confirmation) claim if present
        cnf = decoded_token.get("cnf", {})
        expected_jkt = cnf.get("jkt")
        if expected_jkt:
            actual_jkt = dpop_jwk.thumbprint
            if actual_jkt != expected_jkt:
                raise jwt.InvalidTokenError("DPoP jkt thumbprint mismatch")

        # O(1) Identity Mapping and injection
        identity_claim = decoded_token.get("sub", "unknown_agent")
        request.state.agent_identity_claim = identity_claim
        agent_identity_ctx.set(identity_claim)
        
    except Exception as e:
        if isinstance(e, asyncio.CancelledError):
            raise
        logger.warning(f"Agent Identity Enforcer blocked request: {str(e)}")
        raise HTTPException(
            status_code=401, 
            detail="Cryptographic verification failed. Invalid identity proof."
        )
