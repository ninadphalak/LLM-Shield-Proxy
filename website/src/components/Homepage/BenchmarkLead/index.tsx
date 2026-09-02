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

// Every row is the maintainer's own run. That is the point of showing the counts:
// the table is not asking to be believed, it is asking to be contradicted.
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
    note: 'the reference implementation — written by this benchmark’s author',
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
            The first thing it found was that it was rigged in its author’s favour
          </Heading>
        </div>

        <div className={styles.story}>
          <p>
            The harness was written by the author of one of the gateways it scores. So the
            evidence worth reading is not what it says about that gateway — it is what it has
            said about <em>him</em>.
          </p>
          <p>
            The prompt used to carry three fixed values, chosen to be safe to publish:{' '}
            <code>person@example.invalid</code>, <code>123-45-6789</code>,{' '}
            <code>4532-1234-5678-9012</code>. Every one is a value a <strong>validating</strong>{' '}
            detector is built to reject — <code>.invalid</code> has no public suffix, that SSN is
            a blacklisted sequence, and the card fails its Luhn checksum. Measured against a
            pinned Presidio at <code>score_threshold: 0.0</code>: no <code>EMAIL_ADDRESS</code>,
            no <code>US_SSN</code>, no <code>CREDIT_CARD</code>. Not low confidence — nothing at
            all.
          </p>
          <p>
            This project’s own engine used bare regexes with no checksum and no range check, so
            it caught all three.{' '}
            <strong>
              The benchmark was scoring a careful detector worse than a careless one, in the
              direction that flattered its author.
            </strong>{' '}
            It surfaced by running against a real third party, the offending row was withheld
            rather than published, the fixture was replaced, and every row was re-run.
          </p>
          <p>
            The next thing it found was two defects in this proxy’s own streaming hot path. Both
            are fixed and pinned by regression tests. No speed multiplier is published for that
            fix: the runner and its raw samples were not retained, so there is nothing auditable
            to cite, and an unreproducible number was removed rather than caveated.
          </p>
          <p className={styles.weakness}>
            <strong>And the fixture is still gameable.</strong> A ~35-line{' '}
            <code>str.replace</code> shim with no detector in it passes all five checks. That is
            measured, published, and deliberately unfixed — randomising the fixture cost a
            one-in-three false-accusation rate against a correctly-redacting gateway, which is
            worse than the defect it removes.
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
              # negative control: raw pass-through, MUST report a leak{'\n'}
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
          <strong>Every row is unreplicated, including this project’s own.</strong> A gateway
          needs 3 runs from 3 distinct submitters before it reads as a verdict, and the
          maintainer’s runs never count toward anyone’s replication. `fail` means one thing only:
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
            Submit a run that contradicts one
          </Link>
        </div>
      </div>
    </section>
  );
}
