import { useMemo, useState } from 'react';
import { api } from '../api';
import CapturePanel from '../components/CapturePanel';
import { useAsyncAction, useFetch } from '../hooks';
import type { CaptureTask, Project, SourceInfo } from '../types';
import { Badge, Empty, ErrorNote, Section } from '../components/ui';

export default function TasksPage({ project }: { project: Project }) {
  const tasks = useFetch<CaptureTask[]>(`/api/projects/${project.id}/tasks`);
  const sources = useFetch<SourceInfo[]>(`/api/sources?county=${project.county}`);
  const [sourceFilter, setSourceFilter] = useState('all');
  const [pilotOnly, setPilotOnly] = useState(true);
  const [openId, setOpenId] = useState<number | null>(null);
  const action = useAsyncAction();

  const byKey = useMemo(() => Object.fromEntries((sources.data ?? []).map((s) => [s.key, s])), [sources.data]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of tasks.data ?? []) c[t.source_key] = (c[t.source_key] ?? 0) + 1;
    return c;
  }, [tasks.data]);
  const visible = (tasks.data ?? []).filter((t) => (sourceFilter === 'all' || t.source_key === sourceFilter) && (!pilotOnly || t.is_pilot));

  const skip = (id: number) => action.run(async () => api.post(`/api/tasks/${id}/skip`).then(tasks.reload));
  const saved = (task: CaptureTask) => {
    const index = visible.findIndex((t) => t.id === task.id);
    const next = visible[index + 1];
    tasks.reload();
    setOpenId(next ? next.id : null);
  };

  return (
    <div>
      <Section title="User-assisted retrieval queue">
        <p className="muted">
          These sources need a person: a disclaimer to accept, your own login, or a human verification step. The app never automates or bypasses those steps. Open the official page, look up the property, then paste or upload the page. The app parses it, stores it as evidence and updates the results.
        </p>
        <div className="row gap wrap">
          <button className={`chip ${sourceFilter === 'all' ? 'active' : ''}`} onClick={() => setSourceFilter('all')}>
            All ({tasks.data?.length ?? 0})
          </button>
          {Object.entries(counts).map(([key, n]) => (
            <button key={key} className={`chip ${sourceFilter === key ? 'active' : ''}`} onClick={() => setSourceFilter(key)}>
              {byKey[key]?.name ?? key} ({n})
            </button>
          ))}
          <label className="check">
            <input type="checkbox" checked={pilotOnly} onChange={(e) => setPilotOnly(e.target.checked)} /> Pilot properties only
          </label>
        </div>
        <ErrorNote error={tasks.error ?? action.error} />
        {visible.length === 0 && <Empty>No open tasks in this view.</Empty>}
      </Section>
      {visible.map((t) => (
        <div key={t.id} className={`task ${openId === t.id ? 'open' : ''}`}>
          <div className="task-head" onClick={() => setOpenId(openId === t.id ? null : t.id)}>
            <div>
              <strong>{t.parcel}</strong> · {t.owner_name ?? '—'} · {t.location ?? '—'} {t.is_pilot && <Badge tone="blue">pilot</Badge>}
            </div>
            <div className="row gap">
              <Badge tone={t.reason === 'daily_refresh' ? 'yellow' : 'grey'}>{t.reason.replace('_', ' ')}</Badge>
              <span className="muted small">{byKey[t.source_key]?.name ?? t.source_key}</span>
              <button
                className="small-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  skip(t.id);
                }}
              >
                Skip
              </button>
            </div>
          </div>
          {t.last_error && <div className="note note-warn small">Last attempt: {t.last_error}</div>}
          {openId === t.id && (
            <CapturePanel
              target={{
                propertyId: t.property_id,
                sourceKey: t.source_key,
                sourceName: byKey[t.source_key]?.name,
                category: byKey[t.source_key]?.category,
                url: t.url,
                searchHint: t.search_hint,
                instructions: t.instructions,
              }}
              onSaved={() => saved(t)}
            />
          )}
        </div>
      ))}
    </div>
  );
}
