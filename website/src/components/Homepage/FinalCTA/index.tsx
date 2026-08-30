import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

export default function FinalCTA(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <Heading as="h2" className={styles.title}>
          Make your LLM privacy boundary reproducible.
        </Heading>
        <p className={styles.subtitle}>
          Point your <code>base_url</code> at the proxy and run the conformance checks.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/deployment">
            Install
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
