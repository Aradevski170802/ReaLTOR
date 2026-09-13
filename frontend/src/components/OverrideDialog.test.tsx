import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import OverrideDialog from './OverrideDialog';

describe('OverrideDialog', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('requires a reason and posts the override', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 9 }), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
    const onSaved = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(
      <OverrideDialog target={{ propertyId: 5, field: 'living_area_sqft', label: 'Sq Ft Living Area', currentValue: 1724 }} onClose={onClose} onSaved={onSaved} />,
    );
    const save = screen.getByRole('button', { name: /save override/i });
    expect(save).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/verified living area/i), 'Measured on site');
    expect(save).toBeEnabled();
    await user.click(save);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/properties/5/overrides');
    expect(JSON.parse(String(init.body))).toEqual({ field: 'living_area_sqft', value: '1724', reason: 'Measured on site' });
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('restricts decision overrides to the defined statuses', async () => {
    vi.stubGlobal('fetch', vi.fn());
    const user = userEvent.setup();
    render(<OverrideDialog target={{ propertyId: 1, field: 'decision_status', label: 'Decision', currentValue: null }} onClose={() => {}} onSaved={() => {}} />);
    await user.type(screen.getByPlaceholderText(/verified living area/i), 'Comparable sales');
    expect(screen.getByRole('button', { name: /save override/i })).toBeDisabled();
    await user.selectOptions(screen.getByRole('combobox'), 'Interested');
    expect(screen.getByRole('button', { name: /save override/i })).toBeEnabled();
  });
});
