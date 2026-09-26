import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {badgeSvg, checksPassed, generateResultBadges, LABEL} from './generate-result-badges.mjs';

const row = (issue, counts, status = 'published') => ({status, _submission: {issue}, ...counts});

test('a badge counts the three checks and never says "leaked"', () => {
  assert.equal(checksPassed(row(1, {sentN: 0, leakWholeN: 0, leakSplitN: 0})), 3);
  assert.equal(checksPassed(row(2, {sentN: 0, leakWholeN: 0, leakSplitN: 0.5})), 2);
  assert.equal(checksPassed(row(3, {sentN: 6, leakWholeN: 1, leakSplitN: 1})), 0);
  // Unmeasured is not a pass: a request-only row cannot reach 3 of 3.
  assert.equal(checksPassed(row(4, {sentN: 0})), 1);
  for (const passed of [0, 1, 2, 3]) {
    assert.equal(badgeSvg(passed).toLowerCase().includes('leak check'), false);
    assert.equal(badgeSvg(passed).includes('leaked'), false);
  }
});

test('the full pass is gold and shimmers; partial scores are plain', () => {
  const full = badgeSvg(3);
  assert.match(full, /✓ 3 of 3 passed/);
  assert.match(full, /fill="url\(#gold\)"/);
  assert.match(full, /<animate /);
  const partial = badgeSvg(2);
  assert.match(partial, /2 of 3 passed/);
  assert.match(partial, /#1f6feb/);
  assert.equal(partial.includes('<animate'), false);
  assert.match(badgeSvg(0), /#6e7781/);
});

test('the badge carries the benchmark name, not the proxy project', () => {
  assert.equal(LABEL, 'pii-leak-benchmark');
  const svg = badgeSvg(2);
  assert.match(svg, /pii-leak-benchmark/);
  assert.equal(/llm.?shield/i.test(svg), false);
  assert.match(svg, /<title>pii-leak-benchmark: 2 of 3 checks passed<\/title>/);
});

test('published rows get an SVG and an endpoint file; stale files and drafts do not', () => {
  const root = mkdtempSync(join(tmpdir(), 'result-badges-'));
  try {
    const source = join(root, 'rows.json');
    const output = join(root, 'badges');
    mkdirSync(output);
    writeFileSync(join(output, 'issue-7.json'), '{}');
    writeFileSync(join(output, 'issue-7.svg'), '<svg/>');
    writeFileSync(join(output, 'keep.txt'), 'untouched');
    writeFileSync(source, JSON.stringify({entries: [
      row(101, {sentN: 6, leakWholeN: 1, leakSplitN: 1, note: 'not a badge field'}),
      row(102, {sentN: 0, leakWholeN: 0, leakSplitN: 0}),
      row(104, {sentN: 0}, 'draft'),
    ]}));
    generateResultBadges(source, output);
    for (const name of ['issue-7.json', 'issue-7.svg', 'issue-104.json', 'issue-104.svg']) {
      assert.equal(existsSync(join(output, name)), false, name);
    }
    assert.equal(readFileSync(join(output, 'keep.txt'), 'utf8'), 'untouched');
    assert.deepEqual(JSON.parse(readFileSync(join(output, 'issue-101.json'), 'utf8')),
      {schemaVersion: 1, label: 'pii-leak-benchmark', message: '0 of 3 passed', color: 'lightgrey'});
    assert.match(readFileSync(join(output, 'issue-102.svg'), 'utf8'), /3 of 3 passed/);
    assert.equal(readFileSync(join(output, 'issue-101.svg'), 'utf8').includes('not a badge field'), false);
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});

test('invalid and repeated issue numbers, and invalid counts, fail the build', () => {
  const root = mkdtempSync(join(tmpdir(), 'result-badges-'));
  try {
    const source = join(root, 'rows.json');
    const output = join(root, 'badges');
    writeFileSync(source, JSON.stringify({entries: [row(1, {sentN: 0}), row(1, {sentN: 6})]}));
    assert.throws(() => generateResultBadges(source, output), /repeated issue number/);
    writeFileSync(source, JSON.stringify({entries: [row(2, {sentN: 0, leakWholeN: -0.5})]}));
    assert.throws(() => generateResultBadges(source, output), /invalid measured counts/);
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});

test('a check passes only when all of it was measured', () => {
  // Inconclusive response cases were never judged, so neither response check can pass.
  assert.equal(checksPassed(row(5, {sentN: 0, leakWholeN: 0, leakSplitN: 0, responseInconclusive: 8})), 1);
  assert.equal(checksPassed(row(6, {sentN: 0, leakWholeN: 0, leakSplitN: 0, responseInconclusive: 0})), 3);
  assert.throws(() => checksPassed(row(7, {sentN: 0, responseInconclusive: -1})), /invalid measured counts/);
});
