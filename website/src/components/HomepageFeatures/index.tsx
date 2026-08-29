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
    title: 'Zero-Egress Data Protection',
    icon: '🔒',
    description: (
      <>
        Intercept and sanitize real-time LLM streams within your own VPC. Raw PHI and PII never traverse the public internet, ensuring SOC 2 & HIPAA compliance.
      </>
    ),
    link: '/docs/features-overview'
  },
  {
    title: 'Ultra-Low Latency Streaming',
    icon: '⚡',
    description: (
      <>
        Patent-pending sliding-window buffer reconstructs fragmented tokens across Server-Sent Events with &lt;5 ms overhead, maintaining a real-time UX.
      </>
    ),
    link: '/docs/architecture'
  },
  {
    title: 'Enterprise Policy-as-Code',
    icon: '📜',
    description: (
      <>
        Dynamic role-based access control (RBAC) and zero-downtime hot-reloading for granular tenant-scoped PII profiles and Agent Identity Enforcement.
      </>
    ),
    link: '/docs/policies'
  },
  {
    title: 'Auditing & Compliance',
    icon: '🧾',
    description: (
      <>
        FIPS 140-3 KAT compliance, cryptographically-chained WORM audit logging, and NIST OSCAL automated generation.
      </>
    ),
    link: '/docs/compliance-overview'
  },
  {
    title: 'Agent Identity Enforcer',
    icon: '🤖',
    description: (
      <>
        Tie GenAI API requests directly back to individual agents and users for granular rate-limiting and quota chargebacks.
      </>
    ),
    link: '/docs/features/agent_identity_enforcer'
  },
  {
    title: 'Secure Service Mesh',
    icon: '🌐',
    description: (
      <>
        Zero-dependency mutating webhooks, mTLS sidecar integrations, and deep component health probes integrated via Prometheus.
      </>
    ),
    link: '/docs/deployment'
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
