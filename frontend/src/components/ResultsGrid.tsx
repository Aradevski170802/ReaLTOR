import { AllCommunityModule, ModuleRegistry, themeQuartz, type CellClickedEvent, type GridApi, type RowClassRules } from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';
import { useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { buildColumnDefs } from '../grid/columnDefs';
import { useAsyncAction, useFetch } from '../hooks';
import { DECISION_TONE, QUALITY_LABEL } from '../format';
import type { ColumnSpec, GridResponse, GridRow } from '../types';
import EvidencePanel from './EvidencePanel';
import OverrideDialog, { type OverrideTarget } from './OverrideDialog';
import PropertyDrawer from './PropertyDrawer';
import { Badge, ErrorNote } from './ui';

ModuleRegistry.registerModules([AllCommunityModule]);

const gridTheme = themeQuartz.withParams({ fontSize: 12, headerFontSize: 12, rowHeight: 32, headerHeight: 44, spacing: 5, wrapperBorderRadius: 6 });

interface SavedView {
  id: number;
  name: string;
  state: { columns?: unknown; filters?: unknown; decision?: string; pilotOnly?: boolean };
}

export default function ResultsGrid({ projectId }: { projectId: number }) {
  const { data, error, loading, reload } = useFetch<GridResponse>(`/api/projects/${projectId}/grid`);
  const views = useFetch<SavedView[]>(`/api/views?project_id=${projectId}`);
  const apiRef = useRef<GridApi<GridRow> | null>(null);
  const [quick, setQuick] = useState('');
  const [decision, setDecision] = useState('all');
  const [pilotOnly, setPilotOnly] = useState(false);
  const [paginate, setPaginate] = useState(false);
  const [openProperty, setOpenProperty] = useState<number | null>(null);
  const [override, setOverride] = useState<OverrideTarget | null>(null);
  const [evidence, setEvidence] = useState<{ propertyId: number; col: ColumnSpec } | null>(null);
  const [chooser, setChooser] = useState(false);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const exportAction = useAsyncAction();

  const columnDefs = useMemo(() => (data ? buildColumnDefs(data.columns) : []), [data]);
  const columnsByKey = useMemo(() => Object.fromEntries((data?.columns ?? []).map((c) => [c.key, c])), [data]);
  const rows = useMemo(
    () =>
      (data?.rows ?? []).filter(
        (r) => (decision === 'all' || (r.values.decision_status ?? 'Not evaluated') === decision) && (!pilotOnly || r.is_pilot),
      ),
    [data, decision, pilotOnly],
  );
  const decisionCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of data?.rows ?? []) {
      const d = r.values.decision_status ?? 'Not evaluated';
      counts[d] = (counts[d] ?? 0) + 1;
    }
    return counts;
  }, [data]);

  const rowClassRules: RowClassRules<GridRow> = useMemo(
    () => ({
      'row-red': (p) => p.data?.styles.row === 'red',
      'row-pilot': (p) => !!p.data?.is_pilot,
      'row-excluded': (p) => !!p.data?.excluded,
    }),
    [],
  );

  const onCellClicked = (e: CellClickedEvent<GridRow>) => {
    const key = e.column.getColId();
    const col = columnsByKey[key];
    if (!e.data || !col) return;
    if (e.event && (e.event as MouseEvent).altKey) {
      setEvidence({ propertyId: e.data.id, col });
      return;
    }
    if (col.pinned) setOpenProperty(e.data.id);
  };

  const saveView = async () => {
    const name = window.prompt('Name this view');
    if (!name || !apiRef.current) return;
    await api.post('/api/views', {
      name,
      project_id: projectId,
      state: { columns: apiRef.current.getColumnState(), filters: apiRef.current.getFilterModel(), decision, pilotOnly },
    });
    views.reload();
  };

  const applyView = (id: string) => {
    const view = views.data?.find((v) => String(v.id) === id);
    if (!view || !apiRef.current) return;
    if (view.state.columns) apiRef.current.applyColumnState({ state: view.state.columns as any, applyOrder: true });
    if (view.state.filters) apiRef.current.setFilterModel(view.state.filters as any);
    setDecision(view.state.decision ?? 'all');
    setPilotOnly(!!view.state.pilotOnly);
  };

  const toggleColumn = (key: string, visible: boolean) => {
    apiRef.current?.setColumnsVisible([key], visible);
    setHidden((h) => ({ ...h, [key]: !visible }));
  };

  const exportXlsx = () =>
    exportAction.run(async () => {
      const res = await api.post<{ export_id: number; download: string }>(`/api/projects/${projectId}/exports?sync=true`, {});
      window.location.href = res.download;
    });

  return (
    <div className="grid-page">
      <div className="toolbar">
        <input className="search" placeholder="Search all columns…" value={quick} onChange={(e) => setQuick(e.target.value)} aria-label="Search grid" />
        <div className="chips">
          {['all', ...Object.keys(decisionCounts)].map((d) => (
            <button key={d} className={`chip ${decision === d ? 'active' : ''} tone-${DECISION_TONE[d] ?? 'grey'}`} onClick={() => setDecision(d)}>
              {d === 'all' ? `All (${data?.rows.length ?? 0})` : `${d} (${decisionCounts[d]})`}
            </button>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={pilotOnly} onChange={(e) => setPilotOnly(e.target.checked)} /> Pilot only
        </label>
        <label className="check">
          <input type="checkbox" checked={paginate} onChange={(e) => setPaginate(e.target.checked)} /> Pages
        </label>
        <div className="spacer" />
        <select onChange={(e) => applyView(e.target.value)} value="" aria-label="Saved views">
          <option value="">Saved views…</option>
          {views.data?.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        <button onClick={saveView}>Save view</button>
        <div className="dropdown">
          <button onClick={() => setChooser((c) => !c)}>Columns</button>
          {chooser && (
            <div className="dropdown-menu">
              {data?.columns.map((c) => (
                <label key={c.key} className="check">
                  <input type="checkbox" checked={!(hidden[c.key] ?? c.hidden)} onChange={(e) => toggleColumn(c.key, e.target.checked)} />
                  {c.header}
                </label>
              ))}
            </div>
          )}
        </div>
        <button onClick={reload} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
        <button className="primary" onClick={exportXlsx} disabled={exportAction.busy}>
          {exportAction.busy ? 'Building…' : 'Export XLSX'}
        </button>
      </div>
      <ErrorNote error={error ?? exportAction.error} />
      <div className="legend">
        <Badge tone="red">Row red: Montco 2024 balance $0.00</Badge>
        <Badge tone="orange">Valuation below ${(data?.thresholds.valuation ?? 200000).toLocaleString()}</Badge>
        <Badge tone="green">Listed for Upset Sale / satisfied</Badge>
        <Badge tone="yellow">Review required</Badge>
        {Object.entries(QUALITY_LABEL).map(([q, label]) => (
          <span key={q} className={`legend-q q-${q}`}>
            {label}
          </span>
        ))}
        <span className="muted small">Click a pinned cell for details · double-click an editable cell to override · Alt+click any cell for evidence</span>
      </div>
      <div className="grid-wrap">
        <AgGridReact<GridRow>
          theme={gridTheme}
          rowData={rows}
          columnDefs={columnDefs}
          getRowId={(p) => String(p.data.id)}
          quickFilterText={quick}
          pagination={paginate}
          paginationPageSize={100}
          rowClassRules={rowClassRules}
          onGridReady={(e) => {
            apiRef.current = e.api;
          }}
          onCellClicked={onCellClicked}
          onCellDoubleClicked={(e) => {
            const col = columnsByKey[e.column.getColId()];
            if (!e.data || !col) return;
            if (col.editable) setOverride({ propertyId: e.data.id, field: col.key, label: col.header, currentValue: e.value });
            else setEvidence({ propertyId: e.data.id, col });
          }}
          tooltipShowDelay={350}
          enableCellTextSelection
          suppressDragLeaveHidesColumns
          defaultColDef={{ resizable: true, sortable: true, minWidth: 56 }}
          overlayNoRowsTemplate="No properties yet — import and commit a sale list first."
        />
      </div>
      {openProperty !== null && <PropertyDrawer propertyId={openProperty} onClose={() => setOpenProperty(null)} onChanged={reload} />}
      {override && <OverrideDialog target={override} onClose={() => setOverride(null)} onSaved={reload} />}
      {evidence && (
        <EvidencePanel propertyId={evidence.propertyId} fieldKey={evidence.col.key} header={evidence.col.header} kind={evidence.col.kind} onClose={() => setEvidence(null)} />
      )}
    </div>
  );
}
