"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  X,
  Clock,
  Coins,
  Cpu,
  Wrench,
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  MessageSquare,
  FileCheck,
  CheckCircle2,
  XCircle,
  HelpCircle,
} from "lucide-react";
import { formatChinaShortDateTime } from "@/lib/format";

type ArchivistInputMessage = {
  id: number;
  role: string;
  content: string;
  created_at: string;
  is_context: boolean;
};

type ArchivistBatchChange = {
  kind: string;
  action: string;
  title: string;
  before?: string | null;
  after?: string | null;
  evidence?: string | null;
  items: Array<{
    field?: string;
    label?: string;
    value?: string | null;
    before?: string | null;
    after?: string | null;
  }>;
};

export type RunDetailData = {
  id: number;
  agent_type: string;
  run_kind: string;
  status: string;
  outcome?: string | null;
  service_case_id?: number | null;
  case_no?: string | null;
  user_name?: string | null;
  model_calls: number;
  tool_calls: number;
  total_tokens: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  estimated_cost_yuan: number;
  input_count: number;
  output_count: number;
  latency_ms?: number | null;
  error_type?: string | null;
  started_at: string;
  finished_at?: string | null;
  // Archivist extra fields
  trigger_reason?: string | null;
  input_source?: "exact" | "watermark_range" | "unavailable";
  input_messages?: ArchivistInputMessage[];
  changes?: ArchivistBatchChange[];
  changes_detail_available?: boolean;
  dropped_items?: Array<{
    bucket?: string;
    item_id?: string;
    reason?: string;
  }>;
};

const OUTCOME_LABELS: Record<string, string> = {
  reply_sent: "已发送回复",
  intentional_no_reply: "Agent 主动不回复",
  human_handoff: "已转人工",
  handoff_throttled: "转人工提醒处于节流期",
  handoff_already_recorded: "转人工消息已存在",
  staff_preempted: "班主任抢先回复",
  stale_reply_suppressed: "被更新的用户消息覆盖",
  newer_message_queued: "队列中已有更新消息",
  ai_disabled: "全局 AI 回复关闭",
  ai_disabled_before_send: "发送前全局开关关闭",
  case_auto_reply_disabled: "当前服务单自动回复关闭",
  case_disabled_before_send: "发送前服务单开关关闭",
  inactive_case: "服务单已不在服务中",
  no_active_case: "无有效服务单",
  agent_generation_failed: "回复生成失败",
  send_failed: "发送失败",
  send_unknown: "发送结果未知",
  send_policy_suppressed: "发送策略拦截",
  reply_already_recorded: "回复已记录",
  non_chat_message: "非聊天消息",
  workflow_completed: "流程完成",
  archived_with_output: "归档成功且有产出",
  archived_no_output: "归档成功但无产出",
  archivist_failed: "归档失败",
  archivist_replay_failed: "人工重放失败",
  reminder_sent: "提醒已发送",
  reminder_generation_failed: "提醒生成失败",
  reminder_empty: "提醒生成内容为空",
  reminder_send_failed: "提醒发送失败",
  running: "运行中",
};

const CHANGE_KIND_LABELS: Record<string, string> = {
  checkin: "打卡记录",
  checkin_conflict: "打卡冲突",
  outcome: "阶段成效",
  profile_fact: "稳定画像",
  state_fact: "当前状态",
  personal_guidance: "个人指导",
  correction: "班主任纠错",
  faq_draft: "FAQ 草稿",
  user_summary: "用户历程小结",
  case_material: "优秀案例素材小结",
  case_draft: "优秀案例草稿",
  user_state: "用户档案",
};

