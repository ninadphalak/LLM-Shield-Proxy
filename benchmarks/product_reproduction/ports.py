from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass

from .adapters.base import CaptureEndpoint


@dataclass
class LoopbackCapture:
    """A run-owned loopback listener whose host port is assigned by the OS."""

    endpoint: CaptureEndpoint
    _socket: socket.socket
    _lock: threading.Lock
    _stopped: bool = False

    @classmethod
    def start(cls, *, correlation_id: str) -> LoopbackCapture:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(16)
            host, port = listener.getsockname()[:2]
            endpoint = CaptureEndpoint(
                url=f"http://{host}:{port}",
                host=str(host),
                port=int(port),
                correlation_id=correlation_id,
            )
            return cls(endpoint=endpoint, _socket=listener, _lock=threading.Lock())
        except BaseException:
            listener.close()
            raise

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._socket.close()
