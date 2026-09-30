# OpenResty `body_filter_by_lua_block` with a per-chunk `ngx.re.gsub` recipe

The primitive: the handler runs once per output chain link with `ngx.arg[1]` = that chunk and
`ngx.arg[2]` = eof (lua-nginx-module README, `body_filter_by_lua` section;
`src/ngx_http_lua_bodyfilterby.c:420-421`). `nginx.conf` applies the three patterns to each
chunk the way public recipes do (EOTK's `edit_map` loop, `templates.d/nginx.conf.txt:512-516`;
nutgaard/delegatedlogin `mask_log.lua:8`), with no state between calls.

- `openresty/openresty:alpine` pulled 2026-09-30, `nginx version: openresty/1.31.1.1`, image
  digest `sha256:b3a6f1f432eabdbda4adcb6ec3e6461e621782eaa82dbe67154ddd1afd109569`.
- Origin `split_upstream.py` in a `python:3-alpine` container on the same Docker network,
  60 ms pause between the two writes; the run script substitutes `XD_ORIGIN_HOST`.

| `proxy_buffering` | Case | Whole | Cuts tried | Leaking | Leak classes |
|---|---|---|---|---|---|
| on (default) | email, ssn, pem2048 | none | 37, 28, 1,695 | 0, 0, 0 | |
| off | email (20) | none | 37 | 15 | 11 whole, 4 fragment |
| off | ssn (11) | none | 28 | 10 | 10 whole |
| off | pem2048 (1,678) | none | 1,695 | 1,677 | 1,677 whole |

Reading: with `proxy_buffering on` and a body smaller than the proxy buffers, nginx delivered
the body to the filter chain in one piece and the recipe was clean; that is buffering hiding
the defect, not the recipe carrying state. With `proxy_buffering off` (the streaming setting,
also what `X-Accel-Buffering: no` selects) every cut inside the value leaked, the same shape
as proxy.py. Bodies larger than `proxy_buffers` reach the filter in pieces even with buffering
on; not measured here.

Reports: `report-proxy_buffering-on.json`, `report-proxy_buffering-off.json`.
