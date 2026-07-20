"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";

export function StageReportConfig() {
  const [generateAt, setGenerateAt] = useState("21:00");
  const [initial, setInitial] = useState("21:00");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/stage-report-schedule`);
        const data = await res.json();
        if (data && data.generate_at) {
          setGenerateAt(data.generate_at);
          setInitial(data.generate_at);
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoaded(true);
      }
    }
    load();
  }, []);

  async function save() {
    if (!generateAt) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/settings/stage-report-schedule`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ generate_at: generateAt }),
      });
      const data = await res.json();
      const saved = (data && data.generate_at) || generateAt;
      setGenerateAt(saved);
      setInitial(saved);
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 2000);
    } catch (err) {
      console.error(err);
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return null;

  const isDirty = generateAt !== initial;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">阶段报告生成时间</h3>
          <p className="mt-1 text-sm text-slate-500 leading-relaxed">
            阶段报告（3 / 7 / 14 / 21 / 28 天）每天到设定时间集中生成，并进入待审核。建议设在晚间，运营当晚集中审核、用户次日早上看到。（北京时间，默认 21:00）
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {showSuccess && <span className="text-sm text-emerald-600 font-semibold animate-pulse">已保存！</span>}
          <Button
            type="button"
            className="shrink-0 bg-cyan-700 hover:bg-cyan-800 text-white font-medium shadow-sm transition-all active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none"
            onClick={save}
            disabled={busy || !isDirty}
          >
            {busy ? "保存中..." : "保存配置"}
          </Button>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <label htmlFor="stage-report-generate-at" className="text-sm text-slate-700">
          每天生成时间
        </label>
        <input
          id="stage-report-generate-at"
          type="time"
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none transition-all"
          value={generateAt}
          onChange={(e) => setGenerateAt(e.target.value)}
          disabled={busy}
        />
      </div>
    </div>
  );
}
