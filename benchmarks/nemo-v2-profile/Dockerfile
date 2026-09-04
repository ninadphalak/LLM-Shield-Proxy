# NVIDIA NeMo Guardrails with its Presidio-backed output rail.
#
# Why this one is worth a row: LiteLLM, LLM-Shield-Proxy and Portkey between them have no
# LOCAL response-side PII detector -- LiteLLM buffers and masks via a Presidio service,
# Shield has no response detector at all, and every Portkey PII check is a remote
# call-out. NeMo runs Presidio in-process as an output rail, which is the quadrant none of
# the other three occupies.
#
# The [server] extra is required and is NOT implied by the base install: without it the
# entrypoint exits with "Server dependencies are missing".
FROM python:3.11-slim

RUN pip install --no-cache-dir "nemoguardrails[server]" \
    "presidio-analyzer" "presidio-anonymizer" \
    && python -m spacy download en_core_web_lg

WORKDIR /config
EXPOSE 9000
ENTRYPOINT ["nemoguardrails", "server", "--config", "/config", "--port", "9000"]
