"use client";

import { cn } from "@/lib/utils";
import { Sparkles, Archive } from "lucide-react";

export type GroupType = "new_group" | "legacy";

const config: Record<GroupType, { label: string; icon: typeof Sparkles; className: string }> = {
  new_group: {
    label: "新群",
    icon: Sparkles,
    className:
      "border-cyan-200 bg-cyan-50 text-cyan-800 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.6)]",
  },
  legacy: {
    label: "老群",
    icon: Archive,
    className:
      "border-amber-200/80 bg-amber-50 text-amber-800 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.5)]",
  },
};

export interface GroupTypeBadgeProps {
  groupType?: GroupType | string | null;
  showIcon?: boolean;
  className?: string;
}

export function isNewGroup(groupType?: string | null): groupType is "new_group" {
  return groupType === "new_group";
}

export function normalizeGroupType(groupType?: string | null): GroupType {
  return isNewGroup(groupType) ? "new_group" : "legacy";
}

export function GroupTypeBadge({ groupType, showIcon = true, className }: GroupTypeBadgeProps) {
  const key = normalizeGroupType(groupType);
  const { label, icon: Icon, className: base } = config[key];
  return (
    <span
      className={cn(
        "status-pill transition-colors duration-150",
        base,
        className,
      )}
    >
      {showIcon && <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />}
      {label}
    </span>
  );
}
