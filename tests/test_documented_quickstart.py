"""Run the documented "start the proxy, then check it" commands exactly as written.

Every published way to try the proxy answered 401 until 2026-10: the docs started it
without VALID_VIRTUAL_KEYS, or without UPSTREAM_API_KEY for a non-OpenAI upstream, or
ran the check without the client key. Nothing tested the docs, so nobody noticed. These
tests read the commands out of the pages, start the proxy with only the environment the
page sets, run the page's selfcheck command, and require CLEAN.
"""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

from llm_shield_proxy.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_PAGE = REPO_ROOT / "website" / "docs" / "conformance" / "ci.mdx"
FROM_SCRATCH_PAGE = REPO_ROOT / "website" / "docs" / "conformance" / "try-it-from-scratch.md"
README = REPO_ROOT / "README.md"

_PROXY_MAIN = "import sys; from llm_shield_proxy.cli import main; sys.argv[0] = 'llm-shield-proxy'; sys.exit(main())"
_BENCH_MAIN = "import sys; from pii_leak_benchmark.cli import main; sys.argv[0] = 'pii-leak-benchmark'; sys.exit(main())"


def _code_blocks(path: Path, lang: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(rf"^```{lang}\n(.*?)^```", text, flags=re.S | re.M)


def _commands(block: str) -> list[list[str]]:
    """Split a shell block into commands, joining backslash continuations."""
    joined = re.sub(r"\\\n\s*", " ", block)
    return [shlex.split(line) for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _block_with(path: Path, lang: str, needle: str) -> str:
    matches = [b for b in _code_blocks(path, lang) if needle in b]
    assert len(matches) == 1, f"expected one {lang} block containing {needle!r} in {path.name}, found {len(matches)}"
    return matches[0]


def _selfcheck_args(block: str) -> list[str]:
    for words in _commands(block):
        if words[:2] == ["pii-leak-benchmark", "selfcheck"]:
            return words[1:]
    raise AssertionError("no `pii-leak-benchmark selfcheck` command in the block")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _clean_env(extra: dict[str, str]) -> dict[str, str]:
    """The parent environment minus every proxy setting, plus what the page sets.

    Without the strip, a developer's shell or CI job could supply a key the page forgot.
    """
    settings_names = set(Settings.model_fields)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in settings_names and not k.startswith("CONFORMANCE_")
    }
    # Import this checkout, not whatever an editable install elsewhere points at.
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "pii-leak-benchmark")])
    env.update(extra)
    return env


