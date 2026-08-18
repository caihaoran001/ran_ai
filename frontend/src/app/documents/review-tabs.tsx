"use client";

import { useId, useState, type ReactNode } from "react";

export type ReviewTab = {
  key: string;
  label: string;
  count: number;
  /** 服务端已渲染好的列表，作为 children 传进来，切换只是显示/隐藏，不重新取数 */
  content: ReactNode;
};

/**
 * 审核分区的「待处理 / 已处理」切换。
 *
 * 两份列表都在服务端一次性渲染好再传进来：待办堆到几十条时上下平铺太占版面，
 * 但也不值得为切个页签再跑一趟接口。
 */
export function ReviewTabs({ tabs }: { tabs: ReviewTab[] }) {
  const [active, setActive] = useState(tabs[0]?.key ?? "");
  const groupId = useId();

  return (
    <div>
      <div className="inline-flex flex-wrap gap-1 rounded-xl bg-slate-100 p-1" role="tablist">
        {tabs.map((tab) => {
          const selected = tab.key === active;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              id={`${groupId}-${tab.key}`}
              aria-selected={selected}
              aria-controls={`${groupId}-${tab.key}-panel`}
              onClick={() => setActive(tab.key)}
              className={`rounded-lg px-4 py-2 text-sm font-bold transition-colors ${
                selected ? "bg-white text-slate-950 shadow-sm" : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
              }`}
            >
              {tab.label}
              <span
                className={`ml-2 rounded-md px-1.5 py-0.5 text-xs tabular ${
                  selected ? "bg-cyan-50 text-cyan-700" : "bg-slate-200/70 text-slate-500"
                }`}
              >
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.key}
          role="tabpanel"
          id={`${groupId}-${tab.key}-panel`}
          aria-labelledby={`${groupId}-${tab.key}`}
          hidden={tab.key !== active}
          className="mt-4"
        >
          {tab.content}
        </div>
      ))}
    </div>
  );
}
