import { useEffect, type ReactNode } from 'react';
import { STATUS_TONE } from '../format';

export function Badge({ tone = 'grey', children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge tone-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function StatusBadge({ status, title }: { status: string | null | undefined; title?: string }) {
  const value = status || 'unknown';
  return (
    <Badge tone={STATUS_TONE[value] ?? 'grey'} title={title}>
      {value.replace(/_/g, ' ')}
    </Badge>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="note note-error" role="alert">
      {error}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>{title}</h3>
        {actions && <div className="panel-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v === null || v === undefined || v === '' ? '—' : v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ExternalLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  if (!href) return <>{children}</>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

export function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="progress" aria-label={`${done} of ${total}`}>
      <div style={{ width: `${pct}%` }} />
      <span>
        {done}/{total}
      </span>
    </div>
  );
}
