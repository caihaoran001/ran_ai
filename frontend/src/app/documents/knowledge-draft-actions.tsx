"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  API_BASE,
  KnowledgeBusinessConflict,
  KnowledgeFAQ,
} from "@/lib/api";
import {
  Tag,
  BookOpen,
  Sparkles,
  Database,
  Edit2,
  Check,
  X,
  Compass,
  SlidersHorizontal,
  ChevronRight,
  AlertTriangle,
} from "lucide-react";

export function KnowledgeDraftActions({
  draftId,
  draftType = "qa",
  initialTitle,
  initialQuestion,
  initialAnswer,
  initialCategory,
  initialTags,
}: {
  draftId: number;
  draftType?: string;
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
  const [busyAction, setBusyAction] = useState<"approve" | "use_candidate" | "keep_existing" | "reject" | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [conflict, setConflict] = useState<KnowledgeBusinessConflict | null>(null);
  const [message, setMessage] = useState("");

  async function save(): Promise<boolean> {
    const response = await fetch(`${API_BASE}/knowledge-drafts/${draftId}`, {
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
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      setMessage(errorMessage(payload, "草稿保存失败，请稍后重试"));
      return false;
    }
    return true;
  }

  async function approve(resolution?: "use_candidate" | "keep_existing") {
    setBusyAction(resolution ?? "approve");
    setBusy(true);
    setMessage("");
    try {
      if (!resolution && !(await save())) {
        return;
      }
      const response = await fetch(`${API_BASE}/knowledge-drafts/${draftId}/approve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(resolution ? { resolution } : {}),
      });
      const payload = await response.json().catch(() => null);
      if (
        response.status === 409
        && payload?.detail?.code === "knowledge_business_conflict"
      ) {
        setConflict(payload.detail as KnowledgeBusinessConflict);
        setMessage("");
        return;
      }
      if (!response.ok) {
        setMessage(errorMessage(payload, "查重或入库失败，草稿仍保留在待审核列表"));
        return;
      }
      setConflict(null);
      setMessage(
        payload?.status === "rejected"
          ? payload?.rejected_reason || "现有知识已覆盖，本次候选无需重复入库"
          : "已完成查重并入库",
      );
      router.refresh();
    } catch {
      setMessage("网络请求失败，草稿仍保留在待审核列表");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  async function reject() {
    setBusyAction("reject");
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${API_BASE}/knowledge-drafts/${draftId}/reject`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: "人工审核驳回" }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        setMessage(errorMessage(payload, "驳回失败，请稍后重试"));
        return;
      }
      router.refresh();
    } catch {
      setMessage("网络请求失败，请稍后重试");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  if (!isEditing) {
    return (
      <div className="space-y-4 pt-2">
        {/* Title */}
        <div className="flex items-start gap-2 pt-1">
          <BookOpen className="h-4 w-4 mt-0.5 text-slate-400 shrink-0" />
          <div className="text-sm font-bold text-slate-800 leading-snug">
            {title || <span className="italic text-slate-400 font-normal">（暂无设定标题）</span>}
          </div>
        </div>
        
        {/* Question block */}
        <div className="group rounded-xl bg-slate-50 border border-slate-200/60 p-3.5 shadow-3xs hover:bg-slate-50/80 hover:border-slate-300 transition-all duration-200">
          <div className="flex items-center gap-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
            <Compass className="h-3.5 w-3.5 text-blue-500" />
            <span>{draftType === "case" ? "案例背景与适用场景" : "用户常见提问方式"}</span>
          </div>
          <p className="text-sm text-slate-700 leading-relaxed font-normal whitespace-pre-wrap pl-3.5 border-l-2 border-slate-200">
            {question}
          </p>
        </div>

        {/* Answer block */}
        <div className="group rounded-xl bg-blue-50/10 border border-blue-100/60 p-3.5 shadow-3xs hover:bg-blue-50/20 hover:border-blue-200 transition-all duration-200">
          <div className="flex items-center gap-1.5 text-[10px] font-bold text-blue-700/60 uppercase tracking-wider mb-2">
            <Sparkles className="h-3.5 w-3.5 text-amber-500 animate-pulse" />
            <span>{draftType === "case" ? "金牌服务过程与改善结果" : "推荐业务标准回复"}</span>
          </div>
          <p className="text-sm text-slate-700 leading-relaxed font-normal whitespace-pre-wrap pl-3.5 border-l-2 border-blue-200">
            {answer}
          </p>
        </div>

        {conflict && (
          <div
            className="rounded-xl border border-amber-300 bg-amber-50/70 p-4"
            role="alert"
          >
            <div className="flex items-start gap-2.5">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
              <div>
                <h4 className="text-sm font-bold text-amber-950">
                  同一{draftType === "case" ? "案例" : "问题"}存在不同业务口径
                </h4>
                <p className="mt-1 text-xs leading-relaxed text-amber-900">
                  {conflict.reason}
                </p>
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <ConflictVersion
                label="已入库口径"
                question={conflict.existing.question}
                answer={conflict.existing.answer}
                tone="existing"
              />
              <ConflictVersion
                label="本次候选口径"
                question={conflict.candidate.question}
                answer={conflict.candidate.answer}
                tone="candidate"
              />
            </div>
            <p className="mt-3 text-xs leading-relaxed text-amber-900">
              请按实际业务规则选择正确内容；系统会自动处理版本留痕和检索索引。
            </p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <Button
                type="button"
                className="h-11 bg-blue-600 px-4 text-xs font-semibold text-white hover:bg-blue-500"
                onClick={() => approve("use_candidate")}
                disabled={busy}
                loading={busyAction === "use_candidate"}
                loadingText="保存选择中…"
              >
                采用本次候选口径
              </Button>
              <Button
                type="button"
                className="h-11 border border-slate-300 bg-white px-4 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                onClick={() => approve("keep_existing")}
                disabled={busy}
                loading={busyAction === "keep_existing"}
                loadingText="保存选择中…"
              >
                保留已入库口径
              </Button>
              <Button
                type="button"
                className="h-11 border border-amber-300 bg-amber-50 px-4 text-xs font-semibold text-amber-900 hover:bg-amber-100 sm:col-span-2"
                onClick={() => {
                  setConflict(null);
                  setMessage("");
                  setIsEditing(true);
                }}
                disabled={busy}
              >
                两者适用条件不同，去补充问题条件
              </Button>
            </div>
          </div>
        )}

        {/* Tags preview */}
        {tags.trim() && (
          <div className="flex flex-wrap gap-2 items-center pt-1.5 border-t border-slate-100/60">
            <div className="flex items-center gap-1 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
              <Tag className="h-3 w-3 text-slate-400" />
              <span>关联标签:</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {tags.split(/[、,，]/).map((item) => item.trim()).filter(Boolean).map((tag, idx) => (
                <span 
                  key={idx} 
                  className="rounded-full bg-slate-100 px-2.5 py-0.5 text-2xs font-semibold text-slate-700 border border-slate-200/60 cursor-default"
                >
                  #{tag}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Action buttons */}
        {!conflict && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 mt-3">
            <Button
              type="button"
              className="text-xs border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-800 transition-all duration-205 active:scale-[0.97] font-semibold flex items-center gap-1.5 px-3 py-1.5 h-8.5"
              onClick={() => setIsEditing(true)}
            >
              <Edit2 className="h-3.5 w-3.5 text-slate-500" />
              <span>编辑草稿内容</span>
            </Button>
            <div className="flex gap-2">
              <Button
                type="button"
                className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white font-semibold shadow-2xs transition-all duration-205 active:scale-[0.97] px-4 h-8.5 flex items-center gap-1.5"
                onClick={() => approve()}
                disabled={busy}
                loading={busyAction === "approve"}
                loadingText="处理中…"
              >
                <Check className="h-3.5 w-3.5" />
                <span>批准入库</span>
              </Button>
              <Button
                type="button"
                className="text-xs border border-rose-200 bg-white text-rose-700 hover:bg-rose-50 hover:border-rose-300 transition-all duration-205 active:scale-[0.97] font-semibold h-8.5 flex items-center gap-1.5 px-3"
                onClick={reject}
                disabled={busy}
                loading={busyAction === "reject"}
                loadingText="驳回中…"
              >
                <X className="h-3.5 w-3.5" />
                <span>驳回</span>
              </Button>
            </div>
          </div>
        )}
        {message && (
          <p
            className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-800"
            aria-live="polite"
          >
            {message}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4 pt-4 border-t border-slate-100 mt-3 animate-fadeIn">
      {/* Title form group */}
      <div className="space-y-1.5">
        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1.5">
          <BookOpen className="h-3.5 w-3.5 text-slate-400" />
          <span>{draftType === "case" ? "优秀案例标题" : "知识草稿标题"}</span>
        </label>
        <input
          className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm font-semibold text-slate-800 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white shadow-2xs"
          placeholder={draftType === "case" ? "给此案例起一个清晰的标题描述..." : "给这条知识起一个清晰的主题名称..."}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>

      {/* Category and Tags inline grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1.5">
            <SlidersHorizontal className="h-3.5 w-3.5 text-slate-400" />
            <span>{draftType === "case" ? "案例所属分类" : "所属分类"}</span>
          </label>
          <input
            className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white shadow-2xs"
            placeholder={draftType === "case" ? "例如：情绪转正、坚持执行" : "例如：营养干预、标准解答"}
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1.5">
            <Tag className="h-3.5 w-3.5 text-slate-400" />
            <span>{draftType === "case" ? "案例标签 (用顿号“、”分隔)" : "标签 (用顿号“、”分隔)"}</span>
          </label>
          <input
            className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white shadow-2xs"
            placeholder={draftType === "case" ? "例如：平台期、突破、减脂" : "例如：低血糖、孕妇"}
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
        </div>
      </div>

      {/* Question textarea */}
      <div className="space-y-1.5">
        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1.5">
          <Compass className="h-3.5 w-3.5 text-slate-400" />
          <span>{draftType === "case" ? "案例背景 / 适用场景描述" : "标准提问方式 (常见问法)"}</span>
        </label>
        <Textarea
          className="min-h-20 text-sm leading-relaxed text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white shadow-2xs rounded-lg px-3.5 py-2 border-slate-200"
          placeholder={draftType === "case" ? "描述案例的背景适用场景和遇到的平台期问题..." : "输入标准问题或用户常见提问的模板..."}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
      </div>

      {/* Answer textarea */}
      <div className="space-y-1.5">
        <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-slate-400" />
          <span>{draftType === "case" ? "案例金牌服务过程与改善结果" : "推荐标准回复内容"}</span>
        </label>
        <Textarea
          className="min-h-32 text-sm leading-relaxed text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white shadow-2xs rounded-lg px-3.5 py-2 border-slate-200"
          placeholder={draftType === "case" ? "编写详细的辅导细节、心理鼓励步骤和最后的体重改善结果..." : "编写最规范、科学且具有指导意义的标准回复..."}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
        />
      </div>

      {/* Action buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 mt-3">
        <Button
          type="button"
          className="text-xs border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 transition-all duration-200 active:scale-[0.97] font-semibold h-8.5 flex items-center gap-1.5 px-3"
          onClick={() => setIsEditing(false)}
        >
          <X className="h-3.5 w-3.5 text-slate-400" />
          <span>返回预览</span>
        </Button>
        <div className="flex gap-2">
          <Button
            type="button"
            className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white font-semibold shadow-2xs transition-all duration-200 active:scale-[0.97] px-4 h-8.5 flex items-center gap-1.5"
            onClick={() => approve()}
            disabled={busy}
            loading={busyAction === "approve"}
            loadingText="处理中…"
          >
            <Check className="h-3.5 w-3.5" />
            <span>保存并批准入库</span>
          </Button>
          <Button
            type="button"
            className="text-xs border border-rose-200 bg-white text-rose-700 hover:bg-rose-50 hover:border-rose-300 transition-all duration-200 active:scale-[0.97] font-semibold h-8.5 flex items-center gap-1.5 px-3"
            onClick={reject}
            disabled={busy}
            loading={busyAction === "reject"}
            loadingText="驳回中…"
          >
            <X className="h-3.5 w-3.5" />
            <span>驳回</span>
          </Button>
        </div>
      </div>
      {message && (
        <p
          className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-800"
          aria-live="polite"
        >
          {message}
        </p>
      )}
    </div>
  );
}

function ConflictVersion({
  label,
  question,
  answer,
  tone,
}: {
  label: string;
  question: string;
  answer: string;
  tone: "existing" | "candidate";
}) {
  return (
    <div className={`rounded-lg border bg-white p-3 ${
      tone === "candidate" ? "border-blue-200" : "border-slate-200"
    }`}>
      <div className={`text-xs font-bold ${
        tone === "candidate" ? "text-blue-700" : "text-slate-600"
      }`}>
        {label}
      </div>
      <div className="mt-2 text-xs font-semibold leading-relaxed text-slate-800">
        {question}
      </div>
      <div className="mt-2 whitespace-pre-wrap border-t border-slate-100 pt-2 text-xs leading-relaxed text-slate-600">
        {answer}
      </div>
    </div>
  );
}

function errorMessage(payload: unknown, fallback: string): string {
  if (
    payload
    && typeof payload === "object"
    && "detail" in payload
  ) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (
      detail
      && typeof detail === "object"
      && "message" in detail
      && typeof (detail as { message?: unknown }).message === "string"
    ) {
      return (detail as { message: string }).message;
    }
  }
  return fallback;
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

  // 前台运营界面已隐藏该技术维度按钮，其底层能力已收归至“开发者自测控制台”，保证用户体验的纯粹角色扮演
  return null;
}

export function KnowledgeFAQEditor({ faqs }: { faqs: KnowledgeFAQ[] }) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"active" | "disabled" | "archived">("active");
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [createError, setCreateError] = useState("");
  const [draft, setDraft] = useState({
    title: "",
    question: "",
    answer: "",
    category: "",
    tags: "",
  });
  const statusOptions = [
    { value: "active" as const, label: "检索中" },
    { value: "disabled" as const, label: "已停用" },
    { value: "archived" as const, label: "已归档" },
  ];
  const visibleFaqs = faqs.filter((faq) => faq.status === statusFilter);

  async function createFaq() {
    setBusyId("new");
    setCreateError("");
    try {
      const response = await fetch(`${API_BASE}/knowledge-faqs`, {
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
      if (!response.ok) throw new Error((await response.text()) || "发布失败");
      setDraft({ title: "", question: "", answer: "", category: "", tags: "" });
      setCreating(false);
      router.refresh();
    } catch (err) {
      setCreateError(err instanceof Error ? `FAQ 发布失败：${err.message}` : "FAQ 发布失败，请稍后再试。");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
        <div>
          <h3 className="text-sm font-bold text-slate-800">FAQ 内容管理</h3>
          <p className="mt-1 text-xs text-slate-500 leading-relaxed">
            默认只显示参与检索的 FAQ。停用内容可恢复；归档内容仅保留历史，不参与检索。
          </p>
        </div>
        <Button
          type="button"
          className="border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 font-semibold text-xs px-3 h-8 flex items-center gap-1 active:scale-[0.98] transition-all"
          onClick={() => setCreating((value) => !value)}
        >
          <span>{creating ? "收起面板" : "新增 FAQ"}</span>
        </Button>
      </div>

      {creating && (
        <div className="rounded-xl border border-blue-150 bg-blue-50/5 p-4.5 space-y-4 shadow-xs animate-fadeIn">
          <div className="flex items-center gap-2 border-b border-blue-100/50 pb-2">
            <Sparkles className="h-4 w-4 text-blue-600 shrink-0" />
            <h4 className="text-xs font-bold text-cyan-900">新增结构化 FAQ</h4>
          </div>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                <BookOpen className="h-3.5 w-3.5 text-slate-400" />
                <span>问答主题 / 标题</span>
              </label>
              <input
                className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm font-semibold text-slate-800 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all bg-white"
                placeholder="请输入明确的标题主题，例如：孕妇空腹低血糖应对"
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              />
            </div>
            
            <div className="grid gap-3 grid-cols-2">
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                  <SlidersHorizontal className="h-3.5 w-3.5 text-slate-400" />
                  <span>分类</span>
                </label>
                <input
                  className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all bg-white"
                  placeholder="例如：健康FAQ"
                  value={draft.category}
                  onChange={(event) => setDraft({ ...draft, category: event.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                  <Tag className="h-3.5 w-3.5 text-slate-400" />
                  <span>标签 (用顿号“、”分隔)</span>
                </label>
                <input
                  className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all bg-white"
                  placeholder="例如：孕期、低血糖"
                  value={draft.tags}
                  onChange={(event) => setDraft({ ...draft, tags: event.target.value })}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                <Compass className="h-3.5 w-3.5 text-slate-400" />
                <span>用户常见提问方式</span>
              </label>
              <Textarea
                className="min-h-16 text-sm focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all bg-white rounded-lg border-slate-200 px-3.5 py-2"
                placeholder="例如：孕妇早上起来经常低血糖怎么办？有什么快速恢复的办法吗？"
                value={draft.question}
                onChange={(event) => setDraft({ ...draft, question: event.target.value })}
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                <Sparkles className="h-3.5 w-3.5 text-slate-400" />
                <span>参考标准回复内容</span>
              </label>
              <Textarea
                className="min-h-24 text-sm focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all bg-white rounded-lg border-slate-200 px-3.5 py-2"
                placeholder="请输入最标准的专业指导回复文本，将做为机器人回复模板依据..."
                value={draft.answer}
                onChange={(event) => setDraft({ ...draft, answer: event.target.value })}
              />
            </div>

            <Button 
              type="button" 
              className="bg-blue-600 hover:bg-blue-500 text-white shadow-2xs font-semibold transition-all active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none mt-1 h-9 flex items-center gap-1.5 px-4"
              onClick={createFaq} 
              disabled={!draft.title || !draft.question || !draft.answer}
              loading={busyId === "new"}
              loadingText="发布中…"
            >
              <Check className="h-4 w-4" />
              <span>保存并发布 FAQ</span>
            </Button>
            {createError && (
              <p className="text-xs font-medium text-rose-700" role="alert">{createError}</p>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1 rounded-lg bg-slate-100 p-1" aria-label="FAQ 状态筛选">
        {statusOptions.map((option) => {
          const count = faqs.filter((faq) => faq.status === option.value).length;
          const selected = statusFilter === option.value;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => setStatusFilter(option.value)}
              aria-pressed={selected}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-bold transition-colors ${
                selected
                  ? "bg-white text-cyan-900 shadow-sm"
                  : "text-slate-600 hover:bg-white/70 hover:text-slate-900"
              }`}
            >
              <span>{option.label}</span>
              <span className="tabular-nums text-slate-500">{count}</span>
            </button>
          );
        })}
      </div>

      <div className="max-h-[520px] space-y-3.5 overflow-y-auto pr-1">
        {visibleFaqs.map((faq) => (
          <KnowledgeFAQRow key={faq.id} faq={faq} busy={busyId === faq.id} setBusy={setBusyId} />
        ))}
        {visibleFaqs.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-200 py-12 text-center text-sm text-slate-400 bg-slate-50/20">
            {statusFilter === "active"
              ? "暂无参与检索的 FAQ，可点击右上角新增。"
              : statusFilter === "disabled"
                ? "当前没有已停用的 FAQ。"
                : "当前没有已归档的 FAQ。"}
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
  const [busyAction, setBusyAction] = useState<"save" | "enable" | "disable" | null>(null);
  const [error, setError] = useState("");
  const statusLabel =
    faq.status === "active" ? "● 检索中" : faq.status === "archived" ? "已归档" : "○ 已停用";

  async function save() {
    setBusyAction("save");
    setBusy(faq.id);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/knowledge-faqs/${faq.id}`, {
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
      if (!response.ok) throw new Error((await response.text()) || "保存失败");
      setEditing(false);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? `保存失败：${err.message}` : "保存失败，请稍后再试。");
    } finally {
      setBusyAction(null);
      setBusy(null);
    }
  }

  async function setStatus(action: "enable" | "disable") {
    setBusyAction(action);
    setBusy(faq.id);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/knowledge-faqs/${faq.id}/${action}`, { method: "POST" });
      if (!response.ok) throw new Error((await response.text()) || "操作失败");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? `操作失败：${err.message}` : "操作失败，请稍后再试。");
    } finally {
      setBusyAction(null);
      setBusy(null);
    }
  }

  return (
    <div className={`rounded-xl border p-4 shadow-3xs transition-all duration-200 ${
      faq.status === "active" 
        ? "border-emerald-300 bg-white hover:border-emerald-400"
        : "border-slate-200 bg-slate-50/40 opacity-80 hover:opacity-100 hover:border-slate-300"
    }`}>
      {/* Row Header Info */}
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`rounded-full px-2 py-0.5 text-2xs font-bold tracking-wide ${
            faq.status === "active" 
              ? "bg-emerald-50 text-emerald-700 border border-emerald-250/20" 
              : "bg-slate-150 text-slate-500 border border-slate-200/50"
          }`}>
            {statusLabel}
          </span>
          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-2xs font-semibold text-slate-500 border border-slate-200/40">
            v{faq.version}
          </span>
          {faq.category && (
            <span className="rounded-md bg-blue-50/60 text-blue-700 border border-blue-100/50 px-1.5 py-0.5 text-2xs font-semibold">
              {faq.category}
            </span>
          )}
        </div>
        <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">FAQ #{faq.id}</span>
      </div>

      {editing ? (
        <div className="space-y-4 pt-3.5 border-t border-slate-100 mt-3 animate-fadeIn">
          {/* FAQ Title input */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
              <BookOpen className="h-3.5 w-3.5 text-slate-450" />
              <span>FAQ 主题标题</span>
            </label>
            <input 
              className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm font-semibold text-slate-800 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white" 
              value={title} 
              onChange={(event) => setTitle(event.target.value)} 
            />
          </div>

          {/* FAQ Category & Tags grid */}
          <div className="grid gap-3 grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                <SlidersHorizontal className="h-3.5 w-3.5 text-slate-450" />
                <span>FAQ 类别</span>
              </label>
              <input 
                className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white" 
                value={category} 
                onChange={(event) => setCategory(event.target.value)} 
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
                <Tag className="h-3.5 w-3.5 text-slate-450" />
                <span>FAQ 标签 (顿号分隔)</span>
              </label>
              <input 
                className="w-full rounded-lg border border-slate-200 px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all duration-200 bg-white" 
                value={tags} 
                onChange={(event) => setTags(event.target.value)} 
              />
            </div>
          </div>

          {/* FAQ Question textarea */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
              <Compass className="h-3.5 w-3.5 text-slate-450" />
              <span>标准提问方式</span>
            </label>
            <Textarea 
              className="min-h-16 text-sm focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all rounded-lg border-slate-200 px-3.5 py-2 bg-white" 
              value={question} 
              onChange={(event) => setQuestion(event.target.value)} 
            />
          </div>

          {/* FAQ Answer textarea */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block flex items-center gap-1">
              <Sparkles className="h-3.5 w-3.5 text-slate-450" />
              <span>标准回复内容</span>
            </label>
            <Textarea 
              className="min-h-24 text-sm focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all rounded-lg border-slate-200 px-3.5 py-2 bg-white" 
              value={answer} 
              onChange={(event) => setAnswer(event.target.value)} 
            />
          </div>

          {/* Action buttons during edit */}
          <div className="flex gap-2 pt-2 border-t border-slate-150 mt-2">
            <Button 
              type="button" 
              className="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition-all duration-200 active:scale-[0.97] shadow-2xs px-3.5 text-xs h-8.5 flex items-center gap-1.5" 
              onClick={save} 
              loading={busyAction === "save"}
              loadingText="保存中…"
            >
              <Check className="h-3.5 w-3.5" />
              <span>保存新版本</span>
            </Button>
            <Button 
              type="button" 
              className="border border-slate-250 bg-white text-slate-700 hover:bg-slate-50 font-semibold transition-all duration-200 active:scale-[0.97] text-xs h-8.5 px-3 flex items-center gap-1" 
              onClick={() => setEditing(false)}
              disabled={busy}
            >
              <X className="h-3.5 w-3.5 text-slate-400" />
              <span>取消</span>
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-3.5">
          {/* FAQ Title */}
          <div className="font-bold text-slate-800 leading-snug text-sm">{faq.title}</div>
          
          {/* Collapsible Q&A blocks */}
          <div className="text-xs text-slate-650 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-150">
            <div className="font-bold text-slate-400 mb-1 flex items-center gap-1 text-[10px]">
              <Compass className="h-3 w-3 text-blue-500" />
              <span>标准常见问法：</span>
            </div>
            <div className="pl-4 border-l border-slate-200/80 font-normal whitespace-pre-wrap">{faq.question}</div>
          </div>
          
          <div className="text-xs text-slate-750 leading-relaxed bg-slate-50 p-2.5 rounded-lg border border-slate-150">
            <div className="font-bold text-slate-400 mb-1 flex items-center gap-1 text-[10px]">
              <Sparkles className="h-3 w-3 text-amber-500 animate-pulse" />
              <span>标准回复口径：</span>
            </div>
            <div className="pl-4 border-l border-slate-200/80 font-normal whitespace-pre-wrap">{faq.answer}</div>
          </div>
          
          {/* FAQ Tags */}
          {faq.tags && faq.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1.5 border-t border-slate-100/50">
              {faq.tags.map((tag, idx) => (
                <span 
                  key={idx} 
                  className="rounded bg-slate-100 px-2 py-0.5 text-2xs font-semibold text-slate-700 border border-slate-200/40 cursor-default"
                >
                  #{tag}
                </span>
              ))}
            </div>
          )}

          {/* Action buttons in preview */}
          <div className="mt-3.5 flex flex-wrap gap-2 pt-2.5 border-t border-slate-100/60">
            {faq.status !== "archived" && (
              <Button
                type="button"
                className="border border-slate-250 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-800 font-semibold text-xs transition-all active:scale-[0.98] h-8 px-2.5 flex items-center gap-1"
                onClick={() => setEditing(true)}
                disabled={busy}
              >
                <Edit2 className="h-3.5 w-3.5 text-slate-500" />
                <span>编辑 FAQ</span>
              </Button>
            )}
            {faq.status === "active" ? (
              <Button 
                type="button" 
                className="border border-slate-250 bg-white text-rose-600 hover:bg-rose-50 hover:border-rose-100 font-semibold text-xs transition-all active:scale-[0.98] h-8 px-2.5 flex items-center gap-1" 
                onClick={() => setStatus("disable")} 
                loading={busyAction === "disable"}
                loadingText="禁用中…"
              >
                <X className="h-3.5 w-3.5 text-rose-500" />
                <span>禁用</span>
              </Button>
            ) : faq.status === "disabled" ? (
              <Button 
                type="button" 
                className="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs transition-all active:scale-[0.98] h-8 px-2.5 flex items-center gap-1 shadow-3xs" 
                onClick={() => setStatus("enable")} 
                loading={busyAction === "enable"}
                loadingText="启用中…"
              >
                <Check className="h-3.5 w-3.5 text-white" />
                <span>重新启用</span>
              </Button>
            ) : (
              <span className="inline-flex min-h-8 items-center text-xs font-medium text-slate-500">
                仅保留历史，不提供直接恢复
              </span>
            )}
          </div>
        </div>
      )}
      {error && (
        <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function splitTags(value: string): string[] {
  return value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
}
