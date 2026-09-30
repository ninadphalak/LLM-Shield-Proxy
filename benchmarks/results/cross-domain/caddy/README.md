# Caddy replace-response (stream mode) and icholy/replace

Two measurements: the plugin over the wire inside Caddy, and the library it uses on its own.

## Over the wire: clean

- Image built by `Dockerfile`: `caddy:2-builder-alpine` + `xcaddy build --with
  github.com/caddyserver/replace-response@8096288`, on `caddy:2-alpine`; `caddy version` =
  `v2.11.4 h1:XKxkMTgNSizEvKG6QHue6cAsFOteU2qA61w2tKkCWi0=`. `caddy adapt` confirms
  `"handler": "replace_response", "stream": true` for `Caddyfile`.
- Origin `split_upstream.py` in a container on the same network; `XD_ORIGIN_HOST` substituted.

| Report | Gap between the origin's two writes | Case | Whole | Cuts tried | Leaking |
|---|---|---|---|---|---|
| `report.json` | 60 ms | email, ssn | none | 37, 28 | 0, 0 |
| `report.json` | 60 ms | pem2048 (1,678) | none | 1,695 | 0 |
| `report.json` | 60 ms | pem4096 (3,242) | none | 3,259 | 0 |
| `report-pem4096-pause1000ms.json` | 1,000 ms | pem4096, cuts 2,040 to 2,100 into the key | none | 61 | 0 |

## The library alone: bounded at 2,048 bytes

`icholy_inprocess/main.go` drives `replace.RegexpIndexFunc` with `MaxMatchSize = 2048` (the
value `handler.go:197` sets) through `golang.org/x/text/transform.Writer`, two `Write`s cut at
every offset. icholy/replace v0.6.0, x/text v0.42.0, Go 1.26.8.

| Case | Cuts tried | Leaking | First leaking cut |
|---|---|---|---|
| pem2048 (1,678) | 1,695 | 0 | |
| pem4096 (3,242) | 3,259 | 1,194 | 2,049 chars into the key |

`replace.go:223-231`: when no match completes, bytes older than the last `MaxMatchSize` are
flushed; once the BEGIN marker is out, the rest of the key can never match and is forwarded.

## Reading

The bound is real in the library and did not show through Caddy here: Caddy's reverse proxy
path handed the transform the body in one piece even with a one-second gap between the origin's
writes (why was not determined; the plugin's `Write` is per reverse-proxy read, so a read
coalescing upstream of it is the likely place). A different host of the same transformer, or a
response that reaches the transform in pieces, is exposed at cuts deeper than 2,048 bytes into a
match. Not claimed as a live Caddy instance.
