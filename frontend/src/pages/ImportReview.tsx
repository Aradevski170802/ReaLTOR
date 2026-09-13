import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../api';
import { useAsyncAction, useFetch, useInterval } from '../hooks';
import { formatDate } from '../format';
import type { ExtractedRow, ImportRecord, Project } from '../types';
import { Badge, Empty, ErrorNote, KeyValue, Modal, Section, StatusBadge } from '../components/ui';

interface FieldDef {
  key: string;
  label: string;
  essential: boolean;
  kind: string;
}

export default function ImportReview({ project }: { project: Project }) {
  const imports = useFetch<ImportRecord[]>(`/api/projects/${project.id}/imports`);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const upload = useAsyncAction();

  useEffect(() => {
    if (selectedId === null && imports.data?.length) setSelectedId(imports.data[0].id);
  }, [imports.data, selectedId]);
  const parsing = imports.data?.some((i) => i.status === 'uploaded' || i.status === 'parsing') ?? false;
  useInterval(imports.reload, 2500, parsing);

  const doUpload = () =>
    upload.run(async () => {
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      const res = await api.upload<{ import: ImportRecord }>(`/api/projects/${project.id}/imports`, form);
      setFile(null);
      setSelectedId(res.import.id);
      imports.reload();
    });

  const selected = imports.data?.find((i) => i.id === selectedId) ?? null;

  return (
    <div>
      <Section title="Upload sale list">
        <p className="muted">
          Upload the county upset-sale PDF. Text PDFs are parsed directly; image-only pages use OCR when Tesseract is installed, otherwise they are listed for manual entry. CSV/XLSX lists can be imported too.
        </p>
        <div className="row gap">
          <input type="file" accept=".pdf,.csv,.xlsx,.xlsm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Sale list file" />
          <button className="primary" disabled={!file || upload.busy} onClick={doUpload}>
            {upload.busy ? 'Uploading…' : 'Upload & extract'}
          </button>
        </div>
        <ErrorNote error={upload.error ?? imports.error} />
        {imports.data && imports.data.length > 0 && (
          <table className="table compact">
            <thead>
              <tr>
                <th>File</th>
                <th>Status</th>
                <th>Parser</th>
                <th>Rows</th>
                <th>Needs review</th>
                <th>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {imports.data.map((i) => (
                <tr key={i.id} className={i.id === selectedId ? 'selected' : ''} onClick={() => setSelectedId(i.id)}>
                  <td>{i.filename}</td>
                  <td>
                    <StatusBadge status={i.status} title={i.error ?? undefined} />
                  </td>
                  <td className="small">{i.parser_key}</td>
                  <td>{i.stats?.records ?? '—'}</td>
                  <td>{i.stats?.needs_review ?? '—'}</td>
                  <td>{formatDate(i.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      {selected && selected.status !== 'uploaded' && selected.status !== 'parsing' && (
        <ReviewRows key={selected.id} sale={selected} project={project} onChanged={imports.reload} />
      )}
      {selected && (selected.status === 'uploaded' || selected.status === 'parsing') && <Empty>Extracting… this page updates automatically.</Empty>}
    </div>
  );
}

function ReviewRows({ sale, project, onChanged }: { sale: ImportRecord; project: Project; onChanged: () => void }) {
  const [filter, setFilter] = useState<'review' | 'all'>(sale.stats?.needs_review ? 'review' : 'all');
  const rows = useFetch<{ total: number; rows: ExtractedRow[] }>(`/api/imports/${sale.id}/rows?page_size=2000${filter === 'review' ? '&needs_review=true' : ''}`);
  const fields = useFetch<{ fields: FieldDef[]; parsers: string[] }>(`/api/imports/${sale.id}/fields`);
  const [active, setActive] = useState<ExtractedRow | null>(null);
  const [conflict, setConflict] = useState<{ kind: 'parse' | 'commit'; detail: any } | null>(null);
  const action = useAsyncAction();
  const [commitResult, setCommitResult] = useState<Record<string, any> | null>(null);

  const pageKinds = useMemo(
    () => (sale.detection?.pages ?? []).reduce<Record<string, number>>((acc, p) => ({ ...acc, [p.kind]: (acc[p.kind] ?? 0) + 1 }), {}),
    [sale.detection],
  );
  const labels = useMemo(() => Object.fromEntries((fields.data?.fields ?? []).map((f) => [f.key, f.label])), [fields.data]);
  const visibleFields = (fields.data?.fields ?? []).filter((f) => ['sale_number', 'parcel', 'owner_name', 'property_address', 'municipality', 'amount_due'].includes(f.key));

  const commit = (confirm = false) =>
    action.run(async () => {
      try {
        const res = await api.post<Record<string, any>>(`/api/imports/${sale.id}/commit`, { confirm_overwrite: confirm });
        setCommitResult(res);
        setConflict(null);
        onChanged();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) setConflict({ kind: 'commit', detail: err.detail });
        else throw err;
      }
    });

  const reparse = (confirm = false) =>
    action.run(async () => {
      try {
        await api.post(`/api/imports/${sale.id}/parse`, { confirm_overwrite: confirm });
        setConflict(null);
        onChanged();
        rows.reload();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) setConflict({ kind: 'parse', detail: err.detail });
        else throw err;
      }
    });

  return (
    <Section
      title={`Review extraction — ${sale.filename}`}
      actions={
        <>
          <button onClick={() => reparse(false)} disabled={action.busy}>
            Re-extract
          </button>
          <button className="primary" onClick={() => commit(false)} disabled={action.busy}>
            {sale.status === 'committed' ? 'Re-commit changes' : 'Commit rows to project'}
          </button>
        </>
      }
    >
      <KeyValue
        items={[
          ['Parser', sale.parser_key],
          ['Pages', `${sale.page_count ?? '—'} (${Object.entries(pageKinds).map(([k, n]) => `${n} ${k}`).join(', ')})`],
          ['Rows extracted', sale.stats?.records],
          ['Flagged for review', sale.stats?.needs_review],
          ['Report metadata', [sale.stats?.report_date && `report date ${sale.stats.report_date}`, sale.stats?.list_as_of && `list as of ${sale.stats.list_as_of}`, sale.stats?.sale_date && `sale ${sale.stats.sale_date}`].filter(Boolean).join(' · ')],
          ['Warnings', (sale.stats?.warnings ?? []).join(' | ')],
        ]}
      />
      {commitResult && (
        <div className="note note-ok">
          Committed: {commitResult.created} created, {commitResult.updated} updated, {commitResult.unchanged} unchanged
          {commitResult.skipped?.length ? `, ${commitResult.skipped.length} skipped (invalid ${project.county === 'delco' ? 'folio' : 'parcel'})` : ''}.
        </div>
      )}
      <ErrorNote error={action.error ?? rows.error} />
      <div className="row gap">
        <label className="check">
          <input type="radio" checked={filter === 'review'} onChange={() => setFilter('review')} /> Needs review
        </label>
        <label className="check">
          <input type="radio" checked={filter === 'all'} onChange={() => setFilter('all')} /> All rows ({rows.data?.total ?? '…'})
        </label>
      </div>
      {rows.data?.rows.length === 0 && <Empty>No rows in this view.</Empty>}
      <div className="table-scroll">
        <table className="table compact">
          <thead>
            <tr>
              <th>#</th>
              <th>Page</th>
              {visibleFields.map((f) => (
                <th key={f.key}>{f.label}</th>
              ))}
              <th>Conf.</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {rows.data?.rows.map((r) => (
              <tr key={r.id} className={`${r.excluded ? 'muted strike' : ''} ${r.needs_review ? 'row-review' : ''}`} onClick={() => setActive(r)}>
                <td>{r.row_index + 1}</td>
                <td>{r.page_number ?? '—'}</td>
                {visibleFields.map((f) => (
                  <td key={f.key} className={r.values[f.key]?.corrected ? 'q-manual' : (r.values[f.key]?.confidence ?? 1) < 0.8 ? 'low-conf' : ''}>
                    {r.values[f.key]?.value ?? '—'}
                  </td>
                ))}
                <td>{r.confidence.toFixed(2)}</td>
                <td className="small">{r.review_reasons.join('; ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {active && (
        <RowEditor
          row={active}
          sale={sale}
          labels={labels}
          fields={fields.data?.fields ?? []}
          onClose={() => setActive(null)}
          onChanged={(updated) => {
            setActive(updated);
            rows.reload();
          }}
        />
      )}
      {conflict && (
        <Modal title="Confirm overwrite" onClose={() => setConflict(null)}>
          <p>{conflict.detail?.message ?? 'This action would overwrite user-corrected data.'}</p>
          <pre className="pre">{JSON.stringify(conflict.detail?.conflicts ?? conflict.detail, null, 2).slice(0, 3000)}</pre>
          <div className="actions">
            <button onClick={() => setConflict(null)}>Keep my corrections</button>
            <button className="danger" onClick={() => (conflict.kind === 'commit' ? commit(true) : reparse(true))}>
              Overwrite anyway
            </button>
          </div>
        </Modal>
      )}
    </Section>
  );
}

function RowEditor({
  row,
  sale,
  labels,
  fields,
  onClose,
  onChanged,
}: {
  row: ExtractedRow;
  sale: ImportRecord;
  labels: Record<string, string>;
  fields: FieldDef[];
  onClose: () => void;
  onChanged: (row: ExtractedRow) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.key, row.values[f.key]?.value ?? ''])));
  const action = useAsyncAction();
  const save = () =>
    action.run(async () => {
      let updated = row;
      for (const f of fields) {
        const before = row.values[f.key]?.value ?? '';
        if ((draft[f.key] ?? '') !== before) {
          updated = await api.post<ExtractedRow>(`/api/rows/${row.id}/corrections`, { field: f.key, value: draft[f.key] || null });
        }
      }
      onChanged(updated);
    });
  const patch = (body: Record<string, boolean>) => action.run(async () => onChanged(await api.patch<ExtractedRow>(`/api/rows/${row.id}`, body)));

  return (
    <Modal title={`Row ${row.row_index + 1}${row.page_number ? ` · PDF page ${row.page_number}` : ''}`} onClose={onClose} wide>
      <div className="split">
        <div>
          {row.page_number && sale.kind === 'pdf' ? (
            <img className="page-image" src={`/api/imports/${sale.id}/pages/${row.page_number}`} alt={`PDF page ${row.page_number}`} />
          ) : (
            <Empty>No page image for tabular imports.</Empty>
          )}
          <h4>Original text</h4>
          <pre className="pre">{row.raw_text}</pre>
        </div>
        <div>
          {row.review_reasons.length > 0 && (
            <div className="note note-warn">
              {row.review_reasons.map((r) => (
                <div key={r}>• {r}</div>
              ))}
            </div>
          )}
          {fields.map((f) => {
            const v = row.values[f.key];
            return (
              <label key={f.key} className="field">
                <span>
                  {labels[f.key] ?? f.key} {f.essential && <Badge tone="blue">essential</Badge>} {v?.corrected && <Badge tone="purple">corrected by {v.corrected_by}</Badge>}
                  {v && v.confidence < 0.8 && <Badge tone="yellow">confidence {v.confidence.toFixed(2)}</Badge>}
                </span>
                <input value={draft[f.key] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
                {v?.raw && v.raw !== v.value && <span className="muted small">as printed: {v.raw}</span>}
              </label>
            );
          })}
          <ErrorNote error={action.error} />
          <div className="actions">
            <button onClick={() => patch({ excluded: !row.excluded })}>{row.excluded ? 'Include row' : 'Exclude row'}</button>
            {row.needs_review && <button onClick={() => patch({ reviewed: true })}>Mark reviewed</button>}
            <button className="primary" onClick={save} disabled={action.busy}>
              Save corrections
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
