import type {ReactNode} from 'react';
import styles from './styles.module.css';

/**
 * Credit for people who contributed a case to the corpus.
 *
 * WHY THIS IS THE ONE THING WORTH GAMIFYING. Every other incentive on this page points
 * at a gateway getting a better row, which is a thing its own maintainers control. A
 * case that defeats a gateway is the opposite: it is an outsider improving the
 * instrument, it makes every future run stricter, and it is the contribution this
 * project cannot make for itself without marking its own homework.
 *
 * Two tiers, and the difference is real rather than decorative. A case that defeats one
 * gateway is a finding about that gateway. A case that defeats EVERY gateway measured
 * is a finding about the whole category, which is a considerably rarer and more useful
 * thing, so it is set apart.
 *
 * Status here is earned by breaking something, never by taking part. There are no
 * points, streaks or participation badges: on a page about security failures those read
 * as unserious, and they would undermine the thing that makes the credit worth having.
 */

export type Credit = {
  /** How the contributor wants to be named. A handle, a real name, or an org. */
  who: string;
  /** Optional link the contributor chose: profile, site, or the issue itself. */
  href?: string;
  /** What the case does, in one line a reader can learn something from. */
  what: string;
  /** Corpus identifier once the case is merged, so the credit is checkable. */
  caseId?: string;
  date: string;
};

type Props = {
  /** Cases that defeated at least one gateway. */
  trips?: Credit[];
  /** Cases that defeated every gateway measured at the time. */
  allFallDown?: Credit[];
};

function Name({credit}: {credit: Credit}): ReactNode {
  if (!credit.href) return <>{credit.who}</>;
  return (
    <a href={credit.href} target="_blank" rel="noreferrer">
      {credit.who}
    </a>
  );
}

function Entry({credit, grand}: {credit: Credit; grand?: boolean}): ReactNode {
  return (
    <li className={grand ? styles.grandEntry : styles.entry}>
      <span className={grand ? styles.grandWho : styles.who}>
        <Name credit={credit} />
      </span>
      <span className={styles.what}>{credit.what}</span>
      <span className={styles.meta}>
        {credit.caseId ? <code>{credit.caseId}</code> : null}
        <span className={styles.date}>{credit.date}</span>
      </span>
    </li>
  );
}

export default function CorpusCredits({trips = [], allFallDown = []}: Props): ReactNode {
  const nothingYet = trips.length === 0 && allFallDown.length === 0;

  return (
    <div className={styles.wrap}>
      <section className={styles.grand}>
        <h3 className={styles.grandTitle}>All fall down</h3>
        <p className={styles.grandBlurb}>
          A case that every gateway on this page failed, on the day it was added. The
          rarest contribution here, and the one that moves the whole category rather than
          one product.
        </p>
        {allFallDown.length > 0 ? (
          <ol className={styles.grandList}>
            {allFallDown.map((credit) => (
              <Entry key={`${credit.who}-${credit.date}`} credit={credit} grand />
            ))}
          </ol>
        ) : (
          <p className={styles.empty}>
            Nobody has done this yet. The first person to manage it goes here, by name.
          </p>
        )}
      </section>

      <section className={styles.standard}>
        <h3 className={styles.standardTitle}>Cases that tripped a gateway</h3>
        <p className={styles.standardBlurb}>
          A value, an encoding or a split that a gateway did not catch and the corpus did
          not yet cover. Ours included: we would rather learn it here.
        </p>
        {trips.length > 0 ? (
          <ol className={styles.list}>
            {trips.map((credit) => (
              <Entry key={`${credit.who}-${credit.date}`} credit={credit} />
            ))}
          </ol>
        ) : (
          <p className={styles.empty}>
            None submitted yet. The list starts with whoever sends the first one.
          </p>
        )}
      </section>

      {nothingYet && (
        <p className={styles.callToAction}>
          Both lists are empty on purpose. Nothing has been staged here to make the page
          look busier than it is.
        </p>
      )}
    </div>
  );
}
