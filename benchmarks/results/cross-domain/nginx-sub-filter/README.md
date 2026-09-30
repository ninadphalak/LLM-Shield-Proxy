# nginx sub_filter (expected clean)

`src/http/modules/ngx_http_sub_filter_module.c:251-257,720-736`: literal patterns only, with a
hold-back of (longest pattern - 1) bytes carried between output buffers. `nginx.conf` replaces
the email, the SSN and one 64-character line of the PEM (a multi-line literal is not accepted
by the configuration parser; the run script substitutes the key's longest line for `XD_PEM`
and the origin container name for `XD_ORIGIN_HOST`).

- `nginx:alpine` pulled 2026-09-30, `nginx version: nginx/1.31.6`, image `nginx@sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2` (see `report.json` for the
  image digest), origin `split_upstream.py` in a `python:3-alpine` container on the same Docker
  network, 60 ms pause, `proxy_buffering` default (on).

| Case | Whole | Cuts tried | Literal replaced at every cut |
|---|---|---|---|
| email | replaced | 37 | yes (`[REDACTED]` once in every output) |
| ssn | replaced | 28 | yes |
| pem2048 (64-char line) | replaced | 1,695 | yes |

The `leak` column of the pem2048 rows in `report.json` reads `fragment` because the rest of the
key is, by construction, not a pattern here; the literal itself was replaced in all 1,695 rows.
