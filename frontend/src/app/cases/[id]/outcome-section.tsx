import { OutcomeBundle, OutcomeEvent, OutcomeSnapshot, OutcomeStageState } from "@/lib/api";
import { formatChinaDateTime } from "@/lib/format";

const MILESTONES = [
  ["day1", "第1天", "建档与理解"],
  ["day3", "第3天", "启动执行"],
  ["day7", "第7天", "体感变化"],
  ["day14", "第14天", "平台期与调方案"],
  ["day28", "第28天", "目标达成"],
  ["day90", "第90天", "习惯形成"],
  ["day365", "第365天", "长期会员与推荐"],
] as const;

const FIELD_LABELS: Record<string, string> = {
  profile_completed: "完成建档",
  plan_understood: "理解方案",
  documents_received: "收到文件",
  started_execution: "开始执行",
  product_schedule: "早C/午B/晚A节奏",
  discomfort_feedback: "不适反馈",
  rhythm_understood: "理解执行节奏",
  weight_change: "体重变化",
  waist_or_body_change: "腰围/裤腰/体感变化",
  energy_change: "精神状态变化",
  sleep_change: "睡眠变化",
  bowel_change: "排便变化",
  plateau_signal: "平台期信号",
  execution_rate_signal: "执行率信号",
  adjustment_need: "调方案需求",
  positive_body_signal: "正向体感",
  challenge_goal_result: "目标达成情况",
  weight_result: "28天体重结果",
  waist_result: "28天腰围/体态结果",
  habit_stability: "执行习惯稳定性",
  next_stage_intent: "下一阶段意向",
  lifestyle_habit: "生活方式习惯",
  long_term_weight_stability: "长期体重/围度稳定",
  self_management: "自我管理能力",
  long_term_membership: "长期会员",
  referral_signal: "推荐他人",
  brand_advocacy: "品牌认同",
};

const EVENT_LABELS: Record<string, string> = {
  improved: "改善",
  worsened: "变差",
  unchanged: "无变化",
  completed: "完成",
  not_completed: "未完成",
  feedback: "反馈",
  discomfort: "不适",
  observation: "观察",
};

