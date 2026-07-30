"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AlertCircle, LoaderCircle } from "lucide-react";

export function RiskReplyConfig() {
  const [content, setContent] = useState("");
  const [initialContent, setInitialContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/risk-fixed-reply`);
        if (!res.ok) throw new Error((await res.text()) || "加载失败");
        const data = await res.json();
        if (data && data.content) {
          setContent(data.content);
          setInitialContent(data.content);
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

  async function save() {
    if (!content.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/risk-fixed-reply`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) throw new Error((await res.text()) || "保存失败");
      setInitialContent(content);
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
        正在加载高风险话术配置…
      </div>
    );
  }

  const isDirty = content !== initialContent;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">高风险拦截固定话术配置</h3>
          <p className="mt-1 text-sm text-slate-500 leading-relaxed">
            当用户触发转人工（健康红线、急性体感不适、投诉退款，或高风险用户咨询饮食/用药等需专业判断的问题）时，AI 不再自行回复，改为发送以下安抚话术并 @班主任接手处理。
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
      <Textarea
        className="min-h-28 text-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none transition-all"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="请输入拦截话术内容..."
        disabled={busy}
      />
      {error && (
        <p className="mt-3 flex items-start gap-1.5 text-sm font-medium text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
      <p className="mt-2 text-xs text-slate-400 leading-relaxed">
        * 发送时系统会自动在此话术末尾换行并追加强提醒（例如：<code>@班主任 麻烦有空时接手跟进一下哈～</code>），无需在配置中手动填写艾特标记。
      </p>
    </div>
  );
}