export function RunDetailDrawer({
  run,
  onClose,
}: {
  run: RunDetailData | null;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<"overview" | "messages" | "changes">("overview");

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (run) {
      window.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [run, onClose]);

  if (!run) return null;

  const isArchivist = run.agent_type === "archivist";
  const hasMessages = Boolean(run.input_messages && run.input_messages.length > 0);
  const hasChanges = Boolean(run.changes && run.changes.length > 0);

  return (
    <div className="fixed inset-0 z-[80] flex justify-end overflow-hidden bg-slate-900/60 backdrop-blur-sm animate-fade-in-up">
      {/* Backdrop click */}
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />

      {/* Drawer Box */}
      <div className="relative z-10 flex w-full max-w-2xl flex-col bg-white shadow-2xl transition-transform duration-300 ease-out">
        {/* Drawer Header */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-900 px-6 py-4 text-white">
          <div className="flex items-center gap-3 min-w-0">
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl font-bold text-sm ${
                isArchivist ? "bg-amber-500/20 text-amber-300" : "bg-cyan-500/20 text-cyan-300"
              }`}
            >
              {isArchivist ? "档案" : "回复"}
            </span>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-extrabold tracking-tight truncate">
                  Run #{run.id} · {isArchivist ? "档案官归档" : "回复 Agent"}
                </h2>
                <StatusBadge status={run.status} />
              </div>
              <p className="mt-0.5 text-xs text-slate-400">
                开始于 {formatChinaShortDateTime(run.started_at)}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Drawer Navigation Tabs */}
        <div className="flex border-b border-slate-200 bg-slate-50 px-6">
          <button
            onClick={() => setActiveTab("overview")}
            className={`border-b-2 px-4 py-3 text-sm font-semibold transition-colors ${
              activeTab === "overview"
                ? "border-cyan-600 text-cyan-700 font-bold"
                : "border-transparent text-slate-600 hover:text-slate-900"
            }`}
          >
            运行概览与用量
          </button>
          {isArchivist && (
            <>
              <button
                onClick={() => setActiveTab("messages")}
                className={`flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-semibold transition-colors ${
                  activeTab === "messages"
                    ? "border-cyan-600 text-cyan-700 font-bold"
                    : "border-transparent text-slate-600 hover:text-slate-900"
                }`}
              >
                <MessageSquare className="h-4 w-4" />
                输入消息 ({run.input_messages?.length || run.input_count || 0})
              </button>
              <button
                onClick={() => setActiveTab("changes")}
                className={`flex items-center gap-1.5 border-b-2 px-4 py-3 text-sm font-semibold transition-colors ${
                  activeTab === "changes"
                    ? "border-cyan-600 text-cyan-700 font-bold"
                    : "border-transparent text-slate-600 hover:text-slate-900"
                }`}
              >
                <FileCheck className="h-4 w-4" />
                确定性变更 ({run.changes?.length || run.output_count || 0})
              </button>
            </>
          )}
        </div>

        {/* Drawer Body Scroll Area */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {activeTab === "overview" && (
            <>
              {/* Outcome Banner */}
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  执行终态
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <span className="text-base font-black text-slate-900">
                    {OUTCOME_LABELS[run.outcome ?? "running"] ?? run.outcome ?? "进行中"}
                  </span>
                  <span className="text-xs font-mono bg-white border border-slate-200 px-2 py-0.5 rounded text-slate-600">
                    {run.outcome || "running"}
                  </span>
                </div>
                {run.error_type && (
                  <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
                    <div className="flex items-center gap-1.5 font-bold">
                      <AlertTriangle className="h-4 w-4 text-rose-600 shrink-0" />
                      错误代码：{run.error_type}
                    </div>
                  </div>
                )}
              </div>

              {/* Service Case Info */}
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-bold uppercase tracking-wider text-slate-400">
                  关联对象
                </div>
                <div className="mt-2 flex items-center justify-between">
                  <div>
                    <div className="font-bold text-slate-900 text-base">
                      {run.user_name || "未知用户"}
                    </div>
                    <div className="text-xs text-slate-500 font-mono">
                      {run.case_no || "无服务单号"}
                    </div>
                  </div>
                  {run.service_case_id ? (
                    <Link
                      href={`/cases/${run.service_case_id}`}
                      target="_blank"
                      className="inline-flex items-center gap-1 rounded-lg bg-cyan-50 px-3 py-1.5 text-xs font-bold text-cyan-800 border border-cyan-200 hover:bg-cyan-100 transition-colors"
                    >
                      查看服务单 <ExternalLink className="h-3.5 w-3.5" />
                    </Link>
                  ) : (
                    <span className="text-xs text-slate-400">全局/系统无服务单任务</span>
                  )}
                </div>
              </div>

              {/* Metric Breakdown Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <MetricBox
                  icon={Cpu}
                  label="模型请求"
                  value={`${run.model_calls} 次`}
                  color="cyan"
                />
                <MetricBox
                  icon={Wrench}
                  label="工具调用"
                  value={`${run.tool_calls} 次`}
                  color="indigo"
                />
                <MetricBox
                  icon={Clock}
                  label="响应耗时"
                  value={formatLatency(run.latency_ms)}
                  color="amber"
                />
                <MetricBox
                  icon={Coins}
                  label="估算费用"
                  value={`¥${run.estimated_cost_yuan.toFixed(4)}`}
                  color="emerald"
                />
              </div>

              {/* Token Details */}
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <h3 className="text-sm font-bold text-slate-900 mb-3 flex items-center gap-1.5">
                  <Coins className="h-4 w-4 text-cyan-700" /> Token 消耗结构
                </h3>
                <div className="space-y-2 text-xs">
                  <div className="flex justify-between items-center py-1 border-b border-slate-100">
                    <span className="text-slate-600">总 Token 数量</span>
                    <span className="font-mono font-bold text-slate-900">
                      {run.total_tokens.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-1 border-b border-slate-100">
                    <span className="text-slate-600">输入 (Prompt) Token</span>
                    <span className="font-mono text-slate-700">
                      {run.prompt_tokens ? run.prompt_tokens.toLocaleString() : "估算占比中"}
                    </span>
                  </div>
                  <div className="flex justify-between items-center py-1">
                    <span className="text-slate-600">输出 (Completion) Token</span>
                    <span className="font-mono text-slate-700">
                      {run.completion_tokens ? run.completion_tokens.toLocaleString() : "估算占比中"}
                    </span>
                  </div>
                </div>
              </div>

              {/* Time Details */}
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4 text-xs text-slate-600 space-y-1.5">
                <div className="flex justify-between">
                  <span>Task Run ID:</span>
                  <span className="font-mono font-bold text-slate-800">#{run.id}</span>
                </div>
                <div className="flex justify-between">
                  <span>Task Kind:</span>
                  <span className="font-mono text-slate-800">{run.run_kind}</span>
                </div>
                <div className="flex justify-between">
                  <span>开始时间:</span>
                  <span>{formatChinaShortDateTime(run.started_at)}</span>
                </div>
                {run.finished_at && (
                  <div className="flex justify-between">
                    <span>结束时间:</span>
                    <span>{formatChinaShortDateTime(run.finished_at)}</span>
                  </div>
                )}
                {run.trigger_reason && (
                  <div className="flex justify-between border-t border-slate-200 pt-1.5 mt-1.5">
                    <span>触发原因:</span>
                    <span className="font-semibold text-slate-800">{run.trigger_reason}</span>
                  </div>
                )}
              </div>
            </>
          )}

          {activeTab === "messages" && (
            <div className="space-y-4">
              <p className="text-xs text-slate-500">
                本批次实际送入模型进行理解与归档分析的输入消息序列：
              </p>
              {hasMessages ? (
                <div className="space-y-3">
                  {run.input_messages!.map((msg) => (
                    <div
                      key={msg.id}
                      className="rounded-xl border border-slate-200 bg-white p-3.5 space-y-1.5 shadow-sm"
                    >
                      <div className="flex items-center justify-between text-xs">
                        <span
                          className={`px-2 py-0.5 rounded-full font-bold ${
                            msg.role === "用户"
                              ? "bg-cyan-100 text-cyan-800"
                              : msg.role === "班主任"
                              ? "bg-amber-100 text-amber-800"
                              : "bg-emerald-100 text-emerald-800"
                          }`}
                        >
                          {msg.role}
                        </span>
                        <span className="text-slate-400 font-mono">
                          #{msg.id} · {formatChinaShortDateTime(msg.created_at)}
                        </span>
                      </div>
                      <p className="text-xs leading-5 text-slate-800 whitespace-pre-wrap text-break">
                        {msg.content}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 py-12 text-center text-xs text-slate-400">
                  暂无捕获的输入消息序列
                </div>
              )}
            </div>
          )}

          {activeTab === "changes" && (
            <div className="space-y-4">
              <p className="text-xs text-slate-500">
                经由确定性代码校验通过并持久化落库的变动及丢弃提议：
              </p>
              {hasChanges ? (
                <div className="space-y-3">
                  {run.changes!.map((change, idx) => (
                    <div
                      key={idx}
                      className="rounded-xl border border-slate-200 bg-white p-4 space-y-2 shadow-sm"
                    >
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-0.5 rounded text-xs font-bold bg-emerald-100 text-emerald-800">
                          {CHANGE_KIND_LABELS[change.kind] || change.kind}
                        </span>
                        <span className="text-xs font-bold text-slate-900">{change.title}</span>
                      </div>
                      {change.after && (
                        <div className="rounded-lg bg-emerald-50/70 p-2.5 text-xs text-emerald-950">
                          <span className="font-bold mr-1">更新内容:</span>
                          <span className="whitespace-pre-wrap text-break">{change.after}</span>
                        </div>
                      )}
                      {change.evidence && (
                        <p className="text-xs text-slate-500 italic">依据: {change.evidence}</p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-300 py-12 text-center text-xs text-slate-400">
                  本批次无确定性数据写入（可能经检查无新增信息）
                </div>
              )}

              {run.dropped_items && run.dropped_items.length > 0 && (
                <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50/50 p-4">
                  <h4 className="text-xs font-bold text-amber-900 mb-2">
                    未通过确定性校验的提议 ({run.dropped_items.length} 项)
                  </h4>
                  <div className="space-y-1.5 text-xs text-amber-800">
                    {run.dropped_items.map((item, i) => (
                      <div key={i} className="flex items-start gap-1.5">
                        <span className="font-mono text-amber-600">•</span>
                        <span>
                          [{item.bucket || "常规"}]: {item.reason || "校验不通过"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Drawer Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-4 text-right">
          <button
            onClick={onClose}
            className="rounded-xl bg-slate-900 px-5 py-2 text-xs font-bold text-white hover:bg-slate-800 transition-colors"
          >
            关闭详情
          </button>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/20 px-2.5 py-0.5 text-xs font-bold text-emerald-300 border border-emerald-500/30">
        <CheckCircle2 className="h-3 w-3" /> 已完成
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/20 px-2.5 py-0.5 text-xs font-bold text-rose-300 border border-rose-500/30">
        <XCircle className="h-3 w-3" /> 失败
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/20 px-2.5 py-0.5 text-xs font-bold text-amber-300 border border-amber-500/30">
      <HelpCircle className="h-3 w-3" /> 运行中
    </span>
  );
}

function MetricBox({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: any;
  label: string;
  value: string;
  color: "cyan" | "indigo" | "amber" | "emerald";
}) {
  const colorMap = {
    cyan: "bg-cyan-50 text-cyan-700 border-cyan-100",
    indigo: "bg-indigo-50 text-indigo-700 border-indigo-100",
    amber: "bg-amber-50 text-amber-700 border-amber-100",
    emerald: "bg-emerald-50 text-emerald-700 border-emerald-100",
  };
  return (
    <div className={`rounded-xl border p-3 ${colorMap[color]}`}>
      <div className="flex items-center gap-1 text-[11px] font-bold opacity-75">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1 text-sm font-black tracking-tight">{value}</div>
    </div>
  );
}

function formatLatency(value?: number | null) {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}
