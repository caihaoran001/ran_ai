import type { CheckinParsed } from "@/lib/api";

type CheckinRow = {
  key: string;
  label: string;
  value: string;
};

const FIXED_FIELD_LABELS: Array<[keyof CheckinParsed, string]> = [
  ["water", "饮水"],
  ["sleep", "睡眠"],
  ["mood", "情绪"],
  ["exercise", "运动"],
  ["weight", "体重"],
  ["waist", "腰围"],
  ["diet", "饮食"],
  ["bowel", "排便"],
  ["hunger", "饥饿感"],
  ["sensation", "体感"],
  ["discomfort", "不适"],
  ["tomorrow_focus", "明日重点"],
];

export function StructuredCheckin({ parsed }: { parsed?: CheckinParsed | null }) {
  const fixedRows = fixedCheckinRows(parsed);
  const customRows = customCheckinRows(parsed);

  if (fixedRows.length === 0 && customRows.length === 0) {
    return <p className="text-sm font-medium text-slate-600">本次打卡暂无结构化内容</p>;
  }

  return (
    <div className="space-y-2">
      {fixedRows.length > 0 && (
        <dl className="grid gap-2 text-sm text-slate-900 sm:grid-cols-2 lg:grid-cols-4">
          {fixedRows.map((row) => (
            <div key={row.key} className="text-break rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
              <dt className="inline font-bold text-slate-600">{row.label}：</dt>
              <dd className="inline">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {customRows.length > 0 && (
        <dl className="grid gap-2 text-sm text-slate-900 sm:grid-cols-2">
          {customRows.map((row) => (
            <div key={row.key} className="text-break rounded-lg border border-emerald-100 bg-emerald-50/60 px-2.5 py-1.5">
              <dt className="inline font-bold text-emerald-800">{row.label}：</dt>
              <dd className="inline">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function fixedCheckinRows(parsed?: CheckinParsed | null): CheckinRow[] {
  const product = parsed?.product_schedule;
  const rows: CheckinRow[] = [
    { key: "morning_c", label: "早C", value: textValue(product?.morning_c) },
    { key: "noon_b", label: "午B", value: textValue(product?.noon_b) },
    { key: "night_a", label: "晚A", value: textValue(product?.night_a) },
    { key: "product_summary", label: "产品执行", value: textValue(product?.summary) },
  ];

  for (const [key, label] of FIXED_FIELD_LABELS) {
    rows.push({ key, label, value: textValue(parsed?.[key]) });
  }

  return rows.filter((row) => row.value !== "");
}

function customCheckinRows(parsed?: CheckinParsed | null): CheckinRow[] {
  const stored = parsed?.custom_checkin;
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return [];

  const rows: CheckinRow[] = [];
  for (const [itemId, item] of Object.entries(stored)) {
    const record = item && typeof item === "object" && !Array.isArray(item) ? item : null;
    const answer = textValue(record?.answer ?? item);
    if (!answer) continue;
    rows.push({
      key: itemId,
      label: textValue(record?.question) || "专属执行项",
      value: answer,
    });
  }
  return rows;
}

function textValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}
