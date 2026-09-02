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
        <span className={styles.heroEyebrow}>Apache-2.0 benchmark and self-hosted privacy gateway</span>
        <Heading as="h1" className="hero__title">
          Does your LLM gateway send raw personal data upstream?
        </Heading>
        <p className="hero__subtitle">
          <code>pii-leak-benchmark</code> tests any OpenAI-compatible{' '}
          <GlossaryTerm definition="Server-Sent Events: an HTTP format for delivering model output as a sequence of data events.">
            SSE
          </GlossaryTerm>{' '}
          gateway in about a minute. Its only third-party Python dependency is <code>httpx</code>.
          You do not need to install one gateway to test another.
        </p>
        <p className={styles.heroSubMeta}>
          LLM-Shield-Proxy is listed by name with the other tested gateways. Its result follows the
          same evidence and replication rules as every other result.
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
          {siteConfig.tagline} Follow the links below to inspect the method, reports, and source.
        </p>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Measure whether your LLM gateway leaks PII upstream"
      description="pii-leak-benchmark tests whether an OpenAI-compatible streaming gateway sends unmasked personal data to its model provider. LLM-Shield-Proxy is one of the gateways it tests.">
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
