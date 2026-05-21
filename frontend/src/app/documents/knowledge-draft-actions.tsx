"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE, KnowledgeEvolutionSetting, KnowledgeFAQ } from "@/lib/api";

export function KnowledgeDraftActions({
  draftId,
  initialTitle,
  initialQuestion,
  initialAnswer,
  initialCategory,
  initialTags,
}: {
  draftId: number;
  initialTitle: string;
  initialQuestion: string;
  initialAnswer: string;
  initialCategory?: string | null;
  initialTags?: string[] | null;
}) {
  const router = useRouter();
  const [title, setTitle] = useState(initialTitle);
  const [question, setQuestion] = useState(initialQuestion);
  const [answer, setAnswer] = useState(initialAnswer);
  const [category, setCategory] = useState(initialCategory || "");
  const [tags, setTags] = useState((initialTags || []).join("、"));
  const [busy, setBusy] = useState(false);

  async function save() {
    await fetch(`${API_BASE}/knowledge-drafts/${draftId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title,
        question,
        answer,
        category,
        tags: tags.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
      }),
    });
  }

  async function approve() {
    setBusy(true);
    await save();
    await fetch(`${API_BASE}/knowledge-drafts/${draftId}/approve`, { method: "POST" });
    setBusy(false);
    router.refresh();
  }

  async function reject() {
    setBusy(true);
    await fetch(`${API_BASE}/knowledge-drafts/${draftId}/reject`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reason: "人工审核驳回" }),
    });
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="space-y-3">
      <input
        className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm font-medium"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <input
        className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
        placeholder="分类"
        value={category}
        onChange={(event) => setCategory(event.target.value)}
      />
      <input
        className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
        placeholder="标签，用顿号分隔"
        value={tags}
        onChange={(event) => setTags(event.target.value)}
      />
      <Textarea className="min-h-20 text-sm" value={question} onChange={(event) => setQuestion(event.target.value)} />
      <Textarea className="min-h-32 text-sm" value={answer} onChange={(event) => setAnswer(event.target.value)} />
      <div className="flex flex-wrap gap-2">
        <Button type="button" className="bg-emerald-700 hover:bg-emerald-600" onClick={approve} disabled={busy}>
          {busy ? "处理中..." : "批准入库"}
        </Button>
        <Button
          type="button"
          className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
          onClick={reject}
          disabled={busy}
        >
          驳回
        </Button>
      </div>
    </div>
  );
}

export function KnowledgeDraftScanButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function scan(mode: "recent_qa" | "stage_cases") {
    setBusy(true);
    setMessage("");
    const response = await fetch(`${API_BASE}/knowledge-drafts/scan`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode, hours: 72, limit: 300 }),
    });
    const data = await response.json().catch(() => null);
    setMessage(response.ok ? `新增 ${data?.created ?? 0} 条，跳过 ${data?.skipped ?? 0} 条` : "生成失败");
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
        onClick={() => scan("recent_qa")}
        disabled={busy}
      >
        {busy ? "扫描中..." : "提取近3天FAQ"}
      </Button>
      <Button
        type="button"
        className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
        onClick={() => scan("stage_cases")}
        disabled={busy}
      >
        {busy ? "扫描中..." : "检查阶段案例"}
      </Button>
      {message && <span className="text-sm text-zinc-500">{message}</span>}
    </div>
  );
}

export function KnowledgeEvolutionConfigPanel({ initialConfig }: { initialConfig: KnowledgeEvolutionSetting }) {
  const router = useRouter();
  const [config, setConfig] = useState(initialConfig);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function save() {
    setBusy(true);
    setMessage("");
    const response = await fetch(`${API_BASE}/settings/knowledge-evolution`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(config),
    });
    setMessage(response.ok ? "已保存" : "保存失败");
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-900">知识自我进化配置</h3>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500">
            控制每日 FAQ 草稿、阶段优秀案例草稿的后台定时扫描。未审核内容不会自动上线。
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-zinc-700">
          <input
            type="checkbox"
            checked={config.enabled}
            onChange={(event) => setConfig({ ...config, enabled: event.target.checked })}
          />
          启用定时扫描
        </label>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <Field label="扫描间隔（秒）">
          <input
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            type="number"
            min={60}
            max={86400}
            value={config.interval_seconds}
            onChange={(event) => setConfig({ ...config, interval_seconds: Number(event.target.value) })}
          />
        </Field>
        <Field label="每日 FAQ 扫描时间">
          <input
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            type="time"
            value={config.qa_at}
            onChange={(event) => setConfig({ ...config, qa_at: event.target.value })}
          />
        </Field>
        <Field label="时区">
          <input
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            value={config.timezone}
            onChange={(event) => setConfig({ ...config, timezone: event.target.value })}
          />
        </Field>
        <Field label="每日聊天读取上限">
          <input
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            type="number"
            min={20}
            max={2000}
            value={config.daily_qa_limit}
            onChange={(event) => setConfig({ ...config, daily_qa_limit: Number(event.target.value) })}
          />
        </Field>
        <Field label="阶段案例天数">
          <input
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            value={config.case_days}
            placeholder="3,7,14,21,28"
            onChange={(event) => setConfig({ ...config, case_days: event.target.value })}
          />
        </Field>
        <Field label="每个服务单读取上限">
          <input
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            type="number"
            min={20}
            max={1000}
            value={config.case_limit_per_case}
            onChange={(event) => setConfig({ ...config, case_limit_per_case: Number(event.target.value) })}
          />
        </Field>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <Button type="button" onClick={save} disabled={busy}>
          {busy ? "保存中..." : "保存配置"}
        </Button>
        {message && <span className="text-sm text-zinc-500">{message}</span>}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      {children}
    </label>
  );
}

export function KnowledgeIndexSyncButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function sync(embed: boolean) {
    setBusy(true);
    setMessage("");
    const response = await fetch(`${API_BASE}/knowledge-base/index/sync`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ embed }),
    });
    const data = await response.json().catch(() => null);
    if (response.ok) {
      setMessage(
        `扫描 ${data?.scanned ?? 0} 块，新增 ${data?.created ?? 0}，更新 ${data?.updated ?? 0}，向量化 ${data?.embedded ?? 0}`
      );
    } else {
      setMessage("同步失败");
    }
    setBusy(false);
    router.refresh();
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
        onClick={() => sync(false)}
        disabled={busy}
      >
        {busy ? "同步中..." : "同步知识块"}
      </Button>
      <Button
        type="button"
        className="bg-zinc-900 text-white hover:bg-zinc-700"
        onClick={() => sync(true)}
        disabled={busy}
      >
        {busy ? "向量化中..." : "同步并向量化"}
      </Button>
      {message && <span className="text-sm text-zinc-500">{message}</span>}
    </div>
  );
}

export function KnowledgeFAQEditor({ faqs }: { faqs: KnowledgeFAQ[] }) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState({
    title: "",
    question: "",
    answer: "",
    category: "",
    tags: "",
  });

  async function createFaq() {
    setBusyId("new");
    await fetch(`${API_BASE}/knowledge-faqs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title: draft.title,
        question: draft.question,
        answer: draft.answer,
        category: draft.category || null,
        tags: splitTags(draft.tags),
      }),
    });
    setDraft({ title: "", question: "", answer: "", category: "", tags: "" });
    setCreating(false);
    setBusyId(null);
    router.refresh();
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-zinc-700">结构化 FAQ</h3>
          <p className="mt-1 text-xs text-zinc-500">支持新增、编辑、禁用和版本递增；启用状态会同步到知识索引。</p>
        </div>
        <Button
          type="button"
          className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50"
          onClick={() => setCreating((value) => !value)}
        >
          {creating ? "收起" : "新增 FAQ"}
        </Button>
      </div>

      {creating && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/40 p-3">
          <input
            className="mb-2 w-full rounded-md border border-zinc-200 px-3 py-2 text-sm font-medium"
            placeholder="标题"
            value={draft.title}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
          <input
            className="mb-2 w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            placeholder="分类"
            value={draft.category}
            onChange={(event) => setDraft({ ...draft, category: event.target.value })}
          />
          <input
            className="mb-2 w-full rounded-md border border-zinc-200 px-3 py-2 text-sm"
            placeholder="标签，用顿号分隔"
            value={draft.tags}
            onChange={(event) => setDraft({ ...draft, tags: event.target.value })}
          />
          <Textarea
            className="mb-2 min-h-16 text-sm"
            placeholder="用户常见问法"
            value={draft.question}
            onChange={(event) => setDraft({ ...draft, question: event.target.value })}
          />
          <Textarea
            className="mb-2 min-h-24 text-sm"
            placeholder="参考回复"
            value={draft.answer}
            onChange={(event) => setDraft({ ...draft, answer: event.target.value })}
          />
          <Button type="button" onClick={createFaq} disabled={busyId === "new" || !draft.title || !draft.question || !draft.answer}>
            {busyId === "new" ? "保存中..." : "保存 FAQ"}
          </Button>
        </div>
      )}

      <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
        {faqs.map((faq) => (
          <KnowledgeFAQRow key={faq.id} faq={faq} busy={busyId === faq.id} setBusy={setBusyId} />
        ))}
        {faqs.length === 0 && (
          <div className="rounded-lg border border-dashed border-zinc-200 py-10 text-center text-sm text-zinc-400">
            暂无结构化 FAQ
          </div>
        )}
      </div>
    </div>
  );
}

