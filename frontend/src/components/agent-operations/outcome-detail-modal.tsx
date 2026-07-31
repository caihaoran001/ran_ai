"use client";

import { useEffect } from "react";
import { X, CheckCircle2, AlertTriangle, Info, Bot, UserCheck, ShieldAlert, PauseCircle } from "lucide-react";

export type OutcomeDetail = {
  key: string;
  count: number;
  total: number;
};

type Meta = {
  label: string;
  description: string;
  suggestion: string;
  icon: typeof CheckCircle2;
  tone: "ok" | "warn" | "info" | "risk";
};

const OUTCOME_META: Record<string, Meta> = {
  reply_sent: {
    label: "正常回复了用户",
    description: "AI 读完群里的消息、判断需要回应，并在群里发出了一条针对性的回复。",
    suggestion: "这是最常见的正常情况，不用特别处理。",
    icon: CheckCircle2,
    tone: "ok",
  },
  reply_already_recorded: {
    label: "回复已经发过",
    description: "这条回复此前已经发送过，系统避免了重复发送。",
    suggestion: "属于正常的防重复机制，不用处理。",
    icon: CheckCircle2,
    tone: "ok",
  },
  intentional_no_reply: {
    label: "判断无需插话",
    description: "AI 看过消息后，认为不需要回应，比如用户只是打招呼收尾、发了个表情，或者班主任已经在沟通了。",
    suggestion: "这样可以避免 AI 频繁插话打扰用户，是正常的。",
    icon: Bot,
    tone: "info",
  },
  human_handoff: {
    label: "已通知班主任接手",
    description: "这条消息可能涉及健康风险、强烈不满或退款意向，超出了 AI 能处理的范围，已经自动提醒班主任 / 客服来接手。",
    suggestion: "请班主任或客服尽快到群里或后台查看并处理。",
    icon: ShieldAlert,
    tone: "risk",
  },
  handoff_already_recorded: {
    label: "已通知过班主任",
    description: "针对这位用户此前已经发过转人工通知，这次不再重复通知。",
    suggestion: "查看之前那条转人工提醒即可。",
    icon: UserCheck,
    tone: "warn",
  },
  handoff_throttled: {
    label: "刚提醒过，暂不重复打扰",
    description: "短时间内已经提醒过班主任一次，为了不频繁打扰，这次先不重复弹通知。",
    suggestion: "查看之前那条转人工提醒即可。",
    icon: AlertTriangle,
    tone: "warn",
  },
  staff_preempted: {
    label: "班主任先回复了",
    description: "在 AI 准备回复的几秒里，班主任已经在群里回复了用户，AI 就自动取消了这次回复。",
    suggestion: "保证真人回复优先，避免 AI 和班主任撞车，是正常的。",
    icon: UserCheck,
    tone: "info",
  },
  stale_reply_suppressed: {
    label: "用户又发了新消息，旧回复作废",
    description: "用户连续发了好几条消息，之前那条还在酝酿的回复就过时了，系统丢掉旧的，改成针对最新内容一起回复。",
    suggestion: "这是正常的消息合并机制，不用处理。",
    icon: Info,
    tone: "info",
  },
  newer_message_queued: {
    label: "已有更新的消息在排队",
    description: "已经有一条更新的消息在等待处理，这条就不再单独回复了。",
    suggestion: "正常机制，不用处理。",
    icon: Info,
    tone: "info",
  },
  ai_disabled: {
    label: "全局自动回复被关闭",
    description: "后台的「全局自动回复」总开关被关掉了，所有消息都只记录、不回复。",
    suggestion: "如果想恢复自动回复，去后台系统设置里打开总开关。",
    icon: PauseCircle,
    tone: "warn",
  },
  ai_disabled_before_send: {
    label: "发送前全局开关被关",
    description: "回复已经准备好，但就在发送前，全局自动回复开关被关掉了，于是没有发出去。",
    suggestion: "确认是否需要打开全局自动回复开关。",
    icon: PauseCircle,
    tone: "warn",
  },
  case_auto_reply_disabled: {
    label: "该用户的自动回复被关闭",
    description: "这位用户单独被关掉了自动回复，所以 AI 不回复 TA 的消息。",
    suggestion: "如需恢复，去这位用户的服务单里打开自动回复。",
    icon: PauseCircle,
    tone: "warn",
  },
  case_disabled_before_send: {
    label: "发送前该用户开关被关",
    description: "回复准备好后、发送前，这位用户的自动回复被关掉了，于是没有发出去。",
    suggestion: "确认这位用户是否需要开启自动回复。",
    icon: PauseCircle,
    tone: "warn",
  },
  inactive_case: {
    label: "服务已结束",
    description: "这位用户的服务期已经结束，AI 不再自动回复。",
    suggestion: "正常情况，不用处理。",
    icon: PauseCircle,
    tone: "info",
  },
  no_active_case: {
    label: "没有进行中的服务",
    description: "这条消息没有对应正在进行的服务，AI 不做处理。",
    suggestion: "正常情况，不用处理。",
    icon: PauseCircle,
    tone: "info",
  },
  non_chat_message: {
    label: "不是聊天消息",
    description: "这条不是需要回复的聊天内容（比如系统通知类），AI 不做处理。",
    suggestion: "正常情况，不用处理。",
    icon: Info,
    tone: "info",
  },
  send_policy_suppressed: {
    label: "被发送策略拦下",
    description: "回复生成了，但根据发送规则这次不适合发出去，于是被拦下了。",
    suggestion: "一般是正常的策略控制，如频繁出现可留意。",
    icon: PauseCircle,
    tone: "info",
  },
  agent_generation_failed: {
    label: "回复没能生成",
    description: "AI 在思考回复时出错了，比如模型响应超时或出现异常，最终没能生成合格的回复。",
    suggestion: "如果这类情况变多，建议排查 AI 服务是否正常。",
    icon: AlertTriangle,
    tone: "risk",
  },
  send_failed: {
    label: "回复没发出去",
    description: "回复已经生成，但发送到群里时失败了。",
    suggestion: "如果频繁出现，建议排查企微通道是否正常。",
    icon: AlertTriangle,
    tone: "risk",
  },
  send_unknown: {
    label: "发送结果不明",
    description: "回复发出后没有收到明确的成功确认，无法确定用户是否收到。",
    suggestion: "如果频繁出现，建议排查企微通道是否正常。",
    icon: AlertTriangle,
    tone: "risk",
  },
};

