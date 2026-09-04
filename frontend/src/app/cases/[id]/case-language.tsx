"use client";

import { Globe } from "lucide-react";
import { API_BASE } from "@/lib/api";
import {
  InlineSegmentedToggle,
  type SegmentedOption,
} from "@/components/inline-segmented-toggle";

type Lang = "zh" | "en";

// 中文用中性深色（绝大多数服务单都是中文，不该抢眼），英文用蓝色实心：
// 英文单要走英文提示词，扫一眼列表头就该跳出来。
const OPTIONS: SegmentedOption<Lang>[] = [
  {
    value: "zh",
    label: "中文",
    hint: "服务语种切换为中文",
    icon: Globe,
    activeColor: "#334155", // slate-700
  },
  {
    value: "en",
    label: "English",
    hint: "服务语种切换为英文",
    icon: Globe,
    activeColor: "#1d4ed8", // blue-700
    iconClassName: "text-blue-600",
  },
];

export function CaseLanguage({
  caseId,
  language,
}: {
  caseId: number;
  language: Lang;
}) {
  async function save(next: Lang) {
    const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ language: next }),
    });
    if (!res.ok) throw new Error((await res.text()) || "保存失败");
  }

  return (
    <InlineSegmentedToggle
      label="服务语种"
      serverValue={language === "en" ? "en" : "zh"}
      options={OPTIONS}
      onSave={save}
    />
  );
}
