FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

COPY wheelhouse/ /opt/wheelhouse/
COPY requirements.lock /opt/requirements.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
    --no-index --find-links=/opt/wheelhouse --require-hashes -r /opt/requirements.lock

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "llm_shield_proxy.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
