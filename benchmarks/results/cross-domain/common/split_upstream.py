"""Origin server that controls where the response body is split on the wire.

GET /split/<case>/<cut>  -> the case text cut at byte offset <cut>: first part, flush, pause,
                            second part. <cut> = 0 sends the whole text in one write.
GET /whole/<case>        -> the whole text in one write.
GET /cases               -> JSON: case name -> {"start": offset of the value, "length": value length}

Cases come from the fixtures file (argv[1]); the pause is argv[3] milliseconds (default 60).
Content-Length is exact, Connection: close, no chunked encoding, so every proxy under test sees
plain TCP boundaries chosen here and nothing else.
"""
import json
import socket
import sys
import threading
import time

fixtures = json.load(open(sys.argv[1], encoding="utf-8"))
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 18080
PAUSE = (int(sys.argv[3]) if len(sys.argv) > 3 else 60) / 1000.0

CASES = {}
for name in ("email", "ssn", "pem2048", "pem4096"):
    value = fixtures[name]
    text = f"header line one\nuser record: {value} ; trailing text\n"
    CASES[name] = (text.encode("utf-8"), text.index(value), len(value))


def handle(conn):
    try:
        conn.settimeout(5)
        req = b""
        while b"\r\n\r\n" not in req:
            more = conn.recv(4096)
            if not more:
                return
            req += more
        path = req.split(b" ", 2)[1].decode()
        if path == "/cases":
            body = json.dumps({k: {"start": s, "length": n} for k, (_, s, n) in CASES.items()}).encode()
            parts = [body]
        else:
            _, kind, case, *rest = path.split("/")
            body, _, _ = CASES[case]
            cut = int(rest[0]) if kind == "split" and rest else 0
            parts = [body[:cut], body[cut:]] if 0 < cut < len(body) else [body]
        head = (
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {sum(len(p) for p in parts)}\r\nConnection: close\r\n\r\n"
        ).encode()
        conn.sendall(head + parts[0])
        for p in parts[1:]:
            time.sleep(PAUSE)
            conn.sendall(p)
    finally:
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        conn.close()


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", PORT))
srv.listen(64)
print(f"split_upstream listening on {PORT}, pause {PAUSE * 1000:.0f} ms", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
