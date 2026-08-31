import type {ReactNode} from 'react';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const ROWS: {dimension: string; legacy: string; shield: string}[] = [
  {
    dimension: 'Streaming',
    legacy: 'Buffers the full response before scanning, adding multi-second stalls to a real-time chat UX.',
    shield: 'Scans and rehydrates incrementally. The open conformance report publishes scoped distributions; end-to-end latency must be profiled in the target deployment.',
  },
  {
    dimension: 'Where scanning happens',
    legacy: 'Cloud DLP APIs (AWS Comprehend, Google Cloud DLP, Azure AI Language) require sending raw data to their endpoint to be scanned.',
    shield: 'Runs inside your boundary and tests whether declared protected values appear in the exact serialized payload presented to the configured upstream.',
  },
  {
    dimension: 'The efficiency trade-off',
    legacy: "Contextual NLP detection adds model-dependent memory and inference cost that should be measured on the selected runtime.",
    shield: "Keeps neural detection optional. Allocation and retention measurements are published separately from production RSS claims.",
  },
  {
    dimension: 'Agent / tool-call traffic',
    legacy: "Text-oriented tools don't reason about JSON structure - naive regex over raw JSON can corrupt syntax and crash agents.",
    shield: 'Parses supported JSON-RPC payloads into an AST and masks selected leaf values; schema compatibility is tested separately.',
  },
  {
    dimension: 'Data retention',
    legacy: 'Many gateways log or cache prompts for debugging, creating a new data liability.',
    shield: 'Stateless encryption or configured TTL-backed mappings, with persistence and memory boundaries documented for operators.',
  },
];

export default function ComparisonTable(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Why not just use what you already have</span>
          <Heading as="h2" className={styles.title}>
            Built for the parts of this problem that are actually hard
          </Heading>
          <p className={styles.subtitle}>
            This isn't the only way to redact PII - it's built specifically for the constraints of
            real-time, self-hosted GenAI traffic. Here's where that focus shows up.
          </p>
        </div>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th></th>
                <th>Traditional DLP / cloud APIs</th>
                <th className={styles.highlightCol}>LLM-Shield-Proxy</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((r) => (
                <tr key={r.dimension}>
                  <td className={styles.dimension}>{r.dimension}</td>
                  <td className={styles.legacyCell}>{r.legacy}</td>
                  <td className={styles.shieldCell}>{r.shield}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className={styles.footnote}>
          LLM-Shield-Proxy is not a model router or orchestration framework - it runs alongside
          LangChain, LiteLLM, Portkey, and similar tools, sanitizing traffic before it reaches them.
        </p>
      </div>
    </section>
  );
}
