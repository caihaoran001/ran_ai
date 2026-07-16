"use client";

import { useState, type KeyboardEvent } from "react";
import { ChevronRight } from "lucide-react";
import type { PopulationDistribution } from "@/lib/api";
import { DistributionPanel } from "./distribution-panel";

export function HealthDistributionsPanel({
  distributions,
}: {
  distributions: PopulationDistribution[];
}) {
  const [activeKey, setActiveKey] = useState(distributions[0]?.key ?? "");
  const activeDistribution = distributions.find((dist) => dist.key === activeKey) ?? distributions[0];

  if (!activeDistribution) return null;

  function selectTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (index + 1) % distributions.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + distributions.length) % distributions.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = distributions.length - 1;
    }

    if (nextIndex === null) return;
    event.preventDefault();
    const nextKey = distributions[nextIndex].key;
    setActiveKey(nextKey);
    requestAnimationFrame(() => document.getElementById(`health-tab-${nextKey}`)?.focus());
  }

  return (
    <section className="panel overflow-hidden" aria-labelledby="health-distributions-title">
      <div className="panel-header-clean sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 id="health-distributions-title" className="panel-title">健康资料</h2>
          <p className="panel-subtitle">
            已确认健康情况、长期用药、特殊阶段、待确认项与过敏史集中在这里查看。
          </p>
        </div>
        <span className="status-pill pill-info shrink-0 self-start">{distributions.length} 个维度</span>
      </div>

      <div className="grid min-w-0 md:grid-cols-[15rem_minmax(0,1fr)]">
        <div
          className="grid grid-cols-2 gap-2 border-b border-slate-200 bg-slate-50/70 p-3 sm:p-4 md:grid-cols-1 md:border-b-0 md:border-r"
          role="tablist"
          aria-label="健康资料维度"
        >
          {distributions.map((dist, index) => {
            const active = dist.key === activeDistribution.key;
            return (
              <button
                key={dist.key}
                id={`health-tab-${dist.key}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls="health-distribution-panel"
                tabIndex={active ? 0 : -1}
                onClick={() => setActiveKey(dist.key)}
                onKeyDown={(event) => selectTab(event, index)}
                className={`flex min-h-14 min-w-0 items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:ring-offset-2 ${
                  active
                    ? "bg-white text-cyan-900 shadow-sm ring-1 ring-cyan-200"
                    : "text-slate-600 hover:bg-white hover:text-slate-900"
                }`}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-bold">{dist.label}</span>
                  <span className="mt-0.5 block text-xs tabular-nums text-slate-500">
                    覆盖 {dist.total}
                    {dist.unknown > 0 ? ` · 未采集 ${dist.unknown}` : ""}
                  </span>
                </span>
                <ChevronRight
                  className={`hidden h-4 w-4 shrink-0 md:block ${active ? "text-cyan-600" : "text-slate-300"}`}
                  aria-hidden="true"
                />
              </button>
            );
          })}
        </div>

        <div
          id="health-distribution-panel"
          role="tabpanel"
          aria-labelledby={`health-tab-${activeDistribution.key}`}
          className="min-w-0 p-5 sm:p-6"
        >
          <DistributionPanel key={activeDistribution.key} dist={activeDistribution} embedded />
        </div>
      </div>
    </section>
  );
}
