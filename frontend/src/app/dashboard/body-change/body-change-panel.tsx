"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type BodyChangeStat = {
  sample_count: number;
  median?: number | null;
  mean?: number | null;
  best?: number | null;
  worst?: number | null;
};

export type BodyChangeSegmentBucket = {
  bucket: string;
  stat: BodyChangeStat;
  conclusive: boolean;
};

export type BodyChangeSegment = {
  key: string;
  label: string;
  description: string;
  buckets: BodyChangeSegmentBucket[];
};

export type BodyChangeMetricWindow = {
  stat: BodyChangeStat;
  eligible_cases: number;
  coverage_rate?: number | null;
  unverified_unit_count: number;
  excluded: Record<string, number>;
  segments: BodyChangeSegment[];
};

export type BodyChangeWindow = {
  window_day: number;
  weight: BodyChangeMetricWindow;
  waist: BodyChangeMetricWindow;
};

export type BodyChangeSummary = {
  min_checkin_rate: number;
  windows: BodyChangeWindow[];
  weight_record_reasons: Record<string, number>;
  waist_record_reasons: Record<string, number>;
  generated_at?: string | null;
};

// 未纳入原因用业务语言解释，运营不需要知道内部字段叫什么。
const EXCLUDE_LABELS: Record<string, string> = {
  no_baseline: "没有可用起点（入营问卷没填，服务前三天也没称/没量）",
  no_endpoint: "窗口末那几天没称/没量",
  implausible_delta: "变化幅度不可能，判定为录入问题",
  below_checkin_rate: "没达到打卡率门槛",
};

const RECORD_REASON_LABELS: Record<string, string> = {
  ok: "可用",
  no_number: "写的是「未测/没称」这类，没有数字",
  unit_ambiguous: "有数字但单位存疑，判不出是公斤还是斤",
  out_of_range: "换算后仍不是人体可能的值（例如填 0）",
};

// 与后端主看板大卡同一个门槛：不到这个人数只报人数，不作为对外结论。
const MIN_CONCLUSIVE_SAMPLE = 10;

const WINDOW_LABELS: Record<number, string> = {
  3: "3 天",
  7: "7 天",
  14: "14 天",
  28: "28 天",
};

