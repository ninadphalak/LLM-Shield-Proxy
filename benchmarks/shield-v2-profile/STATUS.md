# LLM-Shield-Proxy v2 profile run -- measured

**Status 2026-09-04: measured, from PyPI.** Results in `../results/v2-response-split/` --
`llm-shield-proxy-1.5.1.json` and `seed-sweep-shield.json`.

## From the index, not from the working tree

The image installs `llm-shield-proxy==1.5.1` from PyPI and copies **no** repo source. That
is the whole point: a local `docker build .` installs `requirements.txt`, which has always
been a superset of the wheel's declared dependencies, so it measures a distribution nobody
can actually download. The first run of this profile against a working-tree build would
have reported a clean gateway.

## The released 1.5.1 cannot be profiled at all as published

`pip install llm-shield-proxy==1.5.1` gives an installation where **every proxied request
returns 500**:

```
File ".../llm_shield_proxy/api/main.py", line 843, in _proxy_catch_all_internal
    from llm_shield_proxy.security.identity import verify_agent_identity
File ".../llm_shield_proxy/security/identity.py", line 18, in <module>
    import jwt
ModuleNotFoundError: No module named 'jwt'
```

- `security/identity.py` imports `jwt` at module scope.
- `api/main.py:843` imports `verify_agent_identity` **inside the request handler**, not
  gated by any setting, so the app starts and `/healthz` answers.
- PyJWT is in `requirements.txt` but was **not** in the wheel's `requires_dist` (38 entries,
  no jwt). The container image and CI install `requirements.txt`, which is why neither saw
  it.

This is the same class of gap `pyproject.toml` already documents for `redis`, one release
later. The guard added then -- `tests/ootb/test_pypi_cli.py` -- could not catch it, because
it imported the ASGI app and a lazily imported module is never reached that way. Verified:
a filesystem walk of the stock 1.5.1 install imports 50 modules and exactly one fails.

Fixed here by declaring `PyJWT>=2.13.0` and by making the guard import **every** module in
the installed package (`tests/ootb/_import_every_module.py`). Proven both directions
against the two images below: exit 1 on `shield-pypi:1.5.1`, exit 0 on
`shield-pypi:1.5.1-jwt`.

`pkgutil.walk_packages` is not usable for that walk -- it swallows the ImportError raised
while importing a subpackage and silently skips its children, which on 1.5.1 reduced 50
modules to 10 and hid the broken one. The helper walks the filesystem instead.

## The recipe

```bash
# Image A: exactly what PyPI gives you. Starts, answers /healthz, 500s on every proxy call.
docker build -f benchmarks/shield-v2-profile/Dockerfile.pypi \
  -t shield-pypi:1.5.1 benchmarks/shield-v2-profile

# Image B: the same wheel plus PyJWT. This is the one that can be profiled, and the
# deviation is declared in the Dockerfile rather than hidden in the result.
docker build -f benchmarks/shield-v2-profile/Dockerfile.pypi-patched \
  -t shield-pypi:1.5.1-jwt benchmarks/shield-v2-profile

MSYS_NO_PATHCONV=1 docker run -d --name shield-v2 --network v2profile \
  --add-host=host.docker.internal:host-gateway -p 8811:8000 \
  -e UPSTREAM_BASE_URL="http://host.docker.internal:8799" \
  -e VALID_VIRTUAL_KEYS="sk-shield-v2-profile" \
  -e OPENAI_API_KEY="sk-dummy-upstream-not-used" \
  -e UPSTREAM_API_KEY="sk-dummy-upstream-not-used" \
  shield-pypi:1.5.1-jwt

V2_GATEWAY_TOKEN=sk-shield-v2-profile python benchmarks/v2_seed_sweep.py --seeds 6 \
  --only llm-shield-proxy-1.5.1 \
  --gateway-url http://127.0.0.1:8811/v1/chat/completions \
  --upstream-port 8799 --model capture \
  --out benchmarks/results/v2-response-split/seed-sweep-shield.json
```

`UPSTREAM_BASE_URL` is operator configuration and is **not** SSRF-validated -- only the
client-supplied `X-Upstream-Base-Url` override goes through `_resolve_and_validate_hostname`
-- so pointing it at a private `host.docker.internal` address works as intended and does not
require weakening anything.

Port 8811, not 8000: something else on this machine holds 8000. Confirm your own process
owns the port (`netstat -ano | grep ":8811 "`) before trusting a response.

## Configuration measured

Defaults except the four variables above. `SHIELD_DEFAULT_MASKING_MODE=SYNTHETIC` (Faker
surrogates), Tier-3 ONNX NER **off** -- names are not redacted. The v2 corpus uses only
EMAIL, SSN and CARDPAN, all Tier 1/2 structured entities, so NER being off does not affect
any scored case. A profile that added a NAME entity would need `ENABLE_TIER3_ONNX_NER=true`
and a model file.

## Result

FidelityRate 1.00, LeakRate 1.00 in both conditions, DeltaFrag 0.00, stable over 6 seeds.
Rehydration works and streaming is preserved (5 SSE events); the response path has no
detector for values the proxy never vaulted, and 1.5.1 has no setting to add one. Full
write-up, including why this row and Portkey's identical row are not the same behaviour, is
in `../results/v2-response-split/README.md`.
