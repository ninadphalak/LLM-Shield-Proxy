import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

export default function FinalCTA(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <Heading as="h2" className={styles.title}>
          Your GenAI traffic is already carrying PII. Keep it in your VPC.
        </Heading>
        <p className={styles.subtitle}>
          Open source, Apache 2.0 licensed, one line to try: change your <code>base_url</code>.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/deployment">
            60-Second Quickstart
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            href="https://github.com/ninadphalak/LLM-Shield-Proxy">
            ⭐ Star on GitHub
          </Link>
        </div>
      </div>
    </section>
  );
}
