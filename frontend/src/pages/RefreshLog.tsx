import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { formatDate } from '../format';
import { Empty, ErrorNote, Section } from '../components/ui';

interface Run {
  id: number;
  kind: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  updated: number;
  changed: number;
  failed: number;
  user_action_needed: number;
  summary: string | null;
}

export default function RefreshLog({ projectId }: { projectId: number }) {
  const { data, error, reload } = useFetch<Run[]>(`/api/projects/${projectId}/refresh-runs`);
  const action = useAsyncAction();
  return (
    <Section
      title="Daily tax-status refresh history"
      actions={
        <>
          <button onClick={reload}>Refresh</button>
          <button className="primary" onClick={() => action.run(async () => api.post(`/api/projects/${projectId}/refresh-tax-status`, {}))}>
            Run refresh now
          </button>
        </>
      }
    >
      <p className="muted small">
        The worker queues this refresh once a day at the configured time. Montgomery Tax Claim status is fetched automatically once its terms are acknowledged. Delaware County status comes from the assessment portal, which needs a person, so each run creates re-capture tasks and older values are marked stale.
      </p>
      <ErrorNote error={error ?? action.error} />
      {data?.length === 0 && <Empty>No refresh runs yet.</Empty>}
      <table className="table compact">
        <thead>
          <tr>
            <th>Run</th>
            <th>Trigger</th>
            <th>Started</th>
            <th>Finished</th>
            <th>Checked</th>
            <th>Updated</th>
            <th>Changed</th>
            <th>Failed</th>
            <th>User action</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((r) => (
            <tr key={r.id} title={r.summary ?? ''}>
              <td>#{r.id}</td>
              <td>{r.trigger}</td>
              <td>{formatDate(r.started_at)}</td>
              <td>{formatDate(r.finished_at)}</td>
              <td>{r.total}</td>
              <td>{r.updated}</td>
              <td>{r.changed}</td>
              <td>{r.failed}</td>
              <td>{r.user_action_needed}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}
