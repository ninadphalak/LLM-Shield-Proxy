import type {ReactNode} from 'react';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const ROWS: {dimension: string; legacy: string; shield: string}[] = [
  {
    dimension: 'Streaming',
    legacy: 'A batch scanner may wait for the full response before it can inspect it.',
    shield: 'Checks and restores values as SSE events arrive. Measure the total delay in your deployment.',
  },
  {
    dimension: 'Where scanning happens',
    legacy: 'A hosted DLP service receives the text that it scans.',
    shield: 'Runs in your environment and can test the request sent to the configured model provider.',
  },
  {
    dimension: 'Detection cost',
    legacy: 'NLP detection uses memory and compute that vary by model and runtime.',
    shield: 'Makes the ONNX detector optional. Measure its memory and request time with your selected model.',
  },
  {
    dimension: 'Agent / tool-call traffic',
    legacy: 'Replacing text directly in raw JSON can damage its syntax or change a field unexpectedly.',
    shield: 'Parses supported JSON-RPC messages and changes selected string values. Test your actual schemas.',
  },
  {
    dimension: 'Data retention',
    legacy: 'Storage and logging behavior varies by service and configuration.',
    shield: 'Offers encrypted in-band values or time-limited Redis mappings. The documentation states where plaintext can remain.',
  },
];

export default function ComparisonTable(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Design choices</span>
          <Heading as="h2" className={styles.title}>
            Compare the available approaches
          </Heading>
          <p className={styles.subtitle}>
            LLM-Shield-Proxy is one option for self-hosted, streaming traffic. These are the main
            differences to test when comparing it with batch or hosted DLP tools.
          </p>
        </div>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th></th>
                <th>Batch or hosted DLP</th>
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
          LLM-Shield-Proxy is not a model router or orchestration framework. It can run alongside
          LangChain, LiteLLM, Portkey, and similar tools.
        </p>
      </div>
    </section>
  );
}