export function OutcomeDetailModal({
  outcome,
  onClose,
}: {
  outcome: OutcomeDetail | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (outcome) {
      window.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [outcome, onClose]);

  if (!outcome) return null;

  const meta: Meta = OUTCOME_META[outcome.key] || {
    label: "其它情况",
    description: "这是一种较少出现的处理结果。",
    suggestion: "如需了解具体情况，可在最近的处理记录里点开查看。",
    icon: Info,
    tone: "info",
  };

  const Icon = meta.icon;
  const percent = outcome.total ? ((outcome.count / outcome.total) * 100).toFixed(1) : "0";

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm animate-fade-in-up">
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />

      <div className="relative z-10 w-full max-w-md rounded-2xl bg-white shadow-2xl overflow-hidden border border-slate-200">
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-900 px-6 py-4 text-white">
          <div className="flex items-center gap-3">
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl font-bold ${
                meta.tone === "ok"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : meta.tone === "risk"
                  ? "bg-rose-500/20 text-rose-300"
                  : meta.tone === "warn"
                  ? "bg-amber-500/20 text-amber-300"
                  : "bg-cyan-500/20 text-cyan-300"
              }`}
            >
              <Icon className="h-5 w-5" />
            </span>
            <div>
              <h3 className="text-base font-extrabold">{meta.label}</h3>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* Stats Bar */}
          <div className="flex items-center justify-between rounded-xl bg-slate-50 border border-slate-100 p-4">
            <div>
              <div className="text-xs text-slate-500 font-semibold">发生次数</div>
              <div className="text-2xl font-black text-slate-900 mt-0.5">
                {outcome.count} 次
              </div>
            </div>
            <div className="text-right">
              <div className="text-xs text-slate-500 font-semibold">占全部对话</div>
              <div className="text-2xl font-black text-cyan-800 mt-0.5">
                {percent}%
              </div>
            </div>
          </div>

          {/* Meaning Description */}
          <div className="space-y-1.5">
            <h4 className="text-xs font-bold text-slate-900">这是什么情况</h4>
            <p className="text-xs leading-5 text-slate-700 bg-slate-50/70 p-3 rounded-xl border border-slate-100">
              {meta.description}
            </p>
          </div>

          {/* Operation Suggestion */}
          <div className="space-y-1.5">
            <h4 className="text-xs font-bold text-slate-900">要不要处理</h4>
            <p className="text-xs leading-5 text-slate-700 bg-amber-50/60 p-3 rounded-xl border border-amber-100">
              {meta.suggestion}
            </p>
          </div>
        </div>

        <div className="border-t border-slate-100 bg-slate-50 px-6 py-3.5 text-right">
          <button
            onClick={onClose}
            className="rounded-xl bg-slate-900 px-5 py-2 text-xs font-bold text-white hover:bg-slate-800 transition-colors"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
