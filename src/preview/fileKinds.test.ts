import { describe, expect, it } from 'vitest';
import type { PreviewFilePayload } from '../shared/bridge';
import { kindOf } from './fileKinds';

function file(name: string, kind?: string): PreviewFilePayload {
  return { path: `reports/${name}`, name, kind };
}

describe('spreadsheet preview classification', () => {
  it('previews OOXML workbooks', () => {
    expect(kindOf(file('report.xlsx'))).toBe('sheet');
  });

  it('fails legacy XLS files to the generic file card even when declared as a sheet', () => {
    expect(kindOf(file('legacy.xls', 'sheet'))).toBe('file');
  });
});
