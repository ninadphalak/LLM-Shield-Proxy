import type {ReactNode} from 'react';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const STEPS: {n: string; title: string; body: string}[] = [
  {
    n: '01',
    title: 'Point your SDK at the proxy',
    body: 'After configuring the proxy, change your client base_url to the proxy address. OpenAI-compatible clients can keep using the same request format.',
  },
  {
    n: '02',
    title: 'Find configured data types',
    body: 'Local regex rules, secret scanning, and optional ONNX name detection inspect the request before it is sent to the model provider.',
  },
  {
    n: '03',
    title: 'Mask and forward',
    body: 'The proxy replaces selected values with synthetic data, tags, empty values, or encrypted text. It then sends the changed request to the model provider.',
  },
  {
    n: '04',
    title: 'Restore allowed values',
    body: 'As SSE events arrive, the proxy joins replacement tokens that were split between events and restores values the client may receive. Measure the added delay in your own deployment.',
  },
];

export default function HowItWorks(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>How it works</span>
          <Heading as="h2" className={styles.title}>
            Four steps from client to provider and back
          </Heading>
        </div>
        <div className={styles.steps}>
          {STEPS.map((s) => (
            <div key={s.n} className={styles.step}>
              <div className={styles.stepNumber}>{s.n}</div>
              <Heading as="h3" className={styles.stepTitle}>
                {s.title}
              </Heading>
              <p className={styles.stepBody}>{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
