"use client";

import { useState, useEffect, useCallback } from "react";
import { API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertCircle, LoaderCircle } from "lucide-react";

type Option = { key: string; text: string };
type Question = { id: string; text: string; options: Option[]; ask_when: string };
type Script = {
  language: string;
  version_no: number | null;
  nodes: Record<string, { questions: Question[] }>;
};

const LANGUAGES = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
];

// 节点顺序与业务时序一致，不跟着后端 dict 的键顺序漂。
const NODE_ORDER = ["results", "goal", "benefit", "handoff"];

const NODE_LABEL: Record<string, string> = {
  results: "第26天 · 问这28天的结果与执行反馈",
  goal: "第27天 · 问下一阶段目标",
  benefit: "第28天报告后次日 · 告知权益 + 问产品服务意愿",
  handoff: "意愿有答案后 · 回应需求 + 问原因 + 问联系偏好",
};

const ASK_WHEN_LABEL: Record<string, string> = {
  always: "每位客户都问",
  other_health_need: "仅当客户提到其他健康困扰时问",
  intent_continue: "仅当客户想继续时问",
  intent_pause: "仅当客户暂停或不再购买时问",
};

async function readError(res: Response) {
  const raw = await res.text();
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // 网关层错误页不是 JSON，原文更有排查价值。
  }
  return raw || "请求失败";
}

export function FinalReviewScriptConfig() {
  const [language, setLanguage] = useState("zh");
  const [script, setScript] = useState<Script | null>(null);
  const [initial, setInitial] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showSuccess, setShowSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (lang: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/final-review-scripts?language=${lang}`);
      if (!res.ok) throw new Error(await readError(res));
      const data: Script = await res.json();
      setScript(data);
      setInitial(JSON.stringify(data.nodes));
    } catch (err) {
      console.error(err);
      setScript(null);
      setError(err instanceof Error ? `话术加载失败：${err.message}` : "话术加载失败，请刷新重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(language);
  }, [language, load]);

  function updateQuestion(node: string, questionId: string, patch: Partial<Question>) {
    setScript((prev) => {
      if (!prev) return prev;
      const questions = prev.nodes[node].questions.map((q) =>
        q.id === questionId ? { ...q, ...patch } : q,
      );
      return { ...prev, nodes: { ...prev.nodes, [node]: { questions } } };
    });
  }

  function updateOption(node: string, questionId: string, key: string, text: string) {
    setScript((prev) => {
      if (!prev) return prev;
      const questions = prev.nodes[node].questions.map((q) =>
        q.id === questionId
          ? { ...q, options: q.options.map((o) => (o.key === key ? { ...o, text } : o)) }
          : q,
      );
      return { ...prev, nodes: { ...prev.nodes, [node]: { questions } } };
    });
  }

  async function save() {
    if (!script) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/settings/final-review-scripts`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ language: script.language, nodes: script.nodes }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data: Script = await res.json();
      setScript(data);
      setInitial(JSON.stringify(data.nodes));
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 2000);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? `保存失败：${err.message}` : "保存失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  const isDirty = script != null && JSON.stringify(script.nodes) !== initial;

  useEffect(() => {
    // 改完话术不点保存就离开会白改；这一页的券配置那边已经真实发生过一次。
    if (!isDirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  });
  const nodeKeys = script
    ? Object.keys(script.nodes).sort(
        (a, b) =>
          (NODE_ORDER.indexOf(a) + 1 || 99) - (NODE_ORDER.indexOf(b) + 1 || 99),
      )
    : [];

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <h3 className="text-base font-semibold text-slate-900">结营复盘话术（第26-28天）</h3>
          <p className="mt-1 text-sm leading-relaxed text-slate-500">
            这里的题目和选项，助教会
            <strong className="font-semibold text-slate-700">一字不改地念给客户</strong>
            ，保存后下一个到点的客户就用新话术。
            选项文案随便改，但每个选项后面那个灰色标记是它的身份，改不了也删不了——
            系统靠它认出客户选的是哪一项（比如认出「想看看其他咖啡」才会发会员券）。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <select
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={busy || isDirty}
            title={isDirty ? "先保存或刷新，再切换语言" : undefined}
          >
            {LANGUAGES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          {showSuccess && <span className="animate-pulse text-sm font-semibold text-emerald-600">已保存！</span>}
          <Button
            type="button"
            className="shrink-0 bg-cyan-700 font-medium text-white shadow-sm transition-all hover:bg-cyan-800 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
            onClick={save}
            disabled={!isDirty}
            loading={busy}
            loadingText="保存中…"
          >
            保存话术
          </Button>
        </div>
      </div>

      {loading ? (
        <div
          className="flex min-h-32 items-center justify-center text-sm font-medium text-slate-500"
          aria-busy="true"
        >
          <LoaderCircle className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          正在加载话术…
        </div>
      ) : script ? (
        <>
          <p className="mb-4 text-xs text-slate-500">
            {script.version_no == null ? (
              <span className="font-medium text-amber-700">
                这套话术还没存进数据库，当前显示的是程序内置的初稿。点一次「保存话术」即可存入，
                在那之前改动不会生效。
              </span>
            ) : (
              <>当前是第 {script.version_no} 版。每次保存都会新存一版，改坏了把旧内容再存一次即可回退。</>
            )}
          </p>

          <div className="grid gap-5">
            {nodeKeys.map((node) => (
              <div key={node} className="rounded-lg border border-slate-200 bg-slate-50/50 p-4">
                <h4 className="mb-3 text-sm font-semibold text-slate-800">
                  {NODE_LABEL[node] ?? node}
                </h4>
                <div className="grid gap-4">
                  {script.nodes[node].questions.map((question) => (
                    <div key={question.id} className="rounded-md border border-slate-200 bg-white p-3">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-500">
                          {question.id}
                        </span>
                        <span className="text-xs text-slate-500">
                          {ASK_WHEN_LABEL[question.ask_when] ?? question.ask_when}
                        </span>
                      </div>
                      <textarea
                        className="w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm leading-relaxed outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                        rows={3}
                        value={question.text}
                        onChange={(e) => updateQuestion(node, question.id, { text: e.target.value })}
                        disabled={busy}
                      />
                      {question.options.length > 0 && (
                        <div className="mt-3 grid gap-2">
                          <span className="text-xs font-medium text-slate-600">选项</span>
                          {question.options.map((option) => (
                            <div key={option.key} className="flex items-center gap-2">
                              <input
                                type="text"
                                className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                                value={option.text}
                                onChange={(e) => updateOption(node, question.id, option.key, e.target.value)}
                                disabled={busy}
                              />
                              <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-400">
                                {option.key}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <p className="mt-4 text-xs leading-relaxed text-slate-500">
            题目里的
            <code className="mx-1 rounded bg-slate-100 px-1 py-0.5 font-mono">
              {script.language === "en" ? "{focus_items}" : "{关注项}"}
            </code>
            会换成这位客户入营时最在意的几件事（最多 3 项），
            <code className="mx-1 rounded bg-slate-100 px-1 py-0.5 font-mono">
              {script.language === "en" ? "{benefits}" : "{权益清单}"}
            </code>
            会换成上面已配置好的券。除这两个之外写别的花括号会保存失败。
          </p>
        </>
      ) : null}

      {error && (
        <p className="mt-3 flex items-start gap-1.5 text-sm font-medium text-rose-700" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  );
}
