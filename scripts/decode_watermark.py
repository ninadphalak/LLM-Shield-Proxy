#!/usr/bin/env python3
"""Standalone Forensic Tool for decoding LLM-Shield Canary Watermarks.

Extracts zero-width steganographic characters from text and decodes them
back into the original HMAC-SHA256 hex fingerprint.

Usage:
  cat leaked_text.txt | python decode_watermark.py
  python decode_watermark.py leaked_text.txt
"""

import argparse
import re
import sys


def decode_steganography(text: str) -> str:
    """Finds zero-width watermark sequences and decodes them to hex."""
    # Look for sequences starting and ending with ZWJ (\u200D)
    # containing only ZWSP (\u200B) and ZWNJ (\u200C).
    pattern = re.compile(r"\u200D([\u200B\u200C]+)\u200D")

    matches = pattern.findall(text)
    if not matches:
        return "No watermark detected."

    fingerprints = []
    for match in matches:
        binary_str = match.replace("\u200b", "0").replace("\u200c", "1")

        # Every 4 bits forms one hex character
        if len(binary_str) % 4 != 0:
            fingerprints.append(f"Invalid watermark length ({len(binary_str)} bits)")
            continue

        hex_str = ""
        for i in range(0, len(binary_str), 4):
            nibble = binary_str[i : i + 4]
            hex_str += hex(int(nibble, 2))[2:]

        fingerprints.append(hex_str)

    # Return unique fingerprints found
    unique = list(set(fingerprints))
    if len(unique) == 1:
        return f"Forensic Fingerprint: {unique[0]}"
    return f"Multiple Fingerprints Found: {', '.join(unique)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Decode zero-width steganographic watermarks.")
    parser.add_argument("file", nargs="?", help="File to read from. If omitted, reads from stdin.")
    args = parser.parse_args()

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            print("Reading from stdin. Press Ctrl-D to finish.", file=sys.stderr)
        content = sys.stdin.read()

    result = decode_steganography(content)
    print(result)


if __name__ == "__main__":
    main()
