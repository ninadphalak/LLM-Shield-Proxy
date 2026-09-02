import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const ALLOWED_STEPS: string[] = [
  'Agent calls tools/call "search_kb"',
  'Proxy checks the client key and allowed_tools policy',
  'Proxy parses the arguments and replaces selected PII',
  'Request goes to your internal tool server',
  'Proxy checks the result and returns it to the agent',
];

const FORBIDDEN_STEPS: string[] = [
  'Agent calls tools/call "shell_exec"',
  'Proxy checks the client key and blocked_tools policy',
  'Proxy blocks the request before contacting the tool server',
  'Configured audit logging records the denial',
  'Agent receives JSON-RPC error -32003',
];

export default function MCPGovernance(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>MCP tool governance</span>
          <Heading as="h2" className={styles.title}>
            Decide which tools each client may call
          </Heading>
          <p className={styles.subtitle}>
            LLM-Shield-Proxy provides an experimental <code>/v1/mcp</code> endpoint for a documented
            subset of Model Context Protocol JSON-RPC calls. It checks each supported tool call
            against the client's policy, replaces selected values in arguments and results, and
            can record the decision. It is not a complete MCP implementation.
          </p>
        </div>

        <div className={styles.flows}>
          <div className={styles.flowCard}>
            <div className={styles.flowHeader}>
              <span className={styles.flowBadgeAllowed}>ALLOWED</span>
              <span className={styles.flowTool}>search_kb</span>
            </div>
            <ol className={styles.flowSteps}>
              {ALLOWED_STEPS.map((step, i) => (
                <li key={i} className={styles.flowStep}>
                  {step}
                </li>
              ))}
            </ol>
          </div>

          <div className={styles.flowCard}>
            <div className={styles.flowHeader}>
              <span className={styles.flowBadgeForbidden}>FORBIDDEN</span>
              <span className={styles.flowTool}>shell_exec</span>
            </div>
            <ol className={styles.flowSteps}>
              {FORBIDDEN_STEPS.map((step, i) => (
                <li key={i} className={styles.flowStep}>
                  {step}
                </li>
              ))}
            </ol>
          </div>
        </div>

        <div className={styles.ctaRow}>
          <Link className="button button--secondary button--lg" to="/docs/guides/mcp-tool-governance">
            Read the MCP Tool Governance guide →
          </Link>
        </div>
      </div>
    </section>
  );
}
