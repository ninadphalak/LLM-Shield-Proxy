import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';

import HomepageFeatures from '@site/src/components/HomepageFeatures';
import TrustBar from '@site/src/components/Homepage/TrustBar';
import StatsBar from '@site/src/components/Homepage/StatsBar';
import LiveDemo from '@site/src/components/Homepage/LiveDemo';
import DualPipeline from '@site/src/components/Homepage/DualPipeline';
import HowItWorks from '@site/src/components/Homepage/HowItWorks';
import ComparisonTable from '@site/src/components/Homepage/ComparisonTable';
import IntegrationStrip from '@site/src/components/Homepage/IntegrationStrip';
import FinalCTA from '@site/src/components/Homepage/FinalCTA';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero', styles.heroBanner)}>
      <div className="container">
        <span className={styles.heroEyebrow}>Open-source · Self-hosted · Zero-egress</span>
        <Heading as="h1" className="hero__title">
          Stop PII from ever leaving your VPC — even mid-stream
        </Heading>
        <p className="hero__subtitle">
          {siteConfig.tagline}. LLM-Shield-Proxy redacts PII, PHI, and secrets from LLM traffic in
          real time — including token-by-token streams and machine-to-machine agent traffic —
          with &lt;85 MB RAM and microsecond overhead.
        </p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/deployment">
            60-Second Quickstart
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            href="https://github.com/ninadphalak/LLM-Shield-Proxy">
            ⭐ GitHub Repository
          </Link>
        </div>
        <p className={styles.heroMeta}>Apache 2.0 · U.S. Patent Pending · No code rewrites — change one base_url</p>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Self-Hosted PII Redaction for LLMs"
      description="LLM-Shield-Proxy is an open-source, zero-egress reverse proxy that redacts PII, PHI, and secrets from LLM traffic in real time — streaming SSE and machine-to-machine JSON-RPC/MCP tool calls included. <85 MB RAM, microsecond overhead, Apache 2.0.">
      <HomepageHeader />
      <TrustBar />
      <StatsBar />
      <main>
        <LiveDemo />
        <DualPipeline />
        <HowItWorks />
        <HomepageFeatures />
        <ComparisonTable />
        <IntegrationStrip />
        <FinalCTA />
      </main>
    </Layout>
  );
}
