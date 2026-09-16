"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertCircle, LoaderCircle, TriangleAlert } from "lucide-react";

type RewardTier = {
  days: number;
  reward_code: string;
  reward_name: string;
  quantity: number;
  unit: string;
  enabled: boolean;
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

export function CheckinRewardTierConfig() {
  const [tiers, setTiers] = useState<RewardTier[]>([]);
  const [initial, setInitial] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/checkin-reward-tiers`);
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        const list: RewardTier[] = data?.tiers ?? [];
        setTiers(list);
        setInitial(JSON.stringify(list));
      } catch (err) {
        console.error(err);
        setError(err instanceof Error ? `配置加载失败：${err.message}` : "配置加载失败，请刷新重试。");
      } finally {
        setLoaded(true);
      }
    }
    load();
  }, []);

  const isDirty = JSON.stringify(tiers) !== initial;

  useEffect(() => {
    // 运营填完不点保存就刷新/关页，内容会全部白填——已经真实发生过一次。
    if (!isDirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  });

  function update(code: string, patch: Partial<RewardTier>) {
    setTiers((prev) =>
      prev.map((item) => (item.reward_code === code ? { ...item, ...patch } : item)),
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/checkin-reward-tiers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tiers }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      const list: RewardTier[] = data?.tiers ?? [];
      setTiers(list);
      setInitial(JSON.stringify(list));
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
        正在加载打卡实物奖励档位配置…
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">打卡实物奖励档位</h3>
          <p className="mt-1 text-sm leading-relaxed text-slate-500">
            客户累计打卡满指定天数后可领取对应实物奖励。
            <strong className="font-semibold text-slate-700">档位天数和编码由系统固定</strong>
            ——这里只填每档发什么、发多少、用什么单位。开关打开但没填奖励名称、数量或单位时，
            该档不会进入发放候选（下面会标出来）。关闭的档位整行置灰，不参与发放。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {showSuccess && <span className="animate-pulse text-sm font-semibold text-emerald-600">已保存！</span>}
          <Button
            type="button"
            className="shrink-0 bg-cyan-700 font-medium text-white shadow-sm transition-all hover:bg-cyan-800 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
            onClick={save}
            disabled={!isDirty}
            loading={busy}
            loadingText="保存中…"
          >
            保存配置
          </Button>
        </div>
      </div>

      <div className="grid gap-4">
        {tiers.map((tier) => {
          const missing =
            tier.enabled &&
            (!tier.reward_name.trim() || !tier.unit.trim() || !(Number(tier.quantity) > 0));
          return (
            <div
              key={tier.reward_code}
              className={`rounded-lg border p-4 transition-colors ${
                missing
                  ? "border-amber-300 bg-amber-50/50"
                  : tier.enabled
                    ? "border-slate-200 bg-slate-50/50"
                    : "border-slate-200 bg-slate-100/70"
              }`}
            >
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="shrink-0 rounded-md bg-cyan-700/10 px-2 py-1 text-sm font-semibold text-cyan-800">
                    满 {tier.days} 天
                  </span>
                  <span className="shrink-0 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-xs text-slate-500">
                    {tier.reward_code}
                  </span>
                  {!tier.enabled && (
                    <span className="shrink-0 rounded bg-slate-200 px-1.5 py-0.5 text-xs font-medium text-slate-500">
                      已关闭
                    </span>
                  )}
                </div>
                <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-slate-300 accent-cyan-700"
                    checked={tier.enabled}
                    onChange={(e) => update(tier.reward_code, { enabled: e.target.checked })}
                    disabled={busy}
                  />
                  启用
                </label>
              </div>

              {missing && (
                <p className="mb-3 flex items-start gap-1.5 text-sm font-medium text-amber-800">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  已启用但奖励名称、数量或单位没填完整，该档不会进入发放候选（请补齐后再保存）。
                </p>
              )}

              <div className="grid gap-3 sm:grid-cols-3">
                <Field
                  label="奖励名称"
                  placeholder="如：咖啡 1 管"
                  value={tier.reward_name}
                  onChange={(value) => update(tier.reward_code, { reward_name: value })}
                  disabled={busy}
                />
                <div className="grid gap-1">
                  <label className="text-xs font-medium text-slate-600">数量</label>
                  <input
                    type="number"
                    min={0}
                    step={1}
                    placeholder="如：1"
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    value={Number.isFinite(tier.quantity) ? tier.quantity : 0}
                    onChange={(e) =>
                      update(tier.reward_code, {
                        quantity: e.target.value === "" ? 0 : Number(e.target.value),
                      })
                    }
                    disabled={busy}
                  />
                </div>
                <Field
                  label="单位"
                  placeholder="如：管 / 盒 / 份"
                  value={tier.unit}
                  onChange={(value) => update(tier.reward_code, { unit: value })}
                  disabled={busy}
                />
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-4 text-xs leading-relaxed text-slate-500">
        档位天数与编码不可编辑，由后端统一维护；发放资格仍以服务端判定为准，这里只维护发什么、发多少。
      </p>

      {error && (
        <p className="mt-3 flex items-start gap-1.5 text-sm font-medium text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  );
}

function Field({
  label,
  placeholder,
  value,
  onChange,
  disabled,
}: {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-1">
      <label className="text-xs font-medium text-slate-600">{label}</label>
      <input
        type="text"
        placeholder={placeholder}
        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </div>
  );
}
