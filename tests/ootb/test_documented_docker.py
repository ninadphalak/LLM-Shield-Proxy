"""The Linux `docker run` of try-it-from-scratch.md step 3, in a real container.

The Docker Desktop form reaches the host through `host.docker.internal`. On Linux that name
maps to the bridge gateway, which cannot reach a capture listening on 127.0.0.1, so the page
gives a host-network form for Linux. This runs that form, with the image built from this
checkout in place of the published one, and the page's own selfcheck command.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.ootb.docker_helpers import REPO_ROOT, container_logs, wait_for_http
from tests.test_documented_quickstart import (
    _status_lines,
    _without_specimens,
    from_scratch_docker_run,
    from_scratch_selfcheck,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="host networking is the Linux form")

_BENCH_MAIN = "import sys; from pii_leak_benchmark.cli import main; sys.argv[0] = 'pii-leak-benchmark'; sys.exit(main())"


def test_linux_docker_form_reports_clean(shield_image, run_container):
    words, _, port = from_scratch_docker_run("--name shield --network host")
    args = words[words.index("shield") + 1 :]
    args[-1] = shield_image  # the published image's place
    name = "shield-documented-linux"
    run_container(name, args)
    wait_for_http(f"http://127.0.0.1:{port}/healthz")

    result = subprocess.run(
        [sys.executable, "-c", _BENCH_MAIN, *from_scratch_selfcheck()],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0 and "CLEAN" in result.stdout, (
        f"exit {result.returncode}\n{_without_specimens(result.stdout)}{_without_specimens(result.stderr)}\n"
        f"--- container (status lines only) ---\n{_status_lines(container_logs(name))}"
    )
