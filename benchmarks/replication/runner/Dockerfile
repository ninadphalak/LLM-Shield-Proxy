# The harness runner for a replication pack.
#
# Build context is `pii-leak-benchmark/` (the harness source in this checkout), so the
# runner measures with the same harness revision the checkout carries. `replicate.py` is
# mounted by each pack's compose file rather than copied, so one script serves every pack.
FROM python:3.12-slim

COPY . /harness
RUN pip install --no-cache-dir "/harness[validate]"

ENTRYPOINT ["python", "/replicate.py"]
