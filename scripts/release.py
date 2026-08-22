#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys


def run_command(cmd, shell=False):
    print(f"\n>> Running: {' '.join(cmd) if not shell else cmd}")
    result = subprocess.run(cmd, shell=shell)
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}")
        sys.exit(1)


def bump_version_files(bump_type: str) -> tuple[str, str]:
    with open("pyproject.toml", "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'version = "(\d+)\.(\d+)\.(\d+)"', content)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")

    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    current_version = f"{major}.{minor}.{patch}"

    if bump_type == "major":
        new_version = f"{major + 1}.0.0"
    elif bump_type == "minor":
        new_version = f"{major}.{minor + 1}.0"
    else:  # patch
        new_version = f"{major}.{minor}.{patch + 1}"

    print(f"\n--- Bumping {bump_type} version: {current_version} -> {new_version} ---")

    # 1. Update pyproject.toml
    content = content.replace(f'version = "{current_version}"', f'version = "{new_version}"')
    content = content.replace(f'current_version = "{current_version}"', f'current_version = "{new_version}"')
    with open("pyproject.toml", "w", encoding="utf-8") as f:
        f.write(content)

    # 2. Update main.py
    main_path = os.path.join("llm_shield_proxy", "api", "main.py")
    if os.path.exists(main_path):
        with open(main_path, "r", encoding="utf-8") as f:
            main_content = f.read()
        main_content = main_content.replace(f'APP_VERSION = "{current_version}"', f'APP_VERSION = "{new_version}"')
        with open(main_path, "w", encoding="utf-8") as f:
            f.write(main_content)

    # 3. Update README.md
    if os.path.exists("README.md"):
        with open("README.md", "r", encoding="utf-8") as f:
            readme_content = f.read()
        readme_content = readme_content.replace(f'version":"{current_version}"', f'version":"{new_version}"')
        readme_content = readme_content.replace(f'v{current_version}.zip', f'v{new_version}.zip')
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(readme_content)

    # 4. Update Chart.yaml
    chart_path = os.path.join("deploy", "helm", "llm-shield-proxy", "Chart.yaml")
    if os.path.exists(chart_path):
        with open(chart_path, "r", encoding="utf-8") as f:
            chart_content = f.read()
        chart_content = chart_content.replace(f'appVersion: "{current_version}"', f'appVersion: "{new_version}"')
        with open(chart_path, "w", encoding="utf-8") as f:
            f.write(chart_content)

    # Git commit and tag
    run_command(["git", "add", "pyproject.toml", main_path, "README.md", chart_path])
    run_command(["git", "commit", "--no-verify", "-m", f"chore(release): bump version to {new_version}"])
    run_command(["git", "tag", f"v{new_version}"])

    return current_version, new_version


def main():
    parser = argparse.ArgumentParser(description="Automate version bump, PyPI publish, and GitHub push.")
    parser.add_argument(
        "bump_type",
        choices=["patch", "minor", "major"],
        default="patch",
        nargs="?",
        help="The type of version bump to perform (default: patch)",
    )
    parser.add_argument(
        "--skip-pypi",
        "--no-pypi",
        dest="skip_pypi",
        action="store_true",
        help="Skip uploading to PyPI",
    )
    args = parser.parse_args()

    # 1. Bump version
    bump_version_files(args.bump_type)

    # 2. Clean dist directory
    dist_dir = "dist"
    if os.path.exists(dist_dir):
        print("\n--- Cleaning old dist directory ---")
        shutil.rmtree(dist_dir)

    # 3. Build package
    print("\n--- Building package ---")
    run_command([sys.executable, "-m", "build"])

    # 4. Upload to PyPI
    if not args.skip_pypi:
        print("\n--- Uploading to PyPI ---")
        # Use shell=True for glob expansion on Windows
        run_command(f"{sys.executable} -m twine upload dist/*", shell=True)
    else:
        print("\n--- Skipping PyPI upload as requested ---")

    # 5. Push to GitHub
    print("\n--- Pushing to GitHub (with tags) ---")
    run_command(["git", "push", "origin", "main", "--tags"])

    print("\n[SUCCESS] Release completed successfully!")


if __name__ == "__main__":
    main()
