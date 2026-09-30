# Docker json-file 16 KiB split + Fluent Bit 5.1.2 tail + lua redaction

The chunker is the Docker daemon: `daemon/logger/copier.go:22` writes a stdout line longer than
16,384 bytes as several json-file records. Fluent Bit's `tail` input treats each record as one
event unless the `docker` multiline parser rejoins them (`plugins/in_tail/tail.c:626-628`,
`docker_mode` default off). A filter placed on the events sees the fragments.

- Docker server 29.7.2, `alpine` container running `cat /line.txt` with the default json-file
  driver; the log file was read back from `/var/lib/docker/containers/<id>/<id>-json.log`.
- `fluent/fluent-bit:5.1.2` digest `sha256:d792375ca8e53be72fc25716c28f291f32c6fc6f4f31d12d0d14bc78cefe9226`.
- Line: 20,001 bytes, the 2048-bit key (newlines turned into spaces, 1,678 bytes) at 15,584 to
  17,262, so it straddles the 16,384 split (`make_line.py`).
- Filter: `redact.lua`, `string.gsub` of the PEM block on the `log` field.

| Config | Records seen by the filter | Redactions | Leak |
|---|---|---|---|
| `no-multiline.conf` (stock `docker` parser) | 2 (16,384 and 3,617 bytes) | 0 | fragment in both records |
| `multiline.conf` (`multiline.parser docker`) | 1 (18,333 bytes after redaction) | 1 | none |

Reports: `report-no-multiline.json`, `report-multiline.json`.

Reproduce:

```
python make_line.py fixtures.json work/
CID=$(docker run -d -v $PWD/work/line.txt:/line.txt:ro alpine cat /line.txt); docker wait $CID
docker run --rm -v /var/lib/docker/containers:/c:ro alpine cat /c/$CID/$CID-json.log > work/container.log
docker run --rm -v $PWD/work:/logs:ro -v $PWD:/conf:ro fluent/fluent-bit:5.1.2 /fluent-bit/bin/fluent-bit -c /conf/no-multiline.conf
docker run --rm -v $PWD/work:/logs:ro -v $PWD:/conf:ro fluent/fluent-bit:5.1.2 /fluent-bit/bin/fluent-bit -c /conf/multiline.conf
```
