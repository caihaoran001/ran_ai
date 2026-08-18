export function formatChinaDateTime(value?: string | null, fallback = "-") {
  if (!value) return fallback;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function formatChinaShortDateTime(value?: string | null, fallback = "-") {
  if (!value) return fallback;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

const CHINA_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/**
 * 距今几天（按北京时间的自然日算）。接受 "2026-08-10" 或完整时间戳。
 *
 * 「几天前」一律显示时现算，**不要存进库**：存下来的那一刻它就开始过期，日历翻一页
 * 它自己就变了。平台期判定曾经把「距今 N 天」写进判定记录，系统据此以为结论变了，
 * 把已跟进的人天天推回待办。
 */
export function daysAgoInChina(value?: string | null): number | null {
  if (!value) return null;
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00+08:00` : value);
  if (Number.isNaN(parsed.getTime())) return null;
  const then = Date.parse(`${CHINA_DAY.format(parsed)}T00:00:00Z`);
  const today = Date.parse(`${CHINA_DAY.format(new Date())}T00:00:00Z`);
  return Math.round((today - then) / 86_400_000);
}

/** 「今天 / 昨天 / N 天前」——运营看这句话是几天前说的，比看一个日期更快做判断。 */
export function daysAgoText(value?: string | null, fallback = ""): string {
  const days = daysAgoInChina(value);
  if (days === null || days < 0) return fallback;
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  return `${days} 天前`;
}

export function formatChinaMonthDayTime(value?: string | null, fallback = "-") {
  if (!value) return fallback;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
