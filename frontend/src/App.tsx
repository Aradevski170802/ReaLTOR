import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { useFetch } from './hooks';
import AuditPage from './pages/AuditPage';
import ProjectPage from './pages/ProjectPage';
import ProjectsPage from './pages/ProjectsPage';
import SettingsPage from './pages/SettingsPage';
import type { Meta } from './types';

export default function App() {
  const meta = useFetch<Meta>('/api/meta');
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>
            ◆
          </span>
          Upset Sale Intel
          <span className="brand-sub">Montgomery &amp; Delaware Counties, Pennsylvania</span>
        </div>
        <nav className="topnav">
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/settings/sources">Settings</NavLink>
          <NavLink to="/audit">Audit log</NavLink>
        </nav>
        <div className="topmeta">
          {meta.data && (
            <>
              v{meta.data.version} · {meta.data.auth_mode} auth{meta.data.demo_mode ? ' · demo mode' : ''}
            </>
          )}
        </div>
      </header>
      {meta.error && (
        <div className="banner" role="alert">
          The API is unreachable. Start the backend (<code>python dev.py</code>). ({meta.error})
        </div>
      )}
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:projectId/:tab?" element={<ProjectPage />} />
          <Route path="/settings/:section?" element={<SettingsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<div className="page">Page not found.</div>} />
        </Routes>
      </main>
      <footer className="footer">
        {meta.data?.disclaimer ?? 'Informational research only — not legal, title, appraisal, tax, investment or lien-clearance advice.'}
      </footer>
    </div>
  );
}
