import { useEffect, useRef } from 'react';
import { api } from '../api';
import { useAsyncAction, useFetch, useInterval } from '../hooks';
import { formatDate, humanize } from '../format';
import type { Job } from '../types';
import { Empty, ErrorNote, ProgressBar, Section, StatusBadge } from '../components/ui';

export default function JobsPanel({ projectId, compact, onFinished }: { projectId: number; compact?: boolean; onFinished?: () => void }) {
  const { data, error, reload } = useFetch<Job[]>(`/api/jobs?project_id=${projectId}&limit=${compact ? 8 : 100}`);
  const action = useAsyncAction();
  const active = data?.some((j) => j.status === 'queued' || j.status === 'running' || (j.children && ((j.children.queued ?? 0) + (j.children.running ?? 0)) > 0)) ?? false;
  useInterval(reload, 2000, active);
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) onFinished?.();
    wasActive.current = active;
  }, [active, onFinished]);

  return (
    <Section title="Background jobs" actions={<button onClick={reload}>Refresh</button>}>
      <ErrorNote error={error ?? action.error} />
      {data?.length === 0 && <Empty>No jobs yet.</Empty>}
      <table className="table compact">
        <thead>
          <tr>
            <th>Job</th>
            <th>Status</th>
            <th>Progress</th>
            {!compact && <th>Created</th>}
            <th />
          </tr>
        </thead>
        <tbody>
          {data?.map((j) => {
            const children = j.children ?? {};
            const total = Object.values(children).reduce((a, b) => a + b, 0);
            const done = (children.succeeded ?? 0) + (children.failed ?? 0) + (children.cancelled ?? 0);
            return (
              <tr key={j.id}>
                <td>
                  #{j.id} {humanize(j.kind)}
                  {j.error && <div className="small note-error-text">{j.error}</div>}
                  {!compact && j.result && <div className="small muted">{JSON.stringify(j.result).slice(0, 180)}</div>}
                </td>
                <td>
                  <StatusBadge status={j.status} />
                  {children.failed ? <StatusBadge status="failed" title={`${children.failed} property job(s) failed`} /> : null}
                </td>
                <td>{total ? <ProgressBar done={done} total={total} /> : j.attempts > 1 ? `attempt ${j.attempts}/${j.max_attempts}` : ''}</td>
                {!compact && <td className="small">{formatDate(j.created_at)}</td>}
                <td>
                  {(j.status === 'queued' || j.status === 'running' || total > done) && (
                    <button className="small-btn" onClick={() => action.run(async () => api.post(`/api/jobs/${j.id}/cancel`).then(reload))}>
                      Cancel
                    </button>
                  )}
                  {(j.status === 'failed' || j.status === 'cancelled') && (
                    <button className="small-btn" onClick={() => action.run(async () => api.post(`/api/jobs/${j.id}/retry`).then(reload))}>
                      Retry
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Section>
  );
}
