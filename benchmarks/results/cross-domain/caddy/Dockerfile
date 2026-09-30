# Caddy with the replace-response plugin pinned to the commit screened on 2026-09-30.
FROM caddy:2-builder-alpine AS builder
RUN xcaddy build --with github.com/caddyserver/replace-response@8096288
FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
