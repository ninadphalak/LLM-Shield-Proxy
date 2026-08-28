import type {ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  Svg: React.ComponentType<React.ComponentProps<'svg'>>;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Zero-Egress Data Protection',
    Svg: require('@site/static/img/undraw_docusaurus_mountain.svg').default,
    description: (
      <>
        Intercept and sanitize real-time LLM streams within your own VPC. Raw PHI and PII never traverse the public internet, ensuring SOC 2 & HIPAA compliance.
      </>
    ),
  },
  {
    title: 'Ultra-Low Latency Streaming',
    Svg: require('@site/static/img/undraw_docusaurus_tree.svg').default,
    description: (
      <>
        Patent-pending sliding-window buffer reconstructs fragmented tokens across Server-Sent Events with &lt;5 µs overhead, maintaining a real-time UX.
      </>
    ),
  },
  {
    title: 'Enterprise Policy-as-Code',
    Svg: require('@site/static/img/undraw_docusaurus_react.svg').default,
    description: (
      <>
        Dynamic role-based access control (RBAC) and zero-downtime hot-reloading for granular tenant-scoped PII profiles and Agent Identity Enforcement.
      </>
    ),
  },
];

function Feature({title, Svg, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4')}>
      <div className="text--center">
        <Svg className={styles.featureSvg} role="img" />
      </div>
      <div className="text--center padding-horiz--md">
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
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
