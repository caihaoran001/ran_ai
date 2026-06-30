"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { Bot, CalendarCheck, Sunrise, BellRing, Send, Workflow } from "lucide-react";

type ToggleKey =
  | "ai_auto_reply_enabled"
  | "ai_agent_enabled"
  | "checkin_link_enabled"
  | "morning_push_enabled"
  | "inactive_wake_enabled";

function Row({
  serviceCaseId,
  field,
  initial,
  icon: Icon,
  title,
  desc,
  disabled = false,
  onUpdated,
}: {
  serviceCaseId: number;
  field: ToggleKey;
  initial: boolean;
  icon: typeof Bot;
  title: string;
  desc: string;
  disabled?: boolean;
  onUpdated?: (value: boolean) => void;
}) {
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (disabled) return;
    setBusy(true);
    const next = !on;
    const res = await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ [field]: next }),
    });
    if (res.ok) {
      setOn(next);
      onUpdated?.(next);
    }
    setBusy(false);
  }

  return (
    <div className="flex items-center justify-between gap-4 py-3">
      <div className="flex min-w-0 items-start gap-3">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-900">{title}</div>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">{desc}</p>
        </div>
      </div>
      <label className="relative inline-flex shrink-0 cursor-pointer items-center">
        <input type="checkbox" checked={on} disabled={busy || disabled} onChange={toggle} className="sr-only peer" />
        <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300 peer-disabled:cursor-not-allowed peer-disabled:opacity-50" />
      </label>
    </div>
  );
}

const AGENT_RECEIPT_TEXT =
  "我这边已开启新的辅助回复模式，后面你问服务进度、近期打卡、体重趋势这类问题时，我会先查准确记录再回复。\n\n如果你问近期影视、音乐、展览、赛事、旅游这类公开信息，我也可以联网查一下再帮你整理。";

function AgentReceiptButton({
  serviceCaseId,
  disabled,
}: {
  serviceCaseId: number;
  disabled: boolean;
}) {
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function sendReceipt() {
    if (disabled || sending) return;
    setSending(true);
    setError(null);
    setSent(false);
    try {
      const res = await fetch(`${API_BASE}/service-cases/${serviceCaseId}/messages/send`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: AGENT_RECEIPT_TEXT }),
      });
      if (!res.ok) {
        throw new Error(await res.text());
      }
      setSent(true);
    } catch (err) {
      console.error("send agent receipt failed", err);
      setError("发送失败，请稍后重试");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pb-3 pl-7">
      <p className="text-xs leading-5 text-slate-500">需要告知用户时，手动向群内发送一次轻提示。</p>
      <div className="flex items-center gap-3">
        {sent && <span className="text-xs text-emerald-600">已发送</span>}
        {error && <span className="text-xs text-rose-600">{error}</span>}
        <button
          type="button"
          onClick={sendReceipt}
          disabled={disabled || sending}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          {sending ? "发送中…" : "发送群内提示"}
        </button>
      </div>
    </div>
  );
}

export function CaseAutomationToggles({
  serviceCaseId,
  aiAutoReplyEnabled,
  aiAgentEnabled,
  checkinLinkEnabled,
  morningPushEnabled,
  inactiveWakeEnabled,
  serviceWindowEnded = false,
}: {
  serviceCaseId: number;
  aiAutoReplyEnabled: boolean;
  aiAgentEnabled: boolean;
  checkinLinkEnabled: boolean;
  morningPushEnabled: boolean;
  inactiveWakeEnabled: boolean;
  serviceWindowEnded?: boolean;
}) {
  const [aiAgentOn, setAiAgentOn] = useState(aiAgentEnabled);

  return (
    <div className="panel p-5">
      <h3 className="font-semibold text-slate-950">自动化设置（仅本服务单）</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500">
        {serviceWindowEnded
          ? "该服务单已满 28 天毕业，打卡链接、个性化提醒和无互动唤醒已由系统自动停用。"
          : "定时推送还需对应全局开关开启才会真正发送；此处只能在全局已开的基础上，单独关掉本服务单。"}
      </p>

      <div className="mt-3 divide-y divide-slate-100">
        <div className="pb-1 pt-2 text-xs font-medium text-slate-400">群内机器人</div>
        <Row
          serviceCaseId={serviceCaseId}
          field="ai_auto_reply_enabled"
          initial={aiAutoReplyEnabled}
          icon={Bot}
          title="AI 自动回复"
          desc="关闭后机器人在群里完全不说话（等同群内「@机器人 退下」）；开启等同「回来」。"
        />
        <Row
          serviceCaseId={serviceCaseId}
          field="ai_agent_enabled"
          initial={aiAgentEnabled}
          icon={Workflow}
          title="Agent 链路"
          desc="默认关闭，走旧回复链路；开启后在全局 Agent 开关也开启时，本服务单可使用增强工具链路，按需查询当前服务记录或联网搜索公开信息。"
          onUpdated={setAiAgentOn}
        />
        <AgentReceiptButton serviceCaseId={serviceCaseId} disabled={!aiAgentOn} />

        <div className="pb-1 pt-3 text-xs font-medium text-slate-400">定时推送</div>
        <Row
          serviceCaseId={serviceCaseId}
          field="checkin_link_enabled"
          initial={checkinLinkEnabled}
          icon={CalendarCheck}
          title="打卡链接"
          desc="晚间定时发送的每日打卡链接。"
          disabled={serviceWindowEnded}
        />
        <Row
          serviceCaseId={serviceCaseId}
          field="morning_push_enabled"
          initial={morningPushEnabled}
          icon={Sunrise}
          title="个性化提醒"
          desc="早间根据当前执行重点推送的一句话提醒。"
          disabled={serviceWindowEnded}
        />
        <Row
          serviceCaseId={serviceCaseId}
          field="inactive_wake_enabled"
          initial={inactiveWakeEnabled}
          icon={BellRing}
          title="无互动唤醒"
          desc="长时间无互动时自动发一句唤醒/承接。"
          disabled={serviceWindowEnded}
        />
      </div>
    </div>
  );
}
