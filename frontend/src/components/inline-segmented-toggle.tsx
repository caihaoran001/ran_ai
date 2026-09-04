"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Check, LoaderCircle, type LucideIcon } from "lucide-react";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** 悬浮提示，说明选中它会发生什么 */
  hint?: string;
  /** 该项左侧的语义图标；不给则沿用上一项，适合语义与取值无关的场景 */
  icon?: LucideIcon;
  /** 选中时的文字色，用来保留原徽章的语义配色 */
  activeClassName?: string;
  /** 选中时左侧图标的颜色 */
  iconClassName?: string;
}

/**
 * 服务单头部的行内二选一开关：一次点击即切换并落库，滑块动画表达"切到哪一边"，
 * 左侧图标位交叉淡入 语义图标 → 转圈 → 绿勾 表达"存没存上"，不弹任何模态。
 *
 * 乐观切换：先滑动再发请求，失败滑回原位并就地显示错误，不用 alert 打断操作。
 */
export function InlineSegmentedToggle<T extends string>({
  label,
  serverValue,
  options,
  onSave,
}: {
  /** 无障碍名称，同时用于区分页面上的多个开关 */
  label: string;
  /** 服务端当前值：路由刷新后据此把本地乐观值对齐回真实值 */
  serverValue: T;
  options: SegmentedOption<T>[];
  /** 只负责写库，失败时抛出 Error，其信息会就地展示给运营 */
  onSave: (next: T) => Promise<void>;
}) {
  const [value, setValue] = useState<T>(serverValue);
  const [synced, setSynced] = useState<T>(serverValue);
  const [busy, setBusy] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, startTransition] = useTransition();
  const router = useRouter();
  const pending = busy || isRefreshing;

  // 服务端数据刷新后（含页面轮询刷新）对齐真实值；保存过程中不打断动画。
  if (!pending && serverValue !== synced) {
    setSynced(serverValue);
    setValue(serverValue);
  }

  async function select(next: T) {
    if (pending || next === value) return;
    const previous = value;
    setValue(next); // 乐观切换：滑块立刻动，不等接口
    setError(null);
    setBusy(true);
    try {
      await onSave(next);
      setJustSaved(true);
      window.setTimeout(() => setJustSaved(false), 1400);
      startTransition(() => router.refresh());
    } catch (err) {
      setValue(previous);
      setError(err instanceof Error && err.message ? err.message : "保存失败");
      console.error(err);
    } finally {
      setBusy(false);
    }
  }

  const current = options.find((option) => option.value === value) ?? options[0];
  const LeadingIcon = current.icon;

  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="radiogroup"
        aria-label={label}
        aria-busy={pending || undefined}
        className={`relative inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-100/80 py-0.5 pl-2 pr-0.5 transition-all duration-200 hover:border-slate-300 ${
          pending ? "opacity-80" : ""
        }`}
        style={{ boxShadow: "var(--shadow-xs)" }}
      >
        <span className="relative inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center">
          {LeadingIcon && (
            <LeadingIcon
              aria-hidden="true"
              className={`absolute h-3.5 w-3.5 transition-all duration-200 ${
                current.iconClassName ?? "text-slate-400"
              } ${pending || justSaved ? "scale-50 opacity-0" : "scale-100 opacity-100"}`}
            />
          )}
          <LoaderCircle
            aria-hidden="true"
            className={`absolute h-3.5 w-3.5 animate-spin text-cyan-600 transition-all duration-200 motion-reduce:animate-none ${
              pending ? "scale-100 opacity-100" : "scale-50 opacity-0"
            }`}
          />
          <Check
            aria-hidden="true"
            className={`absolute h-3.5 w-3.5 text-emerald-600 transition-all duration-200 ${
              justSaved && !pending ? "scale-100 opacity-100" : "scale-50 opacity-0"
            }`}
          />
        </span>

        <span
          className="relative inline-grid"
          style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
        >
          {/* 滑块：跟随选中项左右滑动 */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 rounded-full border border-white/70 bg-white transition-transform duration-300 will-change-transform"
            style={{
              width: `${100 / options.length}%`,
              transform: `translateX(${options.findIndex((option) => option.value === value) * 100}%)`,
              boxShadow: "var(--shadow-sm)",
              transitionTimingFunction: "var(--ease-out)",
            }}
          />
          {options.map((option) => {
            const active = value === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={active}
                title={option.hint}
                disabled={pending}
                onClick={() => select(option.value)}
                className={`relative z-10 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold leading-4 outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:ring-offset-1 disabled:cursor-wait ${
                  active
                    ? option.activeClassName ?? "text-cyan-800"
                    : "text-slate-500 hover:text-slate-800"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </span>
      </span>

      {error && (
        <span className="animate-fade-in-up text-xs font-semibold text-rose-600">
          {error}
        </span>
      )}
    </span>
  );
}
