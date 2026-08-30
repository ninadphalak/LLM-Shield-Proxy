import type {ReactNode} from 'react';
import styles from './styles.module.css';

type Props = {
  children: ReactNode;
  definition: string;
};

export default function GlossaryTerm({children, definition}: Props): ReactNode {
  return (
    <abbr className={styles.term} title={definition} tabIndex={0}>
      {children}
    </abbr>
  );
}
