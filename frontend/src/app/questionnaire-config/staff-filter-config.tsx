"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AlertCircle, LoaderCircle, Trash2, Plus } from "lucide-react";

type Rule = {
  type: string; // "equals" or "contains"
  pattern: string;
};

export function StaffFilterConfig() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [initialRules, setInitialRules] = useState<Rule[]>([]);
  const [newPattern, setNewPattern] = useState("");
  const [newType, setNewType] = useState("equals");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/qiwei-staff-senders`);
        if (!res.ok) throw new Error((await res.text()) || "加载失败");
        const data = await res.json();
        if (data && Array.isArray(data.rules)) {
          setRules(data.rules);
          setInitialRules(data.rules);
        }
      } catch (err) {
        console.error(err);
        setError("配置加载失败，请刷新页面后重试。");
      } finally {
        setLoaded(true);
      }
    }
    load();
  }, []);

  function addRule() {
    const trimmed = newPattern.trim();
    if (!trimmed) return;
    // Prevent duplicate rules
    if (rules.some(r => r.type === newType && r.pattern === trimmed)) return;
    setRules([...rules, { type: newType, pattern: trimmed }]);
    setNewPattern("");
  }

  function removeRule(index: number) {
    const next = [...rules];
    next.splice(index, 1);
    setRules(next);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/qiwei-staff-senders`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ rules }),
      });
      if (!res.ok) throw new Error((await res.text()) || "保存失败");
      setInitialRules(rules);
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
      <div className="flex min-h-32 items-center justify-center rounded-xl border border-slate-200 bg-white p-6 text-sm font-medium text-slate-500" aria-busy="true">
        <LoaderCircle className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        正在加载免打扰配置…
      </div>
    );
  }

  const isDirty = JSON.stringify(rules) !== JSON.stringify(initialRules);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">企微人工回复免打扰配置</h3>
          <p className="mt-1 text-sm text-slate-500 leading-relaxed">
            在群内代表人工客服、助教、班主任或客户本人的昵称/ID规则。符合规则的发言将不会触发AI自动回复，并会被标记为“班主任”发出的聊天记录。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
        {/* Form to add a rule */}
        <div className="flex flex-wrap items-center gap-3 bg-slate-50 p-4 rounded-lg border border-slate-100">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-semibold text-slate-600 mb-1">匹配内容</label>
            <Input
              type="text"
              placeholder="例如：上海 Lucy 或 奢咖..."
              value={newPattern}
              onChange={(e) => setNewPattern(e.target.value)}
              disabled={busy}
              className="w-full bg-white border-slate-200 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-all"
            />
          </div>
          <div className="w-[120px]">
            <label className="block text-xs font-semibold text-slate-600 mb-1">匹配方式</label>
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              disabled={busy}
              className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="equals">等于 (全匹配)</option>
              <option value="contains">包含 (半匹配)</option>
            </select>
          </div>
          <div className="self-end">
            <Button
              type="button"
              onClick={addRule}
              disabled={busy || !newPattern.trim()}
              className="bg-slate-900 hover:bg-slate-800 text-white font-medium shadow-sm transition-all active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none gap-1"
            >
              <Plus className="h-4 w-4" /> 添加规则
            </Button>
          </div>
        </div>

        {/* Rules list */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-slate-600">已配置规则列表</div>
          {rules.length === 0 ? (
            <div className="text-sm text-slate-400 italic py-2">暂无规则，当前默认为空，或仅依赖环境变量。</div>
          ) : (
            <div className="divide-y divide-slate-100 border border-slate-100 rounded-lg overflow-hidden bg-white max-h-60 overflow-y-auto">
              {rules.map((rule, idx) => (
                <div key={idx} className="flex items-center justify-between p-3 hover:bg-slate-50/50 transition-colors">
                  <div className="flex items-center gap-3">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-medium ${
                        rule.type === "equals"
                          ? "bg-blue-50 text-blue-700 border border-blue-100"
                          : "bg-purple-50 text-purple-700 border border-purple-100"
                      }`}
                    >
                      {rule.type === "equals" ? "完全匹配" : "包含匹配"}
                    </span>
                    <span className="text-sm font-medium text-slate-800">{rule.pattern}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeRule(idx)}
                    disabled={busy}
                    className="p-1.5 text-slate-400 hover:text-red-600 rounded transition-colors"
                    title="删除规则"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

      </div>
      {error && (
        <p className="mt-3 flex items-start gap-1.5 text-sm font-medium text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  );
}
