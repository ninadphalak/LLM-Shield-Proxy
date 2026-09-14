# Higress `ai-data-masking` v2 profile -- NOT MEASURED, and the published row was withdrawn

**Status 2026-09-05: withdrawn.** `higress-ai-data-masking.json` was removed from
`../results/v2-response-split/`. It is in git history at commit `5a12969` if anyone needs it.

## Why the published row was not a result about Higress

The row reported FidelityRate 1.00, LeakRate 1.00 / 1.00, `detector_blind` on all four
entities: "the gateway does nothing at all", the same quadrant as Portkey.

**The plugin never loaded.** The `WasmPlugin` config written into the container's file
store used YAML **double-quoted** scalars for the regexes, and a double-quoted YAML scalar
processes backslash escapes. `\.` and `\d` are not legal escapes, so the document does not
parse:

```
$ python -c "import yaml; yaml.safe_load(open('ai-data-masking.yaml'))"
yaml.scanner.ScannerError: while scanning a double-quoted scalar
  found unknown escape character '.'
```

So the row measured a config file that never took effect, and published the result as a
property of a named vendor's product. That is precisely the failure
`../results/v2-response-split/README.md` already records twice ("a row that measures the
harness operator's config file is not a result about the product") and precisely what
`.claude/docs-index.md` calls an unretractable smear. It was also one commit from entering
the manuscript: `PAPER-BRIEF.md` already lists Higress among the measured gateways.

Two things made it survive: no config was committed, so nobody could re-read it -- the
commit message for `5a12969` says "the config is committed so the row can be disputed by
rerunning it", and no config file was in that commit -- and
`checks.configured_upstream_boundary` was a hardcoded pass, so the run could not report
that all four protected values had reached the upstream untouched.

## What happens with a config that parses

`ai-data-masking.yaml` here is the same configuration in **single-quoted** YAML, which
passes backslashes through untouched. Verified to parse, and the four regexes reach the
plugin intact. With it installed and the container restarted, Higress answers **HTTP 500**
and forwards an **empty body** to the configured upstream. All 32 cases score
`inconclusive`; nothing is measurable in either direction.

So the honest position is the one `../results/v2-response-split/GATEWAY-LANDSCAPE.md`
already held before the row was published: **not measured, because of an incomplete
configuration on our side, not a limitation of Higress.**

## Reproducing

```bash
docker run -d --name higress-v2 -p 8461:8080 -p 8472:8001 \
  --add-host=host.docker.internal:host-gateway \
  higress-registry.cn-hangzhou.cr.aliyuncs.com/higress/all-in-one:latest

# The console API answers `AuthException: Login required` from outside the image, so the
# three resources are written into the file-backed config store directly.
docker cp mcpbridge-default.yaml   higress-v2:/data/mcpbridges/default.yaml
docker cp ingress-capture.yaml     higress-v2:/data/ingresses/capture.yaml
docker cp ai-data-masking.yaml     higress-v2:/data/wasmplugins/ai-data-masking.yaml
docker restart higress-v2

python -m pii_leak_benchmark.v2_emitter --only higress-ai-data-masking \
  --gateway-url http://127.0.0.1:8461/v1/chat/completions \
  --upstream-port 8799 --model capture --seed a1b2c3d4e5f60001 \
  --out /tmp/higress
```

The registry must be `type: dns`, not `static`: `host.docker.internal` resolves to IPv6
inside the container and a static registry expects an address.

**Do not publish a row from this profile until the 500 is understood.** An `inconclusive`
run is not a result, and a row that says "does nothing" is an accusation.
