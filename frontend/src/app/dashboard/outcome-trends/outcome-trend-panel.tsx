"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";

type TrendStatus = "ok" | "small_sample" | "no_sample";
type GroupKey = "efficiency" | "participation" | "outcome" | "commercial";

type TrendPoint = {
  month: string;
  label: string;
  value?: number | null;
  numerator: number;
  denominator: number;
  status: TrendStatus;
};

type TrendMetric = {
  key: string;
  label: string;
  unit: string;
  stage_day: 7 | 28;
  direction: "higher_is_better" | "lower_is_better";
  description: string;
  points: TrendPoint[];
};

type TrendGroup = {
  key: GroupKey;
  label: string;
  description: string;
  metrics: TrendMetric[];
};

type TrendCohort = {
  month: string;
  label: string;
  started_cases: number;
  eligible_day7_cases: number;
  eligible_day28_cases: number;
};

type TrendSummary = {
  months: number;
  cohorts: TrendCohort[];
  groups: TrendGroup[];
  history_notes: string[];
  generated_at: string;
};

const RANGE_OPTIONS = [3, 6, 12] as const;

export function OutcomeTrendPanel() {
  const [months, setMonths] = useState<number>(6);
  const [activeGroup, setActiveGroup] = useState<GroupKey>("efficiency");
  const [requestVersion, setRequestVersion] = useState(0);
  const [data, setData] = useState<TrendSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    api<TrendSummary>(`/dashboard/outcome-trends?months=${months}`)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [months, requestVersion]);

  const group = useMemo(
    () => data?.groups.find((item) => item.key === activeGroup) ?? data?.groups[0],
    [activeGroup, data],
  );

  return (
    <div className="page-stack">
      <section className="panel overflow-hidden">
        <div className="panel-header gap-4">
          <div className="toolbar-row">
            <div>
              <h2 className="panel-title">同阶段月度比较</h2>
              <p className="panel-subtitle">每个比例同时显示人数分子和同阶段分母；少于 10 人只作观察，不下结论。</p>
            </div>
            <div className="segmented-control grid-cols-3" role="group" aria-label="趋势时间范围">
              {RANGE_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setMonths(option)}
                  aria-pressed={months === option}
                  className={`min-h-9 rounded-lg px-3 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600 focus-visible:ring-offset-2 ${
                    months === option ? "bg-white text-slate-950 shadow-sm" : "text-slate-500 hover:text-slate-800"
                  }`}
                >
                  近 {option} 月
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-cyan-600" />样本充足</span>
            <span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full border-2 border-amber-500 bg-white" />样本少于10人</span>
            <span>纵轴固定为 0–100%，避免放大微小波动</span>
          </div>
        </div>

        <div className="border-b border-slate-200 px-4 py-3 sm:px-5">
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4" role="tablist" aria-label="趋势指标分组">
            {(data?.groups ?? [
              { key: "efficiency", label: "承接效率" },
              { key: "participation", label: "用户参与" },
              { key: "outcome", label: "服务结果" },
              { key: "commercial", label: "商业信号" },
            ]).map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={activeGroup === item.key}
                onClick={() => setActiveGroup(item.key as GroupKey)}
                className={`min-h-11 rounded-xl border px-3 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600 focus-visible:ring-offset-2 ${
                  activeGroup === item.key
                    ? "border-cyan-300 bg-cyan-50 text-cyan-900"
                    : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {error ? (
          <div className="p-10 text-center">
            <AlertTriangle className="mx-auto h-6 w-6 text-rose-500" />
            <p className="mt-3 text-sm font-semibold text-slate-800">趋势数据加载失败</p>
            <button
              type="button"
              onClick={() => setRequestVersion((value) => value + 1)}
              className="mt-4 inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600"
            >
              <RotateCcw className="h-4 w-4" />重新加载
            </button>
          </div>
        ) : loading && !data ? (
          <TrendSkeleton />
        ) : group ? (
          <div className="p-4 sm:p-5">
            <div className="mb-4">
              <h3 className="text-base font-bold text-slate-950">{group.label}</h3>
              <p className="mt-1 text-sm leading-6 text-slate-500">{group.description}</p>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
              {group.metrics.map((metric) => (
                <TrendMetricCard key={metric.key} metric={metric} />
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {data && <CohortTable cohorts={data.cohorts} />}

      {!!data?.history_notes.length && (
        <section className="panel p-5 sm:p-6">
          <h2 className="panel-title">解读边界</h2>
          <ul className="mt-3 grid gap-2 text-sm leading-6 text-slate-600">
            {data.history_notes.map((note) => (
              <li key={note} className="flex gap-2">
                <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-600" />
                <span>{note}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function TrendMetricCard({ metric }: { metric: TrendMetric }) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-bold text-slate-950">{metric.label}</h4>
          <p className="mt-1 text-xs leading-5 text-slate-500">{metric.description}</p>
        </div>
        <span className="status-pill pill-neutral shrink-0">第 {metric.stage_day} 天</span>
      </div>
      <TrendChart points={metric.points} />
      <div className="mt-3 grid gap-1.5">
        {metric.points.map((point) => (
          <div key={point.month} className="grid grid-cols-[4.25rem_1fr_auto] items-center gap-2 text-xs">
            <span className="text-slate-500">{point.label}</span>
            <span className="text-slate-500">{point.denominator ? `${point.numerator} / ${point.denominator} 人` : "尚无同阶段样本"}</span>
            <span className={`tabular font-bold ${point.status === "small_sample" ? "text-amber-700" : point.value == null ? "text-slate-400" : "text-slate-900"}`}>
              {point.value == null ? "—" : `${point.value}%`}
            </span>
          </div>
        ))}
      </div>
    </article>
  );
}

function TrendChart({ points }: { points: TrendPoint[] }) {
  const width = 600;
  const height = 190;
  const left = 42;
  const right = 18;
  const top = 16;
  const bottom = 36;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const x = (index: number) => left + (points.length === 1 ? plotWidth / 2 : (plotWidth * index) / (points.length - 1));
  const y = (value: number) => top + plotHeight * (1 - value / 100);

  return (
    <div className="mt-4 overflow-hidden rounded-xl bg-slate-50/80 px-2 py-2" aria-label="月度比例折线图">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" className="h-auto w-full" aria-label={points.map((point) => `${point.label}${point.value == null ? "无样本" : `${point.value}%`}`).join("，")}>
        {[0, 50, 100].map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#e2e8f0" strokeWidth="1" />
            <text x={left - 8} y={y(tick) + 4} textAnchor="end" fontSize="11" fill="#94a3b8">{tick}</text>
          </g>
        ))}
        {points.slice(0, -1).map((point, index) => {
          const next = points[index + 1];
          if (point.value == null || next.value == null) return null;
          return <line key={`${point.month}-${next.month}`} x1={x(index)} y1={y(point.value)} x2={x(index + 1)} y2={y(next.value)} stroke="#0891b2" strokeWidth="3" strokeLinecap="round" />;
        })}
        {points.map((point, index) => (
          <g key={point.month}>
            {point.value != null && (
              <circle
                cx={x(index)}
                cy={y(point.value)}
                r="5"
                fill={point.status === "small_sample" ? "white" : "#0891b2"}
                stroke={point.status === "small_sample" ? "#d97706" : "#0891b2"}
                strokeWidth="3"
              />
            )}
            <text x={x(index)} y={height - 10} textAnchor="middle" fontSize="11" fill="#64748b">{point.label}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function CohortTable({ cohorts }: { cohorts: TrendCohort[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <h2 className="panel-title">各月样本成熟度</h2>
        <p className="panel-subtitle">先看每批有多少人已经走到对应服务阶段，再解读上面的比例。</p>
      </div>
      <div className="mobile-card-list p-4">
        {cohorts.map((cohort) => (
          <div key={cohort.month} className="mobile-record-card">
            <div className="font-bold text-slate-950">{cohort.label}</div>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
              <CohortCount label="开始服务" value={cohort.started_cases} />
              <CohortCount label="满7天" value={cohort.eligible_day7_cases} />
              <CohortCount label="满28天" value={cohort.eligible_day28_cases} />
            </dl>
          </div>
        ))}
      </div>
      <div className="hidden overflow-x-auto md:block">
        <table className="data-table">
          <thead><tr><th>服务开始月份</th><th className="text-right">开始服务</th><th className="text-right">已满7天</th><th className="text-right">已满28天</th></tr></thead>
          <tbody>
            {cohorts.map((cohort) => (
              <tr key={cohort.month}>
                <td className="font-semibold text-slate-900">{cohort.label}</td>
                <td className="tabular text-right">{cohort.started_cases} 人</td>
                <td className="tabular text-right">{cohort.eligible_day7_cases} 人</td>
                <td className="tabular text-right">{cohort.eligible_day28_cases} 人</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CohortCount({ label, value }: { label: string; value: number }) {
  return <div><dt className="text-slate-500">{label}</dt><dd className="tabular mt-1 text-base font-bold text-slate-950">{value}</dd></div>;
}

function TrendSkeleton() {
  return (
    <div className="grid gap-4 p-4 sm:p-5 xl:grid-cols-2" aria-label="趋势加载中">
      {[0, 1, 2].map((item) => (
        <div key={item} className="h-72 rounded-2xl border border-slate-200 bg-slate-50" />
      ))}
    </div>
  );
}
