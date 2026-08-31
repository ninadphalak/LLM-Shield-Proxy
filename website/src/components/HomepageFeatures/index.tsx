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
    title: 'Configured-Boundary Data Protection',
    icon: '🔒',
    description: (
      <>
        Transform protected values locally and test the exact serialized configured-upstream boundary. Supports privacy-control programs without claiming detector-perfect coverage.
      </>
    ),
    link: '/docs/features/data-protection-pii-redaction/format-preserving-synthetic-masking-entropy'
  },
  {
    title: 'Bounded Streaming Rehydration',
    icon: '⚡',
    description: (
      <>
        A bounded sliding-window buffer reconstructs placeholders across fragmented Server-Sent Events. Publish scoped latency distributions with the open conformance runner.
      </>
    ),
    link: '/docs/features/ultra-low-latency-streaming-traffic-engineering/sub-millisecond-sse-sliding-window-buffer'
  },
  {
    title: 'Enterprise Policy-as-Code',
    icon: '📜',
    description: (
      <>
        Request-scoped policy mappings and file polling for supported tenant PII profiles. Validate reload, resolver, concurrency, and unknown-identity behavior.
      </>
    ),
    link: '/docs/features/secure-infrastructure-service-mesh/role-based-policy-as-code-hot-reloading'
  },
  {
    title: 'MCP Tool Governance',
    icon: '🔌',
    description: (
      <>
        A dedicated JSON-RPC 2.0 gateway for Claude Desktop, Cursor, and agent frameworks - per-role tool allow-lists, AST-aware argument/result redaction, and dynamic tools/list pruning.
      </>
    ),
    link: '/docs/guides/mcp-tool-governance'
  },
  {
    title: 'Audit Evidence & OSCAL',
    icon: '🧾',
    description: (
      <>
        Signed process-local hash chains, optional durable JSONL delivery, offline verification, and OSCAL 1.2 artifacts that support control evidence. Immutable WORM retention is external.
      </>
    ),
    link: '/docs/features/enterprise-auditing-compliance'
  },
  {
    title: 'Agent Identity Enforcer',
    icon: '🤖',
    description: (
      <>
        Validate configured JWT and DPoP conditions on supported governed paths. Attribution depends on issuer, key, audience, proxy placement, and replay controls.
      </>
    ),
    link: '/docs/features/agent_identity_enforcer'
  },
  {
    title: 'Secure Service Mesh',
    icon: '🌐',
    description: (
      <>
        Envoy ext_proc, a Kubernetes sidecar-injection webhook, mTLS options, scoped readiness checks, and Helm alert rules with deployment-specific boundaries.
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
