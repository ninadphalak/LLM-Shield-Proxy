# Reproduce the Conformance Report

## From a source checkout

```bash
python -m pip install -e .
llm-shield-proxy benchmark \
  --iterations 10000 \
  --json-out CONFORMANCE_LATEST.json
```

The runner performs no public-model call and writes no test PII into the report. Set the exact revision explicitly when running outside GitHub Actions:

```bash
LLM_SHIELD_SOURCE_REVISION=$(git rev-parse HEAD) \
  llm-shield-proxy benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

On PowerShell:

```powershell
$env:LLM_SHIELD_SOURCE_REVISION = git rev-parse HEAD
py -m llm_shield_proxy.cli benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

## Verify the artifact

Confirm that:

1. `schema` ends in `/v1.0.0`;
2. `source_revision` equals the revision tested;
3. all seven `checks` are present and pass;
4. protected vector values are absent;
5. timing scope excludes components not exercised;
6. memory scope distinguishes Python allocations from process RSS.

Use `benchmarks/REPORTING.md` for a production-shaped comparison. Publish unsuccessful runs and deviations alongside successful results.

## Run the OpenAI-compatible HTTP profile

Configure the gateway under test so its upstream base URL is the harness capture service:

```text
http://127.0.0.1:8765/v1              # gateway runs on the host
http://host.docker.internal:8765/v1   # common Docker Desktop host route
```

Then run:

```bash
CONFORMANCE_TARGET_API_KEY=local-evaluation-key \
llm-shield-proxy benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --target-name implementation-under-test \
  --target-version pinned-version \
  --iterations 10 \
  --json-out HTTP_CONFORMANCE.json
```

The command starts a controlled capture upstream on `127.0.0.1:8765`, sends the synthetic
request through the target, and stops the capture server afterward. If a container must reach the
host, bind deliberately with `--capture-host 0.0.0.0` and restrict access with the host firewall.
The report never includes the API key, extra header values, or protected fixture values.

### What the boundary check inspects

Every request the target makes to the capture origin is recorded — any path, any method,
request line as well as body — because a gateway that posts raw values to a sibling route
has leaked them just as surely as one that puts them in the chat payload. Bodies are read
under both `content-length` and chunked framing, decompressed when `content-encoding` says
to, then walked over every JSON type: strings, numbers, dictionary keys, character-code
arrays, and base64, hex or percent-encoded runs found inside strings. Values are matched
literally, across adjacent fragments, and with separators stripped.

A request the harness cannot fully inspect — unparseable, or too large or too deeply nested
to walk within budget — is counted in `uninspectable_requests` and **fails** the boundary
check. Not having looked is not the same as having found nothing.

Each run embeds a random five-word marker in the prompt, and at least three of those words
must come back in a captured request for the boundary check to count it. Without that,
a target can exfiltrate to its real upstream and satisfy the check with one unrelated
request to the capture server. The words are mundane nouns rather than a high-entropy
token, because a conforming gateway's secret and person detectors redact the latter.

Use `--target-base-url capture://self` to publish a raw OpenAI-compatible baseline. It is expected
to fail the configured-upstream privacy check; that negative control proves the results format can
represent a loss rather than only successful project runs.

This profile does not install or configure the target. Publish the target image/package digest,
gateway configuration with secrets removed, command, raw JSON report, and any deviation.

## Contribute an independent reproduction

Open a GitHub Discussion or pull request containing the unmodified JSON artifact, host/runtime description, command, and a statement of affiliation or conflict of interest. Independent artifacts will be listed separately from project-maintainer results.
