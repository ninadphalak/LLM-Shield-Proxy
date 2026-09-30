# NVIDIA NeMo Guardrails 0.24.0 with its Presidio-backed output rail, pinned.
#
# `benchmarks/nemo-v2-profile/Dockerfile` is the build the published row ran, and it pins
# nothing. The versions below were read out of that built image with `pip freeze`, and the
# base image is the digest behind `python:3.11-slim` on the machine that built it. The
# `[server]` extra is required: without it the entrypoint exits with "Server dependencies
# are missing".
FROM python:3.11-slim@sha256:00f89b7f96f13d42900483da3253f8fb2e763eed7a0aa5f0358fec9d15d9f10c

RUN pip install --no-cache-dir \
    "nemoguardrails[server]==0.24.0" \
    "presidio-analyzer==2.2.364" \
    "presidio-anonymizer==2.2.364" \
    "spacy==3.8.16" \
    "en_core_web_lg @ https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl#sha256=293e9547a655b25499198ab15a525b05b9407a75f10255e405e8c3854329ab63"

WORKDIR /config
EXPOSE 9000
# --default-config-id is required: without it every request is 422 with "No guardrails
# config_id provided and server has no default configuration".
ENTRYPOINT ["nemoguardrails", "server", "--config", "/config", "--port", "9000", "--default-config-id", "config"]
