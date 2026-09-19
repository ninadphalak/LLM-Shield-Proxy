import {useId} from 'react';
import type {ReactNode} from 'react';
import styles from './styles.module.css';

type Props = {
  /** The visible term, for example "Detector gap". */
  term: string;
  /** One plain sentence. This is the whole point: no docs reading required. */
  definition: string;
};

/**
 * A term that explains itself on hover or focus.
 *
 * Deliberately not the homepage's GlossaryTerm, which relies on the browser's
 * `title` tooltip and a hardcoded white underline. `title` never appears on a
 * touch device and cannot be styled, and the white underline is invisible on the
 * light docs background. This renders a real popup and shows it on focus too, so
 * it works by keyboard and by tap.
 */
export default function DefinedTerm({term, definition}: Props): ReactNode {
  const id = useId();
  return (
    <span className={styles.wrap}>
      <button type="button" className={styles.term} aria-describedby={id}>
        {term}
      </button>
      <span role="tooltip" id={id} className={styles.bubble}>
        {definition}
      </span>
    </span>
  );
}
