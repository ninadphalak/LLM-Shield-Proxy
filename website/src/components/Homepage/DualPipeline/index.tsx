import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

export default function DualPipeline(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Two kinds of traffic, one proxy</span>
          <Heading as="h2" className={styles.title}>
            Not just chat. Your AI agents talk to each other too.
          </Heading>
          <p className={styles.subtitle}>
            Most redaction tools only look at conversational text. But a growing share of GenAI
            traffic is agents calling tools, functions, and other agents behind the scenes - and
            that structured traffic carries sensitive data too. LLM-Shield-Proxy protects both,
            automatically, with no configuration required to tell them apart.
          </p>
        </div>

        <div className={styles.cards}>
          <div className={styles.card}>
            <div className={styles.cardIcon}>💬</div>
            <Heading as="h3" className={styles.cardTitle}>
              Human ↔ LLM (chat &amp; prompts)
            </Heading>
            <p className={styles.cardBody}>
              When a person is chatting with an AI assistant, you choose how sensitive data gets
              handled - swap it for a realistic-looking fake, replace it with a plain tag, destroy
              it outright, or encrypt it in place. You can change this per request, without
              restarting anything.
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
              When one AI system hands structured data to another - a tool call, a function
              argument, a JSON-RPC message - the proxy automatically finds sensitive values hidden
              inside that structure, swaps in realistic-looking fakes, and can safely restore the
              originals afterward. It never stores anything and it never breaks the format the
              receiving system expects, so your agents don't crash.
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
