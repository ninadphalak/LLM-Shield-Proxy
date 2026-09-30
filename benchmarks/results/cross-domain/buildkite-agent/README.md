# Buildkite agent 4.0.9 log redactor (expected clean)

`internal/replacer/replacer.go`: a streaming `io.Writer` that keeps `partialMatches` across
`Write` calls and releases them on `Flush`. `xd_replacer_test.go` is dropped into that package
directory of a pinned checkout; same every-split loop as the GitLab row.

- Checkout: `github.com/buildkite/agent` tag `v4.0.9` (`cd9a22c`), Go 1.26.8 (`golang:1.26-alpine`;
  the module requires Go 1.26.5 or newer).

| Case | Whole | Cuts tried | Leaking |
|---|---|---|---|
| email | none | 37 | 0 |
| ssn | none | 28 | 0 |
| pem2048 | none | 1,695 | 0 |

Reproduce:

```
git clone --depth 1 --branch v4.0.9 https://github.com/buildkite/agent.git
cp xd_replacer_test.go agent/internal/replacer/
cd agent && XD_FIXTURES=/path/fixtures.json XD_OUT=/path/report.json go test -run TestXDEverySplit -v ./internal/replacer/
```
