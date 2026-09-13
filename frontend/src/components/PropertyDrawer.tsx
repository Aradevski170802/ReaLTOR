import { useState } from 'react';
import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { DECISION_TONE, formatDate, formatMoney, humanize, MORTGAGE_TONE } from '../format';
import type { Mortgage, PropertyDetail } from '../types';
import CapturePanel from './CapturePanel';
import { qualityTone } from './EvidencePanel';
import OverrideDialog, { type OverrideTarget } from './OverrideDialog';
import { Badge, Empty, ErrorNote, ExternalLink, KeyValue, StatusBadge } from './ui';

const TABS = ['Summary', 'Sale list', 'Assessment', 'Valuation', 'Taxes', 'Mortgages', 'Liens', 'Rules', 'Evidence', 'Capture', 'Images'] as const;
type Tab = (typeof TABS)[number];

export default function PropertyDrawer({ propertyId, onClose, onChanged }: { propertyId: number; onClose: () => void; onChanged: () => void }) {
  const { data, error, loading, reload } = useFetch<PropertyDetail>(`/api/properties/${propertyId}`);
  const [tab, setTab] = useState<Tab>('Summary');
  const [override, setOverride] = useState<OverrideTarget | null>(null);
  const refresh = () => {
    reload();
    onChanged();
  };

  return (
    <aside className="drawer" aria-label="Property details">
      <div className="drawer-head">
        <div>
          <div className="muted small">{data?.property.parcel_label ?? 'Parcel'}</div>
          <h2>{data?.property.parcel ?? '…'}</h2>
          <div>{data?.property.owner_name}</div>
          <div className="muted">{data?.cells.full_location?.value ?? data?.property.property_address}</div>
        </div>
        <div className="drawer-head-right">
          {data?.property.decision_status && (
            <Badge tone={DECISION_TONE[data.property.decision_status] ?? 'grey'}>{data.property.decision_status}</Badge>
          )}
          <button className="icon-btn" onClick={onClose} aria-label="Close details">
            ×
          </button>
        </div>
      </div>
      <nav className="tabs small-tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
            {t === 'Capture' && data?.tasks.filter((x) => x.status === 'open').length ? ` (${data.tasks.filter((x) => x.status === 'open').length})` : ''}
          </button>
        ))}
      </nav>
      <div className="drawer-body">
        {loading && !data && <p className="muted">Loading…</p>}
        <ErrorNote error={error} />
        {data && tab === 'Summary' && <Summary data={data} onOverride={setOverride} />}
        {data && tab === 'Sale list' && <SaleList data={data} />}
        {data && tab === 'Assessment' && <Assessment data={data} onOverride={setOverride} />}
        {data && tab === 'Valuation' && <Valuation data={data} onChanged={refresh} />}
        {data && tab === 'Taxes' && <Taxes data={data} onOverride={setOverride} />}
        {data && tab === 'Mortgages' && <Mortgages data={data} onChanged={refresh} />}
        {data && tab === 'Liens' && <Liens data={data} />}
        {data && tab === 'Rules' && <Rules data={data} onOverride={setOverride} />}
        {data && tab === 'Evidence' && <Evidence data={data} onChanged={refresh} />}
        {data && tab === 'Capture' && <Capture data={data} onChanged={refresh} />}
        {data && tab === 'Images' && <Images data={data} onChanged={refresh} />}
        {data && <p className="disclaimer">{data.disclaimer}</p>}
      </div>
      {override && <OverrideDialog target={override} onClose={() => setOverride(null)} onSaved={refresh} />}
    </aside>
  );
}

type OverrideFn = (t: OverrideTarget) => void;

function CellRow({ data, field, label, money, onOverride }: { data: PropertyDetail; field: string; label: string; money?: boolean; onOverride?: OverrideFn }) {
  const cell = data.cells[field];
  if (!cell) return null;
  const shown = money && typeof cell.value === 'number' ? formatMoney(cell.value) : cell.value ?? '—';
  const style = data.styles.cells[field];
  return (
    <tr>
      <th>{label}</th>
      <td className={style ? `cell-${style}` : ''}>{String(shown)}</td>
      <td>
        <Badge tone={qualityTone(cell.quality)} title={cell.notes ?? undefined}>
          {cell.quality}
        </Badge>
      </td>
      <td className="muted small">{cell.source_name}</td>
      <td>
        {onOverride && (
          <button className="link-btn" onClick={() => onOverride({ propertyId: data.property.id, field, label, currentValue: cell.value })}>
            override
          </button>
        )}
      </td>
    </tr>
  );
}

