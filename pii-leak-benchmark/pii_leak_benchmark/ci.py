"""Run gateway checks with a control, optional baseline, and actionable CI artifacts."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess  # nosec B404 - explicit operator process lifecycle
import sys
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from . import __version__
from .artifact import write_json_artifact
from .operator_profile import PROFILE_VERSION, coverage, seeded_fixture
from .selfcheck import (
    VERDICT_CHECK_FAILED,
    VERDICT_CLEAN,
    VERDICT_LEAK,
    VERDICT_NOT_MEASURED,
    build_parser,
    verdict_for,
)


def instrument_digest() -> str:
    digest = hashlib.sha256()
    for source in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(source.name.encode())
        digest.update(source.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def measured_entities(report: dict[str, Any]) -> dict[str, str]:
    boundary = report["checks"]["configured_upstream_boundary"]
    leaked = set(boundary["leaked_entity_types"]) | set(boundary["unattributed_leaked_entity_types"])
    correlated = bool(boundary["correlated_requests"])
    complete = correlated and not (boundary["uninspectable_requests"] or boundary["unattributed_uninspectable_requests"])
    return {entity: "leak" if correlated and entity in leaked else "contained" if complete else "not measured"
            for entity in sorted(report["fixture"]["formats"])}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if baseline.get("schema") != "pii-leak-benchmark/operator-run/v1" or baseline.get("contract") != current["contract"]:
        raise ValueError("Baseline is incompatible: use the same harness, profile, seed, model, iterations and duty")
    for run in (baseline, current):
        if run.get("verdict") == VERDICT_NOT_MEASURED or "not measured" in run.get("entities", {}).values():
            raise ValueError("Cannot compare an unmeasured run")
    if set(baseline["entities"]) != set(current["entities"]):
        raise ValueError("Baseline coverage differs")
    regressions, improvements = [], []
    for entity, state in current["entities"].items():
        before = baseline["entities"][entity]
        if state == "leak" and before == "contained":
            regressions.append(entity)
        elif state == "contained" and before == "leak":
            improvements.append(entity)
    for check, passed in current["required_checks"].items():
        previous = baseline.get("required_checks", {}).get(check)
        if previous is None:
            raise ValueError("Baseline check inventory differs")
        if previous and not passed:
            regressions.append(check)
        elif not previous and passed:
            improvements.append(check)
    return {"status": "regression" if regressions else "no regression",
            "regressions": regressions, "improvements": improvements,
            "baseline_version": baseline["target_version"], "current_version": current["target_version"]}


def _cell(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_summary(run: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    lines = ["# PII Leak Benchmark", "", f"**{run['verdict']}**", "", run["reason"], ""]
    if "contract" not in run:
        lines.extend(["Fix the setup error and rerun. An incomplete check cannot pass CI.", ""])
        return "\n".join(lines)
    contract = run["contract"]
    lines.extend([f"Profile: `{contract['profile']}` | Duty: `{contract['duty']}` | Seed: `{_cell(contract['seed'])}`", "",
                  "| Data type | Baseline | Current | Next step |", "| :--- | :--- | :--- | :--- |"])
    for entity, state in run["entities"].items():
        before = baseline["entities"].get(entity, "not run") if baseline else "not run"
        next_step = ("Enable or repair request redaction for this format." if state == "leak" else
                     "Check upstream routing, credentials and capture reachability." if state == "not measured" else
                     "No leak observed for this test shape.")
        lines.append(f"| {_cell(entity)} | {_cell(before)} | {_cell(state)} | {next_step} |")
    lines.extend(["", "## Required behavior", ""])
    for check, passed in run["required_checks"].items():
        lines.append(f"- `{check}`: {'pass' if passed else 'FAIL'}")
    if run.get("comparison"):
        comp = run["comparison"]
        lines.extend(["", f"**Comparison: {comp['status']}**", "",
                      "New failures: " + (", ".join(comp["regressions"]) or "none"),
                      "", "Improvements: " + (", ".join(comp["improvements"]) or "none")])
    lines.extend(["", "## What to do next", ""])
    if run["verdict"] == VERDICT_NOT_MEASURED:
        lines.append("Verify the gateway sends this model's requests to the capture URL. Check readiness, routing and authentication.")
    elif run["verdict"] == VERDICT_CHECK_FAILED:
        lines.append("No upstream leak was observed. Check response restoration and SSE framing. Select the anonymize duty only for an intentionally one-way gateway.")
    elif run["verdict"] == VERDICT_LEAK:
        lines.append("Inspect the entity rows above. Reproduce with the same seed after fixing the request redaction policy.")
    else:
        lines.append("Keep this run as a baseline. Run broader response-injection and fragmentation profiles separately.")
    lines.extend(["", "## Coverage limits", "",
                  "Only the listed synthetic formats were tested. No statement is made about other formats, response-injected secrets, logs, storage or other outbound destinations."])
    fixed = [item["entity"] for item in run["coverage"] if item["variation"] == "fixed synthetic specimen"]
    if fixed:
        lines.append("Credential checks use fixed examples: " + ", ".join(fixed) + ". Passing these examples does not establish general credential detection.")
    lines.extend(["", "A no-regression result can still contain existing leaks. Current failures always fail this job.", ""])
    return "\n".join(lines)


@contextlib.contextmanager
def gateway(command: str | None, url: str, env: dict[str, str], timeout: float) -> Iterator[None]:
    process = None
    try:
        parsed = urlsplit(url)
        if url != "capture://self":
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Use an HTTP(S) target URL without embedded credentials, query or fragment")
            if command:
                try:
                    with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=0.5):
                        occupied = True
                except OSError:
                    occupied = False
                if occupied:
                    raise ValueError("Managed gateway port is already occupied; stop it or choose another port")
        if command:
            options: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, shell=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)  # nosec B602 - explicit operator startup command
        if url != "capture://self":
            until = time.monotonic() + timeout
            while True:
                if process and process.poll() is not None:
                    raise ValueError("Gateway startup command exited. Run it locally to inspect its logs.")
                try:
                    with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= until:
                        raise ValueError("Gateway did not become reachable before the readiness timeout") from None
                    time.sleep(0.2)
        yield
    finally:
        if process:
            if os.name == "nt":
                if process.poll() is None:
                    taskkill = shutil.which("taskkill")
                    if taskkill is None:
                        process.terminate()
                    else:
                        subprocess.run([taskkill, "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)  # nosec B603 - only the process created above
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)


EPILOG = """\
Two one-time project inputs, then the checks run themselves:

  --start-command   how CI starts your gateway in the foreground
  --upstream-env    the environment variable your gateway reads its upstream /v1 URL
                    from, if it is not BENCHMARK_UPSTREAM_BASE_URL

