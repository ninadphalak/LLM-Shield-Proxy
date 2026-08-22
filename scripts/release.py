#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys


def run_command(cmd, shell=False):
    print(f"\n>> Running: {' '.join(cmd) if not shell else cmd}")
    result = subprocess.run(cmd, shell=shell)
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}")
        sys.exit(1)


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
    print(f"\n--- Bumping {args.bump_type} version ---")
    run_command([sys.executable, "-m", "bump_my_version", "bump", args.bump_type])

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
