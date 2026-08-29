/**
 * Lightweight, client-side illustration of the proxy's Tier-1 (regex) and
 * Tier-2 (format-preserving synthesis) detection logic. This is NOT the
 * production engine - it exists only to give homepage visitors a live,
 * in-browser feel for the four masking modes described in the docs. The
 * real engine additionally runs a local ONNX NER model (Tier 3) for
 * contextual name/organization detection across arbitrary free text, and
 * uses real AES-256-GCM for STATELESS_CRYPTO rather than this demo's
 * illustrative hash-based placeholder.
 */

export type EntityType =
  | 'PERSON'
  | 'EMAIL'
  | 'SSN'
  | 'CREDIT_CARD'
  | 'PHONE'
  | 'IP_ADDRESS'
  | 'API_KEY';

export type MaskMode = 'SYNTHETIC' | 'STRUCTURAL_TAG' | 'SCRUB' | 'STATELESS_CRYPTO';

export interface Segment {
  text: string;
  type: EntityType | null;
}

// A small illustrative name dictionary so the browser demo can show
// PERSON-entity redaction without shipping a real NER model. Each known
// name deterministically maps to a synthetic partner, mirroring the
// production engine's format-preserving substitution behavior.
const NAME_SYNTH_MAP: Record<string, string> = {
  'sarah connor': 'Maya Torres',
  'john doe': 'Michael Ito',
  'jane smith': 'Elena Brooks',
  'robert chen': 'David Kim',
  'maria garcia': 'Lucia Fernandez',
  'james wilson': 'Andre Walsh',
};

