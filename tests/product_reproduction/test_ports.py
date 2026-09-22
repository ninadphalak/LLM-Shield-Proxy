from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor

from benchmarks.product_reproduction.ports import LoopbackCapture


def test_loopback_capture_uses_an_ephemeral_port_and_accepts_connections() -> None:
    capture = LoopbackCapture.start(correlation_id="run-a")
    try:
        assert capture.endpoint.host == "127.0.0.1"
        assert 0 < capture.endpoint.port < 65536
        with socket.create_connection((capture.endpoint.host, capture.endpoint.port), timeout=1):
            pass
    finally:
        capture.stop()


def test_two_simultaneous_captures_never_share_a_host_port() -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(LoopbackCapture.start, correlation_id="run-a")
        second_future = pool.submit(LoopbackCapture.start, correlation_id="run-b")
        first = first_future.result()
        second = second_future.result()
    try:
        assert first.endpoint.port != second.endpoint.port
    finally:
        first.stop()
        second.stop()


def test_stopping_capture_releases_only_its_own_socket() -> None:
    first = LoopbackCapture.start(correlation_id="run-a")
    second = LoopbackCapture.start(correlation_id="run-b")
    first.stop()
    try:
        with socket.create_connection((second.endpoint.host, second.endpoint.port), timeout=1):
            pass
    finally:
        second.stop()