def _run_documented_pair(proxy_env: dict[str, str], doc_port: int, selfcheck: list[str], tmp_path: Path) -> None:
    port = _free_port()
    # A file, not a pipe: nothing drains a pipe while the check runs, and once the
    # proxy's audit lines fill it the proxy blocks mid-request.
    log_path = tmp_path / "proxy.log"
    log_file = log_path.open("w", encoding="utf-8")
    proxy = subprocess.Popen(
        [sys.executable, "-c", _PROXY_MAIN, "--host", "127.0.0.1", "--port", str(port)],
        cwd=tmp_path,
        env=_clean_env(proxy_env),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            assert proxy.poll() is None, f"proxy exited early:\n{log_path.read_text(encoding='utf-8')}"
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "proxy never answered /healthz"
            time.sleep(0.25)

        args = [a.replace(f":{doc_port}/", f":{port}/") for a in selfcheck]
        result = subprocess.run(
            [sys.executable, "-c", _BENCH_MAIN, *args],
            cwd=tmp_path,
            env=_clean_env({}),
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proxy.kill()
            proxy.wait()
        log_file.close()
    proxy_log = log_path.read_text(encoding="utf-8", errors="replace")

    assert result.returncode == 0 and "CLEAN" in result.stdout, (
        f"the documented commands did not produce CLEAN (exit {result.returncode}).\n"
        f"--- selfcheck ---\n{_without_specimens(result.stdout)}{_without_specimens(result.stderr)}\n"
        f"--- proxy (status lines only) ---\n{_status_lines(proxy_log)}"
    )


def _status_lines(proxy_log: str) -> str:
    """Access-log status lines and exception type names, nothing that can carry a value.

    The proxy has just processed the run's synthetic values, so its free-text log is not
    printed. A startup failure is printed whole above: no value has been sent by then.
    """
    keep = re.compile(r'^INFO: +\S+ - "[A-Z]+ \S+ HTTP/[\d.]+" \d{3}|^Unhandled exception on .*exception_type=\w+\)$')
    return "\n".join(line for line in proxy_log.splitlines() if keep.search(line)) + "\n"


def _without_specimens(report: str) -> str:
    """Drop the "What leaked" section, which prints the run's synthetic values.

    Those belong in an operator's terminal, not in a public CI log. The verdict, the
    per-type table and the checks are enough to see what failed.
    """
    kept, skipping = [], False
    for line in report.splitlines():
        if line.strip() == "What leaked, and why it matters":
            skipping = True
        elif skipping and line.strip() in ("How it leaked", "Checks"):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept) + "\n"


@pytest.mark.parametrize(
    "page, needle",
    [(CI_PAGE, "llm-shield-proxy --port"), (README, "--target-api-key sk-demo")],
    ids=["ci.mdx", "README.md"],
)
def test_one_line_start_and_check_reports_clean(page, needle, tmp_path):
    """The ci.mdx LLM-Shield-Proxy block, and the README's "Try it in a minute"."""
    block = _block_with(page, "bash", needle)
    proxy_line = next(w for w in _commands(block) if "llm-shield-proxy" in w and "pip" not in w)
    split = proxy_line.index("llm-shield-proxy")
    env = dict(word.split("=", 1) for word in proxy_line[:split])
    doc_port = int(proxy_line[proxy_line.index("--port") + 1])

    _run_documented_pair(env, doc_port, _selfcheck_args(block), tmp_path)


def test_ci_page_powershell_example_sets_the_same_environment():
    """The PowerShell twin cannot run on Linux CI, so pin it to the bash block it mirrors."""
    bash = _block_with(CI_PAGE, "bash", "llm-shield-proxy --port")
    proxy_line = next(w for w in _commands(bash) if "llm-shield-proxy" in w and "pip" not in w)
    bash_env = dict(word.split("=", 1) for word in proxy_line[: proxy_line.index("llm-shield-proxy")])

    powershell = _block_with(CI_PAGE, "powershell", "Start-Process")
    ps_env = dict(re.findall(r'\$env:(\w+)\s*=\s*"([^"]*)"', powershell))
    assert ps_env == bash_env
    removed = re.search(r"Remove-Item\s+(.+)", powershell)
    assert removed, "the PowerShell block no longer clears the variables it set"
    assert set(re.findall(r"Env:(\w+)", removed.group(1))) == set(bash_env)
    assert _selfcheck_args(powershell.replace("`\n", " ")) == _selfcheck_args(bash)


def from_scratch_docker_run(needle: str) -> tuple[list[str], dict[str, str], int]:
    """One of try-it-from-scratch.md's step 3 `docker run` blocks: (words, -e env, host port)."""
    words = _commands(_block_with(FROM_SCRATCH_PAGE, "bash", needle))[0]
    env, port = {}, 8000
    for flag, value in zip(words, words[1:]):
        if flag == "-e":
            key, _, val = value.partition("=")
            env[key] = val
        if flag == "-p":
            port = int(value.split(":")[-2])
    return words, env, port


def from_scratch_selfcheck() -> list[str]:
    check = _block_with(FROM_SCRATCH_PAGE, "bash", "selfcheck --target-base-url http://localhost:8000/v1")
    selfcheck = [a.replace("localhost", "127.0.0.1") for a in _selfcheck_args(check)]
    return [a for a in selfcheck if a not in ("--json-out", "shield.json")]


@pytest.mark.parametrize("needle", ["--name shield -p", "--name shield --network host"], ids=["docker-desktop", "linux"])
def test_from_scratch_page_shield_step_reports_clean(needle, tmp_path):
    """try-it-from-scratch.md step 3. The page runs the published image; this runs the same
    code from the checkout with the same environment, since the setting names are what break.
    tests/ootb/test_documented_docker.py runs the Linux form in a real container."""
    _, env, doc_port = from_scratch_docker_run(needle)
    env = {key: value.replace("host.docker.internal", "127.0.0.1") for key, value in env.items()}
    _run_documented_pair(env, doc_port, from_scratch_selfcheck(), tmp_path)


def test_compose_demo_key_is_accepted_by_the_compose_proxy():
    """`docker compose up` then `python examples/demo.py`, the README container path.

    The demo needs a real model, so CI cannot run it end to end. What broke was the key:
    the demo sent one the compose proxy did not list. Pin that the two agree.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env_lines = compose["services"]["llm-shield-proxy"]["environment"]
    env = dict(line.split("=", 1) for line in env_lines)
    listed = env.get("VALID_VIRTUAL_KEYS", "")
    default = re.fullmatch(r"\$\{\w+:-([^}]*)\}", listed)
    compose_keys = {k.strip() for k in (default.group(1) if default else listed).split(",") if k.strip()}

    demo = (REPO_ROOT / "examples" / "demo.py").read_text(encoding="utf-8")
    demo_default = re.search(r'os\.getenv\("SHIELD_VIRTUAL_KEY",\s*"([^"]+)"\)', demo)
    assert demo_default, "examples/demo.py no longer reads its proxy key from SHIELD_VIRTUAL_KEY"
    assert demo_default.group(1) in compose_keys

    # The upstream is OpenAI, so the proxy swaps in OPENAI_API_KEY; it must be passed through.
    assert env.get("UPSTREAM_BASE_URL") == "https://api.openai.com"
    assert "OPENAI_API_KEY" in env

    ports = compose["services"]["llm-shield-proxy"]["ports"]
    assert all(str(p).startswith("127.0.0.1:") for p in ports), (
        "the demo key is public, so the compose proxy must not listen beyond this machine"
    )



def test_a_failure_message_carries_no_specimen():
    report = (
        "  LEAK\n\n  What leaked, and why it matters\n\nLEAK  EMAIL reached the model provider\n"
        "      you sent:         someone@example.org\n\n  How it leaked\n    EMAIL  literal\n"
        "\n  Checks\n    sse_validity  pass\n"
    )
    trimmed = _without_specimens(report)
    assert "someone@example.org" not in trimmed
    assert "LEAK" in trimmed and "How it leaked" in trimmed and "sse_validity" in trimmed


def test_a_failure_message_carries_only_proxy_status_lines():
    log = (
        'INFO:     127.0.0.1:5 - "POST /v1/chat/completions HTTP/1.1" 401 Unauthorized\n'
        "PII Engine failure (FAIL_CLOSED): could not parse someone@example.org\n"
        "Unhandled exception on POST /v1/chat/completions (request_id=r1, exception_type=ValueError)\n"
    )
    kept = _status_lines(log)
    assert "someone@example.org" not in kept
    assert '" 401 Unauthorized' in kept and "exception_type=ValueError" in kept
