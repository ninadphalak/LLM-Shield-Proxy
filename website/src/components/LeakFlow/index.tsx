import type {ReactNode} from 'react';
import clsx from 'clsx';
import styles from './styles.module.css';

type LaneProps = {
  state: 'leak' | 'contained';
  badge: string;
  label: string;
  sent: string;
  arrived: string;
  arrivedNote?: string;
  last: string;
  footnote: string;
};

function Lane({state, badge, label, sent, arrived, arrivedNote, last, footnote}: LaneProps): ReactNode {
  return (
    <div className={clsx(styles.lane, styles[state])}>
      <div className={styles.laneHead}>
        <span className={styles.badge}>{badge}</span>
        <span className={styles.laneLabel}>{label}</span>
      </div>

      <div className={styles.flow}>
        <div className={styles.node}>Your app</div>

        <div className={styles.wire}>
          <span className={styles.chip}>{sent}</span>
        </div>

        <div className={styles.node}>Your gateway</div>

        <div className={styles.wire}>
          <span className={styles.chip}>{arrived}</span>
          {arrivedNote ? <span className={styles.wireNote}>{arrivedNote}</span> : null}
        </div>

        <div className={clsx(styles.node, styles.nodeEnd)}>{last}</div>
      </div>

      <p className={styles.footnote}>{footnote}</p>
    </div>
  );
}

export default function LeakFlow(): ReactNode {
  return (
    <div className={styles.wrap}>
      <Lane
        state="leak"
        badge="LEAK"
        label="No redaction, or a rule that quietly stopped matching"
        sent="ada@example.com"
        arrived="ada@example.com"
        arrivedNote="unchanged"
        last="Model provider"
        footnote="The address reaches the provider, and its logs, exactly as your user typed it. Nothing in a normal test suite fails, because the response still looks right."
      />
      <Lane
        state="contained"
        badge="CONTAINED"
        label="Redaction matching the value it was written for"
        sent="ada@example.com"
        arrived="<EMAIL>"
        arrivedNote="replaced"
        last="Model provider"
        footnote="The provider sees a placeholder. Your caller still gets the real address back in the response, so the request works as before."
      />
    </div>
  );
}

export function CaptureFlow(): ReactNode {
  return (
    <div className={styles.wrap}>
      <div className={clsx(styles.lane, styles.method)}>
        <div className={styles.laneHead}>
          <span className={styles.badge}>HOW THE CHECK SEES IT</span>
          <span className={styles.laneLabel}>
            The provider is swapped for a capture server on your own machine
          </span>
        </div>

        <div className={styles.flow}>
          <div className={styles.node}>Benchmark</div>

          <div className={styles.wire}>
            <span className={styles.chip}>synthetic values</span>
          </div>

          <div className={styles.node}>Your gateway</div>

          <div className={styles.wire}>
            <span className={styles.chip}>whatever it sends</span>
          </div>

          <div className={clsx(styles.node, styles.nodeCapture)}>
            Capture
            <span className={styles.nodeSub}>127.0.0.1:8765</span>
          </div>
        </div>

        <p className={styles.footnote}>
          Because the capture stands where the provider normally does, it records the request
          byte for byte and can compare it against what was sent. The values are synthetic and
          nothing leaves your machine: there is no provider on the other end to leak to.
        </p>
      </div>
    </div>
  );
}
