# Research and Publications

LLM-Shield-Proxy maintains an open evidence base for streaming privacy gateways, separating practical engineering reports from academic security research.

## Public Artifacts
- [Streaming Privacy Gateway Conformance Specification v1.0.0](/docs/conformance/specification-v1)
- [Reproduction Guide](/docs/conformance/reproducing)
- [Published Benchmark Results](/docs/conformance/results)
- [Audit Evidence-Plane Status](/docs/evidence-plane-status)
- [Split-Boundary Leaks in Streaming Guardrails](/docs/split-boundary-leaks) ([10.5281/zenodo.22909585](https://doi.org/10.5281/zenodo.22909585))

## Tools

- [`chunk-invariance`](https://pypi.org/project/chunk-invariance/): a test assertion that a streaming filter gives the same output however its input is split into chunks. It reports the smallest split that fails. Python, with a TypeScript version in the [repository](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/chunk-invariance-js).
- [`pii-leak-benchmark`](https://pypi.org/project/pii-leak-benchmark/): measures a running streaming gateway. See the [Reproduction Guide](/docs/conformance/reproducing).
- [`mcp-ssrf-check`](https://pypi.org/project/mcp-ssrf-check/): checks an MCP server you operate for `Host` and `Origin` validation, session id binding, and URL-fetching tools that reach loopback. It runs from the command line or as a GitHub Action; see [MCP Egress Screening](/docs/guides/mcp-egress-screening#checking-your-own-server).

## Citation

For the streaming guardrail defect class, cite the report DOI: Phalak, N. (2026). *Split-Boundary Leaks in Streaming Guardrails*. Zenodo. [10.5281/zenodo.22909585](https://doi.org/10.5281/zenodo.22909585). That is a concept DOI and always resolves to the current version.

To cite the software itself, cite the exact repository commit hash used for your evaluation and attach the corresponding conformance report checksum.
