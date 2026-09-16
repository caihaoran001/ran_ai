"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertCircle, LoaderCircle, TriangleAlert } from "lucide-react";

type Benefit = {
  code: string;
  name: string;
  kind: string;
  face_value: string;
  scope: string;
  threshold: string;
  validity_days: number | null;
  claim_url: string;
  enabled: boolean;
  grant_when: string;
  grantable: boolean;
};

const GRANT_WHEN_LABEL: Record<string, string> = {
  always: "走完 28 天且客户还在群里就发",
  cross_product_intent: "仅当客户表示想看其他产品时才发",
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

export function Day28BenefitConfig() {
  const [benefits, setBenefits] = useState<Benefit[]>([]);
  const [initial, setInitial] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/day28-benefits`);
        if (!res.ok) throw new Error(await readError(res));
        const data = await res.json();
        const list: Benefit[] = data?.benefits ?? [];
        setBenefits(list);
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

  const isDirty = JSON.stringify(benefits) !== initial;

  useEffect(() => {
    // 运营填完不点保存就刷新/关页，内容会全部白填——已经真实发生过一次。
    if (!isDirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  });

  function update(code: string, patch: Partial<Benefit>) {
    setBenefits((prev) =>
      prev.map((item) => (item.code === code ? { ...item, ...patch } : item)),
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/day28-benefits`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ benefits }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      const list: Benefit[] = data?.benefits ?? [];
      setBenefits(list);
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
        正在加载第28天权益配置…
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">第28天权益（券与赠品）</h3>
          <p className="mt-1 text-sm leading-relaxed text-slate-500">
            客户走完 28 天后，助教会在群里告知权益并给出领取链接。
            <strong className="font-semibold text-slate-700">助教只会念这里填的内容</strong>
            ——没填的字段一个字都不会提，也不会自己编面额或链接。
            开关打开但没填领取入口时，这张券不会发出去（下面会标出来）。
            领取入口可以是网址，也可以是微信小店那种 <code>#微信小店://…</code> 口令。
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
        {benefits.map((benefit) => {
          const missingLink = benefit.enabled && !benefit.claim_url.trim();
          return (
            <div
              key={benefit.code}
              className={`rounded-lg border p-4 transition-colors ${
                missingLink ? "border-amber-300 bg-amber-50/50" : "border-slate-200 bg-slate-50/50"
              }`}
            >
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <input
                    type="text"
                    aria-label="券名称"
                    className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-2 py-1 text-sm font-semibold text-slate-900 transition-all hover:border-slate-300 focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-100"
                    value={benefit.name}
                    onChange={(e) => update(benefit.code, { name: e.target.value })}
                    disabled={busy}
                  />
                  <span className="shrink-0 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-xs text-slate-500">
                    {benefit.kind === "gift" ? "赠品" : "优惠券"}
                  </span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {GRANT_WHEN_LABEL[benefit.grant_when] ?? benefit.grant_when}
                  </span>
                </div>
                <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-slate-300 accent-cyan-700"
                    checked={benefit.enabled}
                    onChange={(e) => update(benefit.code, { enabled: e.target.checked })}
                    disabled={busy}
                  />
                  启用
                </label>
              </div>

              {missingLink && (
                <p className="mb-3 flex items-start gap-1.5 text-sm font-medium text-amber-800">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  已启用但没填领取入口，这张券不会发给客户（助教会改为通知班主任人工处理）。
                </p>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label={benefit.kind === "gift" ? "赠品内容" : "面额"}
                  placeholder={benefit.kind === "gift" ? "如：咖啡 1 管" : "如：200元"}
                  value={benefit.face_value}
                  onChange={(value) => update(benefit.code, { face_value: value })}
                  disabled={busy}
                />
                <Field
                  label="适用商品"
                  placeholder="如：28日早C午B晚A"
                  value={benefit.scope}
                  onChange={(value) => update(benefit.code, { scope: value })}
                  disabled={busy}
                />
                <Field
                  label="使用门槛"
                  placeholder="没有门槛就留空"
                  value={benefit.threshold}
                  onChange={(value) => update(benefit.code, { threshold: value })}
                  disabled={busy}
                />
                <div className="grid gap-1">
                  <label className="text-xs font-medium text-slate-600">有效期（天）</label>
                  <input
                    type="number"
                    min={1}
                    max={3650}
                    placeholder="留空表示不告知有效期"
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    value={benefit.validity_days ?? ""}
                    onChange={(e) =>
                      update(benefit.code, {
                        validity_days: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    disabled={busy}
                  />
                </div>
                <div className="grid gap-1 sm:col-span-2">
                  <label className="text-xs font-medium text-slate-600">
                    领取入口（所有客户同一个，网址或小店口令都行）
                  </label>
                  <input
                    type="text"
                    placeholder="https://… 或 #微信小店://店铺/优惠券/编号"
                    className="rounded-md border border-slate-300 px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    value={benefit.claim_url}
                    onChange={(e) => update(benefit.code, { claim_url: e.target.value })}
                    disabled={busy}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-4 text-xs leading-relaxed text-slate-500">
        系统只记录「领取入口已发出」，不知道客户领没领、用没用——商城还没打通，
        所以助教对客户只会说「可以领取」，不会说「已到账」。
        同一位客户的同一张券只会发一次。
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
