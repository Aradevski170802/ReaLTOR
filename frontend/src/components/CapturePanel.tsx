import { useState } from 'react';
import { api } from '../api';
import { useAsyncAction } from '../hooks';
import { Badge, ErrorNote, ExternalLink, StatusBadge } from './ui';

export interface CaptureTarget {
  propertyId: number;
  sourceKey: string;
  sourceName?: string;
  category?: string;
  url: string | null;
  searchHint: string | null;
  instructions: string | null;
}

interface CaptureResult {
  status: string;
  message: string;
  payload: unknown;
  saved: boolean;
}

const BOOKMARKLET =
  "javascript:(()=>{navigator.clipboard.writeText(document.documentElement.outerHTML).then(()=>alert('Page copied — paste it into Upset Sale Intel'))})()";

export default function CapturePanel({ target, onSaved }: { target: CaptureTarget; onSaved: () => void }) {
  const [content, setContent] = useState('');
  const [noResults, setNoResults] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<CaptureResult | null>(null);
  const { busy, error, run } = useAsyncAction();
  const allowsNoResults = target.category === 'recorder' || target.category === 'civil' || /recorder|civil/.test(target.sourceKey);

  const submit = (preview: boolean) =>
    run(async () => {
      let res: CaptureResult;
      if (file) {
        const form = new FormData();
        form.append('source_key', target.sourceKey);
        form.append('preview', String(preview));
        form.append('file', file);
        res = await api.upload<CaptureResult>(`/api/properties/${target.propertyId}/capture/file`, form);
      } else {
        res = await api.post<CaptureResult>(`/api/properties/${target.propertyId}/capture`, {
          source_key: target.sourceKey,
          content,
          no_results: noResults,
          preview,
          url: target.url,
        });
      }
      setResult(res);
      if (res.saved) {
        setContent('');
        setFile(null);
        setNoResults(false);
        onSaved();
      }
    });

  const copyHint = () => {
    if (target.searchHint) void navigator.clipboard?.writeText(target.searchHint.replace(/^.*?:\s*/, '').split(/\s{2,}|\s\(/)[0]);
  };

  return (
    <div className="capture">
      <div className="capture-steps">
        <div>
          <Badge tone="blue">User-assisted</Badge> {target.sourceName ?? target.sourceKey}
        </div>
        {target.url && (
          <p>
            1. <ExternalLink href={target.url}>Open the official site</ExternalLink> in your browser. Accept any disclaimer, sign in or complete any human check yourself.
          </p>
        )}
        {target.searchHint && (
          <p>
            2. Search for <code>{target.searchHint}</code>{' '}
            <button className="link-btn" onClick={copyHint} type="button">
              copy
            </button>
          </p>
        )}
        {target.instructions && <p className="muted small">{target.instructions}</p>}
        <p className="muted small">
          Tip: drag this link to your bookmarks bar to copy a page in one click:{' '}
          <a href={BOOKMARKLET} onClick={(e) => e.preventDefault()} title="Drag to bookmarks bar">
            Copy page for USI
          </a>
        </p>
      </div>
      <label className="field">
        <span>3. Paste the page (Ctrl+A, Ctrl+C on the official page) or choose a saved file</span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={7}
          placeholder="Paste page text or HTML here…"
          disabled={noResults || !!file}
        />
      </label>
      <div className="row gap">
        <input type="file" accept=".html,.htm,.txt,.csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Captured file" />
        {allowsNoResults && (
          <label className="check">
            <input type="checkbox" checked={noResults} onChange={(e) => setNoResults(e.target.checked)} /> The official search returned no results
          </label>
        )}
      </div>
      <ErrorNote error={error} />
      {result && (
        <div className={`note ${result.status === 'success' || result.status === 'no_match' ? 'note-ok' : 'note-warn'}`}>
          <StatusBadge status={result.status} /> {result.message} {result.saved ? '— saved with evidence.' : '— preview only, nothing saved yet.'}
          {!result.saved && result.payload !== null && result.payload !== undefined && (
            <details>
              <summary>Recognised values</summary>
              <pre className="pre">{JSON.stringify(result.payload, null, 2).slice(0, 6000)}</pre>
            </details>
          )}
        </div>
      )}
      <div className="actions">
        <button disabled={busy || (!content && !file && !noResults)} onClick={() => submit(true)}>
          Preview
        </button>
        <button className="primary" disabled={busy || (!content && !file && !noResults)} onClick={() => submit(false)}>
          {busy ? 'Saving…' : 'Save capture'}
        </button>
      </div>
    </div>
  );
}
