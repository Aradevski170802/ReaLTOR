import { useEffect, useState } from 'react';
import { NavLink, useParams } from 'react-router-dom';
import { api, getToken, setToken } from '../api';
import { useAsyncAction, useFetch } from '../hooks';
import { formatDate, humanize } from '../format';
import type { ProviderInfo, SecretStatus, SourceInfo } from '../types';
import { Badge, ErrorNote, ExternalLink, KeyValue, Section, StatusBadge } from '../components/ui';

export default function SettingsPage() {
  const { section = 'sources' } = useParams();
  return (
    <div className="page">
      <nav className="tabs">
        {[
          ['sources', 'Sources & compliance'],
          ['providers', 'Valuation & image providers'],
          ['rules', 'Screening rules'],
          ['templates', 'Workbook templates'],
          ['access', 'API access'],
        ].map(([key, label]) => (
          <NavLink key={key} to={`/settings/${key}`} className={() => (section === key ? 'active' : '')}>
            {label}
          </NavLink>
        ))}
      </nav>
      {section === 'sources' && <SourcesSettings />}
      {section === 'providers' && <ProvidersSettings />}
      {section === 'rules' && <RulesSettings />}
      {section === 'templates' && <TemplatesSettings />}
      {section === 'access' && <AccessSettings />}
    </div>
  );
}

function SourcesSettings() {
  const sources = useFetch<SourceInfo[]>('/api/sources');
  const health = useFetch<Record<string, { counts: Record<string, number>; last_checked: string | null }>>('/api/sources/health');
  const action = useAsyncAction();
  const update = (key: string, body: Record<string, unknown>) => action.run(async () => api.patch(`/api/sources/${key}`, body).then(sources.reload));
  const acknowledge = (s: SourceInfo) => {
    const note = window.prompt(`Acknowledge the terms for "${s.name}".\n\n${s.terms_notes}\n\nDescribe your permitted use (recorded in the audit log):`);
    if (note && note.trim().length >= 3) update(s.key, { acknowledge_terms: true, note });
  };
  return (
    <>
      <ErrorNote error={sources.error ?? action.error} />
      {(['montco', 'delco'] as const).map((county) => (
        <Section key={county} title={county === 'montco' ? 'Montgomery County, Pennsylvania' : 'Delaware County, Pennsylvania'}>
          {sources.data
            ?.filter((s) => s.county === county)
            .map((s) => (
              <details key={s.key} className="source-card">
                <summary>
                  <span className="source-name">{s.name}</span>
                  <Badge tone={s.automated ? 'green' : 'blue'}>{humanize(s.access_method)}</Badge>
                  <Badge tone={s.compliance_status.startsWith('automated') ? 'green' : 'blue'}>{humanize(s.compliance_status)}</Badge>
                  {s.policy && !s.policy.enabled && <Badge tone="red">disabled</Badge>}
                  {s.requires_terms_ack && (s.policy?.terms_acknowledged_at ? <Badge tone="green">terms acknowledged</Badge> : <Badge tone="yellow">terms not acknowledged</Badge>)}
                  {health.data?.[s.key] &&
                    Object.entries(health.data[s.key].counts).map(([status, n]) => (
                      <span key={status} className="inline-stat">
                        <StatusBadge status={status} /> {n}
                      </span>
                    ))}
                </summary>
                <KeyValue
                  items={[
                    ['Terms & access notes', s.terms_notes],
                    ['Findings', s.access_findings.join(' · ')],
                    ['Searches', s.searches.map((x) => <div key={x.url}><ExternalLink href={x.url}>{x.name}</ExternalLink> — {x.input}</div>)],
                    ['Input / parcel rule', `${s.input_requirements}. ${s.parcel_rule}`],
                    ['Field mappings', Object.entries(s.field_mappings).map(([k, v]) => `${k} → ${v}`).join('; ')],
                    ['Rate limit / cache', `${s.rate_limit_per_minute}/min, ≥${s.min_interval_seconds}s apart · cache ${s.cache_ttl_hours}h${s.daily_refresh ? ' · daily refresh' : ''}`],
                    ['Verified', s.verified_on],
                    ['Terms acknowledged', s.policy?.terms_acknowledged_at ? `${s.policy.terms_acknowledged_by} on ${formatDate(s.policy.terms_acknowledged_at)} — ${s.policy.notes ?? ''}` : null],
                  ]}
                />
                <div className="actions">
                  <button onClick={() => update(s.key, { enabled: !(s.policy?.enabled ?? true) })}>{s.policy?.enabled === false ? 'Enable source' : 'Disable source'}</button>
                  {s.requires_terms_ack &&
                    (s.policy?.terms_acknowledged_at ? (
                      <button onClick={() => update(s.key, { acknowledge_terms: false })}>Revoke acknowledgement</button>
                    ) : (
                      <button className="primary" onClick={() => acknowledge(s)}>
                        Acknowledge terms…
                      </button>
                    ))}
                </div>
              </details>
            ))}
        </Section>
      ))}
    </>
  );
}

