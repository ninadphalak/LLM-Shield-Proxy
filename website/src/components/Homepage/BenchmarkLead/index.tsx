import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type Row = {
  target: string;
  note: string;
  outcome: string;
  tone: 'pass' | 'fail' | 'neutral';
  runs: string;
  status: string;
};

// Every measured row comes from the initial project run, so the counts make the lack of independent
// reproduction explicit.
const ROWS: Row[] = [
  {
    target: 'Raw capture endpoint',
    note: 'negative control, not a product',
    outcome: 'fail',
    tone: 'fail',
    runs: '1 / 1',
    status: 'control',
  },
  {
    target: 'LLM-Shield-Proxy',
    note: 'reference implementation',
    outcome: 'pass',
    tone: 'pass',
    runs: '1 / 1',
    status: 'unreplicated',
  },
  {
    target: 'LiteLLM 1.99.0',
    note: 'default configuration, no guardrail attached',
    outcome: 'redaction-not-enabled',
    tone: 'neutral',
    runs: '1 / 1',
    status: 'unreplicated',
  },
  {
    target: 'LiteLLM 1.99.0 + Presidio',
    note: 'no leak; the values are not restored to the client',
    outcome: 'no-leak-profile-not-met',
    tone: 'neutral',
    runs: '1 / 1',
    status: 'unreplicated',
  },
  {
    target: 'Portkey Gateway OSS 1.15.2',
    note: 'default configuration, no guardrails',
    outcome: 'redaction-not-enabled',
    tone: 'neutral',
    runs: '1 / 1',
    status: 'unreplicated',
  },
  {
    target: 'Portkey Gateway OSS 1.15.2 + redaction',
    note: 'no leak; one-way replacement, tester-authored patterns',
    outcome: 'no-leak-profile-not-met',
    tone: 'neutral',
    runs: '1 / 1',
    status: 'unreplicated',
  },
];

export default function BenchmarkLead(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>The benchmark comes first</span>
          <Heading as="h2" className={styles.title}>
            The first result exposed a biased test fixture
          </Heading>
        </div>

        <div className={styles.story}>
          <p>
            All current comparison rows come from the initial project-run measurement set, so each one is labelled
            <code>unreplicated</code> and published with its configuration and raw report.
          </p>
          <p>
            The prompt used to carry three fixed values, chosen to be safe to publish:{' '}
            <code>person@example.invalid</code>, <code>123-45-6789</code>,{' '}
            <code>4532-1234-5678-9012</code>. Every one is a value a <strong>validating</strong>{' '}
            detector is built to reject: <code>.invalid</code> has no public suffix, that SSN is
            a blacklisted sequence, and the card fails its Luhn checksum. Measured against a
            pinned Presidio at <code>score_threshold: 0.0</code>: no <code>EMAIL_ADDRESS</code>,
            no <code>US_SSN</code>, and no <code>CREDIT_CARD</code>.
          </p>
          <p>
            The reference implementation matched all three without those validation checks. The
            fixture therefore favored shape matching over validated detection. A run against
            LiteLLM + Presidio exposed the issue, the affected row was withheld, the fixture was
            corrected, and every row was rerun.
          </p>
          <p>
            The benchmark also found two defects in this proxy’s streaming hot path. Both
            are fixed and pinned by regression tests. No speed multiplier is published for that
            fix because the original runner and raw samples were not retained.
          </p>
          <p className={styles.weakness}>
            <strong>Known limitation:</strong> a roughly 35-line <code>str.replace</code> shim with
            no general detector can pass all five checks. Values now vary within fixed formats;
            broader format variation was rejected after two of six variants produced false leak
            results against a correctly redacting gateway.
          </p>
          <div className={styles.storyLinks}>
            <Link to="/docs/conformance/fixture-threat-model">Read the full fixture threat model →</Link>
          </div>
        </div>

        <div className={styles.runIt}>
          <div className={styles.runItText}>
            <Heading as="h3">Run it against your own gateway</Heading>
            <p>
              Standard library plus <code>httpx</code>. You should not have to install one
              gateway to measure another, and nothing in the harness imports the proxy.
            </p>
          </div>
          <pre className={styles.command}>
            <code>
              pip install pii-leak-benchmark{'\n'}
              {'\n'}
              # negative control: boundary check MUST report a leak{'\n'}
              pii-leak-benchmark --target-base-url capture://self{'\n'}
              {'\n'}
              # your gateway, pointed at the capture upstream{'\n'}
              pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1
            </code>
          </pre>
        </div>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Target</th>
                <th>Outcome</th>
                <th>Runs / submitters</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => (
                <tr key={row.target + row.note}>
                  <td>
                    <strong>{row.target}</strong>
                    <div className={styles.note}>{row.note}</div>
                  </td>
                  <td>
                    <code className={styles[row.tone]}>{row.outcome}</code>
                  </td>
                  <td>{row.runs}</td>
                  <td>{row.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className={styles.floor}>
          <strong>Every measured row is currently unreplicated.</strong> A gateway needs 3 runs
          from 3 distinct submitters before its result is treated as replicated. `fail` means one thing only:
          protected data reached the capture. A gateway that never claimed to redact, or that
          anonymizes one way and leaks nothing, gets a non-verdict outcome instead.
        </p>

        <div className={styles.actions}>
          <Link className="button button--secondary button--lg" to="/docs/conformance/results">
            See the full results table
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            to="/docs/conformance/submitting">
            Submit an independent run
          </Link>
        </div>
      </div>
    </section>
  );
}
