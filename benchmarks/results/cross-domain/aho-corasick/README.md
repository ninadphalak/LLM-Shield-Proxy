# Rust aho-corasick 1.1.5 `try_stream_replace_all` (expected clean)

`src/util/buffer.rs`: the stream searcher rolls a buffer that always keeps the last N bytes,
N = longest pattern, so a match across two `read` calls is found. `src/main.rs` feeds a reader
that returns the text in exactly two reads cut at every offset.

- Crate `aho-corasick = "=1.1.5"` (Cargo.lock confirms 1.1.5), rustc 1.98.1 (`rust:1-alpine`).

| Case | Whole | Cuts tried | Leaking |
|---|---|---|---|
| email | none | 37 | 0 |
| ssn | none | 28 | 0 |
| pem2048 | none | 1,695 | 0 |

Reproduce: `cargo run --release -- fixtures.json report.json` in this directory.
