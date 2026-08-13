// src/components/DateCalendar.tsx
// [v0.38] 日期筛选日历（参考图：ui_design/日期筛选消息历史·参考.png）
//
// 默认显示当前月；左右箭头 + 年/月下拉切换；有消息的日子标圆点；单击选中。

import React, { useEffect, useMemo, useState } from 'react';
import { historyBase } from '../store/records';
import { runtimeFetch } from '../shared/runtimeFetch';
import { useTranslation } from 'react-i18next';

const WEEKDAYS = [
  'date.calendar.wd.01',
  'date.calendar.wd.02',
  'date.calendar.wd.03',
  'date.calendar.wd.04',
  'date.calendar.wd.05',
  'date.calendar.wd.06',
  'date.calendar.wd.07',
];

export interface DateCalendarProps {
  projectId: string;
  selectedDate: string | null;
  onSelect: (date: string) => void;
  /** 项目创建日期 YYYY-MM-DD；早于此的月份不可切到。缺省 → 放开下限。 */
  minDate?: string | null;
  /** 是否探测「哪些天有消息」并标点。数据量大时可关。默认 true。 */
  probeHasMessages?: boolean;
}

function pad2(n: number): string { return n < 10 ? `0${n}` : String(n); }
function ymd(y: number, m0: number, d: number): string { return `${y}-${pad2(m0 + 1)}-${pad2(d)}`; }
function daysInMonth(y: number, m0: number): number { return new Date(y, m0 + 1, 0).getDate(); }
function firstWeekday(y: number, m0: number): number { return new Date(y, m0, 1).getDay(); }

// 按月探测有消息的日子。逐日 GET /history?date=..&page_size=1（并发 4 + 按月缓存）。
// 更优：后端加 /history/days?project_id=..&month=YYYY-MM 一次拿回集合，把这里换成单次请求。
const monthCache = new Map<string, Set<string>>();

async function probeMonth(
  projectId: string, year: number, month0: number, today: Date,
): Promise<Set<string>> {
  const cacheKey = `${projectId}|${year}-${pad2(month0 + 1)}`;
  const cached = monthCache.get(cacheKey);
  if (cached) return cached;

  const total = daysInMonth(year, month0);
  const dates: string[] = [];
  for (let d = 1; d <= total; d++) {
    if (new Date(year, month0, d) > today) break;
    dates.push(ymd(year, month0, d));
  }

  const found = new Set<string>();
  let idx = 0;
  const worker = async (): Promise<void> => {
    while (idx < dates.length) {
      const my = dates[idx++];
      if (!my) return;
      try {
        const q = new URLSearchParams({
          project_id: projectId, date: my, page: '1', page_size: '1',
        });
        const res = await runtimeFetch(`${historyBase()}/history?${q.toString()}`);
        if (res.ok) {
          const data = await res.json();
          if ((data.total ?? 0) > 0) found.add(my);
        }
      } catch { /* 单日探测失败忽略 */ }
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, dates.length) }, () => worker()));
  monthCache.set(cacheKey, found);
  return found;
}

export default function DateCalendar({
  projectId, selectedDate, onSelect, minDate = null, probeHasMessages = true,
}: DateCalendarProps): React.ReactElement {
  const { t } = useTranslation();
  const today = useMemo(() => new Date(), []);
  const todayStr = ymd(today.getFullYear(), today.getMonth(), today.getDate());

  const initial = selectedDate ? new Date(selectedDate) : today;
  const [viewYear, setViewYear] = useState(initial.getFullYear());
  const [viewMonth, setViewMonth] = useState(initial.getMonth());
  const [hasMsgDays, setHasMsgDays] = useState<Set<string>>(new Set());

  const min = minDate ? new Date(minDate) : null;

  const canPrev = !min
    || new Date(viewYear, viewMonth, 1) > new Date(min.getFullYear(), min.getMonth(), 1);
  const canNext =
    new Date(viewYear, viewMonth, 1) < new Date(today.getFullYear(), today.getMonth(), 1);

  const goPrev = (): void => {
    if (!canPrev) return;
    if (viewMonth === 0) { setViewYear((y) => y - 1); setViewMonth(11); } else setViewMonth((m) => m - 1);
  };
  const goNext = (): void => {
    if (!canNext) return;
    if (viewMonth === 11) { setViewYear((y) => y + 1); setViewMonth(0); } else setViewMonth((m) => m + 1);
  };

  useEffect(() => {
    let cancelled = false;
    if (!probeHasMessages || !projectId) { setHasMsgDays(new Set()); return; }
    probeMonth(projectId, viewYear, viewMonth, today).then((s) => { if (!cancelled) setHasMsgDays(s); });
    return () => { cancelled = true; };
  }, [projectId, viewYear, viewMonth, probeHasMessages, today]);

  const years: number[] = [];
  const startYear = min ? min.getFullYear() : today.getFullYear() - 5;
  for (let y = startYear; y <= today.getFullYear(); y++) years.push(y);

  const lead = firstWeekday(viewYear, viewMonth);
  const total = daysInMonth(viewYear, viewMonth);
  const cells: (number | null)[] = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  for (let d = 1; d <= total; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  const dayDisabled = (d: number): boolean => {
    const cur = new Date(viewYear, viewMonth, d);
    if (cur > today) return true;
    if (min && cur < new Date(min.getFullYear(), min.getMonth(), min.getDate())) return true;
    return false;
  };

  return (
    <div className="calendar" role="group" aria-label={t('date.calendar.03')}>
      <div className="cal-head">
        <button type="button" className="cal-nav" onClick={goPrev} disabled={!canPrev} aria-label={t('date.calendar.01')}>‹</button>
        <div className="cal-selects">
          <select
            className="cal-select" value={viewYear} aria-label={t('date.calendar.04')}
            onChange={(e) => setViewYear(Number(e.target.value))}
          >
            {years.map((y) => <option key={y} value={y}>{t('date.calendar.yearOption', { y })}</option>)}
          </select>
          <select
            className="cal-select" value={viewMonth} aria-label={t('date.calendar.05')}
            onChange={(e) => setViewMonth(Number(e.target.value))}
          >
            {Array.from({ length: 12 }, (_, m) => {
              const future = new Date(viewYear, m, 1)
                > new Date(today.getFullYear(), today.getMonth(), 1);
              return <option key={m} value={m} disabled={future}>{t('date.calendar.monthOption', { m: m + 1 })}</option>;
            })}
          </select>
        </div>
        <button type="button" className="cal-nav" onClick={goNext} disabled={!canNext} aria-label={t('date.calendar.02')}>›</button>
      </div>

      <div className="cal-weekdays">
        {WEEKDAYS.map((w) => <span key={w} className="cal-weekday">{t(w)}</span>)}
      </div>

      <div className="cal-grid">
        {cells.map((d, i) => {
          if (d == null) return <span key={`e${i}`} className="cal-day cal-empty" />;
          const dateStr = ymd(viewYear, viewMonth, d);
          const cls = ['cal-day',
            dateStr === todayStr ? 'today' : '',
            dateStr === selectedDate ? 'selected' : '',
            hasMsgDays.has(dateStr) ? 'has-msgs' : ''].filter(Boolean).join(' ');
          return (
            <button
              key={dateStr} type="button" className={cls}
              disabled={dayDisabled(d)} onClick={() => onSelect(dateStr)}
              aria-pressed={dateStr === selectedDate} aria-label={dateStr}
            >
              <span className="cal-day-num">{d}</span>
              {hasMsgDays.has(dateStr) && <span className="cal-dot" aria-hidden="true" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
