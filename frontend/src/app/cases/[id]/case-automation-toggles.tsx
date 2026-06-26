"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";
import { Bot, CalendarCheck, Sunrise, BellRing } from "lucide-react";

type ToggleKey =
  | "ai_auto_reply_enabled"
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
}: {
  serviceCaseId: number;
  field: ToggleKey;
  initial: boolean;
  icon: typeof Bot;
  title: string;
  desc: string;
}) {
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    const next = !on;
    const res = await fetch(`${API_BASE}/service-cases/${serviceCaseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ [field]: next }),
    });
    if (res.ok) setOn(next);
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
        <input type="checkbox" checked={on} disabled={busy} onChange={toggle} className="sr-only peer" />
        <div className="peer h-6 w-11 rounded-full bg-slate-200 after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-cyan-700 peer-checked:after:translate-x-full peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-cyan-300" />
      </label>
    </div>
  );
}

export function CaseAutomationToggles({
  serviceCaseId,
  aiAutoReplyEnabled,
  checkinLinkEnabled,
  morningPushEnabled,
  inactiveWakeEnabled,
}: {
  serviceCaseId: number;
  aiAutoReplyEnabled: boolean;
  checkinLinkEnabled: boolean;
  morningPushEnabled: boolean;
  inactiveWakeEnabled: boolean;
}) {
  return (
    <div className="panel p-5">
      <h3 className="font-semibold text-slate-950">自动化设置（仅本服务单）</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500">
        定时推送还需对应全局开关开启才会真正发送；此处只能在全局已开的基础上，单独关掉本服务单。
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

        <div className="pb-1 pt-3 text-xs font-medium text-slate-400">定时推送</div>
        <Row
          serviceCaseId={serviceCaseId}
          field="checkin_link_enabled"
          initial={checkinLinkEnabled}
          icon={CalendarCheck}
          title="打卡链接"
          desc="晚间定时发送的每日打卡链接。"
        />
        <Row
          serviceCaseId={serviceCaseId}
          field="morning_push_enabled"
          initial={morningPushEnabled}
          icon={Sunrise}
          title="个性化提醒"
          desc="早间根据当前执行重点推送的一句话提醒。"
        />
        <Row
          serviceCaseId={serviceCaseId}
          field="inactive_wake_enabled"
          initial={inactiveWakeEnabled}
          icon={BellRing}
          title="无互动唤醒"
          desc="长时间无互动时自动发一句唤醒/承接。"
        />
      </div>
    </div>
  );
}
