"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  X,
  Clock,
  Coins,
  Wrench,
  AlertTriangle,
  ExternalLink,
  MessageSquare,
  FileCheck,
  CheckCircle2,
  XCircle,
  HelpCircle,
  BookOpen,
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

type AgentConversationMessage = {
  id: number;
  role: string;
  content: string;
  message_type: string;
  status: string;
  created_at: string;
  sent_at?: string | null;
  error?: string | null;
};

type AgentKnowledgeDecision = {
  decision: string;
  original_query?: string | null;
  query?: string | null;
  hit_count?: number | null;
  skipped_product_terms?: string[];
  hits?: AgentKnowledgeHit[];
};

type AgentKnowledgeHit = {
  rank: number;
  chunk_id: number;
  chunk_uid: string;
  title: string;
  source_type: string;
  updated_at?: string | null;
  content_excerpt: string;
};

type ArchivistDroppedItem = {
  bucket?: string;
  item_id?: string;
  item_ids?: string[];
  target_date?: string;
  reason?: string;
  invalid_fields?: string[];
};

export type AgentFailureDetail = {
  stage: string;
  error_type?: string | null;
  summary: string;
  retryable: boolean;
  timeout_seconds?: number | null;
  elapsed_ms?: number | null;
  failed_at?: string | null;
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
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_yuan: number;
  input_count: number;
  output_count: number;
  latency_ms?: number | null;
  error_type?: string | null;
  started_at: string;
  finished_at?: string | null;
  input_message?: AgentConversationMessage | null;
  output_message?: AgentConversationMessage | null;
  knowledge?: AgentKnowledgeDecision | null;
  failure?: AgentFailureDetail | null;
  detail_loading?: boolean;
  detail_error?: string | null;
  // Archivist extra fields
  trigger_reason?: string | null;
  input_source?: "exact" | "watermark_range" | "unavailable";
  input_messages?: ArchivistInputMessage[];
  changes?: ArchivistBatchChange[];
  changes_detail_available?: boolean;
  dropped_items?: ArchivistDroppedItem[];
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
  checkin_days: "打卡记录",
  outcomes: "阶段成效",
  profile_facts: "稳定画像",
  state_facts: "当前状态",
  personal_guidance_count: "个人指导",
  corrections: "班主任纠错",
  faq_drafts: "FAQ 草稿",
  memory_updates: "用户小结 / 案例素材",
  user_summary_updates: "用户历程小结",
  case_material_updates: "优秀案例素材小结",
  case_drafts: "优秀案例草稿",
};

const CHECKIN_FIELD_LABELS: Record<string, string> = {
  weight: "体重",
  waist: "腰围",
  sleep: "睡眠",
  water: "饮水",
  exercise: "运动",
  diet: "饮食",
  mood: "情绪",
  morning_c: "早 C",
  noon_b: "午 B",
  night_a: "晚 A",
  "product_schedule.morning_c": "早 C",
  "product_schedule.noon_b": "午 B",
  "product_schedule.night_a": "晚 A",
  "product_schedule.summary": "产品执行",
};

const TRIGGER_LABELS: Record<string, string> = {
  fast_key: "发现值得归档的信息，用户停说约 3 分钟后触发",
  B_latency: "未归档信息达到最长等待时间",
  C_count: "未归档消息累计达到批量阈值",
  manual_replay: "人工重新处理隔离消息",
};

