"use client";

import { useState, useEffect, useCallback } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { LoaderCircle } from "lucide-react";

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
  const [initialEnabled, setInitialEnabled] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/settings/curated-video-push`);
      if (!res.ok) throw new Error((await res.text()) || "加载失败");
      const data = (await res.json()) as { enabled: boolean };
      setEnabled(data.enabled);
      setInitialEnabled(data.enabled);
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

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/curated-video-push`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error((await res.text()) || "保存失败");
      setInitialEnabled(enabled);
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

  const isDirty = enabled !== initialEnabled;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">视频推送开关</h3>
          <p className="mt-1 text-sm text-slate-500 leading-relaxed">
            控制每天中午 12 点是否把选品群收集到的视频号素材转发给在服务中的群。关闭后素材仍会照常入库存放，只是不再转发；重新开启后从最旧的一条继续推送。
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
            保存
          </Button>
        </div>
      </div>
      <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-3">
        <Switch checked={enabled} onChange={setEnabled} disabled={busy} />
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-900">
            {enabled ? "开启中 — 每天 12:00 转发素材" : "已关闭 — 暂停转发"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {enabled
              ? "选品群的视频素材会按先后顺序推送给所有在服务中的群。"
              : "素材仍会收集入库存放，不会过期前丢失，重新开启后继续推送。"}
          </p>
        </div>
      </div>
      {error && <p className="mt-3 text-sm font-medium text-rose-700" role="alert">{error}</p>}
    </div>
  );
}