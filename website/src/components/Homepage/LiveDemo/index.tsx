import {useMemo, useState} from 'react';
import type {ReactNode} from 'react';
import Heading from '@theme/Heading';
import {analyzeAndMask} from './detectors';
import type {MaskMode} from './detectors';
import styles from './styles.module.css';

type ExampleKey = 'healthcare' | 'finance' | 'agent';

const EXAMPLES: Record<Exclude<ExampleKey, 'agent'>, string> = {
  healthcare:
    "Patient intake note: Sarah Connor called about her upcoming visit. DOB confirmed, SSN on file is 456-12-7890, contact email sarah.connor@acmehealth.com, callback number 555-341-7788. Please pull her chart before the 3pm appointment.",
  finance:
    "Wire transfer request from John Doe. Card on file: 4111-2222-3333-4444. Verification email john.doe@northgatebank.com, phone 555-982-1044. Internal service token for the reconciliation job: sk-live-9f8a2c1e7b3d4f5a.",
};

type DiffPart = {text: string; kind: 'sensitive' | 'protected' | null};

const AGENT_BEFORE: DiffPart[] = [
  {text: '{\n  "jsonrpc": "2.0",\n  "method": "tools/call",\n  "params": {\n    "name": "update_patient_record",\n    "arguments": {\n      "patient_name": "', kind: null},
  {text: 'Sarah Connor', kind: 'sensitive'},
  {text: '",\n      "ssn": "', kind: null},
  {text: '456-12-7890', kind: 'sensitive'},
  {text: '",\n      "notes": "Follow-up scheduled for next week"\n    }\n  },\n  "id": 17\n}', kind: null},
];

const AGENT_AFTER: DiffPart[] = [
  {text: '{\n  "jsonrpc": "2.0",\n  "method": "tools/call",\n  "params": {\n    "name": "update_patient_record",\n    "arguments": {\n      "patient_name": ', kind: null},
  {text: '{\n        "_shield_val": "Maya Torres",\n        "_shield_ctx": "aesgcm:8f2a...c91"\n      }', kind: 'protected'},
  {text: ',\n      "ssn": ', kind: null},
  {text: '{\n        "_shield_val": "839-14-2207",\n        "_shield_ctx": "aesgcm:5b7e...a03"\n      }', kind: 'protected'},
  {text: ',\n      "notes": "Follow-up scheduled for next week"\n    }\n  },\n  "id": 17\n}', kind: null},
];

