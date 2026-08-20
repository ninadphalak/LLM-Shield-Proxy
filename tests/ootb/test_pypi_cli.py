import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def test_pypi_cli_happy_path():
    # Ensure dist directory is clean before building
    if os.path.exists("dist"):
        shutil.rmtree("dist")

    # 1. Build the wheel
    subprocess.run([sys.executable, "-m", "build"], check=True)

    # Find the built wheel
    dist_dir = Path("dist")
    wheels = list(dist_dir.glob("*.whl"))
    assert wheels, "No wheel found in dist/ directory after build."
    wheel_path = wheels[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        # 2. Install into a temporary virtual environment
        venv.create(tmpdir, with_pip=True)

        # Handle cross-platform venv paths
        if os.name == "nt":
            pip_exe = os.path.join(tmpdir, "Scripts", "pip.exe")
            shield_exe = os.path.join(tmpdir, "Scripts", "llm-shield-proxy.exe")
        else:
            pip_exe = os.path.join(tmpdir, "bin", "pip")
            shield_exe = os.path.join(tmpdir, "bin", "llm-shield-proxy")

        subprocess.run([pip_exe, "install", str(wheel_path)], check=True)

        # 3. Run llm-shield --help and assert exit code is 0
        result = subprocess.run([shield_exe, "--help"], capture_output=True, text=True)
        assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}. Stderr: {result.stderr}"


if __name__ == "__main__":
    test_pypi_cli_happy_path()
