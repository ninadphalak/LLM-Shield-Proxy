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
  'Fragmentation safety',
  'Raw-PII egress',
  'SSE validity',
  'Rehydration fidelity',
  'Audit integrity',
  'Memory bounds',
];

export default function EvidenceShowcase(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Evidence, not slogans</span>
          <Heading as="h2" className={styles.title}>
            Independently verifiable, in-VPC streaming privacy
          </Heading>
          <p className={styles.subtitle}>
            The defensible story is not “another AI gateway.” It is a testable upstream privacy
            boundary, bounded streaming behavior, and cryptographically verifiable audit evidence.
          </p>
        </div>

        <div className={styles.grid}>
          <article className={styles.card}>
            <span className={styles.cardLabel}>Audit evidence foundation</span>
            <Heading as="h3">Signed, sequenced, recoverable evidence</Heading>
            <p>
              SHA-256 predecessor links, Ed25519 receipts, monotonic sequences, offline verification,
              OSCAL 1.2 export, and opt-in acknowledged JSONL persistence with restart recovery.
            </p>
            <div className={styles.boundary}>
              Local evidence is tamper-evident - not storage-level{' '}
              <GlossaryTerm definition="Write once, read many storage that prevents protected objects from being changed or deleted during retention.">
                WORM
              </GlossaryTerm>. Signed multi-worker checkpoints are built in; immutable retention,
              external anchoring, and production key custody remain operator controls.
            </div>
            <Link to="/docs/features/enterprise-auditing-compliance/worm-compliant-audit-logging-with-hash-chaining">
              Inspect the audit contract →
            </Link>
            {' · '}
            <Link to="/docs/evidence-plane-status">See what remains →</Link>
          </article>

          <article className={styles.card}>
            <span className={styles.cardLabel}>Open Conformance Lab · v1.0.0</span>
            <Heading as="h3">Six scored domains, one machine-readable report</Heading>
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
