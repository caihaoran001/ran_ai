"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";

async function readError(res: Response) {
  const raw = await res.text();
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // 后端偶发返回非 JSON（网关层错误页），原文更有排查价值。
  }
  return raw || "请求失败";
}

async function fetchFlag(): Promise<boolean> {
  const res = await fetch(`${API_BASE}/settings/final-review-rollout`);
  if (!res.ok) throw new Error(await readError(res));
  const data = await res.json();
  return data?.full_enabled === true;
}

export function FinalReviewRolloutSwitch() {
  const router = useRouter();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      setEnabled(await fetchFlag());
      setError(null);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? `配置加载失败：${err.message}` : "配置加载失败，请刷新重试。");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (pending === null) return;
    dialogRef.current?.focus();
  }, [pending]);

  useEffect(() => {
    if (pending === null) return;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setPending(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
    };
  }, [pending, busy]);

  async function confirmToggle() {
    if (pending === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/final-review-rollout`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ full_enabled: pending }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setEnabled(data?.full_enabled === true);
      setPending(null);
      router.refresh();
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? `操作失败：${err.message}` : "操作失败，请稍后再试。");
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  const statusText =
    enabled === null ? null : enabled ? "已全量" : "仅白名单";

  return (
    <>
      <div className="flex shrink-0 flex-col items-start gap-2 sm:items-end">
        {!loaded ? (
          <span className="status-pill pill-neutral" aria-busy="true">
            <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            正在加载全量开关…
          </span>
        ) : statusText ? (
          <span className={`status-pill ${enabled ? "pill-ok" : "pill-warn"}`}>
            {statusText}
          </span>
        ) : (
          <span className="status-pill pill-risk">
            <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
            全量开关状态读取失败
          </span>
        )}

        {enabled !== null ? (
          <Button
            type="button"
            variant="secondary"
            role="switch"
            aria-checked={enabled}
            aria-label={`结营回访全量开关，当前${enabled ? "已全量" : "仅白名单"}`}
            onClick={() => {
              setError(null);
              setPending(!enabled);
            }}
            disabled={busy}
            className="shrink-0 gap-2 px-3 py-2 font-bold shadow-sm transition-all active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
          >
            <span
              className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-200 motion-reduce:transition-none ${
                enabled ? "bg-emerald-500" : "bg-slate-300"
              }`}
              aria-hidden="true"
            >
              <span
                className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 motion-reduce:transition-none ${
                  enabled ? "translate-x-[1.125rem]" : "translate-x-0.5"
                }`}
              />
            </span>
            结营回访全量开关
          </Button>
        ) : loaded ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => void load()}
            className="shrink-0 gap-2 font-semibold shadow-sm"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            重新加载
          </Button>
        ) : null}

        {error && (
          <p
            role="alert"
            className="text-break max-w-[22rem] text-left text-xs font-medium leading-5 text-rose-700 sm:text-right"
          >
            {error}
          </p>
        )}
      </div>

      {pending !== null && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center overflow-y-auto p-4">
          <div
            className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm"
            onClick={busy ? undefined : () => setPending(null)}
            aria-hidden="true"
          />
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="final-review-rollout-dialog-title"
            tabIndex={-1}
            className="relative z-10 w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl outline-none sm:p-6"
          >
            <h3
              id="final-review-rollout-dialog-title"
              className="text-base font-extrabold text-slate-950"
            >
              {pending ? "确认打开全量回访？" : "确认关回白名单？"}
            </h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {pending
                ? "打开后所有走到26到35天的群都会自动回访并发券，不再看白名单；已超35天的不会回头补，已发过的不会重发。"
                : "关回后只跑白名单里的群；全量期间已发出的问话和券不受影响。"}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setPending(null)}
                disabled={busy}
                className="shrink-0 font-semibold"
              >
                取消
              </Button>
              <Button
                type="button"
                onClick={confirmToggle}
                loading={busy}
                loadingText="处理中…"
                className={`shrink-0 font-medium text-white shadow-sm transition-all active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 ${
                  pending
                    ? "bg-rose-700 hover:bg-rose-800"
                    : "bg-cyan-700 hover:bg-cyan-800"
                }`}
              >
                {pending ? "确认打开全量" : "确认关回白名单"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
