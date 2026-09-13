import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { formatDate } from '../format';
import type { Project } from '../types';
import { Badge, Empty, ErrorNote, Section } from '../components/ui';

const COUNTIES = [
  { value: 'montco', label: 'Montgomery County, Pennsylvania' },
  { value: 'delco', label: 'Delaware County, Pennsylvania' },
];

export default function ProjectsPage() {
  const { data, error, reload } = useFetch<Project[]>('/api/projects');
  const [name, setName] = useState('');
  const [county, setCounty] = useState('montco');
  const [isDemo, setIsDemo] = useState(false);
  const { busy, error: createError, run } = useAsyncAction();
  const navigate = useNavigate();

  const create = () =>
    run(async () => {
      const project = await api.post<Project>('/api/projects', { name, county, is_demo: isDemo });
      reload();
      navigate(`/projects/${project.id}/import`);
    });

  return (
    <div className="page">
      <Section title="New research project">
        <div className="form-grid">
          <label className="field">
            <span>Project name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. 2026 Upset Sale — September" />
          </label>
          <label className="field">
            <span>County</span>
            <select value={county} onChange={(e) => setCounty(e.target.value)}>
              {COUNTIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={isDemo} onChange={(e) => setIsDemo(e.target.checked)} /> Demo project (allows SANDBOX valuations)
          </label>
        </div>
        {county === 'delco' && <p className="muted small">Delaware County, Pennsylvania (Media, PA) — not the State of Delaware.</p>}
        <ErrorNote error={createError} />
        <div className="actions">
          <button className="primary" disabled={!name.trim() || busy} onClick={create}>
            Create project
          </button>
        </div>
      </Section>
      <Section title="Projects">
        <ErrorNote error={error} />
        {data && data.length === 0 && <Empty>No projects yet. Create one above, or run the demo seed (python -m app.cli seed-demo).</Empty>}
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>County</th>
              <th>Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data?.map((p) => (
              <tr key={p.id}>
                <td>
                  <Link to={`/projects/${p.id}`}>{p.name}</Link> {p.is_demo && <Badge tone="yellow">demo</Badge>}
                </td>
                <td>{COUNTIES.find((c) => c.value === p.county)?.label}</td>
                <td>{formatDate(p.created_at)}</td>
                <td>
                  <Link to={`/projects/${p.id}/results`}>Results →</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