function DiffBlock({parts}: {parts: DiffPart[]}): ReactNode {
  return (
    <>
      {parts.map((p, i) =>
        p.kind ? (
          <mark key={i} className={p.kind === 'sensitive' ? styles.diffSensitive : styles.diffProtected}>
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

const MODES: {key: MaskMode; label: string; blurb: string}[] = [
  {key: 'SYNTHETIC', label: 'Synthetic', blurb: 'Realistic fakes that preserve tone and token count.'},
  {key: 'STRUCTURAL_TAG', label: 'Structural Tag', blurb: 'Explicit bracketed tags like [PERSON_1].'},
  {key: 'SCRUB', label: 'Scrub', blurb: 'One-way destruction. Cannot be rehydrated.'},
  {key: 'STATELESS_CRYPTO', label: 'Stateless Crypto', blurb: 'In-band cipher token. Zero Redis dependency.'},
];

const ENTITY_COLORS: Record<string, string> = {
  PERSON: '#00a8ff',
  EMAIL: '#00ff9d',
  SSN: '#ff5f7e',
  CREDIT_CARD: '#ffb020',
  PHONE: '#c792ea',
  IP_ADDRESS: '#39d0d8',
  API_KEY: '#ff8a3d',
};

function HighlightedText({segments}: {segments: {text: string; type: string | null}[]}): ReactNode {
  return (
    <>
      {segments.map((seg, i) =>
        seg.type ? (
          <mark
            key={i}
            className={styles.entity}
            style={{
              background: `${ENTITY_COLORS[seg.type]}22`,
              color: ENTITY_COLORS[seg.type],
              borderColor: `${ENTITY_COLORS[seg.type]}55`,
            }}
            title={seg.type}>
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}

export default function LiveDemo(): ReactNode {
  const [example, setExample] = useState<ExampleKey>('healthcare');
  const [mode, setMode] = useState<MaskMode>('SYNTHETIC');
  const [customText, setCustomText] = useState<string | null>(null);

  const inputText = customText ?? (example === 'agent' ? '' : EXAMPLES[example]);
  const isAgentMode = example === 'agent';

  const result = useMemo(() => {
    if (isAgentMode) return null;
    return analyzeAndMask(inputText, mode);
  }, [inputText, mode, isAgentMode]);

  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Try it yourself</span>
          <Heading as="h2" className={styles.title}>
            Watch it redact PII live, in your browser
          </Heading>
          <p className={styles.subtitle}>
            Paste your own text, or pick an example below. This is a lightweight client-side
            preview of Tier 1 (regex) and Tier 2 (format-preserving synthesis) detection — the
            real engine adds a local ONNX NER model for free-text names and organizations, and
            uses genuine AES-256-GCM for the Stateless Crypto mode.
          </p>
        </div>

        <div className={styles.exampleTabs}>
          <button
            className={example === 'healthcare' ? styles.tabActive : styles.tab}
            onClick={() => {
              setExample('healthcare');
              setCustomText(null);
            }}>
            🏥 Healthcare Prompt
          </button>
          <button
            className={example === 'finance' ? styles.tabActive : styles.tab}
            onClick={() => {
              setExample('finance');
              setCustomText(null);
            }}>
            🏦 Finance Prompt
          </button>
          <button
            className={example === 'agent' ? styles.tabActive : styles.tab}
            onClick={() => {
              setExample('agent');
              setCustomText(null);
            }}>
            🤖 AI Agent Tool Call (JSON-RPC)
          </button>
        </div>

        {!isAgentMode && (
          <div className={styles.modeRow}>
            {MODES.map((m) => (
              <button
                key={m.key}
                className={mode === m.key ? styles.modeChipActive : styles.modeChip}
                title={m.blurb}
                onClick={() => setMode(m.key)}>
                {m.label}
              </button>
            ))}
          </div>
        )}

        {isAgentMode ? (
          <>
            <div className={styles.grid}>
              <div className={styles.panel}>
                <div className={styles.panelLabel}>Before — raw agent tool call</div>
                <pre className={styles.jsonBlock}>
                  <DiffBlock parts={AGENT_BEFORE} />
                </pre>
              </div>
              <div className={styles.panel}>
                <div className={styles.panelLabel}>
                  After — <span className={styles.forcedBadge}>Stateless Synthetic (enforced)</span>
                </div>
                <pre className={styles.jsonBlock}>
                  <DiffBlock parts={AGENT_AFTER} />
                </pre>
              </div>
            </div>
            <div className={styles.legend}>
              <span className={styles.legendItem} style={{color: '#ff5f7e'}}>
                ● Sensitive value found
              </span>
              <span className={styles.legendItem} style={{color: '#00ff9d'}}>
                ● Replaced with synthetic value + in-band cipher
              </span>
            </div>
          </>
        ) : (
          <div className={styles.grid}>
            <div className={styles.panel}>
              <div className={styles.panelLabel}>Before</div>
              <textarea
                className={styles.textarea}
                value={inputText}
                spellCheck={false}
                onChange={(e) => setCustomText(e.target.value)}
              />
            </div>
            <div className={styles.panel}>
              <div className={styles.panelLabel}>After — sanitized before it leaves your VPC</div>
              <div className={styles.output}>
                {result && result.totalEntities > 0 ? (
                  <HighlightedText segments={result.segments} />
                ) : (
                  <span className={styles.emptyState}>No sensitive entities detected in this text.</span>
                )}
              </div>
            </div>
          </div>
        )}

        {!isAgentMode && result && result.totalEntities > 0 && (
          <div className={styles.legend}>
            {Object.entries(result.counts).map(([type, count]) => (
              <span key={type} className={styles.legendItem} style={{color: ENTITY_COLORS[type]}}>
                ● {type} × {count}
              </span>
            ))}
          </div>
        )}

        {isAgentMode && (
          <p className={styles.agentCaption}>
            Structured JSON-RPC / MCP tool calls always use <strong>Stateless Synthetic</strong> mode —
            never Scrub or Structural Tags — because those would break the payload's schema and could
            crash the agent. The <code>_shield_val</code> is a realistic fake the LLM can safely see and
            echo back; <code>_shield_ctx</code> is an in-band AES-256-GCM ciphertext the proxy uses to
            restore the original value on the way out. No Redis, no database, no long-term storage.
          </p>
        )}
      </div>
    </section>
  );
}
