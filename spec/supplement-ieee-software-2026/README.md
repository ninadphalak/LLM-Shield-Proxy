# Reviewer supplement -- IEEE Software submission, 2026

Supporting records for two statements in *Testing LLM Privacy Gateways on the Wire:
Fragmentation-Induced Detection Evasion* that fall **outside** the article's frozen evidence
archive. Everything else in the article traces to that archive.

## The frozen archive is elsewhere, and is unchanged

| | |
|---|---|
| Evidence tag | `v2-evidence-round-8` |
| Commit | `6cbfee3` |
| Harness revision | `0.2.1` |
| Inspector digest | `94262e29a492ab6a` |
| Numeric audit | 1,574 recomputations, zero mismatches |

Both tables and every headline rate in the article come from that tag. **This supplement adds
nothing to it and changes nothing in it.** It exists only because two sentences in the Limits
and Disclosure section refer to work done *after* the tag was cut, and a reviewer should be
able to see what those sentences rest on.

## Item 1 -- the remediation ledger

Article sentence: *"The initial September 17-18 remediation ledger classifies 28 of 29 entries
as defects, including unscanned response carriers absent from this corpus."*

Record: [`REMEDIATION-LEDGER.md`](REMEDIATION-LEDGER.md) -- the 29 entries, their triage
severity, and the pull requests that closed them (#38-#45, public in this repository).

## Item 2 -- the later 1.6.6 profile run

Article sentence: *"Table I's 1.6.0 configurations fail; a later 1.6.6 run at the published
seed passed the unchanged v2 profile with response scanning on. That single-seed result is
outside round 8 and does not establish coverage of omitted carriers."*

Records, both already tracked in this repository:

- [`benchmarks/results/v2-response-split/llm-shield-proxy-1.6.6-response-on.json`](../../benchmarks/results/v2-response-split/llm-shield-proxy-1.6.6-response-on.json)
- [`benchmarks/results/v2-response-split/llm-shield-proxy-1.6.6-response-off.json`](../../benchmarks/results/v2-response-split/llm-shield-proxy-1.6.6-response-off.json)

What they contain:

| | response scanning **on** | response scanning **off** |
|---|---|---|
| Outcome | `pass` | `fail` |
| LeakRate, single-chunk / adversarial | 0.00 / 0.00 | 1.00 / 1.00 |
| FidelityRate | 1.00 | 1.00 |
| DeltaFrag | 0.00 | 0.00 |
| Cases | 32 | 32 |
| Corpus | `minimal-response-split` 0.1.0, sha256 `30efa2eb...9efd3c` | same |
| Seed | `a1b2c3d4e5f60001` (the published seed) | same |
| Harness revision | 0.2.1 | 0.2.1 |
| Inspector digest | `94262e29a492ab6a` | `94262e29a492ab6a` |
| Generated | 2026-09-20T10:44:05Z | 2026-09-20T10:43:45Z |

Fidelity 1.00 on the passing arm is not the trivial kind: `checks.configured_upstream_boundary`
records 32 captured requests, 32 correlated, `uninspectable_requests: 0` and
`leaked_entity_types: []`, so the upstream received masked values while the client still
received the originals.

### The precise scope of this result, and what it is not

- **One seed**, the published seed -- not a multi-seed sweep.
- **One build**, run from a container image -- this pair does not by itself establish a
  separate result for the released PyPI wheel.
- It uses the **unchanged** v2 profile, not an expanded carrier corpus, so it does **not**
  establish coverage of the response carriers the ledger identifies as previously unscanned.
- It is **not** part of round 8 and must not be read as a correction to the 1.6.0 rows the
  article reports in Table I.

An earlier six-seed working-tree-and-wheel run of the same profile was performed on
2026-09-19 and recorded in the author's private working notes, but its reports were written
to a scratch directory and were not retained. **No claim resting on that run is made in the
article**, and the article's sentence was narrowed to the single-seed pair above precisely
because those are the reports that still exist.

## Reproducing the 1.6.6 pair

    docker build -f benchmarks/shield-v2-profile/Dockerfile.pypi       --build-arg SHIELD_VERSION=1.6.6 -t shield-pypi:1.6.6 benchmarks/shield-v2-profile

    docker run -d --name shield-v2       --add-host=host.docker.internal:host-gateway -p 8815:8000       -e UPSTREAM_BASE_URL="http://host.docker.internal:8799"       -e VALID_VIRTUAL_KEYS="sk-shield-v2-profile"       -e OPENAI_API_KEY="sk-dummy-upstream-not-used"       -e UPSTREAM_API_KEY="sk-dummy-upstream-not-used"       -e ENABLE_RESPONSE_PII_REDACTION="true" shield-pypi:1.6.6

The capture binds `127.0.0.1` in `v2_emitter._serve`. Docker Desktop reaches it through
`host.docker.internal`; plain Linux Docker does not, and needs the capture on a routable
address. Tear the gateway down before running the test suite -- a gateway left on 8813/8815
with a capture on 8799 collides with `tests/conformance/test_mode_regression_probe.py`.

## Conflict of interest

The author maintains LLM-Shield-Proxy, one of the systems measured in the article, and wrote
the instrument that measures it. This is disclosed in the article. The specification, harness
and reports are published separately from the gateway.
