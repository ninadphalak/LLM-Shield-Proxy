# replacestream 4.0.3 (npm)

In-process: `replacestream(regex, "[REDACTED]")` is a Transform stream; `run.js` writes the text
as one chunk, then as two chunks cut at every offset, and reads the output.

- Package: `replacestream@4.0.3` (809,349 weekly downloads on 2026-09-30; a `gulp-replace`
  dependency), repo eugeneware/replacestream `8517483`. Node v24.20.0.
- Hold-back: `index.js:15` `maxMatchLen: 100` by default; `index.js:56` keeps only the last
  100 characters after the last match for the next chunk.

| Report | Case | Whole | Cuts tried | Leaking | Leak class | First leaking cut |
|---|---|---|---|---|---|---|
| `report-default.json` | email (20) | none | 37 | 0 | | |
| `report-default.json` | ssn (11) | none | 28 | 0 | | |
| `report-default.json` | pem2048 (1,678) | none | 1,695 | 1,577 | whole | 101 chars into the key |
| `report-maxmatchlen-4096.json` | pem2048 | none | 1,695 | 0 | | |

Reading: with the default bound, every cut deeper than 100 characters into the key forwards
the whole key unredacted (both pieces pass through unmatched). Values shorter than the bound
are safe. Passing `{maxMatchLen: 4096}` (longer than the longest possible match) removes every
leak; that is the fix pattern.

Reproduce:

```
node ../common/gen_fixtures.js fixtures.json
npm install replacestream@4.0.3
node run.js fixtures.json report-default.json
node run.js fixtures.json report-maxmatchlen-4096.json 4096
```
