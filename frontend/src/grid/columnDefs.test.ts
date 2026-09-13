import type { ColDef, ColGroupDef } from 'ag-grid-community';
import type { ColumnSpec, GridRow } from '../types';
import { buildColumnDefs } from './columnDefs';

const base: Omit<ColumnSpec, 'key' | 'header' | 'kind' | 'group'> = {
  width: 12,
  reference: true,
  pinned: false,
  hidden: false,
  editable: false,
  number_format: null,
  header_font_size: 10,
  description: '',
};

const columns: ColumnSpec[] = [
  { ...base, key: 'municipality', header: 'Municipality', kind: 'text', group: 'sale_list', pinned: true, editable: true },
  { ...base, key: 'parcel', header: 'Parcel', kind: 'link', group: 'sale_list', pinned: true },
  { ...base, key: 'assessed_value', header: 'Assessed Value', kind: 'money', group: 'assessment', number_format: '"$"#,##0' },
  { ...base, key: 'last_sale_date', header: 'Sale Date', kind: 'date', group: 'assessment' },
  { ...base, key: 'notes', header: 'Notes', kind: 'text', group: 'audit', reference: false, hidden: true },
];

describe('buildColumnDefs', () => {
  const defs = buildColumnDefs(columns);

  it('pins identifying columns to the left like the workbook freeze panes', () => {
    const first = defs[0] as ColDef<GridRow>;
    expect(first.colId).toBe('municipality');
    expect(first.pinned).toBe('left');
    expect((defs[1] as ColDef<GridRow>).pinned).toBe('left');
  });

  it('groups the remaining columns in workbook order with typed filters', () => {
    const group = defs[2] as ColGroupDef<GridRow>;
    expect(group.headerName).toBe('Assessment');
    const [value, date] = group.children as ColDef<GridRow>[];
    expect(value.filter).toBe('agNumberColumnFilter');
    expect(date.filter).toBe('agDateColumnFilter');
    const audit = defs[3] as ColGroupDef<GridRow>;
    expect((audit.children[0] as ColDef<GridRow>).hide).toBe(true);
    expect((audit.children[0] as ColDef<GridRow>).headerClass).toBe('hdr-addition');
  });

  it('formats values and exposes provenance in tooltips', () => {
    const group = defs[2] as ColGroupDef<GridRow>;
    const value = group.children[0] as ColDef<GridRow>;
    const row = {
      id: 1,
      values: { assessed_value: 66700 },
      quality: { assessed_value: 'verified' },
      sources: { assessed_value: 'Montgomery County GIS' },
      styles: { row: null, cells: { assessed_value: 'orange' } },
      is_pilot: false,
      excluded: false,
      needs_review: false,
      open_tasks: 0,
    } as GridRow;
    expect((value.valueFormatter as any)({ value: 66700 })).toBe('$66,700');
    expect((value.tooltipValueGetter as any)({ data: row })).toBe('Verified from source · Montgomery County GIS');
    expect((value.cellClass as any)({ data: row })).toEqual(['cell-orange', 'q-verified']);
  });
});
