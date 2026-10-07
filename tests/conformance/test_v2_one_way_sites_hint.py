"""The v2 console names the reason when `no-leak-profile-not-met` is one-way sites only.

Measured 2026-10-06: llm-shield-proxy 1.6.8 to 1.6.11 with the response scan on score
fidelity 0.5, leak 0.0, `no-leak-profile-not-met`. By request site: chat-content 1.0,
unrecognised-key 1.0, system-content 0.0, tool-description 0.0. The gateway redacts the
system prompt and tool descriptions and never restores values echoed from them, on purpose.
The outcome is correct; the row alone sent a reader looking for a restoration bug in the
caller's turns. The hint is printed beside the row by `main()` and touches no scorer, so
the inspector digest does not move.
"""

from __future__ import annotations

from pii_leak_benchmark import v2_emitter as v2


def _report(outcome: str, sites: dict[str, tuple[float, int]]) -> dict:
    return {
        "outcome": outcome,
        "metrics": {
            "by_axis": {
                "request_site": {
                    name: {"fidelity_rate": rate, "echo_observable": observable} for name, (rate, observable) in sites.items()
                }
            }
        },
    }


def test_one_way_sites_only_are_named():
    report = _report(
        "no-leak-profile-not-met",
        {"chat-content": (1.0, 8), "unrecognised-key": (1.0, 8), "system-content": (0.0, 8), "tool-description": (0.0, 8)},
    )
    hint = v2.one_way_sites_hint(report)
    assert hint is not None
    assert hint.startswith("Nothing leaked.")
    assert "system-content and tool-description" in hint
    assert "chat-content and unrecognised-key restored in full" in hint


def test_a_miss_on_the_callers_own_turn_gets_no_hint():
    """A real restoration defect must not be explained away as a design choice."""
    report = _report(
        "no-leak-profile-not-met",
        {"chat-content": (0.5, 8), "unrecognised-key": (1.0, 8), "system-content": (0.0, 8), "tool-description": (0.0, 8)},
    )
    assert v2.one_way_sites_hint(report) is None


def test_other_outcomes_get_no_hint():
    for outcome in ("pass", "fail", "inconclusive"):
        report = _report(outcome, {"chat-content": (1.0, 8), "system-content": (0.0, 8)})
        assert v2.one_way_sites_hint(report) is None


def test_the_hint_lives_outside_the_instrument():
    assert "one_way_sites_hint" not in v2._INSTRUMENTED
    assert v2.inspector_digest() == "94262e29a492ab6a"
