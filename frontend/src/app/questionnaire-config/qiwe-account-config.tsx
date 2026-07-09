"use client";

import { useState, useEffect, useCallback } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type QiweEntry = {
  guid: string;
  nickname: string;
  alias: string;
  avatar_url: string;
  real_name: string;
  note: string;
  last_used_at: string | null;
  saved_at: string | null;
  is_current: boolean;
  token_masked: string;
  token_set: boolean;
};

type QiweState = {
  current: QiweEntry | null;
  history: QiweEntry[];
};

function displayName(e: QiweEntry): string {
  return e.nickname || e.alias || e.real_name || "未命名账号";
}

function shortGuid(guid: string): string {
  if (guid.length <= 12) return guid;
  return `${guid.slice(0, 6)}…${guid.slice(-4)}`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { hour12: false });
}

function Avatar({ entry }: { entry: QiweEntry }) {
  const name = displayName(entry);
  if (entry.avatar_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={entry.avatar_url}
        alt={name}
        className="h-10 w-10 shrink-0 rounded-full object-cover border border-slate-200"
      />
    );
  }
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-cyan-100 text-sm font-semibold text-cyan-700">
      {name.slice(0, 1)}
    </div>
  );
}

export function QiweAccountConfig() {
  const [state, setState] = useState<QiweState>({ current: null, history: [] });
  const [guid, setGuid] = useState("");
  const [token, setToken] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [switchingGuid, setSwitchingGuid] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "warn" | "error"; text: string } | null>(null);
  const [editingNoteGuid, setEditingNoteGuid] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [savingNote, setSavingNote] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/settings/qiwe-account`);
      const data = (await res.json()) as QiweState;
      setState(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function addAccount() {
    setMessage(null);
    if (!guid.trim() || !token.trim()) {
      setMessage({ kind: "error", text: "GUID 和 Token 均不能为空" });
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/settings/qiwe-account`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ guid: guid.trim(), token: token.trim() }),
      });
      if (!res.ok) throw new Error((await res.text()) || "保存失败");
      const data = (await res.json()) as { state: QiweState; warning: string };
      setState(data.state);
      setGuid("");
      setToken("");
      setMessage(
        data.warning
          ? { kind: "warn", text: data.warning }
          : { kind: "ok", text: "已保存并切换到新账号" },
      );
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "保存失败" });
    } finally {
      setBusy(false);
    }
  }

  async function switchAccount(entry: QiweEntry) {
    if (entry.is_current) return;
    const ok = window.confirm(
      `确认切换到账号「${displayName(entry)}」(GUID ${shortGuid(entry.guid)}) 吗？\n切换后所有群消息将立即改用该账号收发。`,
    );
    if (!ok) return;
    setMessage(null);
    setSwitchingGuid(entry.guid);
    try {
      const res = await fetch(`${API_BASE}/settings/qiwe-account/switch`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ guid: entry.guid }),
      });
      if (!res.ok) throw new Error((await res.text()) || "切换失败");
      const data = (await res.json()) as { state: QiweState; warning: string };
      setState(data.state);
      setMessage(
        data.warning
          ? { kind: "warn", text: data.warning }
          : { kind: "ok", text: `已切换到「${displayName(entry)}」` },
      );
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "切换失败" });
    } finally {
      setSwitchingGuid(null);
    }
  }

  function startEditNote(entry: QiweEntry) {
    setEditingNoteGuid(entry.guid);
    setNoteDraft(entry.note || "");
    setMessage(null);
  }

  function cancelEditNote() {
    setEditingNoteGuid(null);
    setNoteDraft("");
  }

  async function saveNote(entry: QiweEntry) {
    setSavingNote(true);
    setMessage(null);
    try {
      const res = await fetch(`${API_BASE}/settings/qiwe-account/note`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ guid: entry.guid, note: noteDraft.trim() }),
      });
      if (!res.ok) throw new Error((await res.text()) || "备注保存失败");
      const data = (await res.json()) as { state: QiweState; warning: string };
      setState(data.state);
      setEditingNoteGuid(null);
      setNoteDraft("");
      setMessage({ kind: "ok", text: "备注已保存" });
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "备注保存失败" });
    } finally {
      setSavingNote(false);
    }
  }

  function renderNote(entry: QiweEntry) {
    if (editingNoteGuid === entry.guid) {
      return (
        <div className="mt-1.5 flex items-center gap-2">
          <Input
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            placeholder="填写备注，如「主号-张三的手机」"
            className="h-7 text-xs"
            maxLength={40}
            disabled={savingNote}
            autoFocus
          />
          <button
            type="button"
            onClick={() => saveNote(entry)}
            disabled={savingNote}
            className="shrink-0 rounded-md bg-cyan-700 px-2 py-1 text-xs font-medium text-white hover:bg-cyan-800 disabled:opacity-50"
          >
            {savingNote ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            onClick={cancelEditNote}
            disabled={savingNote}
            className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-slate-500 hover:text-slate-700 disabled:opacity-50"
          >
            取消
          </button>
        </div>
      );
    }
    return (
      <div className="mt-1 flex items-center gap-2">
        {entry.note ? (
          <span className="truncate rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700">
            备注：{entry.note}
          </span>
        ) : (
          <span className="text-xs text-slate-400">暂无备注</span>
        )}
        <button
          type="button"
          onClick={() => startEditNote(entry)}
          className="shrink-0 text-xs font-medium text-cyan-700 hover:text-cyan-800"
        >
          {entry.note ? "编辑备注" : "添加备注"}
        </button>
      </div>
    );
  }

  if (!loaded) return null;

  const messageColor =
    message?.kind === "ok"
      ? "text-emerald-600"
      : message?.kind === "warn"
        ? "text-amber-600"
        : "text-red-600";

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">企微账号切换</h3>
        <p className="mt-1 text-sm text-slate-500 leading-relaxed">
          当企微账号被风控或需要更换机器人时，填写新账号的 GUID 与 Token 即可即时切换，无需联系开发或重启服务。
          已配置过的账号会记录在下方，点击即可一键切回（无需重新输入凭证）。保存后立即对后续消息收发生效。
        </p>
      </div>

      {/* 当前生效账号 */}
      <div className="mb-5 flex items-center gap-3 rounded-lg border border-cyan-200 bg-cyan-50/60 px-4 py-3">
        {state.current ? (
          <>
            <Avatar entry={state.current} />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-semibold text-slate-900">
                  {displayName(state.current)}
                </span>
                <span className="shrink-0 rounded-full bg-cyan-600 px-2 py-0.5 text-[11px] font-medium text-white">
                  当前生效
                </span>
              </div>
              <div className="mt-0.5 truncate text-xs text-slate-500">
                GUID {shortGuid(state.current.guid)} · Token {state.current.token_masked || "—"}
              </div>
              {renderNote(state.current)}
            </div>
          </>
        ) : (
          <span className="text-sm text-slate-500">尚未配置企微账号</span>
        )}
      </div>

      {/* 历史账号列表（不含当前账号，只列可切换的其他账号） */}
      {state.history.filter((e) => !e.is_current).length > 0 && (
        <div className="mb-5">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            历史账号（点击切换）
          </p>
          <div className="grid gap-2">
            {state.history
              .filter((e) => !e.is_current)
              .map((entry) => (
              <div
                key={entry.guid}
                className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5"
              >
                <Avatar entry={entry} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-slate-900">
                      {displayName(entry)}
                    </span>
                  </div>
                  <div className="mt-0.5 truncate text-xs text-slate-500">
                    GUID {shortGuid(entry.guid)}
                    {entry.last_used_at ? ` · 上次使用 ${formatTime(entry.last_used_at)}` : ""}
                  </div>
                  {renderNote(entry)}
                </div>
                <button
                  type="button"
                  onClick={() => switchAccount(entry)}
                  disabled={switchingGuid !== null}
                  className="shrink-0 rounded-md border border-cyan-300 px-2.5 py-1 text-xs font-medium text-cyan-700 transition-all hover:border-cyan-400 hover:bg-cyan-50 active:scale-[0.98] disabled:opacity-50"
                >
                  {switchingGuid === entry.guid ? "切换中…" : "切换"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 新增/切换到新账号 */}
      <div className="rounded-lg border border-slate-200 p-4">
        <p className="mb-3 text-sm font-medium text-slate-700">添加新账号并切换</p>
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
        <div className="mt-3 flex items-center gap-3">
          <Button
            type="button"
            className="bg-cyan-700 hover:bg-cyan-800 text-white font-medium shadow-sm transition-all active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none"
            onClick={addAccount}
            disabled={busy}
          >
            {busy ? "保存中…" : "保存并切换"}
          </Button>
          <span className="text-xs text-slate-400">
            保存时会自动校验凭证并获取账号昵称；出于安全，Token 不回显完整内容。
          </span>
        </div>
      </div>

      {message && <p className={`mt-3 text-sm ${messageColor}`}>{message.text}</p>}
    </div>
  );
}
