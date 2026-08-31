"""verify_worm_log must not certify a log anyone could have written.

The record hash is an unkeyed SHA-256 that a forger can recompute, so hash continuity
alone proves only internal consistency. These cases all exited 0 as "chain_valid" once.
"""

import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from llm_shield_proxy.compliance.report import verify_worm_log

GENESIS = "0" * 64


def _record(sequence, previous_hash, chain_id="forged-chain", event="ATTACKER_CHOSEN_EVENT"):
    body = {
        "chain_id": chain_id,
        "event": event,
        "previous_hash": previous_hash,
        "sequence": sequence,
        "severity": "INFO",
        "timestamp": f"2026-01-01T00:00:0{sequence % 10}Z",
    }
    body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return body


def _write(tmp_path, records, name="audit.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def public_pem():
    key = ed25519.Ed25519PrivateKey.generate().public_key()
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def test_unsigned_log_is_invalid_when_a_key_was_supplied(tmp_path, public_pem):
    previous, records = GENESIS, []
    for sequence in (1, 2, 3):
        record = _record(sequence, previous)
        records.append(record)
        previous = record["hash"]

    result = verify_worm_log(_write(tmp_path, records), public_pem)
    assert result["unsigned_events"] == 3
    assert result["signatures_valid"] == 0
    assert result["chain_valid"] is False


def test_fresh_chain_id_per_record_does_not_bypass_continuity(tmp_path):
    """Giving every record its own chain_id once meant no record had a predecessor."""
    records = [
        _record(sequence, "f" * 64, chain_id=f"chain-{sequence}") for sequence in (9, 1, 4)
    ]
    result = verify_worm_log(_write(tmp_path, records), None)
    assert result["chain_valid"] is False
    assert result["unanchored_chains"] >= 1
    assert any(b["reason"] == "unanchored_chain_start" for b in result["chain_breaks"])


def test_genuine_chain_still_verifies(tmp_path):
    previous, records = GENESIS, []
    for sequence in (1, 2, 3):
        record = _record(sequence, previous, event="PROXY_EVENT")
        records.append(record)
        previous = record["hash"]

    result = verify_worm_log(_write(tmp_path, records), None)
    assert result["chain_valid"] is True
    assert result["chain_breaks"] == []


def test_randomly_seeded_startup_chain_verifies(tmp_path):
    """AuditLogger seeds a fresh chain with a random initial_hash, not a zero genesis."""
    seed = "4b8ddc50f35e6694de8944c3cca2bc21cb9cbefb1d9753be07fda6c13f0464a8"
    first = {
        "chain_id": "runtime-chain",
        "event": "PROXY_STARTUP",
        "initial_hash": seed,
        "previous_hash": seed,
        "sequence": 1,
        "severity": "INFO",
        "timestamp": "2026-01-01T00:00:01Z",
    }
    first["hash"] = hashlib.sha256(json.dumps(first, sort_keys=True).encode("utf-8")).hexdigest()
    second = _record(2, first["hash"], chain_id="runtime-chain", event="PROXY_EVENT")

    result = verify_worm_log(_write(tmp_path, [first, second]), None)
    assert result["chain_valid"] is True, result["chain_breaks"]


def test_reordering_and_deletion_are_detected(tmp_path):
    previous, records = GENESIS, []
    for sequence in (1, 2, 3):
        record = _record(sequence, previous, chain_id="one-chain")
        records.append(record)
        previous = record["hash"]

    dropped = verify_worm_log(_write(tmp_path, [records[0], records[2]], "a.jsonl"), None)
    assert dropped["chain_valid"] is False

    reordered = verify_worm_log(
        _write(tmp_path, [records[0], records[2], records[1]], "b.jsonl"), None
    )
    assert reordered["chain_valid"] is False


def _signing_pair():
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    fingerprint = hashlib.sha256(
        public.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
    ).hexdigest()
    return private, pem, fingerprint


def _signed(private, fingerprint, sequence, previous_hash, event="PROXY_EVENT"):
    """Build a record the way AuditLogger does: append to an already-sorted string."""
    import base64

    body = {
        "chain_id": "prod",
        "event": event,
        "previous_hash": previous_hash,
        "sequence": sequence,
        "severity": "INFO",
        "timestamp": f"2026-01-01T00:00:0{sequence}Z",
    }
    canonical = json.dumps(body, sort_keys=True)
    record_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    hashed = f'{canonical[:-1]}, "hash": "{record_hash}"}}'
    signature = base64.b64encode(private.sign(hashed.encode("utf-8"))).decode("ascii")
    signed = f'{hashed[:-1]}, "signature": "{signature}", "public_key_fingerprint": "{fingerprint}"}}'
    return json.loads(signed), record_hash


def test_declared_anchor_does_not_license_a_chain_per_record(tmp_path):
    """initial_hash is attacker-chosen, so declaring one is not evidence of anything.

    Giving every forged record its own chain_id AND its own declared anchor restored
    the bypass that per-chain continuity was meant to close: no record ever had a
    predecessor, so nothing was checked against anything.
    """
    records = []
    for index in range(3):
        seed = hashlib.sha256(f"seed{index}".encode()).hexdigest()
        body = {
            "chain_id": f"chain-{index}",
            "event": "ATTACKER_WROTE_THIS",
            "initial_hash": seed,
            "previous_hash": seed,
            "sequence": 1,
            "severity": "INFO",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        records.append(body)

    result = verify_worm_log(_write(tmp_path, records), None)
    assert result["chain_valid"] is False
    assert any(b["reason"] == "multiple_chains_in_one_file" for b in result["chain_breaks"])


def test_emptied_log_is_not_valid(tmp_path):
    """Wiping the file is the cleanest tamper there is; it read as chain_valid True."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    blank = tmp_path / "blank.jsonl"
    blank.write_text("\n   \n\n", encoding="utf-8")

    for path in (empty, blank):
        result = verify_worm_log(str(path), None)
        assert result["total_events"] == 0
        assert result["chain_valid"] is False, f"{path.name} certified as valid"
        assert any(b["reason"] == "empty_log" for b in result["chain_breaks"])


@pytest.mark.parametrize("line", ["null", "[1,2,3]", '"a string"', "42"])
def test_non_object_record_is_reported_not_raised(tmp_path, line):
    """A JSON scalar reached record.get() and raised AttributeError out of the verifier."""
    path = tmp_path / "malformed.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    result = verify_worm_log(str(path), None)
    assert result["chain_valid"] is False
    assert any(b["reason"] == "non_object_record" for b in result["chain_breaks"])


def test_continuity_alone_is_not_authenticity(tmp_path):
    previous, records = GENESIS, []
    for sequence in (1, 2, 3):
        record = _record(sequence, previous, chain_id="prod", event="PROXY_EVENT")
        records.append(record)
        previous = record["hash"]

    result = verify_worm_log(_write(tmp_path, records), None)
    assert result["chain_valid"] is True
    assert result["authenticity_verified"] is False


def test_signed_chain_is_authentic(tmp_path):
    private, pem, fingerprint = _signing_pair()
    previous, records = GENESIS, []
    for sequence in (1, 2, 3):
        record, previous = _signed(private, fingerprint, sequence, previous)
        records.append(record)

    result = verify_worm_log(_write(tmp_path, records), pem)
    assert result["chain_valid"] is True
    assert result["authenticity_verified"] is True
    assert result["signatures_valid"] == 3


def _cli(tmp_path, records, pem=None, extra=()):
    from llm_shield_proxy.cli import audit_verify_main

    path = _write(tmp_path, records)
    argv = ["--audit-log", path]
    if pem is not None:
        key_path = tmp_path / "key.pem"
        key_path.write_text(pem, encoding="utf-8")
        argv += ["--pubkey-file", str(key_path)]
    return audit_verify_main(argv + list(extra))


def test_cli_exit_code_requires_authenticity(tmp_path):
    """Exit 0 is what automation reads. A self-consistent unkeyed chain must not earn it."""
    private, pem, fingerprint = _signing_pair()
    previous, signed_records = GENESIS, []
    for sequence in (1, 2, 3):
        record, previous = _signed(private, fingerprint, sequence, previous)
        signed_records.append(record)

    previous, forged = GENESIS, []
    for sequence in (1, 2, 3):
        record = _record(sequence, previous, chain_id="prod", event="ATTACKER_CHOSEN_EVENT")
        forged.append(record)
        previous = record["hash"]

    assert _cli(tmp_path, signed_records, pem) == 0
    assert _cli(tmp_path, forged, pem) == 1, "unsigned records must fail against a key"
    assert _cli(tmp_path, forged) == 1, "continuity without a key must not exit 0"
    assert _cli(tmp_path, forged, extra=["--allow-unsigned"]) == 0