function Summary({ data, onOverride }: { data: PropertyDetail; onOverride: OverrideFn }) {
  return (
    <>
      <p className="decision-summary">{data.property.decision_summary ?? 'Not evaluated yet.'}</p>
      <table className="table compact">
        <tbody>
          <CellRow data={data} field="selected_valuation" label="Selected market valuation" money onOverride={onOverride} />
          <CellRow data={data} field="selected_valuation_method" label="Valuation source & method" />
          <CellRow data={data} field="category" label="Property category" onOverride={onOverride} />
          <CellRow data={data} field={data.property.county === 'montco' ? 'land_use_description' : 'property_type'} label={data.property.county === 'montco' ? 'Land Use Description' : 'Property Type'} onOverride={onOverride} />
          <CellRow data={data} field={data.property.county === 'montco' ? 'living_area_sqft' : 'base_area_sqft'} label={data.property.county === 'montco' ? 'Sq Ft Living Area' : 'Base Area'} onOverride={onOverride} />
          <CellRow data={data} field="acres" label="Acres" onOverride={onOverride} />
          <CellRow data={data} field="tax_sale_status" label="Tax status" />
          <CellRow data={data} field="mortgage_summary" label="Mortgage summary" />
          <CellRow data={data} field="lien_summary" label="Lien cases" />
          <CellRow data={data} field="decision_status" label="Decision" onOverride={onOverride} />
        </tbody>
      </table>
    </>
  );
}

