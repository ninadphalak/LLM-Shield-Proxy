"""Envoy ext_proc gRPC Service for zero-egress LLM shielding over UDS."""

import asyncio
import codecs
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import grpclib
import grpclib.server

from llm_shield_proxy.api.ext_proc_pb import (
    BodyMutation,
    BodyResponse,
    CommonResponse,
    CommonResponseStatus,
    ExternalProcessorBase,
    HeadersResponse,
    ProcessingRequest,
    ProcessingResponse,
)
from llm_shield_proxy.engines.crypto_vault import StatelessCryptoVault
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer

logger = logging.getLogger(__name__)

# Global threadpool for offloading CPU-bound tasks (Tier 1/2/3 cascade)
# Dynamically sized to prevent event loop starvation under high concurrency.
MAX_WORKERS = os.cpu_count() or 4
thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ext_proc_pool")


class ExtProcService(ExternalProcessorBase):
    """Envoy External Processor handling HTTP requests and responses."""

    def __init__(self) -> None:
        pass

    async def process(self, stream: "grpclib.server.Stream[ProcessingRequest, ProcessingResponse]") -> None:
        """Bidirectional async generator handling the Envoy stream."""

        # Instantiate a stateful SSERehydrationBuffer per Envoy stream
        # Using StatelessCryptoVault by default for high-throughput UDS deployments
        vault: "Vault" = StatelessCryptoVault()  # type: ignore
        sse_buffer = SSERehydrationBuffer(vault)
        # Stateful decoder to prevent multibyte UTF-8 character splitting across chunk boundaries
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        try:
            async for request in stream:
                if request.request_body.body:
                    # Request Body Phase (Prompt)
                    # Offload the 3-Tier cascade to threadpool
                    redacted_body = await asyncio.get_running_loop().run_in_executor(
                        thread_pool,
                        self._process_request_body,
                        request.request_body.body,
                        vault,  # type: ignore
                    )

                    body_mutation = BodyMutation()
                    body_mutation.body = redacted_body
                    common_res = CommonResponse()
                    common_res.status = CommonResponseStatus.CONTINUE_AND_REPLACE
                    common_res.body_mutation = body_mutation
                    body_res = BodyResponse()
                    body_res.response = common_res
                    proc_res = ProcessingResponse()
                    proc_res.request_body = body_res
                    await stream.send_message(proc_res)

                elif request.response_body.body:
                    # Response Body Phase (SSE Chunks)
                    # Use stateful decoder to prevent data corruption on chunked multibyte sequences
                    is_final = request.response_body.end_of_stream
                    chunk_text = decoder.decode(request.response_body.body, final=is_final)

                    rehydrated_text = sse_buffer.process_delta_text(chunk_text, is_final=is_final)

                    if rehydrated_text:
                        body_mutation = BodyMutation()
                        body_mutation.body = rehydrated_text.encode("utf-8")
                        common_res = CommonResponse()
                        common_res.status = CommonResponseStatus.CONTINUE_AND_REPLACE
                        common_res.body_mutation = body_mutation
                    else:
                        # Hold the chunk (don't emit incomplete tokens)
                        body_mutation = BodyMutation()
                        body_mutation.clear_body = True
                        common_res = CommonResponse()
                        common_res.status = CommonResponseStatus.CONTINUE_AND_REPLACE
                        common_res.body_mutation = body_mutation

                    body_res = BodyResponse()
                    body_res.response = common_res
                    proc_res = ProcessingResponse()
                    proc_res.response_body = body_res
                    await stream.send_message(proc_res)

                else:
                    # Pass-through for headers/trailers if forwarded
                    # In this minimal integration, we just CONTINUE
                    common_res = CommonResponse()
                    common_res.status = CommonResponseStatus.CONTINUE
                    if request.request_headers.headers:
                        head_res = HeadersResponse()
                        head_res.response = common_res
                        proc_res = ProcessingResponse()
                        proc_res.request_headers = head_res
                        await stream.send_message(proc_res)
                    elif request.response_headers.headers:
                        head_res = HeadersResponse()
                        head_res.response = common_res
                        proc_res = ProcessingResponse()
                        proc_res.response_headers = head_res
                        await stream.send_message(proc_res)

        except Exception as exc:
            logger.error(f"Error in ext_proc stream: {exc}", exc_info=True)

    def _process_request_body(self, raw_body: bytes, vault: StatelessCryptoVault) -> bytes:
        """Executes the 3-Tier cascade on the request body (Runs in threadpool)."""
        try:
            payload = json.loads(raw_body.decode("utf-8"))
            redacted_payload = pii_engine.redact_payload(payload, vault)  # type: ignore
            return json.dumps(redacted_payload).encode("utf-8")
        except json.JSONDecodeError as e:
            logger.error(f"Malformed JSON payload blocked: {e}")
            # CRITICAL: Do not fail open! Return safe sanitized error to prevent smuggling.
            return json.dumps({"error": "Malformed JSON payload blocked by LLM-Shield"}).encode("utf-8")
        except Exception as e:
            logger.error(f"Failed to redact request payload: {e}")
            # Failsafe drop
            return b'{"error": "Internal Shield Processing Error"}'


async def serve_ext_proc(sock_path: str = "/var/run/llm-shield/ext_proc.sock") -> grpclib.server.Server:
    """Instantiates and starts the grpclib ext_proc server over UDS or TCP."""
    server = grpclib.server.Server([ExtProcService()])
    import os

    if os.name == "nt":
        await server.start(host="127.0.0.1", port=50051)
        logger.info("gRPC ext_proc server listening on 127.0.0.1:50051 (Windows)")
    else:
        await server.start(path=sock_path)
        logger.info(f"gRPC ext_proc server listening on {sock_path}")
    return server