function KnowledgeFAQRow({
  faq,
  busy,
  setBusy,
}: {
  faq: KnowledgeFAQ;
  busy: boolean;
  setBusy: (value: number | null) => void;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(faq.title);
  const [question, setQuestion] = useState(faq.question);
  const [answer, setAnswer] = useState(faq.answer);
  const [category, setCategory] = useState(faq.category || "");
  const [tags, setTags] = useState((faq.tags || []).join("、"));

  async function save() {
    setBusy(faq.id);
    await fetch(`${API_BASE}/knowledge-faqs/${faq.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title,
        question,
        answer,
        category,
        tags: splitTags(tags),
        change_note: "后台编辑更新",
      }),
    });
    setBusy(null);
    setEditing(false);
    router.refresh();
  }

  async function setStatus(action: "enable" | "disable") {
    setBusy(faq.id);
    await fetch(`${API_BASE}/knowledge-faqs/${faq.id}/${action}`, { method: "POST" });
    setBusy(null);
    router.refresh();
  }

  return (
    <div className="rounded-lg border border-zinc-200 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <span className={faq.status === "active" ? "text-emerald-700" : "text-zinc-500"}>
          {faq.status === "active" ? "启用" : faq.status === "disabled" ? "已禁用" : faq.status}
        </span>
        <span>v{faq.version}</span>
        {faq.category && <span>{faq.category}</span>}
      </div>
      {editing ? (
        <div className="space-y-2">
          <input className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm font-medium" value={title} onChange={(event) => setTitle(event.target.value)} />
          <input className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm" value={category} onChange={(event) => setCategory(event.target.value)} />
          <input className="w-full rounded-md border border-zinc-200 px-3 py-2 text-sm" value={tags} onChange={(event) => setTags(event.target.value)} />
          <Textarea className="min-h-16 text-sm" value={question} onChange={(event) => setQuestion(event.target.value)} />
          <Textarea className="min-h-24 text-sm" value={answer} onChange={(event) => setAnswer(event.target.value)} />
          <div className="flex gap-2">
            <Button type="button" onClick={save} disabled={busy}>{busy ? "保存中..." : "保存新版本"}</Button>
            <Button type="button" className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50" onClick={() => setEditing(false)}>
              取消
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <div className="font-medium text-zinc-900">{faq.title}</div>
          <p className="mt-2 line-clamp-2 text-sm text-zinc-600">{faq.question}</p>
          <p className="mt-1 line-clamp-2 text-sm text-zinc-500">{faq.answer}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="button" className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50" onClick={() => setEditing(true)}>
              编辑
            </Button>
            {faq.status === "active" ? (
              <Button type="button" className="border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50" onClick={() => setStatus("disable")} disabled={busy}>
                禁用
              </Button>
            ) : (
              <Button type="button" className="bg-emerald-700 hover:bg-emerald-600" onClick={() => setStatus("enable")} disabled={busy}>
                启用
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function splitTags(value: string): string[] {
  return value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
}
