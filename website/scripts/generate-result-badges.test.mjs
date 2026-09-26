import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {generateResultBadges} from './generate-result-badges.mjs';

test('published badges are stable, conservative, and removed with their rows', () => {
  const root = mkdtempSync(join(tmpdir(), 'result-badges-'));
  try {
    const source = join(root, 'rows.json');
    const output = join(root, 'badges');
    mkdirSync(output);
    writeFileSync(join(output, 'issue-7.json'), '{}');
    writeFileSync(join(output, 'keep.txt'), 'untouched');
    writeFileSync(source, JSON.stringify({entries: [
      {status: 'published', _submission: {issue: 101}, sentN: 6, note: 'not a badge field'},
      {status: 'published', _submission: {issue: 102}, sentN: 0,
        leakWholeN: 0, leakSplitN: 0},
      {status: 'published', _submission: {issue: 103}, sentN: 0,
        leakWholeN: 0.5, leakSplitN: 0},
      {status: 'draft', _submission: {issue: 104}, sentN: 6},
    ]}));
    generateResultBadges(source, output);
    assert.equal(existsSync(join(output, 'issue-7.json')), false);
    assert.equal(existsSync(join(output, 'issue-104.json')), false);
    assert.equal(readFileSync(join(output, 'keep.txt'), 'utf8'), 'untouched');
    const leaked = JSON.parse(readFileSync(join(output, 'issue-101.json'), 'utf8'));
    assert.deepEqual(leaked, {schemaVersion: 1, label: 'PII leak check',
      message: 'leaked', color: 'critical'});
    assert.equal(readFileSync(join(output, 'issue-101.json'), 'utf8').includes('not a badge field'), false);
    const noLeak = JSON.parse(readFileSync(join(output, 'issue-102.json'), 'utf8'));
    assert.deepEqual(noLeak, {schemaVersion: 1, label: 'PII leak check',
      message: 'benchmarked', color: 'blue'});
    const responseLeak = JSON.parse(readFileSync(join(output, 'issue-103.json'), 'utf8'));
    assert.equal(responseLeak.message, 'leaked');
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});

test('invalid and repeated issue numbers fail the build', () => {
  const root = mkdtempSync(join(tmpdir(), 'result-badges-'));
  try {
    const source = join(root, 'rows.json');
    const output = join(root, 'badges');
    writeFileSync(source, JSON.stringify({entries: [
      {status: 'published', _submission: {issue: 1}, sentN: 0},
      {status: 'published', _submission: {issue: 1}, sentN: 6},
    ]}));
    assert.throws(() => generateResultBadges(source, output), /repeated issue number/);
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});
