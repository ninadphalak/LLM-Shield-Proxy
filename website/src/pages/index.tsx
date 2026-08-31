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
import GlossaryTerm from '@site/src/components/GlossaryTerm';

import styles from './index.module.css';

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero', styles.heroBanner)}>
      <div className="container">
        <span className={styles.heroEyebrow}>Apache-2.0 · Self-hosted · Independently verifiable</span>
        <Heading as="h1" className="hero__title">
          Verifiable streaming privacy at the LLM boundary
        </Heading>
        <p className="hero__subtitle">
          Inspect and transform protected data inside your VPC, verify the exact{' '}
          <GlossaryTerm definition="The serialized request handed to the configured upstream client after transformations.">
            upstream boundary
          </GlossaryTerm>, preserve incremental{' '}
          <GlossaryTerm definition="Server-Sent Events: an HTTP format for delivering model output as a sequence of data events.">
            SSE
          </GlossaryTerm>{' '}
          delivery, and export audit evidence you can check offline.
        </p>
        <p className={styles.heroSubMeta}>{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/design-partner-pilot">
            Apply for a private pilot
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            to="/docs/guides/pilot-assessment">
            Run the local assessment
          </Link>
        </div>
        <p className={styles.heroMeta}>
          Apache-2.0 source and the conformance checks are publicly inspectable.
        </p>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  return (
    <Layout
      title="Self-Hosted PII Redaction for LLMs"
      description="Apache-2.0 streaming privacy gateway with an open conformance specification, a testable pre-upstream privacy boundary, and signed audit evidence for LLM and MCP traffic.">
      <HomepageHeader />
      <TrustBar />
      <StatsBar />
      <main>
        <InteractiveShieldDemo />
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
