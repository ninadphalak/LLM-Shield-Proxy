"""Minimal Envoy ext_proc betterproto dataclasses for grpclib to maintain < 60 MB RSS."""
import betterproto
from typing import AsyncIterator, Dict, List, Optional
import grpclib.server


class HeaderValueOption(betterproto.Message):
    header: "HeaderValue" = betterproto.message_field(1)
    append_action: "HeaderValueOptionHeaderAppendAction" = betterproto.enum_field(2)

class HeaderValueOptionHeaderAppendAction(betterproto.Enum):
    APPEND_IF_EXISTS_OR_ADD = 0
    ADD_IF_ABSENT = 1
    OVERWRITE_IF_EXISTS_OR_ADD = 2
    OVERWRITE_IF_EXISTS = 3

class HeaderValue(betterproto.Message):
    key: str = betterproto.string_field(1)
    value: str = betterproto.string_field(2)
    raw_value: bytes = betterproto.bytes_field(3)

class HttpHeaders(betterproto.Message):
    headers: "HeaderMap" = betterproto.message_field(1)
    end_of_stream: bool = betterproto.bool_field(2)

class HeaderMap(betterproto.Message):
    headers: List[HeaderValue] = betterproto.message_field(1)

class HttpBody(betterproto.Message):
    body: bytes = betterproto.bytes_field(1)
    end_of_stream: bool = betterproto.bool_field(2)

class CommonResponse(betterproto.Message):
    status: int = betterproto.enum_field(1) # CommonResponseStatus
    header_mutation: "HeaderMutation" = betterproto.message_field(2)
    body_mutation: "BodyMutation" = betterproto.message_field(3)
    clear_route_cache: bool = betterproto.bool_field(4)

class HeaderMutation(betterproto.Message):
    set_headers: List[HeaderValueOption] = betterproto.message_field(1)
    remove_headers: List[str] = betterproto.string_field(2)

class BodyMutation(betterproto.Message):
    body: bytes = betterproto.bytes_field(1, group="mutation")
    clear_body: bool = betterproto.bool_field(2, group="mutation")

class CommonResponseStatus(betterproto.Enum):
    CONTINUE = 0
    CONTINUE_AND_REPLACE = 1

class ProcessingRequest(betterproto.Message):
    async_mode: bool = betterproto.bool_field(1)
    
    request_headers: HttpHeaders = betterproto.message_field(2, group="request")
    response_headers: HttpHeaders = betterproto.message_field(3, group="request")
    request_body: HttpBody = betterproto.message_field(4, group="request")
    response_body: HttpBody = betterproto.message_field(5, group="request")
    request_trailers: HttpHeaders = betterproto.message_field(6, group="request")
    response_trailers: HttpHeaders = betterproto.message_field(7, group="request")

class ProcessingResponse(betterproto.Message):
    request_headers: "HeadersResponse" = betterproto.message_field(1, group="response")
    response_headers: "HeadersResponse" = betterproto.message_field(2, group="response")
    request_body: "BodyResponse" = betterproto.message_field(3, group="response")
    response_body: "BodyResponse" = betterproto.message_field(4, group="response")
    request_trailers: "TrailersResponse" = betterproto.message_field(5, group="response")
    response_trailers: "TrailersResponse" = betterproto.message_field(6, group="response")
    immediate_response: "ImmediateResponse" = betterproto.message_field(7, group="response")
    dynamic_metadata: betterproto.Message = betterproto.message_field(8) # Struct
    mode_override: betterproto.Message = betterproto.message_field(9) # ProcessingMode

class HeadersResponse(betterproto.Message):
    response: CommonResponse = betterproto.message_field(1)

class BodyResponse(betterproto.Message):
    response: CommonResponse = betterproto.message_field(1)

class TrailersResponse(betterproto.Message):
    header_mutation: HeaderMutation = betterproto.message_field(1)

class ImmediateResponse(betterproto.Message):
    status: betterproto.Message = betterproto.message_field(1) # HttpStatus
    headers: HeaderMutation = betterproto.message_field(2)
    body: bytes = betterproto.bytes_field(3)
    grpc_status: betterproto.Message = betterproto.message_field(4) # GrpcStatus
    details: str = betterproto.string_field(5)


class ExternalProcessorBase:
    async def process(self, stream: "grpclib.server.Stream[ProcessingRequest, ProcessingResponse]") -> None:
        raise NotImplementedError()

    def __mapping__(self) -> Dict[str, grpclib.const.Handler]:
        return {
            "/envoy.service.ext_proc.v3.ExternalProcessor/Process": grpclib.const.Handler(
                self.process,
                grpclib.const.Cardinality.STREAM_STREAM,
                ProcessingRequest,
                ProcessingResponse,
            ),
        }
