import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Heading from '@theme/Heading';
import clsx from 'clsx';
import {MEASURED_ROWS} from '@site/src/data/results-wall';
import styles from './styles.module.css';

/**
 * A short, read-only view of the results wall for the homepage.
 *
 * WHY A SEPARATE COMPONENT. `ResultsWall` is the full instrument: every column, sortable,
 * with provenance and architecture. That belongs on its own page. What a first visitor
 * needs is the single most legible finding, which is what reached the provider, and a way through
 * to the real table. Anything more is a wall of numbers before they know what is being
 * counted.
 *
 * It reads the row data, never its own copy of the figures, so it cannot drift from the
 * page it links to.
 *
 * MEASURED ROWS ONLY, and the heading says "measured", which is why. The wall also carries
 * rows other projects submitted about themselves, and folding those into this count would
 * turn a sentence about what we measured into a tally of what people told us. The full
 * table, with the column that says which is which, is one click away.
 */
export default function ResultsPreview(): ReactNode {
  const leaked = MEASURED_ROWS.filter((row) => row.sentN > 0).length;

  return (
    <section className={styles.section}>
      <div className="container">
        <div className={styles.header}>
          <span className={styles.eyebrow}>The results wall</span>
          <Heading as="h2" className={styles.title}>
            {leaked} of {MEASURED_ROWS.length} measured setups sent data to the provider
          </Heading>
          <p className={styles.subtitle}>
            Each row is one gateway at one pinned version in one stated configuration,
            measured with the same check you can run yourself. &ldquo;Reached the
            provider&rdquo; means raw test values arrived at a capture server standing in
            for OpenAI.
          </p>
        </div>

        <div className={styles.rows}>
          {MEASURED_ROWS.map((row) => {
            const clean = row.sentN === 0;
            return (
              <div
                key={`${row.project}-${row.version}`}
                className={clsx(styles.row, clean ? styles.clean : styles.leak)}>
                <div className={styles.who}>
                  <span className={styles.project}>{row.project}</span>
                  <span className={styles.version}>{row.version}</span>
                </div>
                <div className={styles.verdict}>
                  <span className={styles.dot} aria-hidden="true" />
                  <span className={styles.verdictText}>
                    {clean ? 'nothing reached the provider' : `sent ${row.sent}`}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        <p className={styles.caveat}>
          Every row above was measured by this project, including our own, and none has been
          replicated by anyone unaffiliated yet. A row measures the configuration named in
          it, not the product&rsquo;s best possible configuration. The full table carries the
          rest of the columns, who ran each one, and how to disagree with it.
        </p>

        <div className={styles.actions}>
          <Link className="button button--secondary button--lg" to="/docs/conformance/who-has-run-it">
            See the full results wall
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            to="/docs/conformance/ci">
            Measure your own gateway
          </Link>
        </div>
      </div>
    </section>
  );
}
