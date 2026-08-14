"use client";

import { useState, useEffect, useCallback } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { LoaderCircle } from "lucide-react";

const SCOPE_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "active_7d", label: "近 7 天有互动", hint: "近 7 天内有本人发言的群" },
  { value: "active_24h", label: "近 24 小时有互动", hint: "近 24 小时内有本人发言的群" },
  { value: "all", label: "全部", hint: "所有在服务中的群" },
];

function Switch({
  checked,
  onChange,
  disabled = false,
  size = "lg",
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  size?: "md" | "lg";
}) {
  const isLg = size === "lg";
  const trackClass = isLg ? "h-6 w-11" : "h-5 w-9";
  const thumbClass = isLg ? "h-5 w-5" : "h-4 w-4";
  const translateClass = checked
    ? isLg
      ? "translate-x-[22px]"
      : "translate-x-[18px]"
    : "translate-x-[2px]";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
      className={`relative inline-flex shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:ring-offset-2 ${trackClass} ${
        checked ? "bg-cyan-700 hover:bg-cyan-800" : "bg-slate-200 hover:bg-slate-300"
      } ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
    >
      <span
        className={`pointer-events-none inline-block rounded-full bg-white shadow-sm ring-0 transition-transform duration-200 ease-in-out ${thumbClass} ${translateClass}`}
      />
    </button>
  );
}

export function VideoPushConfig() {
  const [enabled, setEnabled] = useState(false);
  const [sendAt, setSendAt] = useState("12:00");
  const [scope, setScope] = useState("active_7d");
  const [initial, setInitial] = useState<{ enabled: boolean; sendAt: string; scope: string }>({
    enabled: false,
    sendAt: "12:00",
    scope: "active_7d",
  });
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/settings/curated-video-push`);
      if (!res.ok) throw new Error((await res.text()) || "加载失败");
      const data = (await res.json()) as { enabled: boolean; send_at: string; scope: string };
      const next = { enabled: data.enabled, sendAt: data.send_at, scope: data.scope };
      setEnabled(next.enabled);
      setSendAt(next.sendAt);
      setScope(next.scope);
      setInitial(next);
    } catch (err) {
      console.error(err);
      setError("配置加载失败，请刷新页面后重试。");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const isDirty =
    enabled !== initial.enabled || sendAt !== initial.sendAt || scope !== initial.scope;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/curated-video-push`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled, send_at: sendAt, scope }),
      });
      if (!res.ok) throw new Error((await res.text()) || "保存失败");
      const data = (await res.json()) as { enabled: boolean; send_at: string; scope: string };
      setEnabled(data.enabled);
      setSendAt(data.send_at);
      setScope(data.scope);
      setInitial({ enabled: data.enabled, sendAt: data.send_at, scope: data.scope });
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 2000);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? `保存失败：${err.message}` : "保存失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) {
    return (
      <div
        className="flex min-h-32 items-center justify-center rounded-xl border border-slate-200 bg-white p-6 text-sm font-medium text-slate-500"
        aria-busy="true"
      >
        <LoaderCircle className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        正在加载视频推送配置…
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">视频推送配置</h3>
          <p className="mt-1 text-sm text-slate-500 leading-relaxed">
            把选品群收集到的视频号素材转发给在服务中的群。可控制开关、转发时间和转发范围。关闭后素材仍会照常入库存放，只是不再转发；重新开启后从最旧的一条继续推送。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {showSuccess && <span className="text-sm text-emerald-600 font-semibold animate-pulse">已保存！</span>}
          <Button
            type="button"
            className="shrink-0 bg-cyan-700 hover:bg-cyan-800 text-white font-medium shadow-sm transition-all active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none"
            onClick={save}
            disabled={!isDirty}
            loading={busy}
            loadingText="保存中…"
          >
            保存配置
          </Button>
        </div>
      </div>

      <div className="space-y-4">
        <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-3">
          <Switch checked={enabled} onChange={setEnabled} disabled={busy} />
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-900">
              {enabled ? "开启中 — 每天转发素材" : "已关闭 — 暂停转发"}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {enabled
                ? "素材会按先后顺序推送给符合条件的群。"
                : "素材仍会收集入库存放，不会过期前丢失，重新开启后继续推送。"}
            </p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
            <label htmlFor="video-push-time" className="text-sm font-medium text-slate-900">
              每天转发时间
            </label>
            <p className="mt-0.5 text-xs text-slate-500">到点后把当前最旧的一条素材转发出去。</p>
            <input
              id="video-push-time"
              type="time"
              value={sendAt}
              onChange={(e) => setSendAt(e.target.value || "12:00")}
              disabled={busy}
              className="mt-2 block w-full max-w-[160px] rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 shadow-sm focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500 disabled:opacity-50"
            />
          </div>

          <fieldset className="rounded-lg border border-slate-200 bg-white px-4 py-3">
            <legend className="text-sm font-medium text-slate-900">转发范围</legend>
            <p className="mt-0.5 text-xs text-slate-500">只推送给符合条件的群。</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {SCOPE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  disabled={busy}
                  onClick={() => setScope(opt.value)}
                  aria-pressed={scope === opt.value}
                  title={opt.hint}
                  className={`rounded-full border px-3 py-1 text-sm font-medium transition-colors ${
                    scope === opt.value
                      ? "border-cyan-700 bg-cyan-700 text-white"
                      : "border-slate-300 bg-white text-slate-600 hover:border-cyan-300 hover:text-cyan-700"
                  } disabled:opacity-50`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
      </div>
      {error && <p className="mt-3 text-sm font-medium text-rose-700" role="alert">{error}</p>}
    </div>
  );
}