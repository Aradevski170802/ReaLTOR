import type { ColDef, ColGroupDef, ValueFormatterParams, ValueGetterParams } from 'ag-grid-community';
import { cellClasses, formatValue, GROUP_LABEL, QUALITY_LABEL } from '../format';
import type { ColumnSpec, GridRow } from '../types';

function filterFor(kind: ColumnSpec['kind']): string {
  if (kind === 'money' || kind === 'int' || kind === 'number') return 'agNumberColumnFilter';
  if (kind === 'date') return 'agDateColumnFilter';
  return 'agTextColumnFilter';
}

function dateComparator(filterDate: Date, cellValue: string | null): number {
  if (!cellValue) return -1;
  const [y, m, d] = cellValue.slice(0, 10).split('-').map(Number);
  const cell = new Date(y, m - 1, d);
  if (cell < filterDate) return -1;
  if (cell > filterDate) return 1;
  return 0;
}

export function columnDef(col: ColumnSpec): ColDef<GridRow> {
  return {
    colId: col.key,
    headerName: col.header,
    headerTooltip: `${col.header}${col.reference ? ' — reference workbook column' : ' — application addition'}${col.editable ? ' (double-click to override)' : ''}`,
    valueGetter: (p: ValueGetterParams<GridRow>) => p.data?.values[col.key] ?? null,
    valueFormatter: (p: ValueFormatterParams<GridRow>) => formatValue(col.kind, p.value, col.number_format),
    width: Math.max(72, Math.round(col.width * 7.2)),
    pinned: col.pinned ? 'left' : undefined,
    hide: col.hidden,
    filter: filterFor(col.kind),
    filterParams: col.kind === 'date' ? { comparator: dateComparator } : undefined,
    sortable: true,
    resizable: true,
    wrapText: col.key === 'decision_summary' ? false : undefined,
    cellClass: (p) => cellClasses(p.data, col.key),
    headerClass: col.reference ? 'hdr-reference' : 'hdr-addition',
    tooltipValueGetter: (p) => {
      const row = p.data;
      if (!row) return '';
      const quality = row.quality[col.key];
      const source = row.sources[col.key];
      return [quality ? QUALITY_LABEL[quality] : null, source].filter(Boolean).join(' · ');
    },
  };
}

export function buildColumnDefs(columns: ColumnSpec[]): (ColDef<GridRow> | ColGroupDef<GridRow>)[] {
  const out: (ColDef<GridRow> | ColGroupDef<GridRow>)[] = [];
  let current: ColGroupDef<GridRow> | null = null;
  for (const col of columns) {
    if (col.pinned) {
      out.push(columnDef(col));
      current = null;
      continue;
    }
    if (!current || current.groupId !== col.group) {
      current = { groupId: col.group, headerName: GROUP_LABEL[col.group] ?? col.group, children: [], marryChildren: false };
      out.push(current);
    }
    current.children.push(columnDef(col));
  }
  return out;
}
