/** XLSX 只读工作簿预览；动态加载维护中的 OOXML 解析器，并限制 DOM 规模。 */

import React, { useMemo, useState } from 'react';
import type { PreviewFilePayload } from '../../shared/bridge';
import { fetchPreviewArrayBuffer } from '../../store/filePreview';
import { PreviewError, PreviewLoading, useAsyncPreview } from './PreviewStates';
import { useTranslation } from 'react-i18next';

const MAX_ROWS = 300;
const MAX_COLS = 60;

interface SheetData {
  name: string;
  rows: string[][];
  truncatedRows: boolean;
  truncatedCols: boolean;
}

interface WorkbookModel {
  sheets: SheetData[];
}

function displayCell(value: unknown): string {
  if (value == null) return '';
  if (value instanceof Date) return value.toLocaleString();
  return String(value);
}

async function loadWorkbook(
  projectId: string,
  file: PreviewFilePayload,
): Promise<WorkbookModel> {
  const buffer = await fetchPreviewArrayBuffer(projectId, file);
  // The universal entry accepts ArrayBuffer directly without importing Node
  // built-ins or the package's separate XML-parser worker implementation.
  const { default: readWorkbook } = await import('read-excel-file/universal');
  const workbook = await readWorkbook(buffer, { trim: false });
  const sheets = workbook.map(({ sheet: name, data: rawRows }) => {
    const truncatedRows = rawRows.length > MAX_ROWS + 1;
    let truncatedCols = false;
    const rows = rawRows.slice(0, MAX_ROWS + 1).map((rawRow) => {
      const cells = rawRow.map(displayCell);
      if (cells.length <= MAX_COLS) return cells;
      truncatedCols = true;
      return cells.slice(0, MAX_COLS);
    });
    return { name, rows, truncatedRows, truncatedCols };
  });
  return { sheets };
}

type SortDirection = 'asc' | 'desc' | null;

const SheetTable: React.FC<{ sheet: SheetData }> = ({ sheet }) => {
  const { t } = useTranslation();
  const [sortColumn, setSortColumn] = useState<number | null>(null);
  const [direction, setDirection] = useState<SortDirection>(null);
  const header = sheet.rows[0] || [];
  const body = useMemo(() => sheet.rows.slice(1), [sheet.rows]);
  const columnCount = sheet.rows.reduce((maximum, row) => Math.max(maximum, row.length), 0);

  const sortedRows = useMemo(() => {
    if (sortColumn == null || direction == null) return body;
    const factor = direction === 'asc' ? 1 : -1;
    return [...body].sort((left, right) => {
      const leftValue = left[sortColumn] ?? '';
      const rightValue = right[sortColumn] ?? '';
      const leftNumber = Number(leftValue);
      const rightNumber = Number(rightValue);
      const numeric = leftValue !== '' && rightValue !== ''
        && Number.isFinite(leftNumber) && Number.isFinite(rightNumber);
      return numeric
        ? (leftNumber - rightNumber) * factor
        : leftValue.localeCompare(rightValue, 'zh-CN') * factor;
    });
  }, [body, direction, sortColumn]);

  const sort = (column: number): void => {
    if (sortColumn !== column) {
      setSortColumn(column);
      setDirection('asc');
      return;
    }
    if (direction === 'asc') {
      setDirection('desc');
      return;
    }
    setSortColumn(null);
    setDirection(null);
  };

  return (
    <div className="pv-sheet-scroll">
      <table className="pv-sheet-table">
        <thead>
          <tr>
            <th className="pv-sheet-corner" aria-label={t('sheet.preview.04')} />
            {Array.from({ length: columnCount }, (_, column) => {
              const arrow = sortColumn === column
                ? direction === 'asc' ? ' ↑' : direction === 'desc' ? ' ↓' : ''
                : '';
              return (
                <th key={column}>
                  <button type="button" className="pv-sheet-sort" onClick={() => sort(column)}>
                    {(header[column] ?? '') || t('sheet.preview.colHeader', { n: column + 1 })}{arrow}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              <th className="pv-sheet-rownum" scope="row">{rowIndex + 1}</th>
              {Array.from({ length: columnCount }, (_, column) => (
                <td key={column} title={row[column] ?? ''}>{row[column] ?? ''}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(sheet.truncatedRows || sheet.truncatedCols) && (
        <div className="pv-sheet-trunc" role="note">
          {t('sheet.preview.truncated', { n: MAX_ROWS, m: MAX_COLS })}
        </div>
      )}
    </div>
  );
};

const SheetPreview: React.FC<{ file: PreviewFilePayload; projectId: string }> = ({
  file,
  projectId,
}) => {
  const { t } = useTranslation();
  const { status, data, error, reload } = useAsyncPreview(
    () => loadWorkbook(projectId, file),
    [projectId, file.path, file.file_id, file.mtime_ns],
  );
  const [activeSheet, setActiveSheet] = useState(0);

  if (status === 'loading') return <PreviewLoading label={t('sheet.preview.03') + '…'} />;
  if (status === 'error') return <PreviewError message={error} onRetry={reload} />;
  const sheets = data?.sheets || [];
  if (sheets.length === 0) return <PreviewError message={t('sheet.preview.05') + '。'} />;
  const index = Math.min(activeSheet, sheets.length - 1);
  const sheet = sheets[index];
  if (!sheet) return <PreviewError message={t('sheet.preview.02') + '。'} />;

  return (
    <div className="pv-sheet">
      {sheets.length > 1 && (
        <div className="pv-sheet-tabs" role="tablist" aria-label={t('sheet.preview.01')}>
          {sheets.map((candidate, sheetIndex) => (
            <button
              key={`${candidate.name}-${sheetIndex}`}
              type="button"
              role="tab"
              aria-selected={sheetIndex === index}
              className={`pv-sheet-tab${sheetIndex === index ? ' active' : ''}`}
              onClick={() => setActiveSheet(sheetIndex)}
              title={candidate.name}
            >
              {candidate.name}
            </button>
          ))}
        </div>
      )}
      <SheetTable key={`${file.path}:${sheet.name}`} sheet={sheet} />
    </div>
  );
};

export default SheetPreview;
