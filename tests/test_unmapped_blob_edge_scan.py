"""An oversized blob in an unclaimed field is the only input that skips the edge scan.

Two ceilings, both 8,192, working against each other:

- `PAYLOAD_MAX_REDACT_STRING_LENGTH` gates ENTRY to the detector, in `_deep_redact`.
- `MAX_BASE64_INSPECTION_CHARS` gates DECODING inside it.

The outer gate stops the blob before the inner gate's edge scan can run, so a 12 KB base64
blob carrying an address is caught when it reaches `detect_spans` and forwarded verbatim
when it sits in a field no policy claims. None of the encoding coverage added in 1.6.6
applies there.

Percent runs, base64 candidates and HTML-entity runs all decode their two 256-char edges
past their own caps, each for the reason written beside it: a limit that skips is a recipe
for how much padding to add. This path skipped.

Asserting through `redact_payload`, NOT `detect_spans`. `_deep_redact` never calls
`detect_spans` for an oversized string, so a test written against the engine directly would
prove nothing about this path -- the same shape of mistake that cost a debug cycle three
times during the 1.6.6 work.
"""

from __future__ import annotations

import base64
import re

import pytest

from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault

EMAIL = "bob@example.com"
FILLER = b"A" * 9000

_PLACEHOLDER = re.compile(r"\[[A-Z0-9_]+_\d+\]")


@pytest.fixture
def deep_redaction(monkeypatch):
    from llm_shield_proxy.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_DEEP_PAYLOAD_REDACTION", True, raising=False)
    monkeypatch.setattr(settings, "UNMAPPED_BLOB_POLICY", "warn", raising=False)
    return settings


def _oversized(payload: bytes) -> str:
    from llm_shield_proxy.core.config import settings

    blob = base64.b64encode(payload).decode()
    assert len(blob) > settings.PAYLOAD_MAX_REDACT_STRING_LENGTH, "fixture must exceed the gate"
    return blob


def _forward(blob: str) -> str:
    """What an upstream provider would receive in the unclaimed field."""
    out = pii_engine.redact_payload(
        {"model": "gpt-4", "vendor_extra": blob}, Vault(synthetic=False)
    )
    return out["vendor_extra"]


def test_pii_at_the_head_of_an_unclaimed_blob_is_not_forwarded(deep_redaction):
    """Measured before the fix: forwarded verbatim, while the same blob via the engine
    was caught as BASE64_OBFUSCATED_PII."""
    blob = _oversized(EMAIL.encode() + b" " + FILLER)

    assert _forward(blob) != blob, "the blob was forwarded verbatim"


def test_pii_at_the_tail_of_an_unclaimed_blob_is_not_forwarded(deep_redaction):
    """The tail probe must decode rather than smear across a misaligned slice."""
    blob = _oversized(FILLER + b" " + EMAIL.encode())

    assert _forward(blob) != blob


def test_pii_buried_in_the_interior_is_still_forwarded(deep_redaction):
    """Control. The bound is the point.

    Decoding the whole blob is the cost this ceiling exists to avoid. If this ever starts
    failing, the bound has been quietly dropped and the cost with it.
    """
    blob = _oversized(FILLER + EMAIL.encode() + FILLER)

    assert _forward(blob) == blob


def test_a_clean_oversized_blob_is_forwarded_byte_for_byte(deep_redaction):
    """Control. An ordinary attachment must be untouched."""
    blob = _oversized(b"B" * 9000)

    assert _forward(blob) == blob


def test_a_data_uri_is_still_skipped(deep_redaction):
    """A `data:` URI is declared media with a known shape, not an unknown field.

    Blocking or rewriting it would break vision models, which is a worse failure than the
    risk it removes.
    """
    uri = "data:image/png;base64," + base64.b64encode(EMAIL.encode() + FILLER).decode()

    assert _forward(uri) == uri


def test_skip_still_forwards_silently(deep_redaction, monkeypatch):
    """`skip` is an explicit opt-out and stays one, edges or no edges."""
    from llm_shield_proxy.core.config import settings

    monkeypatch.setattr(settings, "UNMAPPED_BLOB_POLICY", "skip", raising=False)
    blob = _oversized(EMAIL.encode() + b" " + FILLER)

    assert _forward(blob) == blob


