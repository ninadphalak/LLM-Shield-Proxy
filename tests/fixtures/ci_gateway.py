"""Test-only HTTP gateway: a one-way masker with an optional email regression."""

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--leak-email", action="store_true")
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            patterns = [r"\d{3}-\d{2}-\d{4}", r"\d{4}(?:-\d{4}){3}",
                        r"AKIA[A-Z0-9]{16}", r"ghp_[A-Za-z0-9_]{36}", r"xoxb-(?:\d+-)+[A-Za-z0-9]+"]
            if not args.leak_email:
                patterns.append(r"[a-z]{8}@example\.com")
            for message in data["messages"]:
                for pattern in patterns:
                    message["content"] = re.sub(pattern, "[MASKED]", message["content"])
            upstream = os.environ["BENCHMARK_UPSTREAM_BASE_URL"] + "/chat/completions"
            request = Request(upstream, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=20) as response:  # noqa: S310
                body = response.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