function SaleList({ data }: { data: PropertyDetail }) {
  const p = data.property;
  return (
    <>
      <KeyValue
        items={[
          ['Sale / record number', p.sale_number],
          [`${p.parcel_label} (as printed)`, p.parcel_original],
          ['Normalized', p.parcel],
          ['Owner', p.owner_name],
          ['Mailing address', p.mailing_address],
          ['Location', p.property_address],
          ['Municipality', p.municipality],
          ['Sale date', formatDate(p.sale_date)],
          [p.county === 'montco' ? 'Approx. Sale Price' : 'Total Due', formatMoney(p.listed_amount, 2)],
          ['Current assessment (list)', formatMoney(p.listed_assessment)],
          ['Description', p.listed_description],
          ['Lot size', p.listed_lot_size],
          ['PDF page', p.source_page ? <a key="pg" href={`/api/imports/${p.import_id}/pages/${p.source_page}`} target="_blank" rel="noopener noreferrer">Page {p.source_page}</a> : null],
          ['Extraction confidence', p.extraction_confidence?.toFixed?.(2)],
        ]}
      />
      {data.sale_list_tax_lines.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>Year</th>
              <th>CTY</th>
              <th>SCH</th>
              <th>TWN</th>
              <th>Amount</th>
            </tr>
          </thead>
          <tbody>
            {data.sale_list_tax_lines.map((t) => (
              <tr key={t.year}>
                <td>{t.year}</td>
                <td>{t.cty?.toLocaleString() ?? '—'}</td>
                <td>{t.sch?.toLocaleString() ?? '—'}</td>
                <td>{t.twn?.toLocaleString() ?? '—'}</td>
                <td>{formatMoney(t.amount, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h4>Identifiers</h4>
      <KeyValue items={Object.entries(data.identifiers).map(([k, v]) => [humanize(k), k === 'detail_link' ? <ExternalLink key={k} href={v}>{v}</ExternalLink> : v])} />
    </>
  );
}

function Assessment({ data, onOverride }: { data: PropertyDetail; onOverride: OverrideFn }) {
  const fields: [string, string, boolean?][] =
    data.property.county === 'montco'
      ? [['land_use_description', 'Land Use Description'], ['school_district', 'School District'], ['assessed_value', 'Assessed Value', true], ['lot_size', 'Lot Size'], ['building_style', 'Building Style'], ['year_built', 'Year Built'], ['exterior_wall', 'Exterior Wall'], ['living_area_sqft', 'Sq Ft Living Area'], ['total_rooms', 'Total Rooms'], ['bedrooms', 'Bedrooms'], ['full_baths', 'Bathrooms'], ['half_baths', 'Half Baths'], ['last_sale_date', 'Sale Date'], ['last_sale_price', 'Sale Price', true], ['gross_building_area', 'Gross Building Area'], ['total_living_units', 'Total Living Units'], ['structure_description', 'Structure'], ['acres', 'Acres']]
      : [['site_address', 'Site Location'], ['legal_description', 'Legal Description'], ['school_district', 'School District'], ['property_type', 'Property Type'], ['assessed_value', 'Assessment Value', true], ['building_style', 'Style'], ['base_area_sqft', 'Base Area'], ['year_built', 'Year Built'], ['bedrooms', 'Bed Rooms'], ['full_baths', 'Full Baths'], ['acres', 'Acres'], ['last_sale_date', 'Sale Date'], ['last_sale_price', 'Sale Price', true], ['improvement_name', 'Improvement Name (C)'], ['commercial_living_area', 'Living Area (C)'], ['commercial_year_built', 'Year Built (C)'], ['commercial_units', 'Units (C)']];
  return (
    <>
      <table className="table compact">
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Quality</th>
            <th>Source</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {fields.map(([key, label, money]) => (
            <CellRow key={key} data={data} field={key} label={label} money={money} onOverride={onOverride} />
          ))}
        </tbody>
      </table>
      <h4>Records by source</h4>
      {data.assessments.length === 0 && <Empty>No assessment records yet.</Empty>}
      {data.assessments.map((a) => (
        <details key={a.id}>
          <summary>
            {a.source_key} · {formatDate(a.retrieved_at)} {a.is_current ? <Badge tone="green">current</Badge> : <Badge>superseded</Badge>} <Badge>{a.entry_method}</Badge>
          </summary>
          <pre className="pre">{JSON.stringify(a, null, 2)}</pre>
        </details>
      ))}
    </>
  );
}

function Valuation({ data, onChanged }: { data: PropertyDetail; onChanged: () => void }) {
  const [site, setSite] = useState(Object.keys(data.manual_sites)[0] ?? 'zillow');
  const [value, setValue] = useState('');
  const [url, setUrl] = useState('');
  const [note, setNote] = useState('');
  const { busy, error, run } = useAsyncAction();
  const add = () =>
    run(async () => {
      await api.post(`/api/properties/${data.property.id}/valuations/manual`, { site, value: Number(value), url: url || null, note: note || null });
      setValue('');
      setUrl('');
      setNote('');
      onChanged();
    });
  const runProviders = () => run(async () => api.post(`/api/properties/${data.property.id}/valuations/run`));
  return (
    <>
      <div className="note">
        <strong>{data.selection?.value ? formatMoney(data.selection.value) : 'No selected valuation'}</strong>
        <div className="small">{data.selection?.explanation}</div>
      </div>
      <table className="table compact">
        <thead>
          <tr>
            <th>Provider</th>
            <th>Type</th>
            <th>Point</th>
            <th>Range</th>
            <th>Coverage</th>
            <th>Conf.</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody>
          {data.valuations.filter((v) => v.is_current).map((v) => (
            <tr key={v.id}>
              <td>
                {v.provider_name} {v.is_sandbox && <Badge tone="yellow">SANDBOX</Badge>}
              </td>
              <td>{v.estimate_type}</td>
              <td>{formatMoney(v.point)}</td>
              <td>{v.low && v.high ? `${formatMoney(v.low)}–${formatMoney(v.high)}` : '—'}</td>
              <td>
                <StatusBadge status={v.coverage_status} title={v.notes ?? undefined} />
              </td>
              <td>{v.confidence?.toFixed?.(2) ?? '—'}</td>
              <td>{formatDate(v.estimate_date ?? v.response_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Record a value you looked up yourself</h4>
      <p className="muted small">Listing websites are not scraped. Record what you saw on the site, with a link, and it is stored as a manual observation.</p>
      <div className="form-grid">
        <label className="field">
          <span>Site</span>
          <select value={site} onChange={(e) => setSite(e.target.value)}>
            {Object.entries(data.manual_sites).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Value ($)</span>
          <input type="number" min={1} value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <label className="field">
          <span>Link (optional)</span>
          <input value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label className="field">
          <span>Note (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <ErrorNote error={error} />
      <div className="actions">
        <button onClick={runProviders} disabled={busy}>
          Re-run configured providers
        </button>
        <button className="primary" disabled={busy || !(Number(value) > 0)} onClick={add}>
          Add manual value
        </button>
      </div>
    </>
  );
}

function Taxes({ data, onOverride }: { data: PropertyDetail; onOverride: OverrideFn }) {
  const current = data.tax_claims.filter((t) => t.is_current);
  return (
    <>
      <table className="table compact">
        <tbody>
          <CellRow data={data} field="tax_sale_status" label="Status" />
          {data.property.county === 'montco' ? (
            <>
              <CellRow data={data} field="tax_2023_total" label="2023 Total" money />
              <CellRow data={data} field="tax_2024_total" label="2024 Total" money />
              <CellRow data={data} field="tax_2024_balance" label="2024 Balance" money />
              <CellRow data={data} field="tax_2025_total" label="2025 Total" money />
              <CellRow data={data} field="tax_2025_balance" label="2025 Balance" money />
            </>
          ) : (
            <>
              <CellRow data={data} field="tax_2024_due" label="2024 taxes due" money />
              <CellRow data={data} field="tax_2025_due" label="2025 taxes due" money />
            </>
          )}
          <CellRow data={data} field="delinquent_total" label="Delinquent total" money />
        </tbody>
      </table>
      <div className="actions">
        <button onClick={() => onOverride({ propertyId: data.property.id, field: 'tax_status_interpretation', label: 'Tax status interpretation', currentValue: data.cells.tax_sale_status?.value })}>
          Override tax-status interpretation…
        </button>
      </div>
      {current.map((t) => (
        <div key={t.id}>
          <h4>
            {t.source_key} · {t.as_of_text ?? formatDate(t.retrieved_at)}
          </h4>
          <table className="table compact">
            <thead>
              <tr>
                <th>Year</th>
                <th>Kind</th>
                <th>Face</th>
                <th>Penalty</th>
                <th>Interest</th>
                <th>Total</th>
                <th>Paid</th>
                <th>Balance</th>
              </tr>
            </thead>
            <tbody>
              {t.years.map((y: any) => (
                <tr key={`${y.year}-${y.kind}`} className={y.year === 2024 && y.kind === 'claim' && y.balance === 0 ? 'cell-red' : ''}>
                  <td>{y.year}</td>
                  <td>{y.kind}</td>
                  <td>{formatMoney(y.face, 2)}</td>
                  <td>{formatMoney(y.penalty, 2)}</td>
                  <td>{formatMoney(y.interest, 2)}</td>
                  <td>{formatMoney(y.total, 2)}</td>
                  <td>{formatMoney(y.paid, 2)}</td>
                  <td>{formatMoney(y.balance, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <h4>Status history</h4>
      <table className="table compact">
        <thead>
          <tr>
            <th>Checked</th>
            <th>Lookup</th>
            <th>Status</th>
            <th>2024 balance</th>
            <th>Changed</th>
          </tr>
        </thead>
        <tbody>
          {data.tax_history.map((h) => (
            <tr key={h.id}>
              <td>{formatDate(h.checked_at)}</td>
              <td>
                <StatusBadge status={h.lookup_status} />
              </td>
              <td>{h.status_text ?? '—'}</td>
              <td>{formatMoney(h.balance_2024, 2)}</td>
              <td>{h.changed ? 'yes' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Mortgages({ data, onChanged }: { data: PropertyDetail; onChanged: () => void }) {
  const { busy, error, run } = useAsyncAction();
  const review = (m: Mortgage, satisfactionId: number, confirm: boolean) =>
    run(async () => {
      const note = window.prompt(confirm ? 'Why does this satisfaction discharge the mortgage?' : 'Why is this not the matching satisfaction?') ?? '';
      await api.post(`/api/mortgages/${m.id}/matches`, { satisfaction_document_id: satisfactionId, confirm, note: note || null });
      onChanged();
    });
  if (!data.cells.mortgage_summary || data.cells.mortgage_summary.value === 'Recorder not searched') {
    return <Empty>Recorder of Deeds documents have not been captured yet. Use the Capture tab.</Empty>;
  }
  return (
    <>
      <ErrorNote error={error} />
      <p>
        <strong>{data.cells.mortgage_summary.value}</strong>
      </p>
      {data.mortgages.map((m) => (
        <div key={m.id} className={`mortgage tone-border-${MORTGAGE_TONE[m.effective_status] ?? 'grey'}`}>
          <div className="row between">
            <div>
              <strong>{m.instrument ?? `Document ${m.document_id}`}</strong> · {formatDate(m.recorded_date)} · {formatMoney(m.original_amount, 2)}
            </div>
            <Badge tone={MORTGAGE_TONE[m.effective_status] ?? 'grey'}>{m.effective_status}</Badge>
          </div>
          <div className="small">
            Lender: {m.lender ?? '—'} · Borrower: {m.borrower ?? '—'}
          </div>
          <div className="small muted">{m.status_reason}</div>
          {m.matches.map((x) => (
            <div key={x.satisfaction_document_id} className="match">
              <span>
                {x.instrument ?? x.satisfaction_document_id} ({formatDate(x.recorded_date)}) · score {x.score.toFixed(2)} · {x.decision.replace('_', ' ')}
              </span>
              {(x.decision === 'candidate' || x.decision === 'auto_confirmed') && (
                <span className="row gap">
                  <button className="small-btn" disabled={busy} onClick={() => review(m, x.satisfaction_document_id, true)}>
                    Confirm
                  </button>
                  <button className="small-btn" disabled={busy} onClick={() => review(m, x.satisfaction_document_id, false)}>
                    Reject
                  </button>
                </span>
              )}
              {x.reasons && <div className="muted small">{x.reasons.join('; ')}</div>}
            </div>
          ))}
        </div>
      ))}
      <h4>All recorder documents</h4>
      <table className="table compact">
        <thead>
          <tr>
            <th>Instrument</th>
            <th>Type</th>
            <th>Recorded</th>
            <th>Grantor</th>
            <th>Grantee</th>
            <th>Amount</th>
            <th>References</th>
          </tr>
        </thead>
        <tbody>
          {data.documents.map((d) => (
            <tr key={d.id}>
              <td>{d.source_url ? <ExternalLink href={d.source_url}>{d.instrument_number ?? `${d.book}/${d.page}`}</ExternalLink> : d.instrument_number ?? `${d.book}/${d.page}`}</td>
              <td>{d.doc_type_raw}</td>
              <td>{formatDate(d.recorded_date)}</td>
              <td>{d.grantors}</td>
              <td>{d.grantees}</td>
              <td>{formatMoney(d.amount, 2)}</td>
              <td>{d.related_reference}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Liens({ data }: { data: PropertyDetail }) {
  const searched = data.cells.lien_summary?.value !== 'Not searched';
  if (!searched) return <Empty>Civil / lien search not completed. Use the Capture tab.</Empty>;
  if (data.liens.length === 0) return <Empty>The completed search found no relevant lien cases.</Empty>;
  return (
    <table className="table compact">
      <thead>
        <tr>
          <th>Case</th>
          <th>Status</th>
          <th>Type / classification</th>
          <th>Parties</th>
          <th>Filed</th>
          <th>Amount</th>
        </tr>
      </thead>
      <tbody>
        {data.liens.map((c) => (
          <tr key={c.id}>
            <td>{c.source_url ? <ExternalLink href={c.source_url}>{c.case_number}</ExternalLink> : c.case_number}</td>
            <td>{c.status}</td>
            <td>{c.case_type ?? c.classification}</td>
            <td>{c.parties}</td>
            <td>{formatDate(c.filing_date)}</td>
            <td>{formatMoney(c.amount, 2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Rules({ data, onOverride }: { data: PropertyDetail; onOverride: OverrideFn }) {
  return (
    <>
      <p className="decision-summary">
        <Badge tone={DECISION_TONE[data.property.decision_status ?? ''] ?? 'grey'}>{data.property.decision_status ?? 'Not evaluated'}</Badge>
      </p>
      <table className="table compact">
        <thead>
          <tr>
            <th>Rule</th>
            <th>Outcome</th>
            <th>Explanation</th>
          </tr>
        </thead>
        <tbody>
          {data.rules.map((r) => (
            <tr key={r.rule_key}>
              <td>{humanize(r.rule_key)}</td>
              <td>
                <StatusBadge status={r.outcome} />
              </td>
              <td>
                {r.message}
                <details>
                  <summary className="small muted">inputs</summary>
                  <pre className="pre">{JSON.stringify(r.inputs, null, 2)}</pre>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Overrides</h4>
      {data.overrides.length === 0 && <Empty>No overrides.</Empty>}
      <table className="table compact">
        <tbody>
          {data.overrides.map((o) => (
            <tr key={o.id} className={o.active ? '' : 'muted'}>
              <td>{o.field}</td>
              <td>
                {o.original_value ?? '—'} → <strong>{o.override_value ?? '—'}</strong>
              </td>
              <td>{o.reason}</td>
              <td className="small">
                {o.created_by} · {formatDate(o.created_at)} {o.active ? '' : '(revoked)'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        <button onClick={() => onOverride({ propertyId: data.property.id, field: 'decision_status', label: 'Decision', currentValue: data.property.decision_status })}>
          Override decision…
        </button>
      </div>
    </>
  );
}

function Evidence({ data, onChanged }: { data: PropertyDetail; onChanged: () => void }) {
  const { busy, error, run } = useAsyncAction();
  const rerun = (key: string) =>
    run(async () => {
      await api.post(`/api/properties/${data.property.id}/rerun`, { sources: [key], force: true });
      window.setTimeout(onChanged, 2500);
    });
  return (
    <>
      <ErrorNote error={error} />
      <table className="table compact">
        <thead>
          <tr>
            <th>Source</th>
            <th>Access</th>
            <th>Status</th>
            <th>Message</th>
            <th>Checked</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.sources.map((s) => {
            const lk = data.lookups[s.key];
            return (
              <tr key={s.key}>
                <td title={s.terms_notes}>{s.name}</td>
                <td>
                  <Badge tone={s.automated ? 'green' : 'blue'}>{s.access_method.replace('_', ' ')}</Badge>
                </td>
                <td>{lk ? <StatusBadge status={lk.status} /> : <span className="muted">not run</span>}</td>
                <td className="small">
                  {lk?.message}
                  {lk?.evidence_id && (
                    <>
                      {' '}
                      <a href={`/api/evidence/${lk.evidence_id}/content`} target="_blank" rel="noopener noreferrer">
                        snapshot
                      </a>
                    </>
                  )}
                </td>
                <td className="small">{formatDate(lk?.finished_at)}</td>
                <td>
                  {s.automated && (
                    <button className="small-btn" disabled={busy} onClick={() => rerun(s.key)}>
                      Re-run
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function Capture({ data, onChanged }: { data: PropertyDetail; onChanged: () => void }) {
  const capturable = data.sources.filter((s) => s.supports_capture);
  const [selected, setSelected] = useState(capturable.find((s) => data.tasks.some((t) => t.status === 'open' && t.source_key === s.key))?.key ?? capturable[0]?.key);
  const source = capturable.find((s) => s.key === selected);
  const task = data.tasks.find((t) => t.source_key === selected && t.status === 'open');
  return (
    <>
      <label className="field">
        <span>Source</span>
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {capturable.map((s) => (
            <option key={s.key} value={s.key}>
              {s.name} {data.tasks.some((t) => t.status === 'open' && t.source_key === s.key) ? '— task open' : ''}
            </option>
          ))}
        </select>
      </label>
      {source && (
        <CapturePanel
          key={source.key}
          target={{
            propertyId: data.property.id,
            sourceKey: source.key,
            sourceName: source.name,
            category: source.category,
            url: task?.url ?? source.searches[0]?.url ?? null,
            searchHint: task?.search_hint ?? source.input_requirements,
            instructions: task?.instructions ?? source.terms_notes,
          }}
          onSaved={onChanged}
        />
      )}
    </>
  );
}

function Images({ data, onChanged }: { data: PropertyDetail; onChanged: () => void }) {
  const { busy, error, run } = useAsyncAction();
  const [file, setFile] = useState<File | null>(null);
  const upload = () =>
    run(async () => {
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      await api.upload(`/api/properties/${data.property.id}/images`, form);
      setFile(null);
      onChanged();
    });
  const streetView = () =>
    run(async () => {
      await api.post(`/api/properties/${data.property.id}/images/street-view`);
      onChanged();
    });
  const photo = data.cells.property_photo;
  return (
    <>
      <p className="muted small">
        Photos show only when the selected valuation meets the threshold and a permitted image source exists: your own upload, or a configured Google Street View key. Listing-site photos are never scraped.
      </p>
      {photo?.value && String(photo.value).startsWith('/api/') ? (
        <figure className="photo">
          <img src={String(photo.value)} alt={`Property ${data.property.parcel}`} />
          <figcaption className="small muted">
            {photo.source_name} — {photo.notes}
          </figcaption>
        </figure>
      ) : (
        <Empty>{photo?.value ?? 'No photo shown (valuation below threshold or not available).'}</Empty>
      )}
      <ErrorNote error={error} />
      <div className="row gap">
        <input type="file" accept=".jpg,.jpeg,.png,.webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Photo file" />
        <button disabled={!file || busy} onClick={upload}>
          Upload photo
        </button>
        <button disabled={busy} onClick={streetView}>
          Use Google Street View
        </button>
      </div>
    </>
  );
}
