import type { Language } from './i18n';

const HOLIDAY_CONFIG = {
  2025: {
    holidays: [
      ['2025-01-01', '2025-01-01'],
      ['2025-01-28', '2025-02-04'],
      ['2025-04-04', '2025-04-06'],
      ['2025-05-01', '2025-05-05'],
      ['2025-05-31', '2025-06-02'],
      ['2025-10-01', '2025-10-08'],
    ],
    makeupWorkdays: ['2025-01-26', '2025-02-08', '2025-04-27', '2025-09-28', '2025-10-11'],
  },
  2026: {
    holidays: [
      ['2026-01-01', '2026-01-03'],
      ['2026-02-15', '2026-02-23'],
      ['2026-04-04', '2026-04-06'],
      ['2026-05-01', '2026-05-05'],
      ['2026-06-19', '2026-06-21'],
      ['2026-09-25', '2026-09-27'],
      ['2026-10-01', '2026-10-07'],
    ],
    makeupWorkdays: ['2026-01-04', '2026-02-14', '2026-02-28', '2026-05-09', '2026-09-20', '2026-10-10'],
  },
} as const;

const WEEKDAY_LABELS = {
  'zh-CN': ['周日', '周一', '周二', '周三', '周四', '周五', '周六'],
  'en-US': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
};

function toDate(input?: string | Date) {
  if (input instanceof Date) {
    return new Date(input.getTime());
  }
  if (typeof input === 'string') {
    return new Date(`${input}T00:00:00`);
  }
  return new Date();
}

function addDays(date: Date, days: number) {
  const next = new Date(date.getTime());
  next.setDate(next.getDate() + days);
  return next;
}

function formatDate(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function expandDateRange(start: string, end: string) {
  const result: string[] = [];
  let current = toDate(start);
  const last = toDate(end);

  while (current <= last) {
    result.push(formatDate(current));
    current = addDays(current, 1);
  }

  return result;
}

const HOLIDAY_LOOKUP = Object.fromEntries(
  Object.entries(HOLIDAY_CONFIG).map(([year, config]) => {
    return [
      year,
      {
        holidays: new Set(config.holidays.flatMap(([start, end]) => expandDateRange(start, end))),
        makeupWorkdays: new Set(config.makeupWorkdays),
      },
    ];
  }),
);

export function getDisplayWeekBaseDate(baseDate?: string | Date) {
  const currentDate = toDate(baseDate);
  return currentDate.getDay() === 0 ? addDays(currentDate, 1) : currentDate;
}

export function isWorkday(input?: string | Date) {
  const currentDate = toDate(input);
  const dateKey = formatDate(currentDate);
  const yearConfig = HOLIDAY_LOOKUP[String(currentDate.getFullYear())];

  if (yearConfig?.makeupWorkdays.has(dateKey)) {
    return true;
  }
  if (yearConfig?.holidays.has(dateKey)) {
    return false;
  }

  const weekday = currentDate.getDay();
  return weekday >= 1 && weekday <= 5;
}

export function buildWorkweekDates(baseDate: string | Date, language: Language) {
  const currentDate = getDisplayWeekBaseDate(baseDate);
  const weekday = currentDate.getDay();
  const monday = addDays(currentDate, -(weekday === 0 ? 6 : weekday - 1));
  const weekdayLabels = WEEKDAY_LABELS[language] || WEEKDAY_LABELS['zh-CN'];

  return Array.from({ length: 7 })
    .map((_, index) => addDays(monday, index))
    .filter((date) => isWorkday(date))
    .map((date) => ({
      dateKey: formatDate(date),
      dateLabel:
        language === 'en-US' ? `${`${date.getMonth() + 1}`.padStart(2, '0')}/${`${date.getDate()}`.padStart(2, '0')}` : `${date.getMonth() + 1}月${date.getDate()}日`,
      weekLabel: weekdayLabels[date.getDay()],
      isToday: formatDate(date) === formatDate(currentDate),
    }));
}
