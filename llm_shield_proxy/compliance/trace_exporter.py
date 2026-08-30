import asyncio
import collections
import hashlib
import logging
import time
from typing import Any, Dict, Optional

import orjson
from opentelemetry import trace

from .transport import BaseGRCTransport

logger = logging.getLogger(__name__)

# Tracer retrieves the centralized TracerProvider configured in observability.tracing
tracer = trace.get_tracer("llm_shield.compliance")


class MerkleTreeWORM:
    """
    Append-only Merkle Tree for Cryptographic Decision Traceability.
    """

    def __init__(self, max_records: int = 10_000):
        self.root_hash = hashlib.sha256(b"init").hexdigest()
        self.records: collections.deque = collections.deque(maxlen=max_records)

    def append_record(self, record: dict) -> str:
        # Deterministic serialization to prevent log/schema injection
        serialized = orjson.dumps(record, option=orjson.OPT_SORT_KEYS)

        # Hash of the new record
        record_hash = hashlib.sha256(serialized).hexdigest()

        # New root hash is hash of (old_root + record_hash)
        combined = f"{self.root_hash}{record_hash}".encode("utf-8")
        self.root_hash = hashlib.sha256(combined).hexdigest()

        # Attach the hashes to the stored record for traceability
        stored_record = {"payload": record, "record_hash": record_hash, "merkle_root": self.root_hash}
        self.records.append(stored_record)
        return self.root_hash


class DecisionTraceExporter:
    def __init__(self, transports: list[BaseGRCTransport] | None = None):
        self.merkle_tree = MerkleTreeWORM()
        self.transports = transports or []
        # Retains strong references to fire-and-forget dispatch tasks so they can't
        # be garbage-collected mid-flight (see record_decision below).
        self._background_tasks: set[asyncio.Task] = set()

    def record_decision(
        self,
        tenant_id: str,
        virtual_key_hash: str,
        redacted_prompt_hash: str,
        tool_name: str,
        rbac_decision: str,
        payload_entropy: float,
        tool_call_id: Optional[str] = None,
        pii_redacted_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Records the RBAC decision in the Merkle Tree and emits OTel spans.
        Note: redacted_prompt_hash is computed upstream to save CPU and passed in here.
        """
        timestamp = time.time()

        decision_record = {
            "Timestamp": timestamp,
            "Tenant_ID": tenant_id,
            "Virtual_Key_Hash": virtual_key_hash,
            "Redacted_Prompt_Hash": redacted_prompt_hash,
            "Tool_Name": tool_name,
            "RBAC_Decision": rbac_decision,
            "Payload_Entropy": payload_entropy,
        }

        # 1. Merkle-Attested append
        new_root = self.merkle_tree.append_record(decision_record)

        # 2. OTel Span Emission
        authorized = rbac_decision.upper() == "ALLOW"
        span_name = "gen_ai.client.operation.tool_call"

        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("gen_ai.client.operation.name", "tool_call")
            span.set_attribute("gen_ai.tool.name", tool_name)
            if tool_call_id:
                span.set_attribute("gen_ai.tool.call.id", tool_call_id)
            span.set_attribute("shield.egress.pii_redacted_count", pii_redacted_count)
            span.set_attribute("shield.rbac.authorized", authorized)
            span.set_attribute("shield.merkle_root", new_root)

        if self.transports:
            # Single-event OSCAL payload
            oscal_delta = {
                "assessment-results": {
                    "uuid": "4b5d2b7c-3f2d-489e-8c5e-855a73507d4b",
                    "metadata": {
                        "title": "LLM-Shield-Proxy Runtime Assessment Delta",
                        "last-modified": f"{timestamp}",
                        "version": "1.0.0",
                        "oscal-version": "1.1.2",
                    },
                    "results": [
                        {
                            "uuid": "5d5f2a1b-3c3b-4f9f-9c7a-9f5b8a5d3f2e",
                            "title": "Continuous RBAC Governance Trace Event",
                            "start": f"{timestamp}",
                            "observations": [
                                {
                                    "title": f"Decision for {tool_name}",
                                    "description": f"RBAC decision: {rbac_decision}",
                                    "methods": ["AUTOMATED"],
                                    "relevant-evidence": [
                                        {
                                            "description": "Merkle Hash Chain",
                                            "properties": [
                                                {"name": "merkle_root", "value": new_root},
                                                {"name": "prompt_hash", "value": redacted_prompt_hash},
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            }

            # Fire-and-forget dispatch
            try:
                loop = asyncio.get_running_loop()
                for transport in self.transports:
                    task = loop.create_task(transport.dispatch(oscal_delta))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
            except RuntimeError:
                # No event loop is running (e.g., in some synchronous tests)
                logger.warning("No running event loop found; skipping async transport dispatch.")

        return decision_record

    def generate_oscal_artifact(self) -> bytes:
        """
        Generates NIST SP 800-53 Rev. 5 Assessment Results JSON schema format.
        """
        oscal_payload = {
            "assessment-results": {
                "uuid": "4b5d2b7c-3f2d-489e-8c5e-855a73507d4b",
                "metadata": {
                    "title": "LLM-Shield-Proxy Runtime Assessment",
                    "last-modified": f"{time.time()}",
                    "version": "1.0.0",
                    "oscal-version": "1.1.2",
                },
                "import-ap": {
                    "href": "https://raw.githubusercontent.com/usnistgov/oscal-content/master/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"
                },
                "results": [
                    {
                        "uuid": "5d5f2a1b-3c3b-4f9f-9c7a-9f5b8a5d3f2e",
                        "title": "Continuous RBAC Governance Trace",
                        "description": "Evidence of mid-stream tool execution governance and PII redaction.",
                        "start": f"{time.time()}",
                        "observations": [
                            {
                                "uuid": f"obs-{idx}",
                                "title": f"Decision for {rec['payload']['Tool_Name']}",
                                "description": f"RBAC decision: {rec['payload']['RBAC_Decision']}",
                                "methods": ["AUTOMATED"],
                                "relevant-evidence": [
                                    {
                                        "description": "Merkle Hash Chain",
                                        "properties": [
                                            {"name": "record_hash", "value": rec["record_hash"]},
                                            {"name": "merkle_root", "value": rec["merkle_root"]},
                                            {"name": "prompt_hash", "value": rec["payload"]["Redacted_Prompt_Hash"]},
                                        ],
                                    }
                                ],
                            }
                            for idx, rec in enumerate(self.merkle_tree.records)
                        ],
                    }
                ],
            }
        }
        return orjson.dumps(oscal_payload, option=orjson.OPT_SORT_KEYS)
