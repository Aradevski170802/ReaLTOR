import type { ColumnKind, GridRow, Quality } from './types';

const money0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const money2 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const int = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const num = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });

export function formatValue(kind: ColumnKind | string, value: unknown, numberFormat?: string | null): string {
  if (value === null || value === undefined || value === '') return '';
  if (kind === 'money' && typeof value === 'number') {
    return numberFormat && !numberFormat.includes('.00') ? money0.format(value) : money2.format(value);
  }
  if (kind === 'int' && typeof value === 'number') return int.format(value);
  if (kind === 'number' && typeof value === 'number') return num.format(value);
  if (kind === 'date' && typeof value === 'string') {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
    return m ? `${m[2]}/${m[3]}/${m[1]}` : value;
  }
  if (kind === 'datetime' && typeof value === 'string') {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
  }
  return String(value);
}

export function formatMoney(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined) return '—';
  return decimals ? money2.format(value) : money0.format(value);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  return formatValue(value.length > 10 ? 'datetime' : 'date', value);
}

export const QUALITY_LABEL: Record<Quality, string> = {
  verified: 'Verified from source',
  estimated: 'Estimate',
  unknown: 'Unknown / not retrieved',
  stale: 'Stale — refresh needed',
  manual: 'Manually entered or overridden',
};

export function cellClasses(row: GridRow | undefined, key: string): string[] {
  if (!row) return [];
  const classes: string[] = [];
  const style = row.styles.cells[key];
  if (style) classes.push(`cell-${style}`);
  const quality = row.quality[key];
  if (quality) classes.push(`q-${quality}`);
  return classes;
}

export const DECISION_TONE: Record<string, string> = {
  Interested: 'green',
  'Not interested': 'orange',
  'Review required': 'yellow',
  'Insufficient data': 'grey',
  'Removed/resolved': 'red',
};

export const MORTGAGE_TONE: Record<string, string> = {
  Satisfied: 'green',
  'No satisfaction found': 'red',
  'Potential match — review required': 'yellow',
  'Insufficient data': 'grey',
};

export const STATUS_TONE: Record<string, string> = {
  success: 'green',
  no_match: 'grey',
  ambiguous_match: 'yellow',
  blocked: 'red',
  authentication_needed: 'yellow',
  user_action_needed: 'blue',
  rate_limited: 'yellow',
  parse_failure: 'red',
  unexpected_failure: 'red',
  not_configured: 'grey',
  terms_not_acknowledged: 'yellow',
  skipped: 'grey',
  pending: 'grey',
  queued: 'grey',
  running: 'blue',
  succeeded: 'green',
  failed: 'red',
  cancelled: 'grey',
  review: 'yellow',
  committed: 'green',
  parsing: 'blue',
  uploaded: 'grey',
  pass: 'green',
  fail: 'red',
  unknown: 'grey',
  resolved: 'red',
  not_applicable: 'grey',
};

export const GROUP_LABEL: Record<string, string> = {
  sale_list: 'Sale list',
  assessment: 'Assessment',
  valuation: 'Valuation',
  tax: 'Taxes',
  mortgage: 'Mortgages',
  liens: 'Liens',
  decision: 'Decision',
  audit: 'Audit',
};

export function humanize(key: string): string {
  return key.replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
