import abc
import asyncio
import logging

import httpx
import orjson

try:
    import aiofiles
except ImportError:
    aiofiles = None

logger = logging.getLogger(__name__)


class BaseGRCTransport(abc.ABC):
    """
    Abstract Base Class for GRC Transport Layers.
    """

    @abc.abstractmethod
    async def dispatch(self, oscal_payload: dict):
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
            await self.client.post(self.webhook_url, json=oscal_payload)
        except httpx.TimeoutException:
            logger.warning(f"WORM warning: Webhook transport timed out sending to {self.webhook_url}")
        except httpx.RequestError as e:
            logger.warning(f"WORM warning: Webhook transport failed sending to {self.webhook_url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in Webhook transport: {e}")
