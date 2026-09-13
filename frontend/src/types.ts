export type County = 'montco' | 'delco';
export type Quality = 'verified' | 'estimated' | 'unknown' | 'stale' | 'manual';
export type ColumnKind = 'text' | 'money' | 'int' | 'number' | 'date' | 'datetime' | 'link';

export interface Project {
  id: number;
  name: string;
  county: County;
  description: string | null;
  sale_date: string | null;
  is_demo: boolean;
  archived: boolean;
  created_by: string | null;
  created_at: string;
}

export interface PageInfo {
  number: number;
  kind: string;
  char_count: number;
  image_count: number;
  table_count: number;
  ocr_applied: boolean;
  ocr_engine: string | null;
  records: number;
  notes: string[];
}

export interface ImportRecord {
  id: number;
  project_id: number;
  filename: string;
  kind: string;
  status: string;
  parser_key: string | null;
  page_count: number | null;
  stats: Record<string, any> | null;
  detection: { pages: PageInfo[] } | null;
  column_mapping: Record<string, string> | null;
  error: string | null;
  created_at: string;
  committed_at: string | null;
}

export interface ExtractedValue {
  raw: string | null;
  value: string | null;
  confidence: number;
  corrected: boolean;
  corrected_by: string | null;
}

export interface ExtractedRow {
  id: number;
  row_index: number;
  page_number: number | null;
  raw_text: string | null;
  confidence: number;
  needs_review: boolean;
  review_reasons: string[];
  excluded: boolean;
  values: Record<string, ExtractedValue>;
  tax_lines: string | null;
}

export interface ColumnSpec {
  key: string;
  header: string;
  width: number;
  kind: ColumnKind;
  group: string;
  reference: boolean;
  pinned: boolean;
  hidden: boolean;
  editable: boolean;
  number_format: string | null;
  header_font_size: number;
  description: string;
}

export interface GridRow {
  id: number;
  values: Record<string, any>;
  quality: Record<string, Quality>;
  sources: Record<string, string | null>;
  styles: { row: string | null; cells: Record<string, string> };
  is_pilot: boolean;
  excluded: boolean;
  needs_review: boolean;
  open_tasks: number;
}

export interface GridResponse {
  project: { id: number; name: string; county: County; county_label: string; is_demo: boolean };
  columns: ColumnSpec[];
  rows: GridRow[];
  thresholds: { valuation: number; assessment: number };
}

export interface Cell {
  value: any;
  source_key: string | null;
  source_name: string | null;
  url: string | null;
  retrieved_at: string | null;
  quality: Quality;
  raw: string | null;
  notes: string | null;
  evidence_id: number | null;
  confidence: number | null;
}

export interface Job {
  id: number;
  kind: string;
  status: string;
  project_id: number | null;
  property_id: number | null;
  parent_id: number | null;
  payload: Record<string, any> | null;
  attempts: number;
  max_attempts: number;
  progress_total: number;
  progress_done: number;
  error: string | null;
  result: Record<string, any> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  run_after: string;
  children?: Record<string, number>;
}

export interface CaptureTask {
  id: number;
  property_id: number;
  parcel: string;
  owner_name: string | null;
  location: string | null;
  is_pilot: boolean;
  source_key: string;
  status: string;
  url: string | null;
  search_hint: string | null;
  instructions: string | null;
  reason: string;
  last_error: string | null;
  created_at: string;
}

export interface SourceInfo {
  key: string;
  county: County;
  name: string;
  category: string;
  access_method: string;
  automated: boolean;
  base_urls: string[];
  searches: { name: string; url: string; input: string }[];
  input_requirements: string;
  parcel_rule: string;
  field_mappings: Record<string, string>;
  rate_limit_per_minute: number;
  min_interval_seconds: number;
  cache_ttl_hours: number;
  compliance_status: string;
  terms_notes: string;
  terms_url: string | null;
  requires_terms_ack: boolean;
  daily_refresh: boolean;
  verified_on: string;
  access_findings: string[];
  supports_capture: boolean;
  policy?: { enabled: boolean; terms_acknowledged_by: string | null; terms_acknowledged_at: string | null; notes: string | null };
}

export interface SecretStatus {
  name: string;
  configured: boolean;
  hint?: string;
  updated_at?: string;
  updated_by?: string;
}

export interface ProviderInfo {
  key: string;
  name: string;
  product: string;
  estimate_types: string[];
  docs_url: string;
  secret_names: string[];
  setup_steps: string[];
  coverage_notes: string;
  terms_notes: string;
  verified_integration: boolean;
  is_sandbox: boolean;
  enabled: boolean;
  settings: Record<string, any> | null;
  secrets: SecretStatus[];
  status: string;
}

export interface MortgageMatch {
  satisfaction_document_id: number;
  instrument: string | null;
  recorded_date: string | null;
  score: number;
  method: string;
  reasons: string[] | null;
  decision: string;
  decided_by: string | null;
}

export interface Mortgage {
  id: number;
  document_id: number;
  instrument: string | null;
  recorded_date: string | null;
  original_amount: number | null;
  lender: string | null;
  borrower: string | null;
  status: string;
  user_status: string | null;
  effective_status: string;
  status_reason: string | null;
  reviewed_by: string | null;
  review_note: string | null;
  matches: MortgageMatch[];
}

export interface PropertyDetail {
  property: Record<string, any> & {
    id: number;
    project_id: number;
    county: County;
    parcel: string;
    parcel_label: string;
    owner_name: string | null;
    decision_status: string | null;
    decision_summary: string | null;
    import_id: number | null;
    source_page: number | null;
  };
  identifiers: Record<string, string>;
  sale_list_tax_lines: { year: number; cty: number | null; sch: number | null; twn: number | null; amount: number | null; raw: string }[];
  cells: Record<string, Cell>;
  styles: { row: string | null; cells: Record<string, string> };
  assessments: Record<string, any>[];
  tax_claims: Record<string, any>[];
  tax_history: Record<string, any>[];
  valuations: Record<string, any>[];
  selection: { value: number | null; method: string; provider_key: string | null; explanation: string; computed_at: string } | null;
  manual_sites: Record<string, string>;
  documents: Record<string, any>[];
  mortgages: Mortgage[];
  liens: Record<string, any>[];
  lookups: Record<string, { status: string; message: string | null; finished_at: string | null; evidence_id: number | null }>;
  tasks: Record<string, any>[];
  sources: SourceInfo[];
  rules: { rule_key: string; outcome: string; message: string; inputs: Record<string, any>; evaluated_at: string }[];
  overrides: { id: number; field: string; original_value: string | null; override_value: string | null; reason: string; created_by: string; created_at: string; active: boolean }[];
  images: { id: number; source_kind: string; attribution: string | null; license_note: string | null; is_primary: boolean; url: string }[];
  disclaimer: string;
}

export interface Meta {
  version: string;
  auth_mode: string;
  demo_mode: boolean;
  disclaimer: string;
  counties: { county: County; label: string; parcel_label: string; sources: SourceInfo[] }[];
  decisions: string[];
  manual_sites: Record<string, string>;
}
