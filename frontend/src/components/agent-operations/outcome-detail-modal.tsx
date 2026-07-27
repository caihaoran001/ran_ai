"use client";

import { useEffect } from "react";
import { X, CheckCircle2, AlertTriangle, Info, Bot, UserCheck, ShieldAlert } from "lucide-react";

export type OutcomeDetail = {
  key: string;
  count: number;
  total: number;
};

const OUTCOME_META: Record<
  string,
  { label: string; tag: string; description: string; suggestion: string; icon: any; tone: "ok" | "warn" | "info" | "risk" }
> = {
  reply_sent: {
    label: "已发送回复",
    tag: "正常发送",
    description: "Agent 顺利完成了思考、工具调用与风险检查，并在企微社群中公开发送了定制化回复。",
    suggestion: "属于预期内的正向对话交互。",
    icon: CheckCircle2,
    tone: "ok",
  },
  intentional_no_reply: {
    label: "Agent 主动不回复",
    tag: "策略不回",
    description: "消息已被 Agent 评估，判定为用户打招呼收尾、纯表情/单字回应、班主任已在现场沟通或无需助教插嘴的场景。",
    suggestion: "避免助教频繁插话打扰用户体验，属于正常业务策略控制。",
    icon: Bot,
    tone: "info",
  },
  human_handoff: {
    label: "已转人工审核",
    tag: "触发风控",
    description: "触发了高风险关键词、表达极度不满/退款意图、或超出医疗健康边界，系统已自动阻断并向服务团队发送转人工通知。",
    suggestion: "请班主任或客服及时前往企微/后台查看并人工接管处理。",
    icon: ShieldAlert,
    tone: "risk",
  },
  handoff_throttled: {
    label: "转人工提醒处于节流期",
    tag: "节流防骚扰",
    description: "近期已针对该服务单触发过转人工通知，为防止对班主任手机频繁发打扰通知，当前消息拦截但不重复弹提醒。",
    suggestion: "班主任应查看之前已收到的风控工单。",
    icon: AlertTriangle,
    tone: "warn",
  },
  staff_preempted: {
    label: "班主任抢先回复",
    tag: "真人优先",
    description: "在 AI 思考或等待发送的数秒窗口内，班主任在群里直接回复了用户，Agent 自动撤回本次生成。",
    suggestion: "保证真人客服响应的绝对优先权，防止助教与班主任说话打架。",
    icon: UserCheck,
    tone: "info",
  },
  stale_reply_suppressed: {
    label: "被更新的用户消息覆盖",
    tag: "防旧消息过时",
    description: "用户连续发送了多条消息，导致上一条思考中的回复已过期，系统丢弃旧回复并开启针对最新全量文本的响应。",
    suggestion: "正常消息聚合防抖机制。",
    icon: Info,
    tone: "info",
  },
  ai_disabled: {
    label: "全局 AI 回复关闭",
    tag: "开关管控",
    description: "系统管理员在控制台中关闭了全局自动回复开关，所有消息停留在风控记录阶段，不向用户发消息。",
    suggestion: "如需恢复自动回复，请在后台系统设置中打开全局 AI 自动回复开关。",
    icon: AlertTriangle,
    tone: "warn",
  },
  agent_generation_failed: {
    label: "回复生成失败",
    tag: "模型异常",
    description: "大模型 API 调用超时、格式解析失败或 ReAct 工具链执行出错导致无法产出合规回复。",
    suggestion: "可在最近运行列表中查看具体的异常 traceback 代码并排查模型供应商连通性。",
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

  const meta = OUTCOME_META[outcome.key] || {
    label: outcome.key,
    tag: "业务终态",
    description: `该轮次最终落地为 ${outcome.key} 终态。`,
    suggestion: "如需深入排查请在【最近运行】筛选对应的状态分析日志。",
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
              <p className="text-xs text-slate-400 font-mono">{outcome.key}</p>
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
              <div className="text-xs text-slate-500 font-semibold">占总轮次比例</div>
              <div className="text-2xl font-black text-cyan-800 mt-0.5">
                {percent}%
              </div>
            </div>
          </div>

          {/* Meaning Description */}
          <div className="space-y-1.5">
            <h4 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
              终态说明
            </h4>
            <p className="text-xs leading-5 text-slate-700 bg-slate-50/70 p-3 rounded-xl border border-slate-100">
              {meta.description}
            </p>
          </div>

          {/* Operation Suggestion */}
          <div className="space-y-1.5">
            <h4 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
              运营处置建议
            </h4>
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
