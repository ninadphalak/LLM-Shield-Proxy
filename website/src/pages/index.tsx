import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import Head from '@docusaurus/Head';
import useBrokenLinks from '@docusaurus/useBrokenLinks';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import CodeBlock from '@theme/CodeBlock';

import styles from './index.module.css';

/**
 * The home page answers three questions, in order, and links out for everything else:
 * what is this, can I see it work in a minute, and which of the four packages do I want.
 * Depth (architecture, compliance, results, research) lives one click away in the tabs and the
 * footer, not on this page.
 */

const TRY_IT = `pip install llm-shield-proxy

UPSTREAM_BASE_URL=http://127.0.0.1:8765 UPSTREAM_API_KEY=unused VALID_VIRTUAL_KEYS=sk-demo llm-shield-proxy --port 4000 &

pii-leak-benchmark selfcheck --target-base-url http://127.0.0.1:4000/v1 --target-api-key sk-demo`;

type Package = {
  name: string;
  to: string;
  install: string;
  forWho: string;
  what: string;
};

const PACKAGES: Package[] = [
  {
    name: 'LLM-Shield-Proxy',
    to: '/docs/proxy',
    install: 'pip install llm-shield-proxy',
    forWho: 'For teams calling an LLM API',
    what: 'A self-hosted proxy that hides personal data and secrets before a request reaches the model provider, and puts the real values back in the streamed reply.',
  },
  {
    name: 'Leak Benchmark',
    to: '/docs/conformance',
    install: 'pip install pii-leak-benchmark',
    forWho: 'For anyone running an LLM gateway',
    what: 'Tests whether any OpenAI-compatible gateway sends personal data to its provider, in about a minute, with no API key.',
  },
  {
    name: 'mcp-ssrf-check',
    to: '/docs/mcp-ssrf-check',
    install: 'pip install mcp-ssrf-check',
    forWho: 'For MCP server authors',
    what: 'Checks your own MCP server for missing Host and Origin checks, forged sessions, and tools that can be made to fetch loopback.',
  },
  {
    name: 'chunk-invariance',
    to: '/docs/chunk-invariance',
    install: 'pip install chunk-invariance',
    forWho: 'For guardrail and redactor authors',
    what: 'A one-line test assertion that fails when a value split across two stream chunks slips through your filter.',
  },
];

const STEPS = [
  {title: 'Your app sends a request', body: 'Same OpenAI client, pointed at the proxy.'},
  {title: 'The provider sees placeholders', body: 'Emails, card numbers, SSNs and keys are replaced before the request leaves.'},
  {title: 'Your app gets real values back', body: 'The streamed reply is restored as it arrives, even when a placeholder is split across events.'},
];

const FAQ = [
  {
    q: 'What is LLM-Shield-Proxy?',
    a: 'An open-source (Apache 2.0), self-hosted proxy for OpenAI-compatible LLM APIs. It replaces the personal data and secrets it detects before a request goes to the model provider, and restores them in the reply your application receives.',
  },
  {
    q: 'Do I need an API key to try it?',
    a: 'No. The one-minute trial points the proxy at a local stand-in for the model provider, so nothing calls a real model and nothing costs money.',
  },
  {
    q: 'Which data does it detect?',
    a: 'Eleven structured types by pattern and checksum (emails, card numbers, SSNs, API keys and other identifiers), high-entropy secrets, and names when the optional local NER model is enabled. It does not promise to find every value; the limitations page says what it misses.',
  },
  {
    q: 'Can I test a gateway I already use?',
    a: 'Yes. pii-leak-benchmark works against any OpenAI-compatible gateway, including LiteLLM, Portkey and NeMo Guardrails, and reports per data type whether the value reached the provider.',
  },
  {
    q: 'Does it run without internet access?',
    a: 'Yes. Detection runs locally, and in air-gapped mode the proxy sends masked requests to an internal model gateway.',
  },
];

