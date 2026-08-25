import json

import pytest

from llm_shield_proxy.api.ext_proc_pb import (
    HttpBody,
    ProcessingRequest,
    ProcessingResponse,
)
from llm_shield_proxy.api.grpc_service import ExtProcService


@pytest.mark.asyncio
async def test_ext_proc_redacts_request_body():
    """
    Test that the gRPC ext_proc service intercepts request bodies
    and redacts them via the pii_engine cascade.
    """
    service = ExtProcService()

    # We use a mocked stream approach
    class MockStream:
        def __init__(self, requests):
            self.requests = requests
            self.responses = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.requests:
                raise StopAsyncIteration
            return self.requests.pop(0)

        async def recv_message(self):
            if not self.requests:
                return None
            return self.requests.pop(0)

        def cancel(self):
            pass

        async def send_message(self, response: ProcessingResponse):
            self.responses.append(response)

    # A mock payload with a dummy SSN (Tier 2 Faker replacement target)
    raw_payload = {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "My SSN is 123-45-6789."}]}

    req_body_bytes = json.dumps(raw_payload).encode("utf-8")

    body = HttpBody()
    body.body = req_body_bytes
    body.end_of_stream = True

    mock_request = ProcessingRequest()
    mock_request.request_body = body
    stream = MockStream([mock_request])

    await service.process(stream)  # type: ignore

    assert len(stream.responses) == 1
    resp: ProcessingResponse = stream.responses[0]

    assert resp.request_body is not None
    assert resp.request_body.response is not None
    mutation = resp.request_body.response.body_mutation
    assert mutation is not None

    # The output should be JSON and redacted
    redacted_body = mutation.body
    assert redacted_body is not None

    redacted_json = json.loads(redacted_body.decode("utf-8"))
    redacted_content = redacted_json["messages"][0]["content"]
    assert "123-45-6789" not in redacted_content
