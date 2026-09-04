"use client";

import { Archive, Sparkles } from "lucide-react";
import { API_BASE } from "@/lib/api";
import {
  InlineSegmentedToggle,
  type SegmentedOption,
} from "@/components/inline-segmented-toggle";
import { normalizeGroupType, type GroupType } from "@/components/group-type-badge";

// 选中色沿用 GroupTypeBadge 的语义配色：新群青、老群琥珀，扫一眼仍能分辨。
const OPTIONS: SegmentedOption<GroupType>[] = [
  {
    value: "new_group",
    label: "新群",
    hint: "标记为新群",
    icon: Sparkles,
    activeClassName: "text-cyan-800",
    iconClassName: "text-cyan-600",
  },
  {
    value: "legacy",
    label: "老群",
    hint: "标记为老群",
    icon: Archive,
    activeClassName: "text-amber-800",
    iconClassName: "text-amber-600",
  },
];

export function CaseGroupType({
  caseId,
  groupType,
}: {
  caseId: number;
  groupType: string;
}) {
  async function save(next: GroupType) {
    const res = await fetch(`${API_BASE}/service-cases/${caseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ group_type: next }),
    });
    if (!res.ok) throw new Error((await res.text()) || "保存失败");
  }

  return (
    <InlineSegmentedToggle
      label="群类型"
      serverValue={normalizeGroupType(groupType)}
      options={OPTIONS}
      onSave={save}
    />
  );
}
