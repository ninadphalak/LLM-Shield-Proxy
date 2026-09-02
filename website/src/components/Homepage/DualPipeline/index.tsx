import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

export default function DualPipeline(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Chat and structured requests</span>
          <Heading as="h2" className={styles.title}>
            Protect text prompts and tool-call data
          </Heading>
          <p className={styles.subtitle}>
            LLM requests can contain plain text or structured JSON. LLM-Shield-Proxy has separate
            paths for both. Configure and test the path used by your provider or tool.
          </p>
        </div>

        <div className={styles.cards}>
          <div className={styles.card}>
            <div className={styles.cardIcon}>💬</div>
            <Heading as="h3" className={styles.cardTitle}>
              Human ↔ LLM (chat &amp; prompts)
            </Heading>
            <p className={styles.cardBody}>
              For chat text, choose whether to replace a sensitive value with a synthetic value,
              a tag, an empty value, or encrypted text. The selected policy can vary by request.
            </p>
            <Link to="/docs/features/data-protection-pii-redaction/mode-per-request-masking-pipeline" className={styles.cardLink}>
              See the 4 masking modes →
            </Link>
          </div>

          <div className={styles.card}>
            <div className={styles.cardIcon}>🤖</div>
            <Heading as="h3" className={styles.cardTitle}>
              Machine ↔ Machine (agent tool calls)
            </Heading>
            <p className={styles.cardBody}>
              For supported JSON-RPC requests, the proxy parses the JSON and checks its string
              values. It can replace selected values and restore them in the response. Test the
              exact schemas and response behavior used by your integration.
            </p>
            <Link to="/docs/features/data-protection-pii-redaction/stateless-ast-aware-semantic-pii-firewall" className={styles.cardLink}>
              See the stateless agent firewall →
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
