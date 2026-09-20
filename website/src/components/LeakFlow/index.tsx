import type {ReactNode} from 'react';
import clsx from 'clsx';
import styles from './styles.module.css';

type LaneProps = {
  state: 'leak' | 'contained' | 'broken';
  badge: string;
  label: string;
  sent: string;
  arrived: string;
  arrivedNote?: string;
  /* The return leg. Every lane carries one, because a gateway that never answers
     is a different failure from one that answers with the wrong thing, and the
     job scores them separately. */
  back: string;
  backNote?: string;
  last: string;
  footnote: string;
};

function Lane({
  state,
  badge,
  label,
  sent,
  arrived,
  arrivedNote,
  back,
  backNote,
  last,
  footnote,
}: LaneProps): ReactNode {
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

      <div className={styles.returnStrip}>
        <span className={styles.returnLabel}>back to your app</span>
        <span className={styles.chip}>{back}</span>
        {backNote ? <span className={styles.wireNote}>{backNote}</span> : null}
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
        back="ada@example.com"
        backNote="looks fine"
        last="Model provider"
        footnote="The address arrives exactly as your user typed it, and lands in the provider's logs. The reply your app gets is correct, which is the whole problem: nothing on your side records that it happened."
      />
      <Lane
        state="contained"
        badge="CONTAINED"
        label="Redaction matching the value it was written for"
        sent="ada@example.com"
        arrived="<EMAIL>"
        arrivedNote="replaced"
        back="ada@example.com"
        backNote="restored"
        last="Model provider"
        footnote="The provider sees a placeholder. On the way back the gateway puts the real address in again, so your app gets what it sent and the request works as before."
      />
      <Lane
        state="broken"
        badge="NOT RESTORED"
        label="Masked on the way out, never put back on the way in"
        sent="ada@example.com"
        arrived="<EMAIL>"
        arrivedNote="replaced"
        back="<EMAIL>"
        backNote="never came back"
        last="Model provider"
        footnote="Nothing leaked, and your app is broken anyway: the caller gets a placeholder where its own data should be. The job calls this CHECK FAILED rather than a leak, because the fix is a different one. If your product masks in one direction on purpose, duty: anonymize drops the two restore checks, and a run with nothing else wrong comes back CLEAN."
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

        {/* Two rows, same three columns. Out along the top, back along the bottom,
            so a reader can follow one value through the gateway twice. */}
        <div className={styles.flow}>
          <div className={styles.node}>Benchmark</div>

          <div className={styles.wire}>
            <span className={styles.chip}>synthetic values</span>
          </div>

          <div className={styles.node}>Your gateway</div>

          <div className={styles.wire}>
            <span className={styles.chip}>what it sent</span>
            <span className={styles.wireNote}>leak check</span>
          </div>

          <div className={clsx(styles.node, styles.nodeCapture)}>
            Capture
            <span className={styles.nodeSub}>127.0.0.1:8765</span>
          </div>
        </div>

        <div className={clsx(styles.flow, styles.flowBack)}>
          <div className={clsx(styles.node, styles.nodeGhost)}>
            Benchmark
          </div>

          <div className={clsx(styles.wire, styles.wireBack)}>
            <span className={styles.chip}>what it restored</span>
            <span className={styles.wireNote}>restore check</span>
          </div>

          <div className={clsx(styles.node, styles.nodeGhost)}>
            Your gateway
          </div>

          <div className={clsx(styles.wire, styles.wireBack)}>
            <span className={styles.chip}>1 char per event</span>
            <span className={styles.wireNote}>echoed back</span>
          </div>

          <div className={clsx(styles.node, styles.nodeGhost)}>
            Capture
          </div>
        </div>

        <p className={styles.footnote}>
          Because the capture stands where the provider normally does, it records the request
          byte for byte and can compare it against what was sent. There is no model: the
          capture streams the message it received straight back, one character per event, so
          whatever your gateway sent is exactly what it has to read on the way in. That is the
          hardest case for restoring a value, and the benchmark scores both directions of the
          same trip. The values are synthetic and nothing leaves your machine.
        </p>
      </div>
    </div>
  );
}
