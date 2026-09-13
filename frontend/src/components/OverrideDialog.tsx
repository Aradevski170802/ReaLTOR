import { useState } from 'react';
import { api } from '../api';
import { useAsyncAction } from '../hooks';
import { ErrorNote, Modal } from './ui';

const DECISIONS = ['Interested', 'Not interested', 'Review required', 'Insufficient data', 'Removed/resolved'];

export interface OverrideTarget {
  propertyId: number;
  field: string;
  label: string;
  currentValue: unknown;
}

export default function OverrideDialog({ target, onClose, onSaved }: { target: OverrideTarget; onClose: () => void; onSaved: () => void }) {
  const [value, setValue] = useState(target.currentValue === null || target.currentValue === undefined ? '' : String(target.currentValue));
  const [reason, setReason] = useState('');
  const { busy, error, run } = useAsyncAction();
  const isDecision = target.field === 'decision_status';
  const valid = reason.trim().length >= 3 && (!isDecision || DECISIONS.includes(value));

  const save = () =>
    run(async () => {
      await api.post(`/api/properties/${target.propertyId}/overrides`, { field: target.field, value: value === '' ? null : value, reason: reason.trim() });
      onSaved();
      onClose();
    });

  return (
    <Modal title={`Override: ${target.label}`} onClose={onClose}>
      <p className="muted">
        Overrides never delete source data. The original value stays in the evidence trail, and the change is recorded in the audit log with your reason.
      </p>
      <label className="field">
        <span>Current value</span>
        <input value={target.currentValue === null || target.currentValue === undefined ? '' : String(target.currentValue)} disabled />
      </label>
      <label className="field">
        <span>New value</span>
        {isDecision ? (
          <select value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="">Choose…</option>
            {DECISIONS.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        ) : (
          <input value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        )}
      </label>
      <label className="field">
        <span>Reason (required)</span>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder="e.g. Verified living area on site visit" />
      </label>
      <ErrorNote error={error} />
      <div className="actions">
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!valid || busy} onClick={save}>
          {busy ? 'Saving…' : 'Save override'}
        </button>
      </div>
    </Modal>
  );
}
