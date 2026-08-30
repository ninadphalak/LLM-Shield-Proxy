"""Portable audit checkpoint manifests for external retention and aggregation.

This module deliberately has no cloud SDK dependency. It produces a signed,
privacy-safe artifact that operators can place in their existing immutable store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def load_ed25519_private_key_material(raw: str) -> ed25519.Ed25519PrivateKey:
    """Load an Ed25519 private key from PEM or a base64/hex 32-byte seed."""
    material = raw.strip()
    if "BEGIN" in material:
        key = serialization.load_pem_private_key(material.encode("utf-8"), password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise TypeError("Configured PEM is not an Ed25519 private key")
        return key

    seed: bytes | None = None
    try:
        seed = base64.b64decode(material, validate=True)
    except (binascii.Error, ValueError):
        seed = None
    if seed is None or len(seed) != 32:
        seed = bytes.fromhex(material)
    if len(seed) != 32:
        raise ValueError("Decoded Ed25519 seed is not 32 bytes")
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


def load_ed25519_private_key_file(path: str | Path) -> ed25519.Ed25519PrivateKey:
    return load_ed25519_private_key_material(Path(path).read_text(encoding="utf-8"))


def _public_key_fingerprint(public_key: ed25519.Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_audit_checkpoint(
    audit_log_paths: Sequence[str | Path],
    audit_public_key_pem: str,
    checkpoint_signing_key: ed25519.Ed25519PrivateKey,
) -> dict[str, Any]:
    """Verify one or more worker chains and sign their terminal state.

    The result provides a common checkpoint for independently ordered worker
    chains. It intentionally does not invent a global event order.
    """
    from llm_shield_proxy.compliance.report import verify_worm_log

    if not audit_log_paths:
        raise ValueError("At least one audit log is required")

    chains: list[dict[str, Any]] = []
    for raw_path in sorted((Path(item) for item in audit_log_paths), key=lambda item: item.name):
        summary = verify_worm_log(str(raw_path), audit_public_key_pem)
        if summary["chain_valid"] is not True:
            raise ValueError(f"Audit chain failed verification: {raw_path.name}")
        if summary["total_events"] < 1 or summary["signatures_valid"] != summary["total_events"]:
            raise ValueError(f"Audit chain is empty or not fully signed: {raw_path.name}")
        if len(summary["chain_ids_seen"]) != 1:
            raise ValueError(f"Expected one chain_id in {raw_path.name}")

        chains.append(
            {
                "chain_id": summary["chain_ids_seen"][0],
                "first_sequence": summary["first_sequence"],
                "last_sequence": summary["last_sequence"],
                "terminal_hash": summary["terminal_hash"],
                "total_events": summary["total_events"],
                "first_timestamp": summary["first_timestamp"],
                "last_timestamp": summary["last_timestamp"],
                "audit_key_fingerprints": summary["fingerprints_seen"],
                "source_name": raw_path.name,
                "source_sha256": _sha256_file(raw_path),
            }
        )

    unsigned: dict[str, Any] = {
        "schema": "llm-shield.audit-checkpoint/v1.0.0",
        "checkpoint_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ordering": "independent per-worker chains; no global event order asserted",
        "chains": chains,
        "total_chains": len(chains),
        "total_events": sum(item["total_events"] for item in chains),
    }
    canonical = _canonical_bytes(unsigned)
    manifest_hash = hashlib.sha256(canonical).hexdigest()
    signature = base64.b64encode(checkpoint_signing_key.sign(canonical)).decode("ascii")
    public_key = checkpoint_signing_key.public_key()
    return {
        **unsigned,
        "manifest_hash": manifest_hash,
        "signature": signature,
        "signing_key_fingerprint": _public_key_fingerprint(public_key),
    }


def verify_audit_checkpoint(checkpoint: dict[str, Any], public_key_pem: str) -> dict[str, Any]:
    """Verify a checkpoint's canonical hash, signature, and basic shape."""
    result = {
        "schema_valid": checkpoint.get("schema") == "llm-shield.audit-checkpoint/v1.0.0",
        "hash_valid": False,
        "signature_valid": False,
        "fingerprint_valid": False,
        "chains": len(checkpoint.get("chains", [])) if isinstance(checkpoint.get("chains"), list) else 0,
    }
    signature = checkpoint.get("signature")
    declared_hash = checkpoint.get("manifest_hash")
    declared_fingerprint = checkpoint.get("signing_key_fingerprint")
    unsigned = {
        key: value
        for key, value in checkpoint.items()
        if key not in {"manifest_hash", "signature", "signing_key_fingerprint"}
    }
    canonical = _canonical_bytes(unsigned)
    result["hash_valid"] = hashlib.sha256(canonical).hexdigest() == declared_hash

    loaded = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(loaded, ed25519.Ed25519PublicKey):
        raise TypeError("Checkpoint public key is not Ed25519")
    result["fingerprint_valid"] = _public_key_fingerprint(loaded) == declared_fingerprint
    try:
        loaded.verify(base64.b64decode(signature, validate=True), canonical)
        result["signature_valid"] = True
    except (InvalidSignature, binascii.Error, TypeError, ValueError):
        result["signature_valid"] = False

    result["valid"] = all(
        result[key] for key in ("schema_valid", "hash_valid", "signature_valid", "fingerprint_valid")
    )
    return result


def write_audit_checkpoint(checkpoint: dict[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
