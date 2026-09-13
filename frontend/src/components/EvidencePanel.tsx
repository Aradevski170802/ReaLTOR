import { useFetch } from '../hooks';
import { formatDate, formatValue, QUALITY_LABEL } from '../format';
import type { ColumnKind, PropertyDetail } from '../types';
import { Badge, ErrorNote, ExternalLink, KeyValue, Modal } from './ui';

export default function EvidencePanel({
  propertyId,
  fieldKey,
  header,
  kind,
  onClose,
}: {
  propertyId: number;
  fieldKey: string;
  header: string;
  kind: ColumnKind;
  onClose: () => void;
}) {
  const { data, error, loading } = useFetch<PropertyDetail>(`/api/properties/${propertyId}`);
  const cell = data?.cells[fieldKey];
  return (
    <Modal title={`Evidence — ${header}`} onClose={onClose}>
      {loading && <p className="muted">Loading evidence…</p>}
      <ErrorNote error={error} />
      {data && !cell && <p className="muted">No provenance is recorded for this field.</p>}
      {cell && (
        <>
          <p>
            <strong>{data?.property.parcel}</strong> · {data?.property.owner_name ?? '—'}
          </p>
          <KeyValue
            items={[
              ['Value', <strong key="v">{formatValue(kind, cell.value) || '—'}</strong>],
              ['Quality', <Badge key="q" tone={qualityTone(cell.quality)}>{QUALITY_LABEL[cell.quality]}</Badge>],
              ['Source', cell.source_name],
              ['Source URL', cell.url ? <ExternalLink key="u" href={cell.url}>{cell.url}</ExternalLink> : null],
              ['Retrieved', formatDate(cell.retrieved_at)],
              ['Raw source value', cell.raw],
              ['Confidence', cell.confidence !== null && cell.confidence !== undefined ? cell.confidence.toFixed(2) : null],
              ['Notes', cell.notes],
              [
                'Evidence snapshot',
                cell.evidence_id ? (
                  <a key="e" href={`/api/evidence/${cell.evidence_id}/content`} target="_blank" rel="noopener noreferrer">
                    View immutable snapshot #{cell.evidence_id}
                  </a>
                ) : null,
              ],
            ]}
          />
        </>
      )}
    </Modal>
  );
}

export function qualityTone(quality: string): string {
  return { verified: 'green', estimated: 'blue', unknown: 'grey', stale: 'yellow', manual: 'purple' }[quality] ?? 'grey';
}
