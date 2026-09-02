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
  {value: 'SYNTHETIC', label: 'Synthetic', blurb: 'Fictional values with a similar format.'},
  {value: 'STRUCTURAL_TAG', label: 'Structural', blurb: 'Explicit bracketed tags like [PERSON_1].'},
  {value: 'SCRUB', label: 'Scrub', blurb: 'One-way removal. The original value cannot be restored.'},
  {value: 'STATELESS_CRYPTO', label: 'AES-256-GCM', blurb: 'Encrypted text carried in the request. Redis is not required.'},
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
  sent: '🛰️ Masked request sent to the simulated model',
  received: '✅ Response received and allowed values restored',
};

const AGENT_PHASE_LABEL: Record<Phase, string> = {
  idle: 'Pick a Tier 3 example above to load a tool-call payload',
  typing: '⏳ Building JSON-RPC payload…',
  sent: '🛰️ Masked request sent to the simulated model',
  received: '✅ Response received and allowed values restored',
};

type TerminalPart = {text: string; kind: 'comment' | 'sensitive' | 'protected' | 'restored' | null};

const TERMINAL_LINES: TerminalPart[] = [
  {text: '# Before - talking straight to the provider\n', kind: 'comment'},
  {text: '$ curl https://api.openai.com/v1/chat/completions \\\n', kind: null},
  {text: '    -H "Authorization: Bearer $OPENAI_API_KEY" \\\n', kind: null},
  {text: '    -d \'{"model":"gpt-4o","messages":[{"role":"user","content":"Update record for ', kind: null},
  {text: 'John Doe', kind: 'sensitive'},
  {text: ', SSN ', kind: null},
  {text: '456-12-7890', kind: 'sensitive'},
  {text: '"}]}\'\n\n', kind: null},
  {text: '# After - point the same SDK at LLM-Shield-Proxy. Nothing else changes.\n', kind: 'comment'},
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

// The Agent-mode panels below render this same envelope around whichever
// Tier 3 example is currently selected, with the "notes" value run through
// the identical analyzeAndMask() pipeline used for chat text. This mirrors
// the production AST mutator, which recursively scans every string leaf
// value in a JSON-RPC payload through the full Tier 1/2/3 cascade - not a
// separate, reduced pipeline for structured traffic.
const AGENT_JSON_PREFIX =
  '{\n  "jsonrpc": "2.0",\n  "method": "tools/call",\n  "params": {\n    "name": "update_patient_record",\n    "arguments": {\n      "notes": "';
const AGENT_JSON_SUFFIX = '"\n    }\n  },\n  "id": 17\n}';

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
        if (p.kind === 'restored') {
          return (
            <mark key={i} className={styles.restored}>
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
  // SCRUB is one-way by design - this demo does not retain the replaced value, so
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

type TrafficType = 'CHAT' | 'AGENT';

const AGENT_BLURB =
  'This JSON-RPC example uses AES-256-GCM so the original value can be restored without Redis. ' +
  'The preview checks every string in the JSON object. Choose another example to see which fields match.';

export default function InteractiveShieldDemo(): ReactNode {
  const [text, setText] = useState(TEMPLATES[0].text);
  const [trafficType, setTrafficType] = useState<TrafficType>('CHAT');
  const [maskModeSelection, setMaskModeSelection] = useState<MaskMode>('SYNTHETIC');
  const [activeTemplate, setActiveTemplate] = useState(TEMPLATES[0].key);
  const [phase, setPhase] = useState<Phase>('idle');
  const [sentResult, setSentResult] = useState<RedactionResult | null>(null);

  const isAgentMode = trafficType === 'AGENT';
  const mode: MaskMode = isAgentMode ? 'STATELESS_CRYPTO' : maskModeSelection;

  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const llmRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const backdropRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Real-time highlighting inside the input box itself - recomputed on every
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

  const activeModeBlurb = isAgentMode ? AGENT_BLURB : MODE_OPTIONS.find((m) => m.value === mode)?.blurb;

  const phaseLabel = isAgentMode ? AGENT_PHASE_LABEL[phase] : PHASE_LABEL[phase];

  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>Browser preview with fictional data</span>
          <Heading as="h2" className={styles.title}>
            See how each masking mode changes a request
          </Heading>
          <p className={styles.subtitle}>
            This preview runs in your browser. It does not contact a model or send data anywhere.
            It demonstrates the regex and synthetic-value paths with fictional examples. The server
            can also use an optional local ONNX model and real AES-256-GCM encryption. Choose{' '}
            <strong>Agent tool call</strong> to see how string values in JSON are handled.
          </p>
        </div>

        <div className={styles.controls}>
          <label className={styles.controlGroup}>
            <span className={styles.controlLabel}>Traffic type</span>
            <select
              className={styles.select}
              value={trafficType}
              onChange={(e) => setTrafficType(e.target.value as TrafficType)}>
              <option value="CHAT">💬 Chat: Human ↔ LLM</option>
              <option value="AGENT">🤖 Agent tool call: JSON-RPC (machine ↔ machine)</option>
            </select>
          </label>

          <label className={styles.controlGroup}>
            <span className={styles.controlLabel}>Masking mode</span>
            <select
              className={styles.select}
              value={mode}
              disabled={isAgentMode}
              title={isAgentMode ? 'This structured JSON-RPC demo uses AES-256-GCM' : undefined}
              onChange={(e) => setMaskModeSelection(e.target.value as MaskMode)}>
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
        {activeModeBlurb && (
          <p className={isAgentMode ? styles.agentModeBlurb : styles.modeBlurb}>
            {isAgentMode && '🔒 '}
            {activeModeBlurb}
          </p>
        )}

        {isAgentMode ? (
          <>
            <div className={styles.grid}>
              <div className={styles.panel}>
                <div className={styles.panelLabel}>Raw agent tool call (JSON-RPC)</div>
                <pre className={styles.jsonBlock}>
                  {AGENT_JSON_PREFIX}
                  <HighlightedInput segments={liveResult.segments} />
                  {AGENT_JSON_SUFFIX}
                </pre>
              </div>
              <div className={styles.panel}>
                <div className={styles.panelLabel}>Sent to LLM</div>
                <pre className={styles.jsonBlock}>
                  {sentResult && (phase === 'sent' || phase === 'received') ? (
                    sentResult.totalEntities > 0 ? (
                      <>
                        {AGENT_JSON_PREFIX}
                        <MaskedPayload segments={sentResult.segments} />
                        {AGENT_JSON_SUFFIX}
                      </>
                    ) : (
                      <span className={styles.emptyState}>No sensitive entities detected - sent as-is.</span>
                    )
                  ) : (
                    <span className={styles.emptyState}>Waiting for the tool call to build…</span>
                  )}
                </pre>
              </div>
              <div className={styles.panel}>
                <div className={styles.panelLabel}>Received back by the agent (rehydrated)</div>
                <pre className={styles.jsonBlock}>
                  {phase === 'received' && sentResult ? (
                    sentResult.totalEntities > 0 ? (
                      <>
                        {AGENT_JSON_PREFIX}
                        <RehydratedPayload segments={sentResult.segments} mode={mode} />
                        {AGENT_JSON_SUFFIX}
                      </>
                    ) : (
                      <span className={styles.emptyState}>Nothing to rehydrate - no entities were masked.</span>
                    )
                  ) : phase === 'sent' ? (
                    <span className={styles.pending}>
                      <span className={styles.spinner} /> Non-blocking - proxy is holding the mapping while
                      the LLM responds…
                    </span>
                  ) : (
                    <span className={styles.emptyState}>The rehydrated reply will appear here.</span>
                  )}
                </pre>
              </div>
            </div>

            <div className={styles.statusRow}>
              <span className={phase === 'typing' ? styles.statusPending : styles.status}>{phaseLabel}</span>
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
            <p className={styles.agentCaption}>
              This example checks the <code>notes</code> value inside a JSON-RPC{' '}
              <code>tools/call</code> request. The proxy recursively checks string values, including
              nested fields. This preview uses <strong>AES-256-GCM</strong> so the original value can
              be restored without a Redis mapping. Test your own tool schemas before deployment.
            </p>
          </>
        ) : (
          <>
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
                      <span className={styles.emptyState}>No sensitive entities detected - sent as-is.</span>
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
                      <span className={styles.emptyState}>Nothing to rehydrate - no entities were masked.</span>
                    )
                  ) : phase === 'sent' ? (
                    <span className={styles.pending}>
                      <span className={styles.spinner} /> Non-blocking - proxy is holding the mapping while
                      the LLM responds…
                    </span>
                  ) : (
                    <span className={styles.emptyState}>The rehydrated reply will appear here.</span>
                  )}
                </div>
              </div>
            </div>

            <div className={styles.statusRow}>
              <span className={phase === 'typing' ? styles.statusPending : styles.status}>{phaseLabel}</span>
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
                Scrub mode does not save the original values, so it cannot restore them in the
                response. Choose Synthetic, Structural, or AES-256-GCM to preview restoration.
              </p>
            )}
          </>
        )}

        <div className={styles.terminalSection}>
          <span className={styles.eyebrow}>Client setup</span>
          <Heading as="h3" className={styles.subsectionTitle}>
            Change the client address after configuring the proxy
          </Heading>
          <p className={styles.terminalIntro}>
            OpenAI-compatible clients can keep the same request format. Set <code>base_url</code> to
            the proxy address and configure provider and client keys on the server.
          </p>
          <div className={styles.terminal}>
            <div className={styles.terminalBar}>
              <span className={styles.dot} style={{background: '#ff5f57'}} />
              <span className={styles.dot} style={{background: '#febc2e'}} />
              <span className={styles.dot} style={{background: '#28c840'}} />
              <span className={styles.terminalTitle}>OpenAI-compatible client setup</span>
            </div>
            <pre className={styles.terminalBody}>
              <TerminalBody parts={TERMINAL_LINES} />
            </pre>
          </div>
        </div>
      </div>
    </section>
  );
}
