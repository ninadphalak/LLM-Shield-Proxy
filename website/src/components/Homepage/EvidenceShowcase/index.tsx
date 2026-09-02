import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import GlossaryTerm from '@site/src/components/GlossaryTerm';
import styles from './styles.module.css';

// Six SCORED domains. 'Latency reporting' used to sit in this list; the check behind
// it gated on percentiles of monotonic-clock deltas being non-negative, which cannot
// fail under any implementation or input, so it was deleted. Timings are published,
// not scored.
const DOMAINS = [
  'Split-event handling',
  'Unmasked values sent upstream',
  'SSE validity',
  'Original-value restoration',
  'Audit integrity',
  'Memory bounds',
];

export default function EvidenceShowcase(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Reports you can inspect</span>
          <Heading as="h2" className={styles.title}>
            Check the behavior and the evidence yourself
          </Heading>
          <p className={styles.subtitle}>
            Run the tests, inspect the JSON reports, and verify signed audit records. The results
            state what was tested and what remains outside the test.
          </p>
        </div>

        <div className={styles.grid}>
          <article className={styles.card}>
            <span className={styles.cardLabel}>Audit records</span>
            <Heading as="h3">Signed records with ordering checks</Heading>
            <p>
              Each record links to the previous record. Sequence numbers reveal missing or reordered
              entries. Ed25519 signatures can be checked offline. OSCAL 1.2 export and optional
              confirmed JSONL writes are also available.
            </p>
            <div className={styles.boundary}>
              Local files can reveal later changes, but they are not storage-level{' '}
              <GlossaryTerm definition="Write once, read many storage that prevents protected objects from being changed or deleted during retention.">
                WORM
              </GlossaryTerm>. You must provide immutable storage, external copies, and secure key
              management if your deployment requires them.
            </div>
            <Link to="/docs/features/enterprise-auditing-compliance/worm-compliant-audit-logging-with-hash-chaining">
              Inspect the audit contract →
            </Link>
            {' · '}
            <Link to="/docs/evidence-plane-status">See what remains →</Link>
          </article>

          <article className={styles.card}>
            <span className={styles.cardLabel}>Gateway test suite · v1.0.0</span>
            <Heading as="h3">Six checks in one JSON report</Heading>
            <div className={styles.chips}>
              {DOMAINS.map((domain) => <span key={domain}>{domain}</span>)}
            </div>
            <pre className={styles.command}><code>pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1</code></pre>
            <Link to="/docs/conformance/specification-v1">
              Read the public specification →
            </Link>
          </article>
        </div>

        <div className={styles.openSource}>
          <div><strong>Apache-2.0 open source.</strong></div>
          <div className={styles.actions}>
            <Link className="button button--secondary" to="/docs/deployment">Install</Link>
            <Link className="button button--outline button--secondary" href="https://github.com/ninadphalak/LLM-Shield-Proxy">
              Inspect the source
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