function SecretForm({ secret, onSaved }: { secret: SecretStatus; onSaved: () => void }) {
  const [value, setValue] = useState('');
  const action = useAsyncAction();
  return (
    <div className="row gap wrap">
      <code>{secret.name}</code>
      {secret.configured ? <Badge tone="green">configured {secret.hint}</Badge> : <Badge tone="grey">not configured</Badge>}
      <input type="password" autoComplete="off" placeholder="Paste key (stored encrypted, never shown again)" value={value} onChange={(e) => setValue(e.target.value)} />
      <button
        disabled={value.length < 4 || action.busy}
        onClick={() =>
          action.run(async () => {
            await api.put(`/api/secrets/${secret.name}`, { value });
            setValue('');
            onSaved();
          })
        }
      >
        Save
      </button>
      {secret.configured && <button onClick={() => action.run(async () => api.del(`/api/secrets/${secret.name}`).then(onSaved))}>Remove</button>}
      <ErrorNote error={action.error} />
    </div>
  );
}

function ProvidersSettings() {
  const { data, error, reload } = useFetch<{ valuation: ProviderInfo[]; images: (ProviderInfo & { secrets: SecretStatus[] })[] }>('/api/providers');
  const action = useAsyncAction();
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  return (
    <>
      <Section title="Valuation providers">
        <p className="muted">
          Market values come only from documented, licensed APIs, or from values you record yourself while viewing a site. Without a configured provider the app shows "No permitted source available" and never invents a value.
        </p>
        <ErrorNote error={error ?? action.error} />
        {data?.valuation.map((p) => (
          <div key={p.key} className="provider-card">
            <div className="row between">
              <div>
                <strong>{p.name}</strong> <span className="muted">{p.product}</span> {!p.verified_integration && <Badge tone="yellow">verify field mapping before use</Badge>}
              </div>
              <StatusBadge status={p.status === 'ready' ? 'success' : p.status} />
            </div>
            <div className="small">{p.coverage_notes}</div>
            <div className="small muted">{p.terms_notes}</div>
            <ol className="small">
              {p.setup_steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            {p.docs_url && <ExternalLink href={p.docs_url}>Documentation</ExternalLink>}
            {p.secrets.map((s) => (
              <SecretForm key={s.name} secret={s} onSaved={reload} />
            ))}
            {!p.is_sandbox && (
              <div className="actions">
                <button onClick={() => action.run(async () => api.put(`/api/providers/${p.key}`, { enabled: !p.enabled }).then(reload))}>{p.enabled ? 'Disable' : 'Enable'}</button>
                <button
                  onClick={() =>
                    action.run(async () => {
                      const street = window.prompt('Test address — street', '1002 DEKALB ST');
                      const city = street ? window.prompt('City', 'Bridgeport') : null;
                      if (!street || !city) return;
                      setTestResult(await api.post(`/api/providers/${p.key}/test`, { street, city }));
                    })
                  }
                >
                  Test
                </button>
              </div>
            )}
          </div>
        ))}
        {testResult && <pre className="pre">{JSON.stringify(testResult, null, 2)}</pre>}
      </Section>
      <Section title="Property images">
        {data?.images.map((p) => (
          <div key={p.key} className="provider-card">
            <strong>{p.name}</strong>
            <div className="small muted">{p.terms_notes}</div>
            <ol className="small">
              {p.setup_steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            {p.secrets.map((s) => (
              <SecretForm key={s.name} secret={s} onSaved={reload} />
            ))}
          </div>
        ))}
      </Section>
    </>
  );
}

function RulesSettings() {
  const { data, error, reload } = useFetch<{ active: { version: number; config: Record<string, Record<string, any>>; change_reason: string; created_by: string; created_at: string }; history: any[] }>('/api/rules');
  const [draft, setDraft] = useState<Record<string, Record<string, any>> | null>(null);
  const [reason, setReason] = useState('');
  const action = useAsyncAction();
  useEffect(() => {
    if (data) setDraft(structuredClone(data.active.config));
  }, [data]);
  const setValue = (section: string, key: string, value: any) => setDraft((d) => (d ? { ...d, [section]: { ...d[section], [key]: value } } : d));
  const save = () =>
    action.run(async () => {
      if (!draft || !data) return;
      const patch: Record<string, Record<string, any>> = {};
      for (const [section, values] of Object.entries(draft)) {
        for (const [key, value] of Object.entries(values)) {
          if (JSON.stringify(value) !== JSON.stringify(data.active.config[section]?.[key])) (patch[section] ??= {})[key] = value;
        }
      }
      await api.put('/api/rules', { patch, reason });
      setReason('');
      reload();
    });
  return (
    <Section title={`Screening rules — version ${data?.active.version ?? '…'}`}>
      <p className="muted">
        Each change creates a new rule-set version, is recorded in the audit log, and re-evaluates every property in the background.
      </p>
      <ErrorNote error={error ?? action.error} />
      {draft &&
        Object.entries(draft).map(([section, values]) => (
          <fieldset key={section} className="rules-section">
            <legend>{humanize(section)}</legend>
            <div className="form-grid">
              {Object.entries(values).map(([key, value]) => (
                <label key={key} className="field">
                  <span>{humanize(key)}</span>
                  {typeof value === 'boolean' ? (
                    <input type="checkbox" checked={value} onChange={(e) => setValue(section, key, e.target.checked)} />
                  ) : typeof value === 'number' ? (
                    <input type="number" value={value} step="any" onChange={(e) => setValue(section, key, Number(e.target.value))} />
                  ) : Array.isArray(value) ? (
                    <input value={value.join(', ')} onChange={(e) => setValue(section, key, e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} />
                  ) : key.endsWith('_policy') ? (
                    <select value={value} onChange={(e) => setValue(section, key, e.target.value)}>
                      {['review', 'pass', 'fail', 'informational', 'removed', 'insufficient'].map((o) => (
                        <option key={o}>{o}</option>
                      ))}
                    </select>
                  ) : key === 'strategy' ? (
                    <select value={value} onChange={(e) => setValue(section, key, e.target.value)}>
                      <option value="primary_then_lowest">Primary AVM, then lowest credible</option>
                      <option value="lowest_credible">Lowest credible estimate (reference workbook)</option>
                    </select>
                  ) : (
                    <input value={String(value)} onChange={(e) => setValue(section, key, e.target.value)} />
                  )}
                </label>
              ))}
            </div>
          </fieldset>
        ))}
      <label className="field">
        <span>Reason for change (required)</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="actions">
        <button className="primary" disabled={reason.trim().length < 3 || action.busy} onClick={save}>
          Save new version
        </button>
      </div>
      <h4>History</h4>
      <table className="table compact">
        <tbody>
          {data?.history.map((h) => (
            <tr key={h.id}>
              <td>v{h.version}</td>
              <td>{h.change_reason}</td>
              <td>{h.created_by}</td>
              <td>{formatDate(h.created_at)}</td>
              <td>{h.is_active && <Badge tone="green">active</Badge>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function TemplatesSettings() {
  const { data, error, reload } = useFetch<any[]>('/api/templates');
  const [county, setCounty] = useState('montco');
  const [file, setFile] = useState<File | null>(null);
  const action = useAsyncAction();
  const upload = () =>
    action.run(async () => {
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      await api.upload(`/api/templates?county=${county}`, form);
      setFile(null);
      reload();
    });
  return (
    <Section title="Reference workbook templates">
      <p className="muted">
        The Montgomery reference workbook layout ships with the app. Uploading a newer reference workbook updates column widths and header styles. Only the layout is stored, never cell data.
      </p>
      <div className="row gap">
        <select value={county} onChange={(e) => setCounty(e.target.value)}>
          <option value="montco">Montgomery County PA</option>
          <option value="delco">Delaware County PA</option>
        </select>
        <input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Template workbook" />
        <button className="primary" disabled={!file || action.busy} onClick={upload}>
          Upload template
        </button>
      </div>
      <ErrorNote error={error ?? action.error} />
      <table className="table compact">
        <tbody>
          {data?.map((t) => (
            <tr key={t.id}>
              <td>{t.name}</td>
              <td>{t.county}</td>
              <td>{t.sheets.map((s: any) => `${s.title} (${s.columns})`).join(', ')}</td>
              <td>{formatDate(t.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function AccessSettings() {
  const [token, setTokenValue] = useState(getToken() ?? '');
  return (
    <Section title="API access token">
      <p className="muted">
        In multi-user mode (USI_AUTH_MODE=token), an administrator creates users with <code>python -m app.cli create-user</code>. Paste your token here; it is kept in this browser only.
      </p>
      <div className="row gap">
        <input type="password" value={token} onChange={(e) => setTokenValue(e.target.value)} placeholder="usi_…" />
        <button onClick={() => setToken(token || null)}>Save token</button>
        <button onClick={() => { setToken(null); setTokenValue(''); }}>Clear</button>
      </div>
    </Section>
  );
}
