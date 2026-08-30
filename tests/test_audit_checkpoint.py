from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from llm_shield_proxy.cli import audit_checkpoint_main, checkpoint_verify_main
from llm_shield_proxy.compliance.evidence import (
    build_audit_checkpoint,
    load_ed25519_private_key_file,
    verify_audit_checkpoint,
)


def _write_chain(path, key: ed25519.Ed25519PrivateKey, chain_id: str) -> None:
    public_key = key.public_key()
    fingerprint = hashlib.sha256(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).hexdigest()
    previous_hash = "0" * 64
    lines: list[str] = []
    for sequence in range(1, 3):
        record = {
            "chain_id": chain_id,
            "event": "CHECKPOINT_TEST",
            "instance_id": chain_id,
            "previous_hash": previous_hash,
            "sequence": sequence,
            "severity": "INFO",
            "timestamp": f"2026-08-30T00:00:0{sequence}Z",
        }
        canonical = json.dumps(record, sort_keys=True)
        record_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        hashed = f'{canonical[:-1]}, "hash": "{record_hash}"}}'
        signature = base64.b64encode(key.sign(hashed.encode("utf-8"))).decode("ascii")
        lines.append(
            f'{hashed[:-1]}, "signature": "{signature}", '
            f'"public_key_fingerprint": "{fingerprint}"}}'
        )
        previous_hash = record_hash
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_keys(tmp_path, key: ed25519.Ed25519PrivateKey, stem: str):
    private_path = tmp_path / f"{stem}-private.pem"
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path = tmp_path / f"{stem}-public.pem"
    public_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def test_checkpoint_aggregates_independent_worker_chains_and_verifies(tmp_path):
    audit_key = ed25519.Ed25519PrivateKey.generate()
    checkpoint_key = ed25519.Ed25519PrivateKey.generate()
    _, audit_public_path = _write_keys(tmp_path, audit_key, "audit")
    checkpoint_private_path, checkpoint_public_path = _write_keys(tmp_path, checkpoint_key, "checkpoint")
    worker_a = tmp_path / "worker-a.jsonl"
    worker_b = tmp_path / "worker-b.jsonl"
    _write_chain(worker_a, audit_key, "chain-a")
    _write_chain(worker_b, audit_key, "chain-b")

    checkpoint = build_audit_checkpoint(
        [worker_b, worker_a],
        audit_public_path.read_text(encoding="utf-8"),
        load_ed25519_private_key_file(checkpoint_private_path),
    )

    assert checkpoint["total_chains"] == 2
    assert checkpoint["total_events"] == 4
    assert checkpoint["ordering"].startswith("independent per-worker")
    assert [item["source_name"] for item in checkpoint["chains"]] == ["worker-a.jsonl", "worker-b.jsonl"]
    result = verify_audit_checkpoint(checkpoint, checkpoint_public_path.read_text(encoding="utf-8"))
    assert result["valid"] is True

    checkpoint["chains"][0]["last_sequence"] = 999
    assert verify_audit_checkpoint(
        checkpoint, checkpoint_public_path.read_text(encoding="utf-8")
    )["valid"] is False


def test_checkpoint_cli_round_trip(tmp_path, capsys):
    audit_key = ed25519.Ed25519PrivateKey.generate()
    checkpoint_key = ed25519.Ed25519PrivateKey.generate()
    _, audit_public_path = _write_keys(tmp_path, audit_key, "audit")
    checkpoint_private_path, checkpoint_public_path = _write_keys(tmp_path, checkpoint_key, "checkpoint")
    worker = tmp_path / "worker.jsonl"
    destination = tmp_path / "checkpoint.json"
    _write_chain(worker, audit_key, "chain-cli")

    assert audit_checkpoint_main(
        [
            "--audit-log",
            str(worker),
            "--audit-pubkey-file",
            str(audit_public_path),
            "--signing-key-file",
            str(checkpoint_private_path),
            "--out",
            str(destination),
        ]
    ) == 0
    assert checkpoint_verify_main(
        ["--checkpoint", str(destination), "--pubkey-file", str(checkpoint_public_path)]
    ) == 0
    output = capsys.readouterr().out
    assert "Worker chains: 1" in output
    assert "Checkpoint valid: True" in output


def test_checkpoint_rejects_tampered_worker_chain(tmp_path):
    audit_key = ed25519.Ed25519PrivateKey.generate()
    checkpoint_key = ed25519.Ed25519PrivateKey.generate()
    _, audit_public_path = _write_keys(tmp_path, audit_key, "audit")
    worker = tmp_path / "worker.jsonl"
    _write_chain(worker, audit_key, "chain-tampered")
    records = [json.loads(line) for line in worker.read_text(encoding="utf-8").splitlines()]
    records[1]["event"] = "TAMPERED"
    worker.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    try:
        build_audit_checkpoint(
            [worker],
            audit_public_path.read_text(encoding="utf-8"),
            checkpoint_key,
        )
    except ValueError as exc:
        assert "failed verification" in str(exc)
    else:
        raise AssertionError("Tampered worker chain was accepted")
