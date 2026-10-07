import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {checkEvidenceArchives} from './check-evidence-archives.mjs';

function setup(entries, files = []) {
  const root = mkdtempSync(join(tmpdir(), 'archives-'));
  for (const file of files) {
    mkdirSync(join(root, file, '..'), {recursive: true});
    writeFileSync(join(root, file), '{}\n');
  }
  const source = join(root, 'rows.json');
  writeFileSync(source, JSON.stringify({entries}));
  return {root, source};
}

const row = (issue, archive, status = 'published') => ({
  status,
  _submission: {issue, ...(archive === undefined ? {} : {archive})},
});

test('a row from before the archive, with none named, passes', () => {
  const {root, source} = setup([row(56)]);
  assert.doesNotThrow(() => checkEvidenceArchives(source, root));
});

test('a row whose saved report is present passes', () => {
  const dir = 'benchmarks/results/submitted/7';
  const {root, source} = setup([row(7, dir)], [`${dir}/reports.json`, `${dir}/provenance.json`]);
  assert.doesNotThrow(() => checkEvidenceArchives(source, root));
});

test('the build refuses a row whose saved report is missing', () => {
  const dir = 'benchmarks/results/submitted/7';
  const {root, source} = setup([row(7, dir)], [`${dir}/provenance.json`]);
  assert.throws(() => checkEvidenceArchives(source, root), /7\/reports\.json does not exist/);
});

test("the build refuses a row that names another issue's directory, or any other path", () => {
  const other = 'benchmarks/results/submitted/8';
  const files = [`${other}/reports.json`, `${other}/provenance.json`];
  for (const archive of [other, '../../etc', 'benchmarks/results/submitted/7/../8']) {
    const {root, source} = setup([row(7, archive)], files);
    assert.throws(() => checkEvidenceArchives(source, root), /not this issue's directory/);
  }
});

test('a draft row is not checked', () => {
  const {root, source} = setup([row(7, 'benchmarks/results/submitted/7', 'draft')]);
  assert.doesNotThrow(() => checkEvidenceArchives(source, root));
});
