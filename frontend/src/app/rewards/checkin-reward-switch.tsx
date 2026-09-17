"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";

type RewardTierEntry = {
  days: number;
  reward_text: string;
  enabled: boolean;
};

type CheckinRewardConfig = {
  enabled: boolean;
  program_start_date: string | null;
  tiers: RewardTierEntry[];
};

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

function parseConfig(data: unknown): CheckinRewardConfig {
  const raw = (data ?? {}) as Record<string, unknown>;
  const tierList = Array.isArray(raw.tiers) ? raw.tiers : [];
  return {
    enabled: raw.enabled === true,
    program_start_date:
      typeof raw.program_start_date === "string" ? raw.program_start_date : null,
    tiers: tierList.map((item) => {
      const tier = (item ?? {}) as Record<string, unknown>;
      return {
        days: typeof tier.days === "number" && Number.isInteger(tier.days) ? tier.days : 0,
        reward_text: typeof tier.reward_text === "string" ? tier.reward_text : "",
        enabled: tier.enabled !== false,
      };
    }),
  };
}

async function fetchConfig(): Promise<CheckinRewardConfig> {
  const res = await fetch(`${API_BASE}/settings/checkin-reward-tiers`);
  if (!res.ok) throw new Error(await readError(res));
  return parseConfig(await res.json());
}

export function CheckinRewardSwitch() {
  const router = useRouter();
  const [config, setConfig] = useState<CheckinRewardConfig | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingEnabled, setPendingEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await fetchConfig();
      setConfig(next);
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
    if (pendingEnabled === null) return;
    dialogRef.current?.focus();
  }, [pendingEnabled]);

  useEffect(() => {
    if (pendingEnabled === null) return;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setPendingEnabled(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
    };
  }, [pendingEnabled, busy]);

  async function confirmToggle() {
    if (pendingEnabled === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      // 写前重新拉取：档位一律回传服务端当前值，避免把别人刚改的档位覆盖掉。
      const fresh = await fetchConfig();
      setConfig(fresh);
      const res = await fetch(`${API_BASE}/settings/checkin-reward-tiers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled: pendingEnabled, tiers: fresh.tiers }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setConfig(parseConfig(await res.json()));
      setPendingEnabled(null);
      router.refresh();
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? `操作失败：${err.message}` : "操作失败，请稍后再试。");
      setPendingEnabled(null);
    } finally {
      setBusy(false);
    }
  }

  const enabled = config?.enabled ?? false;
  const statusText = !config
    ? null
    : !enabled
      ? "已关闭"
      : config.program_start_date
        ? `已开启 · 活动自 ${config.program_start_date} 起`
        : "已开启";

  return (
    <>
      <div className="flex shrink-0 flex-col items-start gap-2 sm:items-end">
        {!loaded ? (
          <span className="status-pill pill-neutral" aria-busy="true">
            <LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            正在加载总开关…
          </span>
        ) : statusText ? (
          <span className={`status-pill ${enabled ? "pill-ok" : "pill-warn"}`}>
            {statusText}
          </span>
        ) : (
          <span className="status-pill pill-risk">
            <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
            总开关状态读取失败
          </span>
        )}

        {config ? (
          <Button
            type="button"
            variant="secondary"
            role="switch"
            aria-checked={enabled}
            aria-label={`打卡奖励总开关，当前${enabled ? "已开启" : "已关闭"}`}
            onClick={() => {
              setError(null);
              setPendingEnabled(!enabled);
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
            打卡奖励总开关
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

      {pendingEnabled !== null && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center overflow-y-auto p-4">
          <div
            className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm"
            onClick={busy ? undefined : () => setPendingEnabled(null)}
            aria-hidden="true"
          />
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="checkin-reward-switch-dialog-title"
            tabIndex={-1}
            className="relative z-10 w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl outline-none sm:p-6"
          >
            <h3
              id="checkin-reward-switch-dialog-title"
              className="text-base font-extrabold text-slate-950"
            >
              {pendingEnabled ? "确认开启打卡奖励？" : "确认关闭打卡奖励？"}
            </h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {pendingEnabled
                ? "开启当天会记为新一轮的活动起始日；只有之后新开启的服务单参与，其他在服客户的打卡不会计入本轮。"
                : "关闭后停止累计与发放新资格；当前批次会清空，已发奖励与历史批次保留，寄送不受影响。"}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setPendingEnabled(null)}
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
                  pendingEnabled
                    ? "bg-cyan-700 hover:bg-cyan-800"
                    : "bg-rose-700 hover:bg-rose-800"
                }`}
              >
                {pendingEnabled ? "确认开启" : "确认关闭"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
