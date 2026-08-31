import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

export default function FinalCTA(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <Heading as="h2" className={styles.title}>
          Evaluate your LLM privacy boundary with us.
        </Heading>
        <p className={styles.subtitle}>
          Apply for a confidential 30-day design-partner pilot or run the same assessment locally.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/design-partner-pilot">
            Apply for a private pilot
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            to="/docs/guides/pilot-assessment">
            Run the local assessment
          </Link>
        </div>
        <p className={styles.subtitle}>
          Prefer to inspect the code first?{' '}
          <Link href="https://github.com/ninadphalak/LLM-Shield-Proxy">View the repository</Link>.
        </p>
      </div>
    </section>
  );
}
