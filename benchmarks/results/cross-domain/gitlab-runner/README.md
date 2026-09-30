# GitLab Runner 19.4.1 job-log masker (expected clean)

`common/buildlogger/internal/masker/masker.go`: one stacked writer per phrase, each carrying its
partial-match state across `Write` calls. `xd_masker_test.go` is dropped into that package
directory of a pinned checkout and run with `go test`; it writes the text as one `Write`, then
as two `Write`s cut at every offset, and checks the output after `Close`.

- Checkout: `gitlab.com/gitlab-org/gitlab-runner` tag `v19.4.1` (`3c39fce`), Go 1.26.8
  (`golang:1.26-alpine`).

| Case | Whole | Cuts tried | Leaking |
|---|---|---|---|
| email | none | 37 | 0 |
| ssn | none | 28 | 0 |
| pem2048 | none | 1,695 | 0 |

Reproduce:

```
git clone --depth 1 --branch v19.4.1 https://gitlab.com/gitlab-org/gitlab-runner.git
cp xd_masker_test.go gitlab-runner/common/buildlogger/internal/masker/
cd gitlab-runner && XD_FIXTURES=/path/fixtures.json XD_OUT=/path/report.json go test -run TestXDEverySplit -v ./common/buildlogger/internal/masker/
```
