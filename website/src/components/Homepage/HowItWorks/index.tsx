import type {ReactNode} from 'react';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const STEPS: {n: string; title: string; body: string}[] = [
  {
    n: '01',
    title: 'Point your SDK at the proxy',
    body: 'Change one base_url. No rewrites, no new SDKs — the proxy speaks the OpenAI API spec and translates to Anthropic, Gemini, and vLLM under the hood.',
  },
  {
    n: '02',
    title: 'Detect, in your VPC',
    body: 'A 3-tier cascade — pre-compiled regex, Shannon-entropy secret scanning, and optional local ONNX NER — finds PII, PHI, and raw secrets before anything leaves your network.',
  },
  {
    n: '03',
    title: 'Mask and forward',
    body: 'Sensitive values are swapped per your policy (synthetic, tagged, scrubbed, or encrypted) and the sanitized payload continues on to your LLM provider.',
  },
  {
    n: '04',
    title: 'Stream back, rehydrated',
    body: 'As the response streams in over SSE, a sliding-window buffer catches tokens split across chunks and restores the original values in real time — with no visible delay.',
  },
];

export default function HowItWorks(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>How it works</span>
          <Heading as="h2" className={styles.title}>
            A transparent proxy, not a black box
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
