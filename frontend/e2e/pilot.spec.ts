import { expect, test } from '@playwright/test';
import path from 'node:path';

const fixture = path.resolve(process.cwd(), '../backend/tests/fixtures/pdf/delco_sales_report_subset.pdf');

test('create project, extract PDF, review, commit, choose pilot and open results', async ({ page }) => {
  const name = `E2E Delco ${Date.now()}`;
  await page.goto('/projects');
  await page.getByPlaceholder('e.g. 2026 Upset Sale — September').fill(name);
  await page.locator('select').first().selectOption('delco');
  await page.getByRole('button', { name: 'Create project' }).click();

  await expect(page.getByRole('heading', { name })).toBeVisible();
  await page.getByLabel('Sale list file').setInputFiles(fixture);
  await page.getByRole('button', { name: 'Upload & extract' }).click();
  await expect(page.getByText('Review extraction — delco_sales_report_subset.pdf')).toBeVisible({ timeout: 60_000 });
  await page.getByLabel(/All rows/).check();
  await expect(page.getByRole('cell', { name: '01-00-00254-00' })).toBeVisible();

  await page.getByRole('button', { name: 'Commit rows to project' }).click();
  await expect(page.getByText(/Committed: 15 created/)).toBeVisible();

  await page.getByRole('link', { name: '2 · Pilot & enrichment' }).click();
  await page.getByRole('button', { name: 'Auto-select first 8' }).click();
  await expect(page.getByRole('button', { name: 'Run pilot (8)' })).toBeEnabled();

  await page.getByRole('link', { name: '4 · Results grid' }).click();
  await expect(page.getByRole('columnheader', { name: 'Folio-NBR' })).toBeVisible();
  await expect(page.getByText('01-00-00254-00').first()).toBeVisible();
});
