import { cellClasses, formatValue } from './format';
import type { GridRow } from './types';

describe('formatValue', () => {
  it('formats money using the column number format', () => {
    expect(formatValue('money', 1234.5, '"$"#,##0.00')).toBe('$1,234.50');
    expect(formatValue('money', 1234.5, '"$"#,##0')).toBe('$1,235');
  });

  it('formats integers, numbers and ISO dates', () => {
    expect(formatValue('int', 1724)).toBe('1,724');
    expect(formatValue('number', 0.1315)).toBe('0.132');
    expect(formatValue('date', '2016-09-23')).toBe('09/23/2016');
  });

  it('renders empty values as blank, never as zero', () => {
    expect(formatValue('money', null)).toBe('');
    expect(formatValue('text', undefined)).toBe('');
    expect(formatValue('money', 0)).toBe('$0.00');
  });
});

describe('cellClasses', () => {
  const row: GridRow = {
    id: 1,
    values: {},
    quality: { selected_valuation: 'estimated', owner_name: 'manual' },
    sources: {},
    styles: { row: 'red', cells: { selected_valuation: 'orange' } },
    is_pilot: true,
    excluded: false,
    needs_review: false,
    open_tasks: 0,
  };

  it('combines conditional style and data-quality classes', () => {
    expect(cellClasses(row, 'selected_valuation')).toEqual(['cell-orange', 'q-estimated']);
    expect(cellClasses(row, 'owner_name')).toEqual(['q-manual']);
    expect(cellClasses(undefined, 'x')).toEqual([]);
  });
});