function fnv1a(str: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

function digitsOnly(value: string): string {
  return value.replace(/\D/g, '');
}

function deterministicDigits(seed: string, length: number): string {
  const hash = fnv1a(seed).toString().padStart(length, '0');
  return hash.slice(0, length);
}

function syntheticForType(type: EntityType, original: string): string {
  switch (type) {
    case 'PERSON': {
      const known = NAME_SYNTH_MAP[original.toLowerCase()];
      return known ?? 'Alex Rivera';
    }
    case 'EMAIL': {
      const [, domain] = original.split('@');
      const fakeLocal = `user.${deterministicDigits(original, 4)}`;
      return domain ? `${fakeLocal}@${domain}` : `${fakeLocal}@example.com`;
    }
    case 'SSN': {
      const d = deterministicDigits(original, 9);
      return `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5, 9)}`;
    }
    case 'CREDIT_CARD': {
      const raw = digitsOnly(original);
      const d = deterministicDigits(original, raw.length || 16);
      const groups = d.match(/.{1,4}/g) ?? [d];
      return groups.join(original.includes('-') ? '-' : original.includes(' ') ? ' ' : '');
    }
    case 'PHONE': {
      const raw = digitsOnly(original);
      const d = deterministicDigits(original, raw.length || 10);
      if (raw.length === 10) return `${d.slice(0, 3)}-${d.slice(3, 6)}-${d.slice(6, 10)}`;
      return d;
    }
    case 'IP_ADDRESS': {
      const d = deterministicDigits(original, 12);
      return `${1 + (+d[0] % 9)}${d[1]}.${d.slice(2, 5)}.${d.slice(5, 8)}.${d.slice(8, 11)}`.replace(/\.(\d)$/, '.$1');
    }
    case 'API_KEY': {
      const prefix = original.slice(0, original.indexOf('-') + 1) || 'key_';
      return `${prefix}${deterministicDigits(original, 24)}`;
    }
    default:
      return original;
  }
}

const STRUCTURAL_LABEL: Record<EntityType, string> = {
  PERSON: 'PERSON',
  EMAIL: 'EMAIL',
  SSN: 'SSN',
  CREDIT_CARD: 'CC',
  PHONE: 'PHONE',
  IP_ADDRESS: 'IP',
  API_KEY: 'API_KEY',
};

function cryptoLikeToken(original: string): string {
  return `enc_${fnv1a(original).toString(36)}`;
}

export function maskValue(type: EntityType, original: string, mode: MaskMode, tagIndex: number): string {
  switch (mode) {
    case 'SYNTHETIC':
      return syntheticForType(type, original);
    case 'STRUCTURAL_TAG':
      return `[${STRUCTURAL_LABEL[type]}_${tagIndex}]`;
    case 'SCRUB':
      return '***';
    case 'STATELESS_CRYPTO':
      return `[${cryptoLikeToken(original)}]`;
    default:
      return original;
  }
}

interface RawMatch {
  start: number;
  end: number;
  type: EntityType;
  text: string;
}

const PATTERNS: Array<{ type: EntityType; regex: RegExp }> = [
  { type: 'EMAIL', regex: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g },
  { type: 'SSN', regex: /\b\d{3}-\d{2}-\d{4}\b/g },
  { type: 'CREDIT_CARD', regex: /\b(?:\d[ -]?){13,16}\b/g },
  { type: 'API_KEY', regex: /\b(?:sk|key|token)[-_][A-Za-z0-9_-]{10,}\b/gi },
  { type: 'IP_ADDRESS', regex: /\b(?:\d{1,3}\.){3}\d{1,3}\b/g },
  { type: 'PHONE', regex: /\b\d{3}[-.]\d{3}[-.]\d{4}\b/g },
];

function findNameMatches(text: string): RawMatch[] {
  const matches: RawMatch[] = [];
  for (const name of Object.keys(NAME_SYNTH_MAP)) {
    const idx = text.toLowerCase().indexOf(name);
    if (idx !== -1) {
      matches.push({ start: idx, end: idx + name.length, type: 'PERSON', text: text.slice(idx, idx + name.length) });
    }
  }
  return matches;
}

function findRegexMatches(text: string): RawMatch[] {
  const matches: RawMatch[] = [];
  for (const { type, regex } of PATTERNS) {
    for (const m of text.matchAll(regex)) {
      if (m.index === undefined) continue;
      matches.push({ start: m.index, end: m.index + m[0].length, type, text: m[0] });
    }
  }
  return matches;
}

function resolveOverlaps(matches: RawMatch[]): RawMatch[] {
  const sorted = [...matches].sort(
    (a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start),
  );
  const resolved: RawMatch[] = [];
  let lastEnd = -1;
  for (const m of sorted) {
    if (m.start >= lastEnd) {
      resolved.push(m);
      lastEnd = m.end;
    }
  }
  return resolved;
}

export interface RedactionResult {
  segments: Segment[];
  counts: Partial<Record<EntityType, number>>;
  totalEntities: number;
}

export function analyzeAndMask(text: string, mode: MaskMode): RedactionResult {
  const matches = resolveOverlaps([...findNameMatches(text), ...findRegexMatches(text)]);

  const segments: Segment[] = [];
  const counts: Partial<Record<EntityType, number>> = {};
  const tagIndexByValue = new Map<string, number>();
  const tagCounterByType: Partial<Record<EntityType, number>> = {};

  let cursor = 0;
  for (const match of matches) {
    if (match.start > cursor) {
      segments.push({ text: text.slice(cursor, match.start), type: null });
    }

    const key = `${match.type}:${match.text.toLowerCase()}`;
    let tagIndex = tagIndexByValue.get(key);
    if (tagIndex === undefined) {
      tagCounterByType[match.type] = (tagCounterByType[match.type] ?? 0) + 1;
      tagIndex = tagCounterByType[match.type]!;
      tagIndexByValue.set(key, tagIndex);
    }

    const masked = maskValue(match.type, match.text, mode, tagIndex);
    segments.push({ text: masked, type: match.type });
    counts[match.type] = (counts[match.type] ?? 0) + 1;
    cursor = match.end;
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), type: null });
  }

  const totalEntities = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
  return { segments, counts, totalEntities };
}
