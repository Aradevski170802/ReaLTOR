import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { formatDate } from '../format';
import { Empty, ErrorNote, Section } from '../components/ui';

interface ExportRow {
  id: number;
  filename: string;
  rows: number;
  sha256: string;
  created_by: string;
  created_at: string;
  download: string;
}

export default function ExportsPanel({ projectId }: { projectId: number }) {
  const { data, error, reload } = useFetch<ExportRow[]>(`/api/projects/${projectId}/exports`);
  const action = useAsyncAction();
  const build = () => action.run(async () => api.post(`/api/projects/${projectId}/exports?sync=true`, {}).then(reload));
  return (
    <Section
      title="Workbook exports"
      actions={
        <button className="primary" onClick={build} disabled={action.busy}>
          {action.busy ? 'Building…' : 'Build new export'}
        </button>
      }
    >
      <p className="muted small">
        The export follows the reference workbook's main sheet, with its columns, widths, frozen panes, filters and colour rules as conditional formatting. It adds Mortgage Info, Lien Cases, Sources, Source Catalog, Refresh Log, Tax Status History, Errors &amp; Review Queue, User Action Tasks, PDF Extraction, Configuration, Audit Log and Notes &amp; Legend sheets.
      </p>
      <ErrorNote error={error ?? action.error} />
      {data?.length === 0 && <Empty>No exports yet.</Empty>}
      <table className="table compact">
        <thead>
          <tr>
            <th>File</th>
            <th>Rows</th>
            <th>Created</th>
            <th>SHA-256</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((e) => (
            <tr key={e.id}>
              <td>
                <a href={e.download}>{e.filename}</a>
              </td>
              <td>{e.rows}</td>
              <td>
                {formatDate(e.created_at)} · {e.created_by}
              </td>
              <td className="mono small">{e.sha256?.slice(0, 16)}…</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}
