import type {ReactNode} from 'react';
import Layout from '@theme/Layout';
import InteractiveShieldDemo from '@site/src/components/Homepage/InteractiveShieldDemo';

/** The in-browser redaction demo, on its own page so the home page stays short. */
export default function Playground(): ReactNode {
  return (
    <Layout
      title="Redaction playground"
      description="Type a prompt with personal data and watch what an LLM provider would receive through LLM-Shield-Proxy, and what your application gets back. Runs entirely in your browser.">
      <main>
        <InteractiveShieldDemo />
      </main>
    </Layout>
  );
}
