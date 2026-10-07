"""Re-measure the eight per-target seed sweeps on the current instrument.

`rerun-external-gateway-rows.sh` re-measures the eight SINGLE-RUN gateway rows. These eight
SWEEP files are the other half, and the README calls them "the numbers to cite" -- they
carry the six-seed means and ranges that every noncontrol row in the manuscript's Table I
is taken from. They had no provenance block at all until 2026-09-05 and are now guarded by
`test_every_sweep_records_which_instrument_produced_it`, which is red until they are
re-run on the current inspector.

THEY CANNOT RUN IN PARALLEL. Every one of them pins the capture to port 8799 so the
containerised gateways can reach it at a fixed address, and two runs would fight over that
socket -- the exact failure `_stop` and `Connection: close` were added to close. The
per-seed emitter stages elsewhere in this refresh use ephemeral ports and do run in
parallel; these do not.

EVERY ROW CARRIES THE ENVIRONMENT IT WAS ORIGINALLY MEASURED UNDER. The bearer token,
Portkey's header block and `V2_REQUEST_PATH_REDACTION` are part of the measured
configuration, not decoration. Portkey in particular FAILS OPEN: without its
`x-portkey-config` header it answers 200 having applied no guardrail at all, which reads in
the report as a gateway that redacts nothing. The values below are transcribed from
`rerun-external-gateway-rows.sh`, which read them back out of the published artefacts.

The read deadline is raised for the two transformer-backed targets. LLM Guard's published
p95 is 1.8 s per request against a 10 s default, and a timeout would be recorded as an
inconclusive case -- that is, as a property of the gateway rather than of the machine.

Usage:
    python benchmarks/refresh_gateway_sweeps.py            # all eight
    python benchmarks/refresh_gateway_sweeps.py litellm portkey
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGING = ROOT / "benchmarks" / "results" / "staging-refresh"
CAPTURE_PORT = 8799
SEEDS = 6

PORTKEY_HEADERS = (
    '{"x-portkey-provider":"openai",'
    '"x-portkey-custom-host":"http://host.docker.internal:8799/v1",'
    '"x-portkey-config":"{\\"output_guardrails\\":[{\\"checks\\":'
    '[{\\"id\\":\\"portkey.pii\\",\\"parameters\\":{\\"redact\\":true}}]}]}"}'
)

# name -> (policy, port, model, output file, extra environment)
ROWS: dict[str, tuple[str, int, str, str, dict[str, str]]] = {
    "litellm": (
        "litellm-presidio", 4321, "capture", "seed-sweep-litellm.json",
        {"V2_GATEWAY_TOKEN": "sk-v2-profile-local",
         "V2_REQUEST_PATH_REDACTION": "configured"},
    ),
    "portkey": (
        "portkey-gateway-oss", 8788, "capture", "seed-sweep-portkey.json",
        {"V2_GATEWAY_TOKEN": "sk-dummy",
         "V2_GATEWAY_HEADERS": PORTKEY_HEADERS,
         "V2_REQUEST_PATH_REDACTION": "not-configured"},
    ),
    "nemo": (
        "nemo-guardrails-0.24.0", 9001, "config", "seed-sweep-nemo.json",
        {"V2_REQUEST_PATH_REDACTION": "not-configured",
         "V2_CLIENT_READ_TIMEOUT": "30"},
    ),
    "shield-on": (
        "llm-shield-proxy-1.6.0-response-on", 8813, "capture",
        "seed-sweep-shield-160-on.json",
        {"V2_GATEWAY_TOKEN": "sk-shield-v2-profile",
         "V2_REQUEST_PATH_REDACTION": "configured"},
    ),
    "shield-off": (
        "llm-shield-proxy-1.6.0-response-off", 8814, "capture",
        "seed-sweep-shield-160-off.json",
        {"V2_GATEWAY_TOKEN": "sk-shield-v2-profile",
         "V2_REQUEST_PATH_REDACTION": "configured"},
    ),
    "llm-guard-chunk": (
        "llm-guard-chunk-local", 8790, "capture",
        "seed-sweep-llm-guard-chunk-local.json",
        {"V2_REQUEST_PATH_REDACTION": "configured",
         "V2_CLIENT_READ_TIMEOUT": "45"},
    ),
    "llm-guard-buffered": (
        "llm-guard-buffered", 8792, "capture",
        "seed-sweep-llm-guard-buffered.json",
        {"V2_REQUEST_PATH_REDACTION": "configured",
         "V2_CLIENT_READ_TIMEOUT": "45"},
    ),
    "guardrails": (
        "guardrails-ai-stream-validate", 8791, "capture", "seed-sweep-guardrails.json",
        {"V2_REQUEST_PATH_REDACTION": "not-configured"},
    ),
}


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run(name: str) -> bool:
    policy, port, model, out_name, extra = ROWS[name]
    if not _listening(port):
        print(f"SKIP  {name}: nothing listening on 127.0.0.1:{port}", flush=True)
        return False
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "pii-leak-benchmark") + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    STAGING.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable, "benchmarks/v2_seed_sweep.py",
        "--seeds", str(SEEDS), "--only", policy,
        "--gateway-url", f"http://127.0.0.1:{port}/v1/chat/completions",
        "--upstream-port", str(CAPTURE_PORT), "--model", model,
        "--out", str(STAGING / out_name),
    ]
    if "V2_CLIENT_READ_TIMEOUT" in extra:
        argv += ["--read-timeout", extra["V2_CLIENT_READ_TIMEOUT"]]
    print(f"\n=== {name} -> {out_name}", flush=True)
    started = time.perf_counter()
    result = subprocess.run(argv, cwd=ROOT, env=env, check=False)
    print(f"--- {name}: exit {result.returncode} in {time.perf_counter() - started:.0f}s",
          flush=True)
    # A non-zero exit here is usually the all-cases-refused guard in `v2_seed_sweep`, which
    # is the guard working: a target that answered nothing produces four rates of 0.00 and
    # reads as flawless. Report it and keep going, so one dead container does not cost the
    # other seven sweeps.
    return result.returncode == 0


def main(argv: list[str]) -> int:
    names = argv or list(ROWS)
    unknown = [n for n in names if n not in ROWS]
    if unknown:
        raise SystemExit(f"unknown row(s) {unknown}; known: {list(ROWS)}")
    failed = [name for name in names if not run(name)]
    if failed:
        print(f"\nNOT PRODUCED: {failed}", flush=True)
        print("A missing sweep is a gap to document, not a row to leave stale.", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