export function OutcomeSection({
  bundle,
}: {
  bundle: OutcomeBundle | null;
}) {
  const events = bundle?.events ?? [];
  const states = bundle?.states ?? [];
  const snapshots = bundle?.snapshots ?? [];
  const latestState = states[states.length - 1];
  const snapshotMap = new Map(snapshots.map((item) => [item.milestone, item]));

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm md:col-span-2">
      <div className="flex flex-col gap-3 border-b border-slate-100 px-6 py-4 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="font-semibold text-slate-900">客户成效引擎</h2>
          <p className="mt-1 text-sm text-slate-500">
            自动从聊天与打卡中沉淀正向进展，客观指标看趋势去噪、主观体感看多次印证。
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <MiniMetric label="成效证据" value={events.length} title="已沉淀的可追溯事实条数" />
          <MiniMetric label="阶段状态" value={states.length} title="已经开始收集的阶段数" />
          <MiniMetric label="阶段快照" value={snapshots.length} title="系统在阶段节点沉淀的汇总记录数" />
        </div>
      </div>

      <div className="space-y-6 p-6">
        {latestState ? (
          <StageStateCard state={latestState} />
        ) : (
          <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm text-slate-500">
            暂无成效状态。用户在服务群内产生普通聊天后，系统会自动提取可追溯的成效证据。
          </div>
        )}

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-900">阶段快照</h3>
            <span className="text-xs text-slate-400">系统到达节点后自动沉淀</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {MILESTONES.map(([key, dayLabel, label]) => {
              const snapshot = snapshotMap.get(key);
              const state = states.find((item) => item.milestone === key);
              return (
                <div key={key} className="rounded-lg border border-slate-200 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs text-slate-500">{dayLabel}</p>
                      <p className="mt-1 font-medium text-slate-900">{label}</p>
                    </div>
                    <SnapshotStatus hasSnapshot={Boolean(snapshot)} stale={isSnapshotStale(snapshot, state)} />
                  </div>
                  {snapshot ? (
                    <SnapshotSummary snapshot={snapshot} state={state} />
                  ) : (
                    <p className="mt-3 text-xs leading-5 text-slate-400">尚未生成该阶段快照。</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-900">最近成效证据</h3>
            <span className="text-xs text-slate-400">按聊天提取时间倒序</span>
          </div>
          {events.length > 0 ? (
            <div className="space-y-3">
              {events.slice(0, 8).map((event) => (
                <OutcomeEventRow key={event.id} event={event} />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 py-8 text-center text-sm text-slate-400">
              暂无聊天成效证据
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StageStateCard({ state }: { state: OutcomeStageState }) {
  const collectedEntries = Object.entries(state.collected_fields ?? {});
  const missing = state.missing_fields ?? [];
  const total = collectedEntries.length + missing.length;
  const progress = total > 0 ? Math.round((collectedEntries.length / total) * 100) : 0;
  const isComplete = missing.length === 0 && collectedEntries.length > 0;

  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-blue-500">{milestoneName(state.milestone)}</p>
          <h3 className="mt-1 text-lg font-semibold text-slate-900">当前阶段收集进度 {progress}%</h3>
          <p className="mt-1 text-sm text-slate-500">
            已收集 {collectedEntries.length} 项，仍缺 {missing.length} 项。
          </p>
        </div>
        {isComplete && (
          <div className="max-w-md rounded-lg border border-emerald-100 bg-white px-3 py-2 text-xs text-emerald-700">
            当前阶段关键证据已收集完整。
          </div>
        )}
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-white">
        <div className="h-full rounded-full bg-blue-500" style={{ width: `${progress}%` }} />
      </div>

      <div className="mt-4">
        <p className="mb-2 text-xs font-medium text-slate-500">已收集</p>
        {collectedEntries.length > 0 ? (
          <div className="grid gap-2 md:grid-cols-2">
            {collectedEntries.slice(0, 6).map(([field, value]) => (
              <div key={field} className="rounded-lg bg-white px-3 py-2 text-sm">
                <p className="font-medium text-slate-900">{fieldLabel(field)}</p>
                <p className="mt-1 text-slate-600">{value?.value || value?.evidence_text || "已记录"}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">暂无</p>
        )}
      </div>
    </div>
  );
}

function SnapshotSummary({ snapshot, state }: { snapshot: OutcomeSnapshot; state?: OutcomeStageState }) {
  const metrics = snapshot.metrics_json ?? {};
  const stale = isSnapshotStale(snapshot, state);
  return (
    <div className="mt-3 space-y-2 text-xs text-slate-500">
      {stale && (
        <p className="rounded-md bg-amber-50 px-2 py-1 text-amber-700">
          快照早于当前阶段状态，等待系统下次自动刷新。
        </p>
      )}
      <p className="line-clamp-2 text-slate-600">{snapshot.ai_summary || "已生成快照。"}</p>
      <div className="flex flex-wrap gap-2">
        <MetricPill label="打卡" value={metricValue(metrics.checkin_days, "天")} />
        <MetricPill label="已收集" value={metricValue(metrics.collected_count, "项")} />
        <MetricPill label="缺失" value={metricValue(metrics.missing_count, "项")} />
        {trendLabel(metrics.weight_trend) && <MetricPill label="体重趋势" value={trendLabel(metrics.weight_trend)} />}
        {trendLabel(metrics.waist_trend) && <MetricPill label="腰围趋势" value={trendLabel(metrics.waist_trend)} />}
      </div>
      <p className="text-slate-400">{formatDateTime(snapshot.generated_at)}</p>
    </div>
  );
}

function OutcomeEventRow({ event }: { event: OutcomeEvent }) {
  return (
    <div className="rounded-lg border border-slate-100 p-4">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
              {milestoneName(event.milestone)}
            </span>
            <span className="text-sm font-medium text-slate-900">{fieldLabel(event.field_key)}</span>
            <span className="text-xs text-slate-400">{eventLabel(event.event_type)}</span>
          </div>
          <p className="mt-2 text-sm text-slate-700">{event.value_text}</p>
          <p className="mt-1 text-xs text-slate-500">证据：{event.evidence_text}</p>
        </div>
        <div className="shrink-0 text-right text-xs text-slate-400">
          <p>{Math.round((event.confidence || 0) * 100)}%</p>
          <p className="mt-1">{formatDateTime(event.created_at)}</p>
        </div>
      </div>
    </div>
  );
}

function MiniMetric({ label, value, title }: { label: string; value: number; title: string }) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2" title={title}>
      <p className="text-lg font-semibold text-slate-900">{value}</p>
      <p className="text-slate-400">{label}</p>
    </div>
  );
}

function SnapshotStatus({ hasSnapshot, stale }: { hasSnapshot: boolean; stale: boolean }) {
  const label = hasSnapshot ? (stale ? "待刷新" : "已沉淀") : "待自动生成";
  return (
    <span
      className={
        stale
          ? "rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700"
          : hasSnapshot
          ? "rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700"
          : "rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-500"
      }
    >
      {label}
    </span>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded-md bg-slate-100 px-2 py-1 text-slate-600">
      {label} {value}
    </span>
  );
}

function metricValue(value: unknown, suffix: string) {
  return typeof value === "number" ? `${value}${suffix}` : "暂无";
}

function trendLabel(trend: unknown): string {
  if (!trend || typeof trend !== "object") return "";
  const { direction, net, consistent } = trend as {
    direction?: string;
    net?: number;
    consistent?: boolean;
  };
  const arrow = direction === "down" ? "↓" : direction === "up" ? "↑" : "持平";
  const magnitude = typeof net === "number" ? Math.abs(net) : "";
  const steadiness = consistent ? "稳" : "波动";
  return `${arrow}${magnitude} ${steadiness}`.trim();
}

function milestoneName(key: string) {
  const item = MILESTONES.find(([milestone]) => milestone === key);
  return item ? `${item[1]} ${item[2]}` : key;
}

function fieldLabel(key: string) {
  return FIELD_LABELS[key] || key;
}

function eventLabel(key: string) {
  return EVENT_LABELS[key] || key;
}

function formatDateTime(value?: string | null) {
  return formatChinaDateTime(value, "暂无");
}

function isSnapshotStale(snapshot?: OutcomeSnapshot, state?: OutcomeStageState) {
  if (!snapshot || !state || !snapshot.generated_at) return false;
  return new Date(state.updated_at).getTime() > new Date(snapshot.generated_at).getTime();
}
