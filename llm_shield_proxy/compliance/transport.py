import abc
import asyncio
import logging

import httpx
import orjson

try:
    import aiofiles
except ImportError:
    aiofiles = None  # type: ignore

logger = logging.getLogger(__name__)


class BaseGRCTransport(abc.ABC):
    """
    Abstract Base Class for GRC Transport Layers.
    """

    @abc.abstractmethod
    async def dispatch(self, oscal_payload: dict):
        pass

    async def aclose(self) -> None:
        """Closes any underlying network connections or open resources."""
        pass


class SidecarFileTransport(BaseGRCTransport):
    """
    Kube Sidecar Native Transport.
    Appends the OSCAL JSON payload as a single line (JSONL format) to a file.
    """

    def __init__(self, file_path: str = "/var/log/llm_shield/oscal.jsonl"):
        self.file_path = file_path

    async def dispatch(self, oscal_payload: dict):
        try:
            line = orjson.dumps(oscal_payload).decode("utf-8") + "\n"
            if aiofiles:
                async with aiofiles.open(self.file_path, mode="a", encoding="utf-8") as f:
                    await f.write(line)
            else:
                # Fallback to standard async I/O if aiofiles is not installed
                def write_sync():
                    with open(self.file_path, mode="a", encoding="utf-8") as f:
                        f.write(line)

                await asyncio.to_thread(write_sync)
        except Exception as e:
            # Do not crash the data plane
            logger.error(f"Failed to write OSCAL payload to {self.file_path}: {e}")


class AsyncWebhookTransport(BaseGRCTransport):
    """
    Asynchronous Webhook Transport.
    Fires a non-blocking POST request to a configured webhook URL.
    """

    def __init__(self, webhook_url: str, timeout: float = 2.0):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=self.timeout)

    async def dispatch(self, oscal_payload: dict):
        try:
            # We reuse the client to avoid exhausting memory with connection pools
            response = await self.client.post(self.webhook_url, json=oscal_payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("GRC webhook transport timed out; the event was not acknowledged")
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "GRC webhook transport received HTTP %s; the event was not acknowledged",
                exc.response.status_code,
            )
        except httpx.RequestError:
            logger.warning("GRC webhook transport request failed; the event was not acknowledged")
        except Exception:
            logger.exception("Unexpected GRC webhook transport error; the event was not acknowledged")

    async def aclose(self) -> None:
        """Gracefully close the underlying HTTP client connection pool."""
        if self.client and not self.client.is_closed:
            await self.client.aclose()