function describeDroppedItem(item: ArchivistDroppedItem): {
  title: string;
  detail: string;
} {
  const datePrefix = item.target_date ? `${item.target_date} ` : "";
  const itemCount = item.item_ids?.length ?? (item.item_id ? 1 : 0);
  const itemSuffix = itemCount > 0 ? `，涉及 ${itemCount} 项回答` : "";

  if (item.bucket === "checkin" && item.reason === "bad_args") {
    return {
      title: `${datePrefix}打卡内容未保存`,
      detail: `系统参数格式校验失败${itemSuffix}，本项没有写入打卡记录。`,
    };
  }
  if (item.reason === "checkin_date_not_in_batch") {
    return {
      title: `${datePrefix}打卡日期未匹配`,
      detail: "上报日期不在这批聊天覆盖的日期内，因此没有写入。",
    };
  }
  if (item.reason === "checklist_not_in_whitelist") {
    return {
      title: `${datePrefix}清单回答未保存`,
      detail: "回答的项目不在当时有效的每日清单中。",
    };
  }
  if (item.reason === "followup_not_in_whitelist") {
    return {
      title: `${datePrefix}追问回答未保存`,
      detail: "回答没有匹配到已发送的追问项目。",
    };
  }
  if (item.reason === "followup_empty_answer" || item.reason === "checklist_empty_answer") {
    return {
      title: `${datePrefix}空回答未保存`,
      detail: "没有识别到明确回答，因此未写入打卡记录。",
    };
  }
  if (item.reason === "duplicate_memory_report" || item.reason === "duplicate_case_candidate") {
    return {
      title: "重复内容未采用",
      detail: "同一批整理已经保留了一份对应内容。",
    };
  }
  return {
    title: "内容未写入档案",
    detail: "系统校验未通过，可结合本次输入消息进一步核对。",
  };
}

