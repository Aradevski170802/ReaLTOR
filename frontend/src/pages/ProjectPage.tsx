import { NavLink, useParams } from 'react-router-dom';
import ResultsGrid from '../components/ResultsGrid';
import { useFetch } from '../hooks';
import type { Project } from '../types';
import { Badge, ErrorNote } from '../components/ui';
import EnrichmentPanel from './EnrichmentPanel';
import ExportsPanel from './ExportsPanel';
import ImportReview from './ImportReview';
import JobsPanel from './JobsPanel';
import OverviewPanel from './OverviewPanel';
import RefreshLog from './RefreshLog';
import TasksPage from './TasksPage';

const TABS = [
  ['overview', 'Overview'],
  ['import', '1 · Import & review'],
  ['enrich', '2 · Pilot & enrichment'],
  ['tasks', '3 · User-assisted tasks'],
  ['results', '4 · Results grid'],
  ['jobs', 'Jobs'],
  ['refresh', 'Refresh log'],
  ['exports', 'Exports'],
] as const;

export default function ProjectPage() {
  const { projectId, tab = 'overview' } = useParams();
  const id = Number(projectId);
  const { data: project, error } = useFetch<Project>(`/api/projects/${id}`);

  return (
    <div className={tab === 'results' ? 'page page-full' : 'page'}>
      <div className="project-head">
        <h2>
          {project?.name ?? '…'} {project?.is_demo && <Badge tone="yellow">demo — fixture & sandbox data</Badge>}
        </h2>
        <span className="muted">{project?.county === 'montco' ? 'Montgomery County, PA' : project?.county === 'delco' ? 'Delaware County, PA' : ''}</span>
      </div>
      <ErrorNote error={error} />
      <nav className="tabs">
        {TABS.map(([key, label]) => (
          <NavLink key={key} to={`/projects/${id}/${key}`} className={() => (tab === key ? 'active' : '')}>
            {label}
          </NavLink>
        ))}
      </nav>
      {project && tab === 'overview' && <OverviewPanel project={project} />}
      {project && tab === 'import' && <ImportReview project={project} />}
      {project && tab === 'enrich' && <EnrichmentPanel project={project} />}
      {project && tab === 'tasks' && <TasksPage project={project} />}
      {project && tab === 'results' && <ResultsGrid projectId={project.id} />}
      {project && tab === 'jobs' && <JobsPanel projectId={project.id} />}
      {project && tab === 'refresh' && <RefreshLog projectId={project.id} />}
      {project && tab === 'exports' && <ExportsPanel projectId={project.id} />}
    </div>
  );
}
