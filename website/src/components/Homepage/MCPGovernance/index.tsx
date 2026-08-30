import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

const ALLOWED_STEPS: string[] = [
  'Agent calls tools/call "search_kb"',
  'Virtual Key checked against allowed_tools',
  'Arguments AST-walked, PII synthetically masked',
  'Forwarded to your internal tool server',
  'Result scrubbed, returned to the agent',
];

const FORBIDDEN_STEPS: string[] = [
  'Agent calls tools/call "shell_exec"',
  'Virtual Key checked against blocked_tools',
  'Gate trips - upstream is never contacted',
  'Ed25519-signed, hash-chained audit receipt emitted',
  'JSON-RPC error -32003 returned instantly',
];

export default function MCPGovernance(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>MCP tool governance</span>
          <Heading as="h2" className={styles.title}>
            Your agents call tools. Something should decide which ones.
          </Heading>
          <p className={styles.subtitle}>
            Claude Desktop, Cursor, LangChain, and CrewAI all speak the Model Context Protocol -
            JSON-RPC 2.0 calls that can read databases, run code, or send email. LLM-Shield-Proxy
            sits in front of that traffic as a dedicated <code>/v1/mcp</code> gateway: every
            tool call is checked against a per-role allow/block list, every argument and result
            is redacted, and every decision is cryptographically logged - before a single byte
            reaches your tool server.
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