export function RunDetailDrawer({
  run,
  onClose,
}: {
  run: RunDetailData | null;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<"overview" | "messages" | "changes">("overview");

  useEffect(() => {
    if (run) {
      setActiveTab("overview");
    }
  }, [run]);

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
  const hasInputMessagesList = Boolean(run.input_messages && run.input_messages.length > 0);
  const appliedChanges = (run.changes ?? []).filter(
    (change) => change.action !== "not_applied",
  );
  const notAppliedChanges = (run.changes ?? []).filter(
    (change) => change.action === "not_applied",
  );
  const hasChangesList = appliedChanges.length > 0;
  const msgCount = Math.max(run.input_count || 0, run.input_messages?.length ?? 0);
  const changeCount = appliedChanges.length || run.output_count || 0;

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
                  {isArchivist ? "档案整理详情" : "群内回复详情"}
                </h2>
                <StatusBadge status={run.status} />
              </div>
              <p className="mt-0.5 text-xs text-slate-400">
                {formatChinaShortDateTime(run.started_at)}
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
            概览
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
                输入消息 ({msgCount})
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
                整理出的信息 ({changeCount})
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
                <div className="text-xs font-bold text-slate-400">
                  这次的结果
                </div>
                <div className="mt-1">
                  <span className="text-base font-black text-slate-900">
                    {OUTCOME_LABELS[run.outcome ?? "running"] ?? "进行中"}
                  </span>
                </div>
                {(run.failure || run.error_type) && (
                  <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
                    <div className="flex items-center gap-1.5 font-bold">
                      <AlertTriangle className="h-4 w-4 text-rose-600 shrink-0" />
                      {run.failure?.summary || "运行过程出现异常，本轮未完成"}
                    </div>
                    {run.failure?.elapsed_ms != null && (
                      <div className="mt-1.5 pl-5 text-rose-700">
                        失败前已运行 {formatLatency(run.failure.elapsed_ms)}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {run.agent_type === "reply" && (
                <ReplyConversationEvidence run={run} />
              )}

              {/* Service Case Info */}
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-bold text-slate-400">
                  服务对象
                </div>
                <div className="mt-2 flex items-center justify-between">
                  <div>
                    <div className="font-bold text-slate-900 text-base">
                      {run.user_name || "未知用户"}
                    </div>
                    {run.case_no && (
                      <div className="text-xs text-slate-500">
                        {run.case_no}
                      </div>
                    )}
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
                    <span className="text-xs text-slate-400">系统任务，无关联用户</span>
                  )}
                </div>
              </div>

              {/* Metric Breakdown Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                <MetricBox
                  icon={Clock}
                  label="耗时"
                  value={formatLatency(run.latency_ms)}
                  color="amber"
                />
                <MetricBox
                  icon={Wrench}
                  label="用了几次工具"
                  value={`${run.tool_calls} 次`}
                  color="indigo"
                />
                <MetricBox
                  icon={Coins}
                  label="花费"
                  value={`¥${run.estimated_cost_yuan.toFixed(4)}`}
                  color="emerald"
                />
              </div>

              {/* Time Details */}
              <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4 text-xs text-slate-600 space-y-1.5">
                <div className="flex justify-between">
                  <span>开始时间</span>
                  <span>{formatChinaShortDateTime(run.started_at)}</span>
                </div>
                {run.finished_at && (
                  <div className="flex justify-between">
                    <span>结束时间</span>
                    <span>{formatChinaShortDateTime(run.finished_at)}</span>
                  </div>
                )}
                {run.trigger_reason && (
                  <div className="flex justify-between border-t border-slate-200 pt-1.5 mt-1.5">
                    <span>为什么整理这一批</span>
                    <span className="max-w-[70%] text-right font-semibold text-slate-800">
                      {formatTriggerReason(run.trigger_reason)}
                    </span>
                  </div>
                )}
              </div>
            </>
          )}

          {activeTab === "messages" && (
            <div className="space-y-4">
              <p className="text-xs text-slate-500">
                这次整理读了下面这些聊天记录：
              </p>
              {hasInputMessagesList ? (
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
                <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-6 text-center text-xs text-amber-900 space-y-1.5">
                  <div className="font-bold">
                    {run.input_count > 0
                      ? `这次读了 ${run.input_count} 条聊天`
                      : "这次没有聊天可读"}
                  </div>
                  <p className="text-slate-600 leading-5">
                    {run.input_count > 0
                      ? "这里没保存聊天原文，可从「整理档案」里的每次整理记录点开查看完整内容。"
                      : "这一批还没确定读哪些聊天就结束了。"}
                  </p>
                </div>
              )}
            </div>
          )}

          {activeTab === "changes" && (
            <div className="space-y-4">
              <p className="text-xs text-slate-500">
                这次从聊天里整理出、并存进档案的信息：
              </p>
              {hasChangesList ? (
                <div className="space-y-3">
                  {appliedChanges.map((change, idx) => (
                    <ChangeDetail key={`${change.kind}-${change.title}-${idx}`} change={change} />
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-6 text-center text-xs text-slate-600 space-y-1">
                  <div className="font-bold text-slate-800">
                    {run.output_count > 0
                      ? `整理出 ${run.output_count} 项新信息`
                      : "这次没有整理出新信息"}
                  </div>
                  <p className="text-slate-500">
                    {run.output_count > 0
                      ? "可从「整理档案」里的每次整理记录点开查看具体内容。"
                      : "检查后发现没有值得新增的信息。"}
                  </p>
                </div>
              )}

              {notAppliedChanges.length > 0 && (
                <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50/50 p-4">
                  <h4 className="mb-2 text-xs font-bold text-amber-900">
                    发现但没有采纳（与已有信息冲突，{notAppliedChanges.length} 项）
                  </h4>
                  <div className="space-y-3">
                    {notAppliedChanges.map((change, idx) => (
                      <ChangeDetail
                        key={`${change.kind}-${change.title}-not-applied-${idx}`}
                        change={change}
                      />
                    ))}
                  </div>
                </div>
              )}

              {run.dropped_items && run.dropped_items.length > 0 && (
                <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50/50 p-4">
                  <h4 className="flex items-center gap-1.5 text-xs font-bold text-amber-950">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-700" />
                    未写入档案（{run.dropped_items.length} 项）
                  </h4>
                  <p className="mt-1 text-xs leading-5 text-amber-900">
                    以下内容经过识别，但没有保存到业务档案。
                  </p>
                  <div className="mt-3 divide-y divide-amber-200/80 border-y border-amber-200/80">
                    {run.dropped_items.map((item, i) => {
                      const copy = describeDroppedItem(item);
                      return (
                        <div key={i} className="py-2.5 text-xs">
                          <div className="font-bold text-amber-950">{copy.title}</div>
                          <div className="mt-0.5 leading-5 text-amber-900">{copy.detail}</div>
                        </div>
                      );
                    })}
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

function ReplyConversationEvidence({ run }: { run: RunDetailData }) {
  if (run.detail_loading) {
    return (
      <section className="rounded-xl border border-cyan-200 bg-cyan-50/40 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-bold text-slate-900">
          <MessageSquare className="h-4 w-4 text-cyan-700" />
          本轮对话
        </h3>
        <div className="mt-4 space-y-3 animate-pulse">
          <div className="h-16 rounded-lg bg-white/80" />
          <div className="h-20 rounded-lg bg-white/80" />
        </div>
      </section>
    );
  }

  if (run.detail_error) {
    return (
      <section className="rounded-xl border border-rose-200 bg-rose-50 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-bold text-rose-900">
          <AlertTriangle className="h-4 w-4 text-rose-600" />
          对话原文加载失败
        </h3>
        <p className="mt-2 text-xs leading-5 text-rose-800">
          {run.detail_error}
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="flex items-center gap-1.5 text-sm font-bold text-slate-900">
        <MessageSquare className="h-4 w-4 text-cyan-700" />
        本轮对话
      </h3>
      <p className="mt-1 text-xs text-slate-500">
        用户说了什么、AI 又回了什么。
      </p>

      <div className="mt-4 space-y-3">
        {run.input_message ? (
          <ConversationMessageCard
            label="用户"
            message={run.input_message}
            tone="user"
          />
        ) : (
          <ConversationEmptyState
            title="没找到用户消息"
            description={
              run.input_count > 0
                ? "记录里有这条消息，但原文暂时读不出来。"
                : "这一轮没有用户消息。"
            }
            tone="warn"
          />
        )}

        {run.output_message ? (
          <ConversationMessageCard
            label="AI 回复"
            message={run.output_message}
            tone="assistant"
          />
        ) : (
          <ConversationEmptyState
            title={
              run.output_count > 0
                ? "没找到发出的回复"
                : "这一轮没有回复用户"
            }
            description={
              run.output_count > 0
                ? "记录里有这条回复，但原文暂时读不出来。"
                : `结果：${
                    OUTCOME_LABELS[run.outcome ?? "running"] ?? "进行中"
                  }`
            }
            tone={run.output_count > 0 ? "warn" : "neutral"}
          />
        )}

        <KnowledgeDecisionCard knowledge={run.knowledge} />
      </div>
    </section>
  );
}

const KNOWLEDGE_DECISION_LABELS: Record<string, string> = {
  searched: "已按改写问题检索",
  skipped: "判定无需检索，未查知识库",
  fallback: "改写缺失，退回原问题检索",
};

function KnowledgeDecisionCard({
  knowledge,
}: {
  knowledge?: AgentKnowledgeDecision | null;
}) {
  if (!knowledge) return null;

  const skipped = knowledge.decision === "skipped";
  const missedProductTerms = knowledge.skipped_product_terms ?? [];
  const hits = knowledge.hits ?? [];
  // 提到产品名却没查库 = 产品规则这轮可能没进提示词，是最需要人工回看的情况。
  const isSuspect = skipped && missedProductTerms.length > 0;
  const emptyHit = knowledge.hit_count === 0;

  return (
    <article
      className={`rounded-lg border p-3.5 ${
        isSuspect
          ? "border-amber-300 bg-amber-50/70"
          : "border-slate-200 bg-slate-50/70"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="flex items-center gap-1.5 font-bold text-slate-900">
          <BookOpen className="h-3.5 w-3.5 text-cyan-700" />
          参考问答检索
        </span>
        <span
          className={`rounded-full border px-2 py-0.5 font-semibold ${
            skipped
              ? "border-slate-300 bg-white text-slate-600"
              : "border-cyan-200 bg-white text-cyan-700"
          }`}
        >
          {KNOWLEDGE_DECISION_LABELS[knowledge.decision] ?? knowledge.decision}
        </span>
        {!skipped && typeof knowledge.hit_count === "number" && (
          <span
            className={`rounded-full border px-2 py-0.5 font-semibold ${
              emptyHit
                ? "border-amber-300 bg-white text-amber-700"
                : "border-emerald-200 bg-white text-emerald-700"
            }`}
          >
            {emptyHit ? "命中 0 条" : `命中 ${knowledge.hit_count} 条`}
          </span>
        )}
      </div>

      <div className="mt-2 space-y-1.5 text-xs text-slate-700">
        {knowledge.original_query && (
          <p className="break-words">
            <span className="text-slate-500">用户原问：</span>
            {knowledge.original_query}
          </p>
        )}
        {knowledge.query && knowledge.query !== knowledge.original_query && (
          <p className="break-words">
            <span className="text-slate-500">改写问题：</span>
            {knowledge.query}
          </p>
        )}
      </div>

      {hits.length > 0 && (
        <ol className="mt-3 space-y-2" aria-label="实际命中的参考问答">
          {hits.map((hit) => (
            <li
              key={`${hit.chunk_id}-${hit.rank}`}
              className="rounded-md border border-slate-200 bg-white px-3 py-2"
            >
              <div className="flex items-start gap-2">
                <span className="flex h-5 min-w-5 items-center justify-center rounded bg-cyan-50 px-1 text-[10px] font-black text-cyan-800">
                  {hit.rank}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="break-words text-xs font-bold text-slate-900">
                    {hit.title}
                  </p>
                  <p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-slate-500">
                    <span>知识 #{hit.chunk_id}</span>
                    <span>{hit.source_type === "faq" ? "参考问答" : hit.source_type}</span>
                    {hit.updated_at && (
                      <span>更新于 {formatChinaShortDateTime(hit.updated_at)}</span>
                    )}
                  </p>
                  <div className="mt-2 border-l-2 border-cyan-100 pl-2">
                    <p className="text-[10px] font-bold tracking-wide text-slate-400">
                      当时召回内容
                    </p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-slate-700">
                      {hit.content_excerpt}
                    </p>
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      {!skipped &&
        knowledge.hit_count != null &&
        knowledge.hit_count > 0 &&
        hits.length === 0 && (
          <p className="mt-2 text-xs text-slate-500">
            这条历史运行只保存了命中数量，当时的具体知识未留存。
          </p>
        )}

      {isSuspect && (
        <p className="mt-2 flex items-start gap-1.5 text-xs font-semibold text-amber-800">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          用户消息提到 {missedProductTerms.join("、")}，但本轮没查知识库，
          产品规则可能没进提示词——请回看回复是否准确。
        </p>
      )}

      {emptyHit && !skipped && (
        <p className="mt-2 text-xs text-amber-800">
          查了知识库但没有匹配内容，说明这类问题缺 FAQ，可考虑补充。
        </p>
      )}
    </article>
  );
}

function ConversationMessageCard({
  label,
  message,
  tone,
}: {
  label: string;
  message: AgentConversationMessage;
  tone: "user" | "assistant";
}) {
  const statusLabel = MESSAGE_STATUS_LABELS[message.status] ?? message.status;
  const isDeliveryProblem =
    message.status === "failed" || message.status === "unknown";
  return (
    <article
      className={`rounded-lg border p-3.5 ${
        tone === "user"
          ? "border-cyan-200 bg-cyan-50/60"
          : isDeliveryProblem
            ? "border-rose-200 bg-rose-50/60"
            : "border-emerald-200 bg-emerald-50/60"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-bold text-slate-900">{label}</span>
          <span className="text-slate-500">{message.role}</span>
          {tone === "assistant" && (
            <span
              className={`rounded-full border px-2 py-0.5 font-semibold ${
                isDeliveryProblem
                  ? "border-rose-200 bg-white text-rose-700"
                  : "border-emerald-200 bg-white text-emerald-700"
              }`}
            >
              {statusLabel}
            </span>
          )}
        </div>
        <span className="font-mono text-slate-400">
          #{message.id} · {formatChinaShortDateTime(message.created_at)}
        </span>
      </div>
      <p className="mt-2 whitespace-pre-wrap text-break text-sm leading-6 text-slate-900">
        {message.content || "（空消息）"}
      </p>
      {message.error && isDeliveryProblem && (
        <p className="mt-2 rounded-md bg-white/80 px-2.5 py-2 text-xs leading-5 text-rose-800">
          发送错误：{message.error}
        </p>
      )}
    </article>
  );
}

function ConversationEmptyState({
  title,
  description,
  tone,
}: {
  title: string;
  description: string;
  tone: "neutral" | "warn";
}) {
  return (
    <div
      className={`rounded-lg border px-3.5 py-3 text-xs ${
        tone === "warn"
          ? "border-amber-200 bg-amber-50 text-amber-950"
          : "border-slate-200 bg-slate-50 text-slate-700"
      }`}
    >
      <div className="font-bold">{title}</div>
      <p className="mt-1 leading-5 text-slate-600">{description}</p>
    </div>
  );
}

const MESSAGE_STATUS_LABELS: Record<string, string> = {
  received: "已接收",
  pending: "等待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "发送失败",
  unknown: "发送结果未知",
};

function ChangeDetail({ change }: { change: ArchivistBatchChange }) {
  const notApplied = change.action === "not_applied";
  const kindLabel = CHANGE_KIND_LABELS[change.kind] || change.kind;

  return (
    <div
      className={`rounded-xl border bg-white p-4 ${
        notApplied ? "border-amber-200" : "border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={`status-pill ${notApplied ? "pill-warn" : "pill-ok"}`}>
          {notApplied ? "未写入" : actionLabel(change.action)}
        </span>
        <span className="text-xs font-bold text-slate-900">{kindLabel}</span>
        {change.title && change.title !== change.kind && change.title !== kindLabel && (
          <span className="text-xs text-slate-600">
            {formatChangeTitle(change.title)}
          </span>
        )}
      </div>

      {change.items.length > 0 && (
        <dl className="mt-3 divide-y divide-slate-100 border-y border-slate-100">
          {change.items.map((item, index) => {
            const label =
              item.label ||
              CHECKIN_FIELD_LABELS[item.field || ""] ||
              item.field ||
              "更新项";
            const nextValue = item.value ?? item.after;
            return (
              <div
                key={`${item.field || item.label || "item"}-${index}`}
                className="grid gap-1 py-2.5 text-xs sm:grid-cols-[7rem_minmax(0,1fr)]"
              >
                <dt className="font-semibold text-slate-600">{label}</dt>
                <dd className="min-w-0 text-slate-900">
                  {nextValue ? (
                    <span className="text-break whitespace-pre-wrap">
                      {notApplied ? `聊天提议：${nextValue}` : nextValue}
                    </span>
                  ) : (
                    "—"
                  )}
                  {item.before && (
                    <span className="mt-1 block text-slate-500">
                      {notApplied ? "已有表单值" : "更新前"}：{item.before}
                    </span>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      )}

      {(change.before || change.after) && (
        <div className="mt-3 grid gap-2 text-xs">
          {change.before && (
            <div className="rounded-lg bg-slate-100 px-3 py-2 text-slate-700">
              <span className="mr-2 font-semibold">更新前</span>
              <span className="text-break whitespace-pre-wrap">
                {formatAuditValue(change.before)}
              </span>
            </div>
          )}
          {change.after && (
            <div
              className={`rounded-lg px-3 py-2 ${
                notApplied
                  ? "bg-amber-100/70 text-amber-950"
                  : "bg-emerald-50 text-emerald-950"
              }`}
            >
              <span className="mr-2 font-semibold">
                {notApplied ? "聊天提议" : "更新后"}
              </span>
              <span className="text-break whitespace-pre-wrap">
                {formatAuditValue(change.after)}
              </span>
            </div>
          )}
        </div>
      )}

      {change.evidence && (
        <p className="mt-3 text-xs leading-5 text-slate-600">依据：{change.evidence}</p>
      )}
    </div>
  );
}

function actionLabel(action: string) {
  return (
    {
      created: "新建",
      added: "新增",
      updated: "更新",
      recorded: "已记录",
      count_only: "已写入",
    }[action] || "已写入"
  );
}

function formatChangeTitle(title: string) {
  if (!title) return "";
  return title
    .replace(/\s*·\s*value$/i, "")
    .replace(/\.value$/i, "");
}

function formatTriggerReason(reason: string) {
  return reason
    .split(",")
    .map((item) => TRIGGER_LABELS[item] || item)
    .join("；");
}

function formatAuditValue(value: string) {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.entries(parsed)
        .map(([key, item]) => {
          const label =
            {
              question: "问题",
              answer: "参考回答",
              background: "背景",
              case: "案例正文",
            }[key] || key;
          return `${label}：${String(item ?? "")}`;
        })
        .join("\n");
    }
  } catch {
    // 普通文本直接展示。
  }
  return value;
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
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${value} 毫秒`;
}