Both are passed to the gateway with the capture address already listening, so a
gateway that checks or contacts its provider during startup finds it there. Without
--start-command the gateway must already be running and already be configured to send
its upstream traffic to the capture (default http://127.0.0.1:8765/v1); nothing here
reconfigures a gateway it did not start.

A negative control runs first on every invocation. It measures the no-gateway floor
and must report LEAK; if it does not, no verdict from this run is trusted.

Comparing against the previous version, either way round:

  --baseline-base-url URL [--baseline-start-command CMD]   measure both, this run
  --baseline-report PATH                                   reuse a stored current.json

A live baseline survives environment drift; a stored one is cheaper and weaker.
Comparison never waives a current failure: an existing leak fails this job whether or
not it is new.

Exit status:
  0  CLEAN        every required check passed and the run was attributable
  1  LEAK / CHECK FAILED  observed leakage or a separate behavioural failure
  2  NOT MEASURED nothing reached the capture, or the run could not be trusted

Artifacts land in --out: summary.md, current.json, the raw report per measurement, and
baseline.json when one was used. The summary is appended to GITHUB_STEP_SUMMARY.
"""


def main(argv: list[str] | None = None) -> int:
    from .cli import headers_from_args
    from .http_profile import capture_session, run_http_conformance

    parser = build_parser("pii-leak-benchmark ci")
    parser.description = "Check a gateway, compare an optional previous version, and write a CI summary."
    # selfcheck's epilog tells the operator to configure their gateway's upstream
    # themselves and promises nothing is started for them. Both are wrong here.
    parser.epilog = EPILOG
    parser.add_argument("--out", default="pii-check", help="Fresh artifact directory")
    parser.add_argument("--seed", default="gateway-ci-v1", help="Identical fixtures across baseline and current")
    parser.add_argument("--target-version", default="current")
    parser.add_argument("--start-command", help="Optional foreground gateway command; stopped after the run")
    parser.add_argument("--upstream-env", help="Gateway environment variable to receive the capture /v1 URL")
    parser.add_argument("--readiness-timeout", type=float, default=60)
    baseline_group = parser.add_mutually_exclusive_group()
    baseline_group.add_argument("--baseline-report", help="Previous current.json from this command")
    baseline_group.add_argument("--baseline-base-url", help="Live previous gateway's /v1 URL")
    parser.add_argument("--baseline-start-command")
    parser.add_argument("--baseline-version", default="baseline")
    args = parser.parse_args(argv)
    if args.json_out:
        parser.error("use --out; this command writes raw and operator reports together")
    if args.baseline_start_command and not args.baseline_base_url:
        parser.error("--baseline-start-command requires --baseline-base-url")
    if args.readiness_timeout <= 0 or not 0 < args.capture_port < 65536:
        parser.error("readiness timeout must be positive and capture port must be 1..65535")
    if args.upstream_env and (not args.upstream_env.replace("_", "a").isalnum() or args.upstream_env[0].isdigit()):
        parser.error("invalid upstream environment variable name")
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("artifact directory must be empty; choose a new --out")
    out.mkdir(parents=True, exist_ok=True)
    credentials = args.profile == "pii-secrets-v1" and not args.no_credentials
    profile = "pii-secrets-v1" if credentials else "pii-v1"
    fixture, nonce = seeded_fixture(args.seed, credentials)
    contract = {"profile": profile, "workload_version": PROFILE_VERSION, "duty": args.duty,
                "seed": args.seed, "model": args.target_model, "iterations": args.iterations,
                "harness_version": __version__, "instrument_sha256": instrument_digest(),
                "fixture_sha256": hashlib.sha256(json.dumps([fixture, nonce], sort_keys=True).encode()).hexdigest()}
    base_env = dict(os.environ)
    baseline = None
    run: dict[str, Any] = {"schema": "pii-leak-benchmark/operator-run/v1", "verdict": VERDICT_NOT_MEASURED,
                           "reason": "Run did not complete."}
    exit_code = 2

    def measure(url: str, command: str | None, label: str, version: str) -> dict[str, Any]:
        # Capture first, gateway second. A managed gateway is handed the capture URL
        # as its upstream and a real one resolves, health-checks or lists models there
        # BEFORE it opens its own port; with the capture bound only once measurement
        # began, that gateway exited or timed out and the run blamed the operator.
        # A fresh session per measurement also keeps the baseline's captured traffic
        # out of the candidate's record.
        with capture_session(
            capture_host=args.capture_host, capture_port=args.capture_port,
            capture_public_url=args.capture_public_url,
            capture_token=os.getenv("CONFORMANCE_CAPTURE_TOKEN") or args.capture_token,
            timeout_seconds=args.timeout_seconds,
        ) as capture:
            # The bound address, not the requested one, so the gateway is told where
            # the capture actually is.
            env = dict(base_env)
            env["BENCHMARK_UPSTREAM_BASE_URL"] = capture.advertised_base_url
            if args.upstream_env:
                env[args.upstream_env] = capture.advertised_base_url
            with gateway(command, url, env, args.readiness_timeout):
                report = run_http_conformance(
                    url, api_key=args.target_api_key, model=args.target_model,
                    iterations=args.iterations, timeout_seconds=args.timeout_seconds,
                    session=capture, extra_headers=headers_from_args(args),
                    include_credentials=credentials, fixture_seed=args.seed,
                    implementation_version=version,
                )
        write_json_artifact(out / f"{label}.raw.json", report, indent=2)
        verdict, reason = verdict_for(report, duty=args.duty)
        ignored = {"response_fidelity", "fragmentation_safety"} if args.duty == "anonymize" else set()
        return {"schema": "pii-leak-benchmark/operator-run/v1", "contract": contract,
                "verdict": verdict, "reason": reason, "target_version": version,
                "generated_at": report["generated_at"], "entities": measured_entities(report),
                "required_checks": {name: check["passed"] for name, check in report["checks"].items() if name not in ignored},
                "coverage": coverage(report["fixture"]["formats"])}

    try:
        floor = run_http_conformance("capture://self", capture_port=0, iterations=1,
                                     include_credentials=credentials, fixture_seed=args.seed)
        write_json_artifact(out / "control.raw.json", floor, indent=2)
        if verdict_for(floor)[0] != VERDICT_LEAK or any(state != "leak" for state in measured_entities(floor).values()):
            raise ValueError("Negative control did not detect every fixture type; no gateway verdict can be trusted")
        if args.baseline_report:
            baseline = json.loads(Path(args.baseline_report).read_text(encoding="utf-8"))
        elif args.baseline_base_url:
            baseline = measure(args.baseline_base_url, args.baseline_start_command, "baseline", args.baseline_version)
        if baseline:
            write_json_artifact(out / "baseline.json", baseline, indent=2)
        run = measure(args.target_base_url, args.start_command, "current", args.target_version)
        if baseline:
            run["comparison"] = compare(baseline, run)
        exit_code = 0 if run["verdict"] == VERDICT_CLEAN else 2 if run["verdict"] == VERDICT_NOT_MEASURED else 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Startup commands and credential-bearing URLs are deliberately not echoed.
        run["verdict"] = VERDICT_NOT_MEASURED
        run["reason"] = "Setup or comparison failed: " + str(exc)
    try:
        write_json_artifact(out / "current.json", run, indent=2)
        summary = render_summary(run, baseline)
        (out / "summary.md").write_text(summary, encoding="utf-8")
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
                handle.write(summary + "\n")
        print(summary)
        print(f"Reports: {out}")
    except (OSError, ValueError, KeyError) as exc:
        print(f"NOT MEASURED: could not write the result ({type(exc).__name__}).", file=sys.stderr)
        return 2
    return exit_code
