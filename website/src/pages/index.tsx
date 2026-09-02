import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';

import HomepageFeatures from '@site/src/components/HomepageFeatures';
import TrustBar from '@site/src/components/Homepage/TrustBar';
import StatsBar from '@site/src/components/Homepage/StatsBar';
import InteractiveShieldDemo from '@site/src/components/Homepage/InteractiveShieldDemo';
import DualPipeline from '@site/src/components/Homepage/DualPipeline';
import HowItWorks from '@site/src/components/Homepage/HowItWorks';
import MCPGovernance from '@site/src/components/Homepage/MCPGovernance';
import ComparisonTable from '@site/src/components/Homepage/ComparisonTable';
import IntegrationStrip from '@site/src/components/Homepage/IntegrationStrip';
import FinalCTA from '@site/src/components/Homepage/FinalCTA';
import EvidenceShowcase from '@site/src/components/Homepage/EvidenceShowcase';
import BenchmarkLead from '@site/src/components/Homepage/BenchmarkLead';
import GlossaryTerm from '@site/src/components/GlossaryTerm';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero', styles.heroBanner)}>
      <div className="container">
        <span className={styles.heroEyebrow}>Apache-2.0 · a benchmark, and the gateway it scores</span>
        <Heading as="h1" className="hero__title">
          Does your LLM gateway send raw personal data upstream?
        </Heading>
        <p className="hero__subtitle">
          <code>pii-leak-benchmark</code> measures it, against any OpenAI-compatible{' '}
          <GlossaryTerm definition="Server-Sent Events: an HTTP format for delivering model output as a sequence of data events.">
            SSE
          </GlossaryTerm>{' '}
          gateway, in about a minute. Standard library plus <code>httpx</code>: you should not
          have to install one gateway to measure another.
        </p>
        <p className={styles.heroSubMeta}>
          LLM-Shield-Proxy is one of the gateways it scores — the reference implementation, one
          row among several, and its row is as unreplicated as everyone else’s.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/conformance/results">
            See what it measured
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            to="/docs/conformance/reproducing">
            Run it against your gateway
          </Link>
        </div>
        <p className={styles.heroMeta}>
          {siteConfig.tagline} Nothing above the fold you cannot verify in five minutes.
        </p>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Measure whether your LLM gateway leaks PII upstream"
      description="pii-leak-benchmark is an Apache-2.0 conformance harness that measures whether an OpenAI-compatible streaming gateway sends raw personal data to its upstream. LLM-Shield-Proxy is the reference implementation it scores.">
      <HomepageHeader />
      <StatsBar />
      <main>
        <BenchmarkLead />
        <InteractiveShieldDemo />
        <TrustBar />
        <EvidenceShowcase />
        <DualPipeline />
        <HowItWorks />
        <MCPGovernance />
        <HomepageFeatures />
        <ComparisonTable />
        <IntegrationStrip />
        <FinalCTA />
      </main>
    </Layout>
  );
}
