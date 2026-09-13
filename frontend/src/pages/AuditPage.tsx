import { useState } from 'react';
import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { formatDate } from '../format';
import { Badge, ErrorNote, Section } from '../components/ui';

interface AuditEntry {
  id: number;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  project_id: number | null;
  details: Record<string, unknown> | null;
  hash: string;
}

export default function AuditPage() {
  const [projectId, setProjectId] = useState('');
  const { data, error, reload } = useFetch<AuditEntry[]>(`/api/audit?limit=500${projectId ? `&project_id=${projectId}` : ''}`);
  const [verify, setVerify] = useState<{ intact: boolean; first_invalid_id: number | null } | null>(null);
  const action = useAsyncAction();

  return (
    <div className="page">
      <Section
        title="Immutable audit log"
        actions={
          <>
            <input placeholder="Project ID" value={projectId} onChange={(e) => setProjectId(e.target.value.replace(/\D/g, ''))} style={{ width: 110 }} />
            <button onClick={reload}>Refresh</button>
            <button onClick={() => action.run(async () => setVerify(await api.get('/api/audit/verify')))}>Verify hash chain</button>
          </>
        }
      >
        <p className="muted small">
          Each entry is hash-chained to the one before, so edits or deletions are detectable. The log covers enrichment, captures, manual edits and overrides, rule changes, terms acknowledgements, secret changes (never the values), refreshes and exports.
        </p>
        {verify && (
          <div className={`note ${verify.intact ? 'note-ok' : 'note-error'}`}>
            {verify.intact ? 'Audit chain intact.' : `Audit chain broken at entry #${verify.first_invalid_id}.`}
          </div>
        )}
        <ErrorNote error={error ?? action.error} />
        <div className="table-scroll">
          <table className="table compact">
            <thead>
              <tr>
                <th>#</th>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {data?.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td>
                  <td className="small">{formatDate(a.occurred_at)}</td>
                  <td>{a.actor}</td>
                  <td>
                    <Badge tone="blue">{a.action}</Badge>
                  </td>
                  <td className="small">
                    {a.entity_type} {a.entity_id}
                    {a.project_id ? ` · project ${a.project_id}` : ''}
                  </td>
                  <td className="small mono details-cell">{JSON.stringify(a.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
