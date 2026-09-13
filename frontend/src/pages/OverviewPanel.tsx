import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { DECISION_TONE, formatDate } from '../format';
import type { Project } from '../types';
import { Badge, ErrorNote, KeyValue, Section, StatusBadge } from '../components/ui';

interface Summary {
  properties: number;
  pilot: number;
  decisions: Record<string, number>;
  open_tasks: Record<string, number>;
  source_health: Record<string, Record<string, number>>;
  imports: { id: number; filename: string; status: string; records: number | null }[];
  last_refresh: { id: number; started_at: string; finished_at: string | null; summary: string | null } | null;
}

export default function OverviewPanel({ project }: { project: Project }) {
  const { data, error, reload } = useFetch<Summary>(`/api/projects/${project.id}/summary`);
  const action = useAsyncAction();
  const refresh = () => action.run(async () => api.post(`/api/projects/${project.id}/refresh-tax-status`, {}).then(reload));

  return (
    <div className="grid-2">
      <Section title="Progress">
        <ErrorNote error={error ?? action.error} />
        {data && (
          <KeyValue
            items={[
              ['Sale lists', data.imports.map((i) => `${i.filename} (${i.status}, ${i.records ?? '?'} rows)`).join(' · ') || 'none — start with Import'],
              ['Properties', data.properties],
              ['Pilot properties', data.pilot || 'none selected'],
              ['Open user-assisted tasks', Object.values(data.open_tasks).reduce((a, b) => a + b, 0)],
              ['Last tax-status refresh', data.last_refresh ? `${formatDate(data.last_refresh.started_at)} — ${data.last_refresh.summary ?? 'running'}` : 'never'],
            ]}
          />
        )}
        <div className="actions">
          <Link className="button" to={`/projects/${project.id}/import`}>
            Import sale list
          </Link>
          <Link className="button" to={`/projects/${project.id}/enrich`}>
            Run pilot
          </Link>
          <button onClick={refresh} disabled={action.busy}>
            Refresh tax status now
          </button>
          <Link className="button primary" to={`/projects/${project.id}/results`}>
            Open results
          </Link>
        </div>
      </Section>
      <Section title="Decisions">
        {data &&
          Object.entries(data.decisions).map(([d, n]) => (
            <div key={d} className="row between">
              <Badge tone={DECISION_TONE[d] ?? 'grey'}>{d}</Badge>
              <strong>{n}</strong>
            </div>
          ))}
      </Section>
      <Section title="Source health">
        <table className="table compact">
          <thead>
            <tr>
              <th>Source</th>
              <th>Statuses</th>
              <th>Open tasks</th>
            </tr>
          </thead>
          <tbody>
            {data &&
              Object.entries(data.source_health).map(([key, counts]) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td>
                    {Object.entries(counts).map(([status, n]) => (
                      <span key={status} className="inline-stat">
                        <StatusBadge status={status} /> {n}
                      </span>
                    ))}
                  </td>
                  <td>{data.open_tasks[key] ?? 0}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </Section>
      <Section title="How this project works">
        <ol className="steps">
          <li>Upload the county's upset-sale PDF. Check the extracted rows and fix anything flagged, then commit.</li>
          <li>Pick 7–8 pilot properties and run enrichment. Automated sources run straight away: the county GIS services, plus Montgomery's Tax Claim Bureau once its terms are acknowledged.</li>
          <li>Work through user-assisted tasks for sources with disclaimers, logins or human checks. Open the official page, then paste or upload what you see.</li>
          <li>Review the results grid, override where you have better information (a reason is required), then run the full list.</li>
          <li>Export the workbook. Tax status refreshes daily; sources that need a person create re-capture tasks.</li>
        </ol>
      </Section>
    </div>
  );
}