def test_block_still_rejects(deep_redaction, monkeypatch):
    """`block` is unchanged."""
    from llm_shield_proxy.core.config import settings
    from llm_shield_proxy.engines.pii_engine import UnmappedBlobError

    monkeypatch.setattr(settings, "UNMAPPED_BLOB_POLICY", "block", raising=False)

    with pytest.raises(UnmappedBlobError):
        _forward(_oversized(b"B" * 9000))


def test_the_audit_record_distinguishes_inspected_from_uninspected(deep_redaction):
    """An operator tuning payload_skip_keys must tell the two cases apart.

    "I could not inspect this" and "I inspected the edges and found PII" are different
    events and a single record type conflates them.
    """
    from unittest.mock import patch

    carrying = EMAIL.encode() + b" " + FILLER
    # Deliberately the SAME decoded length, so `size_bytes` is identical and cannot make
    # the records differ on its own. Without this the assertion passes on payload size
    # and proves nothing about whether the two events are distinguishable.
    clean_payload = b"B" * len(carrying)

    with patch(
        "llm_shield_proxy.engines.pii_engine.AuditLogger.log_unmapped_blob"
    ) as logged:
        _forward(_oversized(carrying))
        found = dict(logged.call_args.kwargs)

    with patch(
        "llm_shield_proxy.engines.pii_engine.AuditLogger.log_unmapped_blob"
    ) as logged:
        _forward(_oversized(clean_payload))
        clean = dict(logged.call_args.kwargs)

    assert found.get("size_bytes") == clean.get("size_bytes"), "fixture sizes must match"
    assert found != clean, "the audit record cannot distinguish the two cases"


@pytest.mark.parametrize("trim", [0, 1, 2, 3])
def test_tail_alignment_holds_for_unpadded_blobs(deep_redaction, trim: int):
    """Greptile P1 on #46: the tail slice must be aligned to the blob's own framing.

    An unpadded blob whose length is not a multiple of four starts its tail slice
    mid-group, and the slice then decodes to a shifted smear that matches nothing. Tail
    PII would be forwarded while the code looked like it had checked it. The oversized
    base64 path already aligns its tail offset; this one did not.

    Parametrised across all four length remainders, because only some of them misalign.
    """
    payload = FILLER + b" " + EMAIL.encode() + (b"x" * trim)
    blob = _oversized(payload).rstrip("=")

    assert _forward(blob) != blob, f"tail PII forwarded at length remainder {len(blob) % 4}"


def test_a_matched_blob_is_not_retained_in_the_vault(deep_redaction):
    """Greptile P1 on #46: the bounded probe must not cause unbounded retention.

    Minting a vault token for the match would hash and keep the WHOLE blob in both token
    maps, and push it to Redis where one is configured. A field near the request-size
    limit would then cost work and plaintext retention proportional to the entire value,
    including the interior this scan never looked at, which is the opposite of what a
    bounded probe is for.
    """
    blob = _oversized(EMAIL.encode() + b" " + FILLER)
    vault = Vault(synthetic=False)

    out = pii_engine.redact_payload({"model": "gpt-4", "vendor_extra": blob}, vault)

    assert out["vendor_extra"] != blob
    assert blob not in vault.token_to_original.values(), "the blob was retained in the vault"
    assert blob not in vault.original_to_token, "the blob was retained in the vault"


def test_a_failed_edge_scan_is_not_recorded_as_clean(deep_redaction, monkeypatch):
    """Greptile P1 on #46: `clean` must mean the probe ran and found nothing.

    An operator who cannot tell a failed inspection from a successful clean one may
    suppress a path that was never inspected at all. Request behaviour stays fail-open;
    only the record changes.
    """
    from unittest.mock import patch

    def _boom(*_args, **_kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(pii_engine, "detect_spans", _boom)
    blob = _oversized(EMAIL.encode() + b" " + FILLER)

    with patch(
        "llm_shield_proxy.engines.pii_engine.AuditLogger.log_unmapped_blob"
    ) as logged:
        out = _forward(blob)

    assert out == blob, "a probe failure must stay fail-open for the request"
    assert logged.call_args.kwargs["edge_scan"] == "failed"