export function BodyChangePanel() {
  const [windowDay, setWindowDay] = useState(28);
  const [strict, setStrict] = useState(false);
  const [data, setData] = useState<BodyChangeSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const query = strict ? "?min_checkin_rate=0.6" : "";
    api<BodyChangeSummary>(`/dashboard/body-change${query}`)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch(() => {
        if (!cancelled) setError("加载失败，请重试");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [strict]);

  const current = data?.windows.find((item) => item.window_day === windowDay);

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap gap-2" role="group" aria-label="时间窗口">
            {[3, 7, 14, 28].map((day) => (
              <button
                key={day}
                type="button"
                onClick={() => setWindowDay(day)}
                aria-pressed={windowDay === day}
                className={
                  windowDay === day
                    ? "min-h-10 rounded-lg bg-cyan-600 px-4 text-sm font-semibold text-white"
                    : "min-h-10 rounded-lg border border-slate-200 px-4 text-sm font-medium text-slate-600 hover:bg-slate-50"
                }
              >
                {WINDOW_LABELS[day]}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setStrict((value) => !value)}
            aria-pressed={strict}
            className={
              strict
                ? "min-h-10 rounded-lg bg-slate-900 px-4 text-sm font-semibold text-white"
                : "min-h-10 rounded-lg border border-slate-200 px-4 text-sm font-medium text-slate-600 hover:bg-slate-50"
            }
          >
            {strict ? "当前：只算打卡率≥60% 的人" : "当前：全部用户（点此只看打卡率≥60%）"}
          </button>
        </div>
        <p className="px-4 pb-4 text-xs leading-5 text-slate-500 sm:px-5">
          起点统一取入营问卷填的体重/腰围（问卷缺失才退到服务前三天的打卡值），终点取第
          {windowDay} 天前后两天里最接近的一次记录。分母是服务天数已经走到第 {windowDay}{" "}
          天的人，还没到时间的用户不参与，避免把「没到时间」算成「没效果」。
        </p>
      </section>

      {error && <div className="panel p-8 text-center text-rose-600">{error}</div>}
      {loading && !data && <div className="panel p-12 text-center text-slate-500">加载中…</div>}

      {current && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <MetricCard title="体重变化" unit="kg" metric={current.weight} windowDay={windowDay} />
            <MetricCard title="腰围变化" unit="cm" metric={current.waist} windowDay={windowDay} />
          </div>

          <SegmentTables title="体重" unit="kg" metric={current.weight} />
          <SegmentTables title="腰围" unit="cm" metric={current.waist} />

          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">打卡记录的可用情况</h2>
              <p className="panel-subtitle">
                用户填的每一条体重/腰围都在这里交代清楚，剔除了什么、为什么，不让样本悄悄变小。
              </p>
            </div>
            <div className="grid gap-4 p-4 sm:p-5 md:grid-cols-2">
              <RecordReasons title="体重" reasons={data?.weight_record_reasons ?? {}} />
              <RecordReasons title="腰围" reasons={data?.waist_record_reasons ?? {}} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function MetricCard({
  title,
  unit,
  metric,
  windowDay,
}: {
  title: string;
  unit: string;
  metric: BodyChangeMetricWindow;
  windowDay: number;
}) {
  const { stat } = metric;
  const coverage =
    metric.coverage_rate != null ? `${Math.round(metric.coverage_rate * 100)}%` : "—";

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">
          {windowDay} 天{title}
        </h2>
        <p className="panel-subtitle">中位数为主口径；均值同时列出，便于判断是否被个别极端个案带偏。</p>
      </div>
      <div className="p-4 sm:p-5">
        {stat.sample_count === 0 ? (
          <div className="empty-state">这个窗口暂无可用样本</div>
        ) : (
          <>
            {stat.sample_count < MIN_CONCLUSIVE_SAMPLE && (
              <p className="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
                只有 {stat.sample_count} 人，样本太少，仅供内部参考，不要作为对外结论引用。
              </p>
            )}
            <div className="flex flex-wrap items-end gap-6">
              <div>
                <div className="text-xs font-medium text-slate-500">中位数</div>
                <ChangeValue value={stat.median} unit={unit} large />
              </div>
              <div>
                <div className="text-xs font-medium text-slate-500">均值</div>
                <ChangeValue value={stat.mean} unit={unit} />
              </div>
            </div>
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
              <Row label="有效样本">
                {stat.sample_count} 人 / 满 {windowDay} 天的 {metric.eligible_cases} 人（{coverage}）
              </Row>
              <Row label="减得最多 / 增得最多">
                <ChangeValue value={stat.best} unit={unit} inline /> ·{" "}
                <ChangeValue value={stat.worst} unit={unit} inline />
              </Row>
              {metric.unverified_unit_count > 0 && (
                <Row label="单位未核对">
                  {metric.unverified_unit_count} 人没有入营问卷值可对照单位，方向可信、幅度可能偏差一倍
                </Row>
              )}
            </dl>
          </>
        )}
        {Object.keys(metric.excluded).length > 0 && (
          <div className="mt-4 rounded-xl bg-slate-50 p-4">
            <div className="text-xs font-semibold text-slate-600">未纳入的人及原因</div>
            <ul className="mt-2 space-y-1 text-xs text-slate-600">
              {Object.entries(metric.excluded)
                .sort((a, b) => b[1] - a[1])
                .map(([reason, count]) => (
                  <li key={reason}>
                    {EXCLUDE_LABELS[reason] ?? reason}：{count} 人
                  </li>
                ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function SegmentTables({
  title,
  unit,
  metric,
}: {
  title: string;
  unit: string;
  metric: BodyChangeMetricWindow;
}) {
  if (!metric.segments.length || metric.stat.sample_count === 0) return null;
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <h2 className="panel-title">{title}变化 · 分人群</h2>
        <p className="panel-subtitle">不同执行力、年龄和基数的人效果本来就不一样，分开看才判断得准。</p>
      </div>
      <div className="grid gap-4 p-4 sm:p-5 lg:grid-cols-3">
        {metric.segments.map((segment) => (
          <div key={segment.key} className="rounded-xl border border-slate-200">
            <div className="border-b border-slate-100 px-4 py-3">
              <div className="text-sm font-semibold text-slate-900">{segment.label}</div>
              <p className="mt-1 text-xs leading-5 text-slate-500">{segment.description}</p>
            </div>
            {segment.buckets.length === 0 ? (
              <div className="px-4 py-6 text-center text-xs text-slate-400">暂无样本</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-500">
                    <th className="px-4 py-2 text-left font-medium">分组</th>
                    <th className="px-2 py-2 text-right font-medium">人数</th>
                    <th className="px-4 py-2 text-right font-medium">中位数</th>
                  </tr>
                </thead>
                <tbody>
                  {segment.buckets.map((bucket) => (
                    <tr key={bucket.bucket} className="border-t border-slate-50">
                      <td className="px-4 py-2 text-slate-700">{bucket.bucket}</td>
                      <td className="px-2 py-2 text-right text-slate-600">{bucket.stat.sample_count}</td>
                      <td className="px-4 py-2 text-right">
                        {bucket.conclusive ? (
                          <ChangeValue value={bucket.stat.median} unit={unit} inline />
                        ) : (
                          <span className="text-xs text-slate-400">样本太少</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function RecordReasons({ title, reasons }: { title: string; reasons: Record<string, number> }) {
  const total = Object.values(reasons).reduce((sum, count) => sum + count, 0);
  return (
    <div className="rounded-xl border border-slate-200 p-4">
      <div className="text-sm font-semibold text-slate-900">
        {title}：共填过 {total} 条
      </div>
      <ul className="mt-2 space-y-1 text-xs text-slate-600">
        {Object.entries(reasons)
          .sort((a, b) => b[1] - a[1])
          .map(([reason, count]) => (
            <li key={reason}>
              {RECORD_REASON_LABELS[reason] ?? reason}：{count} 条
            </li>
          ))}
        {total === 0 && <li className="text-slate-400">暂无记录</li>}
      </ul>
    </div>
  );
}

/** 负数=减少。用文字写出方向，避免像原来那样把「+9.1cm」读成「缩减 9 厘米」。 */
function ChangeValue({
  value,
  unit,
  large = false,
  inline = false,
}: {
  value?: number | null;
  unit: string;
  large?: boolean;
  inline?: boolean;
}) {
  if (value == null) {
    return <span className={inline ? "text-xs text-slate-400" : "text-sm text-slate-400"}>暂无</span>;
  }
  const decreased = value < 0;
  const flat = value === 0;
  const text = flat ? `持平` : `${decreased ? "减少" : "增加"} ${Math.abs(value)} ${unit}`;
  const tone = flat ? "text-slate-600" : decreased ? "text-emerald-600" : "text-amber-600";
  if (inline) {
    return <span className={`text-sm font-semibold ${tone}`}>{text}</span>;
  }
  return (
    <div className={`${large ? "text-3xl" : "text-xl"} font-bold ${tone}`}>{text}</div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-slate-700">{children}</dd>
    </div>
  );
}
