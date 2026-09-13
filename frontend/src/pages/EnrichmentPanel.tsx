import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import type { GridResponse, Project, SourceInfo } from '../types';
import { Badge, Empty, ErrorNote, Section } from '../components/ui';
import JobsPanel from './JobsPanel';

export default function EnrichmentPanel({ project }: { project: Project }) {
  const grid = useFetch<GridResponse>(`/api/projects/${project.id}/grid`);
  const sources = useFetch<SourceInfo[]>(`/api/sources?county=${project.county}`);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [chosenSources, setChosenSources] = useState<Set<string> | null>(null);
  const [force, setForce] = useState(false);
  const [filter, setFilter] = useState('');
  const action = useAsyncAction();
  const [jobsTick, setJobsTick] = useState(0);

  const rows = useMemo(() => grid.data?.rows ?? [], [grid.data]);
  const pilotIds = useMemo(() => rows.filter((r) => r.is_pilot).map((r) => r.id), [rows]);
  const visible = rows.filter((r) => !filter || JSON.stringify(r.values).toLowerCase().includes(filter.toLowerCase()));

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const savePilot = (body: { property_ids?: number[]; count?: number }) =>
    action.run(async () => {
      await api.post(`/api/projects/${project.id}/pilot`, body);
      setSelected(new Set());
      grid.reload();
    });

  const enrich = (selection: 'pilot' | 'all') =>
    action.run(async () => {
      if (selection === 'all' && !window.confirm(`Run enrichment for all ${rows.length} properties? Automated sources are rate-limited, so this can take a while.`)) return;
      await api.post(`/api/projects/${project.id}/enrich`, { selection, sources: chosenSources ? [...chosenSources] : null, force });
      setJobsTick((t) => t + 1);
    });

  const sourceList = sources.data ?? [];
  return (
    <div className="grid-2 wide-left">
      <Section
        title={`Choose pilot properties (${pilotIds.length} selected)`}
        actions={
          <>
            <button onClick={() => savePilot({ count: 8 })} disabled={action.busy || rows.length === 0}>
              Auto-select first 8
            </button>
            <button onClick={() => savePilot({ property_ids: [...selected] })} disabled={action.busy || selected.size === 0}>
              Use checked ({selected.size})
            </button>
          </>
        }
      >
        {rows.length === 0 && (
          <Empty>
            No properties yet. <Link to={`/projects/${project.id}/import`}>Import and commit a sale list</Link> first.
          </Empty>
        )}
        <input className="search" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <div className="table-scroll short">
          <table className="table compact">
            <thead>
              <tr>
                <th />
                <th>{project.county === 'delco' ? 'Folio-NBR' : 'Parcel'}</th>
                <th>Owner</th>
                <th>Location</th>
                <th>Municipality</th>
                <th>Pilot</th>
              </tr>
            </thead>
            <tbody>
              {visible.slice(0, 600).map((r) => (
                <tr key={r.id} className={r.is_pilot ? 'row-pilot' : ''}>
                  <td>
                    <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} aria-label={`Select ${r.values.parcel}`} />
                  </td>
                  <td>{r.values.parcel}</td>
                  <td>{r.values.owner_name}</td>
                  <td>{r.values.full_location ?? r.values.site_address}</td>
                  <td>{r.values.municipality}</td>
                  <td>{r.is_pilot && <Badge tone="blue">pilot</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      <div>
        <Section title="Run enrichment">
          <p className="muted small">
            Automated sources run in the background worker. User-assisted sources create tasks for you. Cached results are reused unless you force a refresh.
          </p>
          {sourceList.map((s) => (
            <label key={s.key} className="check block">
              <input
                type="checkbox"
                checked={chosenSources ? chosenSources.has(s.key) : true}
                onChange={(e) =>
                  setChosenSources((prev) => {
                    const next = new Set(prev ?? sourceList.map((x) => x.key));
                    if (e.target.checked) next.add(s.key);
                    else next.delete(s.key);
                    return next;
                  })
                }
              />
              <span>
                {s.name} <Badge tone={s.automated ? 'green' : 'blue'}>{s.automated ? 'automated' : 'user-assisted'}</Badge>
                {s.requires_terms_ack && !s.policy?.terms_acknowledged_at && <Badge tone="yellow">terms not acknowledged</Badge>}
              </span>
            </label>
          ))}
          <label className="check">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Ignore cache (re-fetch)
          </label>
          <ErrorNote error={action.error ?? grid.error} />
          <div className="actions">
            <button className="primary" onClick={() => enrich('pilot')} disabled={action.busy || pilotIds.length === 0}>
              Run pilot ({pilotIds.length})
            </button>
            <button onClick={() => enrich('all')} disabled={action.busy || rows.length === 0}>
              Run all ({rows.length})
            </button>
          </div>
          <p className="small">
            Next: <Link to={`/projects/${project.id}/tasks`}>work through user-assisted tasks</Link> · <Link to={`/projects/${project.id}/results`}>review the results grid</Link>
          </p>
        </Section>
        <JobsPanel key={jobsTick} projectId={project.id} compact onFinished={grid.reload} />
      </div>
    </div>
  );
}
