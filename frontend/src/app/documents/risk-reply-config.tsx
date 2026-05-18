"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function RiskReplyConfig() {
  const [content, setContent] = useState("");
  const [initialContent, setInitialContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/risk-fixed-reply`);
        const data = await res.json();
        if (data && data.content) {
          setContent(data.content);
          setInitialContent(data.content);
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
    if (!content.trim()) return;
    setBusy(true);
    try {
      await fetch(`${API_BASE}/settings/risk-fixed-reply`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content }),
      });
      setInitialContent(content);
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 2000);
    } catch (err) {
      console.error(err);
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return null;

  const isDirty = content !== initialContent;

  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm md:col-span-2">
      <div className="mb-3">
        <h3 className="font-semibold text-zinc-900">高风险拦截固定话术配置</h3>
        <p className="mt-1 text-sm text-zinc-500">
          当识别到高风险用户或涉及医疗/红线词汇时，AI 将自动停止发散回复，统一使用以下固定安全话术引导用户。
        </p>
      </div>
      <div className="space-y-3">
        <Textarea
          className="min-h-24 text-sm"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="请输入拦截话术..."
          disabled={busy}
        />
        <div className="flex items-center gap-3">
          <Button
            type="button"
            className="bg-zinc-900 text-white hover:bg-zinc-800"
            onClick={save}
            disabled={busy || !isDirty}
          >
            {busy ? "保存中..." : "保存话术配置"}
          </Button>
          {showSuccess && <span className="text-sm text-emerald-600">已保存！</span>}
        </div>
      </div>
    </div>
  );
}
