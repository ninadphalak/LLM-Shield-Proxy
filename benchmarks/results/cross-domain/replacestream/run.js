// Every-split measurement of npm `replacestream` (in-process; the filter is a Transform stream).
// Usage: node run.js <fixtures.json> <out.json> [maxMatchLen]
// For each value: run the stream on the whole text, then on every two-part split of the text
// (cut at every offset inside the value, plus a margin on each side). One leaking offset is a leak.
'use strict';
const fs = require('fs');
const path = require('path');
const replaceStream = require('replacestream');
const { classify, masked } = require(path.join(__dirname, '..', 'common', 'leakcheck.js'));

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const patterns = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'common', 'patterns.json'), 'utf8'));
const outPath = process.argv[3];
const maxMatchLen = process.argv[4] ? Number(process.argv[4]) : undefined;

function runOnce(regexSource, chunks) {
  return new Promise((resolve, reject) => {
    const opts = maxMatchLen ? { maxMatchLen } : undefined;
    const rs = replaceStream(new RegExp(regexSource, 'g'), '[REDACTED]', opts);
    let out = '';
    rs.setEncoding('utf8');
    rs.on('data', (d) => { out += d; });
    rs.on('end', () => resolve(out));
    rs.on('error', reject);
    for (const c of chunks) rs.write(c);
    rs.end();
  });
}

(async () => {
  const cases = [
    ['email', 'email', fixtures.email],
    ['ssn', 'ssn', fixtures.ssn],
    ['pem2048', 'pem', fixtures.pem2048],
  ];
  const report = {
    target: 'replacestream',
    version: require('replacestream/package.json').version,
    options: maxMatchLen ? { maxMatchLen } : { maxMatchLen: '100 (library default)' },
    node: process.version,
    splits: 'whole + every single cut from 8 chars before the value to 8 chars after it',
    cases: [],
  };
  for (const [name, patKey, value] of cases) {
    const text = `header line one\nuser record: ${value} ; trailing text\n`;
    const start = text.indexOf(value);
    const whole = await runOnce(patterns[patKey], [text]);
    const rows = [];
    for (let cut = Math.max(1, start - 8); cut <= Math.min(text.length - 1, start + value.length + 8); cut++) {
      const out = await runOnce(patterns[patKey], [text.slice(0, cut), text.slice(cut)]);
      rows.push({ cut, cut_in_value: cut - start, leak: classify(out, value), output: masked(out, value) });
    }
    const leaking = rows.filter((r) => r.leak !== 'none');
    report.cases.push({
      name, pattern: patterns[patKey], value_length: value.length,
      whole: { leak: classify(whole, value), output: masked(whole, value) },
      splits_tried: rows.length, leaking_splits: leaking.length,
      first_leaking_cut_in_value: leaking.length ? leaking[0].cut_in_value : null,
      rows,
    });
    console.log(`${name}: whole=${classify(whole, value)} splits=${rows.length} leaking=${leaking.length}`);
  }
  fs.writeFileSync(outPath, JSON.stringify(report, null, 1));
})();
