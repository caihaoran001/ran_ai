"use client";

import { useState, useEffect } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type QiweAccount = {
  guid: string;
  token_masked: string;
  token_set: boolean;
};

export function QiweAccountConfig() {
  const [guid, setGuid] = useState("");
  const [token, setToken] = useState("");
  const [current, setCurrent] = useState<QiweAccount | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/settings/qiwe-account`);
        const data = (await res.json()) as QiweAccount;
        setCurrent(data);
        setGuid(data.guid ?? "");
      } catch (err) {
        console.error(err);
      } finally {
        setLoaded(true);
      }
    }
    load();
  }, []);

  async function save() {
    setError("");
    if (!guid.trim() || !token.trim()) {
      setError("GUID 和 Token 均不能为空");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/settings/qiwe-account`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ guid: guid.trim(), token: token.trim() }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "保存失败");
      }
      const data = (await res.json()) as QiweAccount;
      setCurrent(data);
      setGuid(data.guid ?? "");
      setToken("");
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 3000);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900">企微账号切换</h3>
          <p className="mt-1 text-sm text-slate-500 leading-relaxed">
            当企微账号被风控或需要更换机器人时，填写新账号的 GUID 与 Token 即可即时切换，
            无需联系开发或重启服务。保存后立即对后续消息收发生效。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {showSuccess && (
            <span className="text-sm text-emerald-600 font-semibold animate-pulse">已切换！</span>
          )}
          <Button
            type="button"
            className="shrink-0 bg-cyan-700 hover:bg-cyan-800 text-white font-medium shadow-sm transition-all active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none"
            onClick={save}
            disabled={busy}
          >
            {busy ? "切换中..." : "保存并切换"}
          </Button>
        </div>
      </div>

      {current && (
        <div className="mb-4 rounded-lg bg-slate-50 border border-slate-200 px-4 py-3 text-sm text-slate-600">
          <span className="font-medium text-slate-700">当前生效账号：</span>
          <span className="ml-1">GUID {current.guid || "（未配置）"}</span>
          <span className="ml-3">
            Token {current.token_set ? current.token_masked : "（未配置）"}
          </span>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="grid gap-1.5">
          <span className="text-sm font-medium text-slate-700">GUID</span>
          <Input
            value={guid}
            onChange={(e) => setGuid(e.target.value)}
            placeholder="请输入新账号 GUID"
            disabled={busy}
          />
        </label>
        <label className="grid gap-1.5">
          <span className="text-sm font-medium text-slate-700">Token</span>
          <Input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="请输入新账号 Token"
            autoComplete="off"
            disabled={busy}
          />
        </label>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      <p className="mt-3 text-xs text-slate-400 leading-relaxed">
        * 出于安全考虑，页面不回显完整 Token，只展示尾号掩码；切换时需重新完整填写 GUID 与 Token。
      </p>
    </div>
  );
}
