import type {ReactNode} from 'react';
import styles from './styles.module.css';

const GROUPS: {label: string; items: string[]}[] = [
  {label: 'Orchestration', items: ['LangChain', 'LlamaIndex', 'Semantic Kernel', 'AutoGen', 'CrewAI']},
  {label: 'AI Gateways', items: ['LiteLLM', 'Portkey', 'Kong AI Gateway', 'Cloudflare AI Gateway']},
  {label: 'Inference', items: ['vLLM', 'Ollama', 'NVIDIA NIM', 'Hugging Face TGI']},
  {label: 'Providers', items: ['OpenAI', 'Anthropic', 'Google Gemini', 'DeepSeek', 'Mistral']},
];

export default function IntegrationStrip(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <p className={styles.caption}>Examples of systems you can test with the proxy</p>
        <div className={styles.groups}>
          {GROUPS.map((g) => (
            <div key={g.label} className={styles.group}>
              <div className={styles.groupLabel}>{g.label}</div>
              <div className={styles.items}>
                {g.items.map((item) => (
                  <span key={item} className={styles.item}>
                    {item}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
