import type {ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import Link from '@docusaurus/Link';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  Svg?: React.ComponentType<React.ComponentProps<'svg'>>;
  description: ReactNode;
  link?: string;
  icon?: string;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Local Data Replacement',
    icon: '🔒',
    description: (
      <>
        Replace selected values before a request reaches the model provider. The included test checks whether its known values remain in the outgoing request.
      </>
    ),
    link: '/docs/features/data-protection-pii-redaction/format-preserving-synthetic-masking-entropy'
  },
  {
    title: 'Streaming Value Restoration',
    icon: '⚡',
    description: (
      <>
        Join replacement tokens split across SSE events and restore allowed values without waiting for the full response. Measure delay with the included runner.
      </>
    ),
    link: '/docs/features/ultra-low-latency-streaming-traffic-engineering/sub-millisecond-sse-sliding-window-buffer'
  },
  {
    title: 'Request Policies',
    icon: '📜',
    description: (
      <>
        Select PII rules by tenant and request. Test policy reloads, remote resolvers, concurrent requests, and unknown client identities before deployment.
      </>
    ),
    link: '/docs/features/secure-infrastructure-service-mesh/role-based-policy-as-code-hot-reloading'
  },
  {
    title: 'MCP Tool Governance',
    icon: '🔌',
    description: (
      <>
        An experimental JSON-RPC endpoint checks tool allowlists and blocklists and can replace selected values in arguments and results. It supports only a documented MCP subset.
      </>
    ),
    link: '/docs/guides/mcp-tool-governance'
  },
  {
    title: 'Audit Records and OSCAL',
    icon: '🧾',
    description: (
      <>
        Create signed, ordered audit records, optional confirmed JSONL writes, and OSCAL 1.2 output. Add external immutable storage if your retention policy requires it.
      </>
    ),
    link: '/docs/features/enterprise-auditing-compliance'
  },
  {
    title: 'Agent Identity Enforcer',
    icon: '🤖',
    description: (
      <>
        Check configured JWT and DPoP fields on supported paths. Identity still depends on correct issuer, key, audience, proxy, and replay settings.
      </>
    ),
    link: '/docs/features/agent_identity_enforcer'
  },
  {
    title: 'Deployment Integrations',
    icon: '🌐',
    description: (
      <>
        Includes experimental Envoy and Kubernetes integrations, mTLS options, health checks, and Helm alert rules. Read each feature's test status before using it.
      </>
    ),
    link: '/docs/features/secure-infrastructure-service-mesh'
  },
];

function Feature({title, Svg, description, icon, link}: FeatureItem) {
  return (
    <div className={clsx('col col--4', styles.featureCol)}>
      <div className={styles.featureCard}>
        {link ? (
          <Link to={link} className={styles.featureCardLink}>
            <div className="text--center">
              {icon ? <span className={styles.featureIcon}>{icon}</span> : Svg && <Svg className={styles.featureSvg} role="img" />}
            </div>
            <div className="text--center padding-horiz--md">
              <Heading as="h3" className={styles.featureTitle}>{title}</Heading>
              <p className={styles.featureDesc}>{description}</p>
            </div>
          </Link>
        ) : (
          <div>
            <div className="text--center">
              {icon ? <span className={styles.featureIcon}>{icon}</span> : Svg && <Svg className={styles.featureSvg} role="img" />}
            </div>
            <div className="text--center padding-horiz--md">
              <Heading as="h3" className={styles.featureTitle}>{title}</Heading>
              <p className={styles.featureDesc}>{description}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
