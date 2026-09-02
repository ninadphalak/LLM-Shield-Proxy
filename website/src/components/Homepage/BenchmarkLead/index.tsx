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
    note: 'this project',
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
          <span className={styles.eyebrow}>What the first runs found</span>
          <Heading as="h2" className={styles.title}>
            The first test values were biased
          </Heading>
        </div>

        <div className={styles.story}>
          <p>
            All six current results were produced by this project on one workstation. No outside
            contributor has repeated them, so every product result is marked <code>unreplicated</code>.
          </p>
          <p>The first prompt used invalid examples of all three data types:</p>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Old test value</th>
                <th>Why Presidio rejected it</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><code>person@example.invalid</code></td>
                <td><code>.invalid</code> is not a public domain suffix</td>
              </tr>
              <tr>
                <td><code>123-45-6789</code></td>
                <td>Presidio blocks this well-known invalid SSN sequence</td>
              </tr>
              <tr>
                <td><code>4532-1234-5678-9012</code></td>
                <td>The number fails the Luhn card-number checksum</td>
              </tr>
            </tbody>
          </table>
          <p>
            LLM-Shield-Proxy matched the patterns without checking whether the values were valid.
            This gave it an unfair advantage over validating detectors. A LiteLLM and Presidio run
            revealed the problem. The affected result was not published, the values were replaced,
            and all six configurations were tested again.
          </p>
          <p>
            The benchmark also found two streaming bugs in LLM-Shield-Proxy. Both are fixed and
            covered by regression tests.
          </p>
          <p className={styles.weakness}>
            <strong>Known limitation:</strong> the test uses three fixed data formats. A small
            program written specifically for those formats can pass without being a general PII
            detector. The values change on every run, but the formats do not. Testing more formats
            caused two false failures in six trials.
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
          <strong>Every product result was run once by this project's maintainer.</strong> A result
          becomes replicated only after three different people each submit a run of the same
          gateway and configuration. <code>fail</code> means the gateway sent an unmasked test value
          to the benchmark's capture server. Products that do not advertise PII redaction are
          marked not applicable, not failed.
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