const STRUCTURED_DATA = {
  '@context': 'https://schema.org',
  '@graph': [
    ...PACKAGES.map((pkg) => ({
      '@type': 'SoftwareApplication',
      name: pkg.name,
      description: pkg.what,
      applicationCategory: 'DeveloperApplication',
      operatingSystem: 'Linux, macOS, Windows',
      license: 'https://www.apache.org/licenses/LICENSE-2.0',
      offers: {'@type': 'Offer', price: '0', priceCurrency: 'USD'},
      url: `https://llmshieldproxy.com${pkg.to}`,
      codeRepository: 'https://github.com/ninadphalak/LLM-Shield-Proxy',
      author: {'@type': 'Person', name: 'Ninad Phalak'},
    })),
    {
      '@type': 'FAQPage',
      mainEntity: FAQ.map(({q, a}) => ({
        '@type': 'Question',
        name: q,
        acceptedAnswer: {'@type': 'Answer', text: a},
      })),
    },
  ],
};

function Hero(): ReactNode {
  return (
    <header className={clsx('hero', styles.heroBanner)}>
      <div className="container">
        <span className={styles.heroEyebrow}>Open source · Apache 2.0 · Self-hosted</span>
        <Heading as="h1" className="hero__title">
          Keep personal data out of your LLM requests
        </Heading>
        <p className="hero__subtitle">
          LLM-Shield-Proxy hides emails, card numbers, IDs and secrets before a request reaches
          the model provider, and puts the real values back in the streamed reply.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--primary button--lg" to="#try-it">
            Try it in a minute
          </Link>
          <Link className="button button--outline button--secondary button--lg" to="/playground">
            See it in your browser
          </Link>
        </div>
      </div>
    </header>
  );
}

function TryIt(): ReactNode {
  // The id is rendered by this component, so register it for the broken-anchor check.
  useBrokenLinks().collectAnchor('try-it');
  return (
    <section id="try-it" className={styles.section}>
      <div className="container">
        <Heading as="h2">Try it in a minute, with no API key</Heading>
        <p className={styles.lead}>
          Install, start the proxy, and run the leak check through it. The check plays the model
          provider, so it sees exactly what the proxy forwarded. It should print{' '}
          <code>CLEAN</code>.
        </p>
        <CodeBlock language="bash">{TRY_IT}</CodeBlock>
        <p className={styles.note}>
          Using Windows PowerShell? <Link to="/docs/conformance/ci">Use these commands</Link>.
          Ready for real traffic? <Link to="/docs/proxy">Set it up with your app</Link>.
        </p>
      </div>
    </section>
  );
}

function HowItWorks(): ReactNode {
  return (
    <section className={clsx(styles.section, styles.alt)}>
      <div className="container">
        <Heading as="h2">How it works</Heading>
        <ol className={styles.steps}>
          {STEPS.map((step) => (
            <li key={step.title}>
              <strong>{step.title}</strong>
              <span>{step.body}</span>
            </li>
          ))}
        </ol>
        <p className={styles.note}>
          <Link to="/docs/architecture">Architecture</Link> ·{' '}
          <Link to="/docs/limitations">What it does not do</Link> ·{' '}
          <Link to="/docs/conformance/results">Published test results</Link>
        </p>
      </div>
    </section>
  );
}

function Packages(): ReactNode {
  return (
    <section className={styles.section}>
      <div className="container">
        <Heading as="h2">Four tools, each usable on its own</Heading>
        <div className={styles.grid}>
          {PACKAGES.map((pkg) => (
            <Link key={pkg.name} to={pkg.to} className={styles.card}>
              <span className={styles.cardFor}>{pkg.forWho}</span>
              <Heading as="h3">{pkg.name}</Heading>
              <p>{pkg.what}</p>
              <code>{pkg.install}</code>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

function Faq(): ReactNode {
  return (
    <section className={clsx(styles.section, styles.alt)}>
      <div className="container">
        <Heading as="h2">Questions</Heading>
        {FAQ.map(({q, a}) => (
          <details key={q} className={styles.faq}>
            <summary>{q}</summary>
            <p>{a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Open-source PII redaction proxy for LLM APIs"
      description="LLM-Shield-Proxy is a self-hosted, open-source proxy that hides personal data and secrets from LLM providers and restores them in streamed replies. Also: a leak benchmark for any LLM gateway, an MCP security checker, and a streaming-filter test library.">
      <Head>
        <script type="application/ld+json">{JSON.stringify(STRUCTURED_DATA)}</script>
      </Head>
      <Hero />
      <main>
        <TryIt />
        <HowItWorks />
        <Packages />
        <Faq />
      </main>
    </Layout>
  );
}
