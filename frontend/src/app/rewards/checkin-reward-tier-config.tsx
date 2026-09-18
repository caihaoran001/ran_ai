"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertCircle, LoaderCircle, TriangleAlert } from "lucide-react";

type RewardTier = {
  days: number | "";
  reward_text: string;
};

type SavedTier = {
  days: number;
  reward_text: string;
};

const MAX_REWARD_DAYS = 365;

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

function normalizeTiers(list: unknown): RewardTier[] {
  if (!Array.isArray(list)) return [];
  return list.map((item) => {
    const raw = item as Partial<SavedTier>;
    return {
      days:
        typeof raw.days === "number" && Number.isInteger(raw.days) ? raw.days : "",
      reward_text: typeof raw.reward_text === "string" ? raw.reward_text : "",
    };
  });
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
        const list = normalizeTiers(data?.tiers);
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

  const snapshot = JSON.stringify(tiers);
  const isDirty = snapshot !== initial;

  useEffect(() => {
    // 运营填完不点保存就刷新/关页，内容会全部白填——已经真实发生过一次。
    if (!isDirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  });

  // 前端只做空值/重复等输入校验，发放资格仍以服务端判定为准。
  const dayCounts = new Map<number, number>();
  for (const tier of tiers) {
    if (typeof tier.days === "number" && Number.isInteger(tier.days)) {
      dayCounts.set(tier.days, (dayCounts.get(tier.days) ?? 0) + 1);
    }
  }
  const rowErrors = tiers.map((tier) => {
    const daysInvalid =
      typeof tier.days !== "number" ||
      !Number.isInteger(tier.days) ||
      tier.days < 1 ||
      tier.days > MAX_REWARD_DAYS;
    const duplicate =
      typeof tier.days === "number" &&
      Number.isInteger(tier.days) &&
      (dayCounts.get(tier.days) ?? 0) > 1;
    const emptyReward = tier.reward_text.trim() === "";
    return { daysInvalid, duplicate, emptyReward, invalid: daysInvalid || duplicate || emptyReward };
  });
  const hasErrors = rowErrors.some((row) => row.invalid);

  function update(index: number, patch: Partial<RewardTier>) {
    setTiers((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }

  function remove(index: number) {
    setTiers((prev) => prev.filter((_, i) => i !== index));
  }

  function add() {
    const numbers = tiers.filter(
      (tier): tier is RewardTier & { days: number } =>
        typeof tier.days === "number" && Number.isInteger(tier.days),
    ).map((tier) => tier.days);
    const max = numbers.length > 0 ? Math.max(...numbers) : 0;
    const next = max + 7;
    setTiers((prev) => [...prev, { days: next <= MAX_REWARD_DAYS ? next : "", reward_text: "" }]);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // 写前重新拉取总开关状态：档位用本地编辑值，总开关只回传服务端当前值，避免互相覆盖。
      const freshRes = await fetch(`${API_BASE}/settings/checkin-reward-tiers`);
      if (!freshRes.ok) throw new Error(await readError(freshRes));
      const fresh = await freshRes.json();
      const payload = {
        enabled: fresh?.enabled === true,
        tiers: tiers.map((tier) => ({
          days: tier.days,
          reward_text: tier.reward_text,
        })),
      };
      const res = await fetch(`${API_BASE}/settings/checkin-reward-tiers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      const list = normalizeTiers(data?.tiers);
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
            客户累计打卡满指定天数后可领取对应实物奖励。总开关在页面标题右侧控制；
            这里维护各档位天数与奖励内容；28 天结营后仍在群内完整打卡，也会继续累计奖励。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {showSuccess && <span className="animate-pulse text-sm font-semibold text-emerald-600">已保存！</span>}
          <Button
            type="button"
            className="shrink-0 bg-cyan-700 font-medium text-white shadow-sm transition-all hover:bg-cyan-800 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
            onClick={save}
            disabled={!isDirty || hasErrors}
            loading={busy}
            loadingText="保存中…"
          >
            保存配置
          </Button>
        </div>
      </div>

      <div className="grid gap-4">
        {tiers.map((tier, index) => {
          const rowError = rowErrors[index];
          return (
            <div
              key={index}
              className={`rounded-lg border p-4 transition-colors ${
                rowError.invalid
                  ? "border-rose-300 bg-rose-50/50"
                  : "border-slate-200 bg-slate-50/50"
              }`}
            >
              <div className="flex flex-wrap items-center gap-3">
                <div className="grid gap-1">
                  <label className="text-xs font-medium text-slate-600">满多少天</label>
                  <input
                    type="number"
                    min={1}
                    max={MAX_REWARD_DAYS}
                    step={1}
                    aria-label={`第${index + 1}档天数`}
                    placeholder={`1..${MAX_REWARD_DAYS}`}
                    className={`w-24 rounded-md border px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100 ${
                      rowError.daysInvalid || rowError.duplicate
                        ? "border-rose-400 bg-white"
                        : "border-slate-300"
                    }`}
                    value={tier.days}
                    onChange={(e) =>
                      update(index, {
                        days: e.target.value === "" ? "" : Number(e.target.value),
                      })
                    }
                    disabled={busy}
                  />
                </div>
                <div className="grid min-w-0 flex-1 gap-1">
                  <label className="text-xs font-medium text-slate-600">奖励（一句话）</label>
                  <input
                    type="text"
                    aria-label={`第${index + 1}档奖励`}
                    placeholder="如：咖啡1管"
                    className={`rounded-md border px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100 ${
                      rowError.emptyReward ? "border-rose-400 bg-white" : "border-slate-300"
                    }`}
                    value={tier.reward_text}
                    onChange={(e) => update(index, { reward_text: e.target.value })}
                    disabled={busy}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => remove(index)}
                  disabled={busy}
                  className="mt-5 shrink-0 rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-slate-600 transition-all hover:border-rose-300 hover:text-rose-600 disabled:pointer-events-none disabled:opacity-50"
                >
                  删除
                </button>
              </div>

              {rowError.invalid && (
                <p className="mt-3 flex items-start gap-1.5 text-sm font-medium text-rose-700">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  {rowError.duplicate
                    ? `天数与其他档位重复，请改成 1..${MAX_REWARD_DAYS} 内不重复的天数。`
                    : rowError.daysInvalid
                      ? `天数请填写 1..${MAX_REWARD_DAYS} 的整数。`
                      : "奖励不能为空，请填写一句话奖励。"}
                </p>
              )}
            </div>
          );
        })}
      </div>

      {tiers.length === 0 && (
        <p className="mt-4 rounded-lg border border-dashed border-slate-300 bg-slate-50/50 px-4 py-3 text-sm text-slate-500">
          还没有档位，点击下方「新增一档」添加第一档。
        </p>
      )}

      <div className="mt-4">
        <Button
          type="button"
          variant="secondary"
          className="shrink-0 font-medium shadow-sm transition-all active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
          onClick={add}
          disabled={busy}
        >
          新增一档
        </Button>
      </div>

      <p className="mt-4 text-xs leading-relaxed text-slate-500">
        天数取 1..{MAX_REWARD_DAYS} 的整数且不可重复，奖励为一句话描述；校验不通过时会标红并禁用保存。
        后端仍会整份校验并拒收，不合规时错误信息会直接显示在下方。
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
