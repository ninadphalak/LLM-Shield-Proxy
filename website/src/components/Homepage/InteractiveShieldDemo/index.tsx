import {useEffect, useMemo, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import Heading from '@theme/Heading';
import {analyzeAndMask} from './engine';
import type {MaskMode, RedactionResult} from './engine';
import {TEMPLATES} from './templates';
import styles from './styles.module.css';

const STOP_TYPING_DELAY_MS = 500;
const SIMULATED_LLM_LATENCY_MS = 400;

type Phase = 'idle' | 'typing' | 'sent' | 'received';

const MODE_OPTIONS: {value: MaskMode; label: string; blurb: string}[] = [
  {value: 'SYNTHETIC', label: 'Synthetic', blurb: 'Realistic fake values that preserve tone and token count.'},
  {value: 'STRUCTURAL_TAG', label: 'Structural', blurb: 'Explicit bracketed tags like [PERSON_1].'},
  {value: 'SCRUB', label: 'Scrub', blurb: 'One-way destruction — nothing is kept, so nothing comes back.'},
  {value: 'STATELESS_CRYPTO', label: 'AES-256-GCM', blurb: 'In-band authenticated ciphertext. Zero Redis dependency.'},
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

const PHASE_LABEL: Record<Phase, string> = {
  idle: 'Type a prompt, or load an example below, to begin',
  typing: '⌨️ Typing…',
  sent: '🛰️ Redacted payload sent to LLM — awaiting response…',
  received: '✅ Response received — PII rehydrated for your screen',
};

type TerminalPart = {text: string; kind: 'comment' | 'sensitive' | 'protected' | null};

const TERMINAL_LINES: TerminalPart[] = [
  {text: '# Before — talking straight to the provider\n', kind: 'comment'},
  {text: '$ curl https://api.openai.com/v1/chat/completions \\\n', kind: null},
  {text: '    -H "Authorization: Bearer $OPENAI_API_KEY" \\\n', kind: null},
  {text: '    -d \'{"model":"gpt-4o","messages":[{"role":"user","content":"Update record for ', kind: null},
  {text: 'John Doe', kind: 'sensitive'},
  {text: ', SSN ', kind: null},
  {text: '456-12-7890', kind: 'sensitive'},
  {text: '"}]}\'\n\n', kind: null},
  {text: '# After — point the same SDK at LLM-Shield-Proxy. Nothing else changes.\n', kind: 'comment'},
  {text: '$ curl ', kind: null},
  {text: 'https://shield.internal.acme.corp', kind: 'protected'},
  {text: '/v1/chat/completions \\\n', kind: null},
  {text: '    -H "Authorization: Bearer $OPENAI_API_KEY" \\\n', kind: null},
  {text: '    -d \'{"model":"gpt-4o","messages":[{"role":"user","content":"Update record for ', kind: null},
  {text: 'John Doe', kind: 'sensitive'},
  {text: ', SSN ', kind: null},
  {text: '456-12-7890', kind: 'sensitive'},
  {text: '"}]}\'\n\n', kind: null},
  {text: '→ the LLM only ever sees: ', kind: 'comment'},
  {text: '"Update record for Michael Ito, SSN 839-14-2207"\n', kind: null},
  {text: '→ your agent still receives: ', kind: 'comment'},
  {text: '"Update record for John Doe, SSN 456-12-7890"', kind: null},
];

const AGENT_BEFORE: TerminalPart[] = [
  {text: '{\n  "jsonrpc": "2.0",\n  "method": "tools/call",\n  "params": {\n    "name": "update_patient_record",\n    "arguments": {\n      "patient_name": "', kind: null},
  {text: 'Sarah Connor', kind: 'sensitive'},
  {text: '",\n      "ssn": "', kind: null},
  {text: '456-12-7890', kind: 'sensitive'},
  {text: '",\n      "notes": "Follow-up scheduled for next week"\n    }\n  },\n  "id": 17\n}', kind: null},
];

const AGENT_AFTER: TerminalPart[] = [
  {text: '{\n  "jsonrpc": "2.0",\n  "method": "tools/call",\n  "params": {\n    "name": "update_patient_record",\n    "arguments": {\n      "patient_name": ', kind: null},
  {text: '{\n        "_shield_val": "Maya Torres",\n        "_shield_ctx": "aesgcm:8f2a...c91"\n      }', kind: 'protected'},
  {text: ',\n      "ssn": ', kind: null},
  {text: '{\n        "_shield_val": "839-14-2207",\n        "_shield_ctx": "aesgcm:5b7e...a03"\n      }', kind: 'protected'},
  {text: ',\n      "notes": "Follow-up scheduled for next week"\n    }\n  },\n  "id": 17\n}', kind: null},
];

function TerminalBody({parts}: {parts: TerminalPart[]}): ReactNode {
  return (
    <>
      {parts.map((p, i) => {
        if (p.kind === 'comment') {
          return (
            <span key={i} className={styles.comment}>
              {p.text}
            </span>
          );
        }
        if (p.kind === 'sensitive') {
          return (
            <mark key={i} className={styles.diffSensitive}>
              {p.text}
            </mark>
          );
        }
        if (p.kind === 'protected') {
          return (
            <mark key={i} className={styles.diffProtected}>
              {p.text}
            </mark>
          );
        }
        return <span key={i}>{p.text}</span>;
      })}
    </>
  );
}

function HighlightedInput({segments}: {segments: RedactionResult['segments']}): ReactNode {
  return (
    <>
      {segments.map((seg, i) =>
        seg.type ? (
          <mark
            key={i}
            className={styles.entity}
            style={{
              background: `${ENTITY_COLORS[seg.type]}33`,
              color: ENTITY_COLORS[seg.type],
              borderColor: `${ENTITY_COLORS[seg.type]}66`,
            }}>
            {seg.original}
          </mark>
        ) : (
          <span key={i}>{seg.original}</span>
        ),
      )}
      {'​'}
    </>
  );
}

function MaskedPayload({segments}: {segments: RedactionResult['segments']}): ReactNode {
  return (
    <>
      {segments.map((seg, i) =>
        seg.type ? (
          <mark
            key={i}
            className={styles.entity}
            style={{
              background: `${ENTITY_COLORS[seg.type]}33`,
              color: ENTITY_COLORS[seg.type],
              borderColor: `${ENTITY_COLORS[seg.type]}66`,
            }}
            title={seg.type}>
            {seg.masked}
          </mark>
        ) : (
          <span key={i}>{seg.masked}</span>
        ),
      )}
    </>
  );
}

function RehydratedPayload({segments, mode}: {segments: RedactionResult['segments']; mode: MaskMode}): ReactNode {
  // SCRUB is one-way by design — the proxy never stores what it destroyed, so
  // there is nothing to restore. Showing the real values here would misrepresent
  // how the mode actually behaves in production.
  const canRehydrate = mode !== 'SCRUB';
  return (
    <>
      {segments.map((seg, i) =>
        seg.type ? (
          <mark
            key={i}
            className={canRehydrate ? styles.restored : styles.entity}
            style={
              canRehydrate
                ? undefined
                : {
                    background: `${ENTITY_COLORS[seg.type]}33`,
                    color: ENTITY_COLORS[seg.type],
                    borderColor: `${ENTITY_COLORS[seg.type]}66`,
                  }
            }>
            {canRehydrate ? seg.original : seg.masked}
          </mark>
        ) : (
          <span key={i}>{seg.original}</span>
        ),
      )}
    </>
  );
}

export default function InteractiveShieldDemo(): ReactNode {
  const [text, setText] = useState(TEMPLATES[0].text);
  const [mode, setMode] = useState<MaskMode>('SYNTHETIC');
  const [activeTemplate, setActiveTemplate] = useState(TEMPLATES[0].key);
  const [phase, setPhase] = useState<Phase>('idle');
  const [sentResult, setSentResult] = useState<RedactionResult | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const llmRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const backdropRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Real-time highlighting inside the input box itself — recomputed on every
  // keystroke, independent of the debounced "send to LLM" simulation below.
  const liveResult = useMemo(() => analyzeAndMask(text, mode), [text, mode]);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    clearTimeout(llmRef.current);

    if (!text.trim()) {
      setPhase('idle');
      setSentResult(null);
      return;
    }

    setPhase('typing');
    debounceRef.current = setTimeout(() => {
      setSentResult(analyzeAndMask(text, mode));
      setPhase('sent');
      llmRef.current = setTimeout(() => setPhase('received'), SIMULATED_LLM_LATENCY_MS);
    }, STOP_TYPING_DELAY_MS);

    return () => {
      clearTimeout(debounceRef.current);
      clearTimeout(llmRef.current);
    };
  }, [text, mode]);

  const syncScroll = () => {
    if (backdropRef.current && textareaRef.current) {
      backdropRef.current.scrollTop = textareaRef.current.scrollTop;
      backdropRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  };

  const activeModeBlurb = MODE_OPTIONS.find((m) => m.value === mode)?.blurb;

  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Try it yourself — no signup, no server, no data leaves your browser</span>
          <Heading as="h2" className={styles.title}>
            Type real PII. Watch it never reach the LLM.
          </Heading>
          <p className={styles.subtitle}>
            This is a live, 100% client-side preview of Tier 1 (regex) and Tier 2 (format-preserving
            synthesis) detection — nothing here calls a real LLM or leaves your machine. The
            production engine adds a local ONNX NER model (Tier 3) for free-text names and
            organizations, and genuine AES-256-GCM for the AES-256-GCM mode.
          </p>
        </div>

        <div className={styles.controls}>
          <label className={styles.controlGroup}>
            <span className={styles.controlLabel}>Masking mode</span>
            <select
              className={styles.select}
              value={mode}
              onChange={(e) => setMode(e.target.value as MaskMode)}>
              {MODE_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.controlGroup}>
            <span className={styles.controlLabel}>Tier 3 · ONNX NER examples</span>
            <select
              className={styles.select}
              value={activeTemplate}
              onChange={(e) => {
                const template = TEMPLATES.find((t) => t.key === e.target.value);
                if (template) {
                  setActiveTemplate(template.key);
                  setText(template.text);
                }
              }}>
              {TEMPLATES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {activeModeBlurb && <p className={styles.modeBlurb}>{activeModeBlurb}</p>}

        <div className={styles.grid}>
          <div className={styles.panel}>
            <div className={styles.panelLabel}>You type this</div>
            <div className={styles.inputWrap}>
              <div ref={backdropRef} className={styles.backdrop} aria-hidden="true">
                <HighlightedInput segments={liveResult.segments} />
              </div>
              <textarea
                ref={textareaRef}
                className={styles.textarea}
                value={text}
                spellCheck={false}
                onScroll={syncScroll}
                onChange={(e) => {
                  setActiveTemplate('');
                  setText(e.target.value);
                }}
                placeholder="Type a prompt with a name, email, SSN, or card number…"
              />
            </div>
          </div>

          <div className={styles.panel}>
            <div className={styles.panelLabel}>Sent to LLM</div>
            <div className={styles.output}>
              {sentResult && (phase === 'sent' || phase === 'received') ? (
                sentResult.totalEntities > 0 ? (
                  <MaskedPayload segments={sentResult.segments} />
                ) : (
                  <span className={styles.emptyState}>No sensitive entities detected — sent as-is.</span>
                )
              ) : (
                <span className={styles.emptyState}>Waiting for you to stop typing…</span>
              )}
            </div>
          </div>

          <div className={styles.panel}>
            <div className={styles.panelLabel}>Received from LLM (rehydrated)</div>
            <div className={styles.output}>
              {phase === 'received' && sentResult ? (
                sentResult.totalEntities > 0 ? (
                  <RehydratedPayload segments={sentResult.segments} mode={mode} />
                ) : (
                  <span className={styles.emptyState}>Nothing to rehydrate — no entities were masked.</span>
                )
              ) : phase === 'sent' ? (
                <span className={styles.pending}>
                  <span className={styles.spinner} /> Non-blocking — proxy is holding the mapping while the
                  LLM responds…
                </span>
              ) : (
                <span className={styles.emptyState}>The rehydrated reply will appear here.</span>
              )}
            </div>
          </div>
        </div>

        <div className={styles.statusRow}>
          <span className={phase === 'typing' ? styles.statusPending : styles.status}>{PHASE_LABEL[phase]}</span>
          {sentResult && sentResult.totalEntities > 0 && (phase === 'sent' || phase === 'received') && (
            <span className={styles.legend}>
              {Object.entries(sentResult.counts).map(([type, count]) => (
                <span key={type} className={styles.legendItem} style={{color: ENTITY_COLORS[type]}}>
                  ● {type} × {count}
                </span>
              ))}
            </span>
          )}
        </div>
        {mode === 'SCRUB' && phase === 'received' && sentResult && sentResult.totalEntities > 0 && (
          <p className={styles.scrubNote}>
            Scrub mode destroys the original values on the way in — there's nothing stored to restore,
            so the response above still shows the redacted placeholders. Switch to Synthetic, Structural,
            or AES-256-GCM to see full rehydration.
          </p>
        )}

        <div className={styles.terminalSection}>
          <span className={styles.eyebrow}>It's not just human chat</span>
          <Heading as="h3" className={styles.subsectionTitle}>
            Machine-to-machine traffic gets the same protection
          </Heading>
          <p className={styles.terminalIntro}>
            Same request. Same SDK. The only change is the <code>base_url</code> — everything upstream
            of the proxy is invisible to your existing agents, scripts, and machine-to-machine tool calls.
          </p>
          <div className={styles.terminal}>
            <div className={styles.terminalBar}>
              <span className={styles.dot} style={{background: '#ff5f57'}} />
              <span className={styles.dot} style={{background: '#febc2e'}} />
              <span className={styles.dot} style={{background: '#28c840'}} />
              <span className={styles.terminalTitle}>machine-to-machine · zero code changes</span>
            </div>
            <pre className={styles.terminalBody}>
              <TerminalBody parts={TERMINAL_LINES} />
            </pre>
          </div>

          <p className={styles.terminalIntro}>
            It's not just chat. When one AI agent hands structured data to another — a tool call, a
            function argument, a JSON-RPC message — LLM-Shield-Proxy finds sensitive values{' '}
            <em>inside the structure</em> and swaps them in place, without touching the schema.
          </p>
          <div className={styles.gridTwo}>
            <div className={styles.panel}>
              <div className={styles.panelLabel}>Before — raw agent tool call</div>
              <pre className={styles.jsonBlock}>
                <TerminalBody parts={AGENT_BEFORE} />
              </pre>
            </div>
            <div className={styles.panel}>
              <div className={styles.panelLabel}>
                After — <span className={styles.forcedBadge}>Stateless Synthetic (enforced)</span>
              </div>
              <pre className={styles.jsonBlock}>
                <TerminalBody parts={AGENT_AFTER} />
              </pre>
            </div>
          </div>
          <p className={styles.agentCaption}>
            Structured JSON-RPC / MCP tool calls always use <strong>Stateless Synthetic</strong> mode —
            never Scrub or Structural Tags — because those would break the payload's schema and could
            crash the agent. <code>_shield_val</code> is a realistic fake the LLM can safely see and
            echo back; <code>_shield_ctx</code> is an in-band AES-256-GCM ciphertext the proxy uses to
            restore the original value on the way out. No Redis, no database, no long-term storage —
            this is the same stateless design as the AES-256-GCM mode above, applied automatically.
          </p>
        </div>
      </div>
    </section>
  );
}
