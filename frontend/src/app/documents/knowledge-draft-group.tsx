"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type {
  KnowledgeDraft,
  KnowledgeIndexStats,
  KnowledgeFAQ,
  KnowledgeEvolutionSetting,
} from "@/lib/api";
import { API_BASE } from "@/lib/api";
import {
  KnowledgeDraftActions,
  KnowledgeFAQEditor,
  KnowledgeIndexSyncButton,
} from "./knowledge-draft-actions";
import {
  BookOpen,
  Award,
  Compass,
  Sparkles,
  Settings2,
  RefreshCw,
  Check,
  ChevronDown,
  ChevronUp
} from "lucide-react";
import { Button } from "@/components/ui/button";

export function FAQEvolutionCenter({
  pendingDrafts,
  approvedDrafts,
  indexStats,
  faqs,
  evolutionConfig,
}: {
  pendingDrafts: KnowledgeDraft[];
  approvedDrafts: KnowledgeDraft[];
  indexStats: KnowledgeIndexStats;
  faqs: KnowledgeFAQ[];
  evolutionConfig: KnowledgeEvolutionSetting;
}) {
  const [activeTab, setActiveTab] = useState<"pending" | "approved">("pending");
  const [showConfig, setShowConfig] = useState(false);
  const router = useRouter();

  const qaPending = pendingDrafts.filter((d) => d.draft_type === "qa");
  const qaApproved = approvedDrafts.filter((d) => d.draft_type === "qa");

  const [config, setConfig] = useState(evolutionConfig);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");

  const sourceText = Object.entries(indexStats.by_source_type)
    .map(([source, count]) => `${source} ${count}`)
    .join(" · ");

  async function saveConfig() {
    setSaveBusy(true);
    setSaveMessage("");
    const response = await fetch(`${API_BASE}/settings/knowledge-evolution`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(config),
    });
    setSaveMessage(response.ok ? "配置已保存" : "保存失败");
    setSaveBusy(false);
    router.refresh();
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden animate-fadeIn">
      {/* Title block */}
      <div className="border-b border-slate-100 px-6 py-5 bg-gradient-to-r from-blue-50/10 to-transparent">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-start gap-2.5">
            <BookOpen className="h-6 w-6 text-blue-650 shrink-0 mt-0.5" />
            <div>
              <h2 className="text-lg font-bold text-slate-900">📖 参考问答库 (FAQ) 进化工作台</h2>
              <p className="mt-1 text-sm text-slate-500 leading-relaxed">
                自动从每日群聊对话中识别高频提问与优秀回答，沉淀入标准 FAQ 参考问答库。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setShowConfig(!showConfig)}
              className="text-xs font-semibold border border-slate-200 bg-white text-slate-655 hover:bg-slate-50 hover:text-slate-800 transition-all px-3 py-1.5 rounded-lg flex items-center gap-2 shadow-2xs outline-none"
            >
              <Settings2 className="h-3.5 w-3.5 text-slate-500" />
              <span>FAQ 自动提取配置</span>
              <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-bold border ${
                config.enabled ? "pill-ok" : "pill-neutral"
              }`}>
                {config.enabled ? "● 自动运行中" : "○ 已暂停"}
              </span>
              {showConfig ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </button>
          </div>
        </div>

        {/* Config Panel */}
        {showConfig && (
          <div className="border-t border-slate-200/60 mt-4 pt-4 animate-slideDown">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200/60 pb-3 mb-4">
              <h3 className="text-xs font-bold text-slate-700">FAQ 自动生成提取参数</h3>
              <div className="flex items-center gap-3 bg-white py-1.5 px-3.5 rounded-lg border border-slate-200 shadow-3xs">
                <span className="text-xs font-bold text-slate-700">启用每日自动生成 FAQ</span>
                <button
                  type="button"
                  onClick={() => setConfig({ ...config, enabled: !config.enabled })}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out outline-none ${
                    config.enabled ? "bg-blue-600" : "bg-slate-200"
                  }`}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out ${
                      config.enabled ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 max-w-4xl">
              <div className="space-y-1">
                <span className="block text-2xs font-bold text-slate-450 uppercase tracking-wider">每日 FAQ 扫描时间</span>
                <input
                  className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all"
                  type="time"
                  value={config.qa_at}
                  onChange={(event) => setConfig({ ...config, qa_at: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <span className="block text-2xs font-bold text-slate-450 uppercase tracking-wider">系统时区</span>
                <select
                  className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 outline-none transition-all font-medium cursor-pointer"
                  value={config.timezone}
                  onChange={(event) => setConfig({ ...config, timezone: event.target.value })}
                >
                  <option value="Asia/Shanghai">亚洲/上海 (Asia/Shanghai)</option>
                </select>
              </div>
            </div>
            <div className="mt-4 flex items-center gap-3 border-t border-slate-200/60 pt-3">
              <Button
                type="button"
                onClick={saveConfig}
                disabled={saveBusy}
                className="bg-blue-600 hover:bg-blue-500 text-white font-semibold transition-all duration-200 active:scale-[0.97] px-4 shadow-2xs h-8.5 flex items-center gap-1.5"
              >
                {saveBusy ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                <span>保存配置</span>
              </Button>
              {saveMessage && <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-100 px-2 py-1 rounded-md">{saveMessage}</span>}
              <span className="text-xs font-medium text-slate-500">
                系统会按计划自动扫描每日聊天并生成 FAQ 草稿，运营侧只需审核与修订。
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="border-b border-slate-100 bg-slate-50/50 px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="grid gap-6 sm:grid-cols-3">
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">数据库知识块</div>
              <div className="mt-1 text-xl font-bold text-slate-900">{indexStats.total_chunks}</div>
            </div>
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">已向量化</div>
              <div className="mt-1 text-xl font-bold text-slate-900">{indexStats.embedded_chunks}</div>
            </div>
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">知识来源分布</div>
              <div className="mt-1 max-w-xs truncate text-sm font-medium text-slate-700" title={sourceText}>
                {sourceText || "暂无"}
              </div>
            </div>
          </div>
          <KnowledgeIndexSyncButton />
        </div>
      </div>

      {/* Main 2-column layout */}
      <div className="grid gap-6 p-6 xl:grid-cols-[1.7fr_1.3fr] bg-white">
        {/* Left Column: Drafts queue with Tab switch */}
        <div className="space-y-4">
          <div className="flex border-b border-slate-200">
            <button
              type="button"
              onClick={() => setActiveTab("pending")}
              className={`pb-3 text-sm font-semibold transition-all relative px-2 mr-8 outline-none ${
                activeTab === "pending"
                  ? "text-blue-600 font-bold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
            >
              待审核参考问答
              <span className={`ml-1.5 px-1.5 py-0.5 text-2xs rounded-full ${activeTab === "pending" ? "pill-info" : "pill-neutral"}`}>
                {qaPending.length}
              </span>
              {activeTab === "pending" && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-600 rounded-full" />
              )}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("approved")}
              className={`pb-3 text-sm font-semibold transition-all relative px-2 outline-none ${
                activeTab === "approved"
                  ? "text-emerald-600 font-bold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
            >
              已审核历史
              <span className={`ml-1.5 px-1.5 py-0.5 text-2xs rounded-full ${activeTab === "approved" ? "pill-ok" : "pill-neutral"}`}>
                {qaApproved.length}
              </span>
              {activeTab === "approved" && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-full" />
              )}
            </button>
          </div>

          <div className="min-h-[350px]">
            {activeTab === "pending" ? (
              qaPending.length > 0 ? (
                <div className="space-y-5 max-h-[650px] overflow-y-auto pr-1.5 animate-fadeIn">
                  {qaPending.map((draft) => (
                    <div key={draft.id} className="rounded-xl border border-slate-200/80 p-5 bg-slate-50/20 shadow-2xs hover:border-slate-300 transition-all">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-md bg-blue-50 text-blue-700 border border-blue-100 px-2 py-0.5 text-xs font-semibold">
                            📖 参考问答
                          </span>
                          {draft.category && (
                            <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 border border-slate-200/50">
                              {draft.category}
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-slate-400 font-medium">{draft.created_at}</span>
                      </div>
                      
                      {draft.reason && (
                        <div className="mb-3 text-xs text-amber-950 bg-amber-50/40 border border-amber-100/60 p-2.5 rounded-lg">
                          <span className="font-semibold text-amber-800">推荐原因：</span>
                          {draft.reason}
                        </div>
                      )}
                      {draft.evidence && (
                        <div className="mb-4 rounded-lg bg-slate-50 border border-slate-100 p-3 text-xs leading-relaxed text-slate-600">
                          <span className="font-semibold text-slate-500 block mb-1">聊天对话证据：</span>
                          <div className="whitespace-pre-wrap">{draft.evidence}</div>
                        </div>
                      )}

                      <KnowledgeDraftActions
                        draftId={draft.id}
                        draftType="qa"
                        initialTitle={draft.title}
                        initialQuestion={draft.question}
                        initialAnswer={draft.answer}
                        initialCategory={draft.category}
                        initialTags={draft.tags}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 py-16 text-center text-slate-400 animate-fadeIn">
                  <span className="text-3xl block mb-2">🎈</span>
                  <p className="text-sm font-medium">暂无待审核参考问答</p>
                  <p className="text-xs text-slate-400 mt-1">系统未自动生成新的候选 FAQ</p>
                </div>
              )
            ) : (
              <div className="max-h-[650px] space-y-3 overflow-y-auto pr-1.5 animate-fadeIn">
                {qaApproved.slice(0, 30).map((draft) => (
                  <div key={draft.id} className="rounded-lg border border-slate-200/80 p-4 text-sm bg-white hover:shadow-2xs transition-all">
                    <div className="flex items-start justify-between gap-3">
                      <div className="font-semibold text-slate-800 leading-relaxed">{draft.title}</div>
                      <span className="shrink-0 rounded-full px-2 py-0.5 text-2xs font-semibold bg-blue-50 text-blue-700">
                        FAQ
                      </span>
                    </div>
                    <div className="mt-2 flex items-center justify-between text-2xs text-slate-400 font-medium">
                      <span>分类：{draft.category || "未分类"}</span>
                      <span>已同步至 {draft.published_to || "结构化 FAQ 库"}</span>
                    </div>
                  </div>
                ))}
                {qaApproved.length === 0 && (
                  <div className="rounded-xl border border-dashed border-slate-200 py-16 text-center text-slate-400">
                    <p className="text-sm font-medium">暂无已入库问答历史</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: FAQ Editor */}
        <div className="border-t border-slate-100 pt-6 xl:border-t-0 xl:border-l xl:border-slate-100 xl:pl-6 xl:pt-0">
          <KnowledgeFAQEditor faqs={faqs} />
        </div>
      </div>
    </section>
  );
}

export function ExcellentCaseEvolutionCenter({
  pendingDrafts,
  approvedDrafts,
  evolutionConfig,
}: {
  pendingDrafts: KnowledgeDraft[];
  approvedDrafts: KnowledgeDraft[];
  evolutionConfig: KnowledgeEvolutionSetting;
}) {
  const [activeTab, setActiveTab] = useState<"pending" | "approved">("pending");
  const [showConfig, setShowConfig] = useState(false);
  const router = useRouter();

  const casePending = pendingDrafts.filter((d) => d.draft_type === "case");
  const caseApproved = approvedDrafts.filter((d) => d.draft_type === "case");

  const [config, setConfig] = useState(evolutionConfig);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");

  async function saveConfig() {
    setSaveBusy(true);
    setSaveMessage("");
    const response = await fetch(`${API_BASE}/settings/knowledge-evolution`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(config),
    });
    setSaveMessage(response.ok ? "配置已保存" : "保存失败");
    setSaveBusy(false);
    router.refresh();
  }

  return (
    <section className="rounded-xl border border-cyan-200 bg-white shadow-sm overflow-hidden animate-fadeIn">
      {/* Title block */}
      <div className="border-b border-cyan-100 px-6 py-5 bg-cyan-50/30">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-start gap-2.5">
            <Award className="h-6 w-6 text-cyan-700 shrink-0 mt-0.5" />
            <div>
              <h2 className="text-lg font-bold text-slate-900">优秀标杆案例库进化工作台</h2>
              <p className="mt-1 text-sm text-slate-500 leading-relaxed">
                按照服务阶段（3/7/14/21/28天）自动提取群聊中的金牌沟通案例与客户坚持表现。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setShowConfig(!showConfig)}
              className="text-xs font-semibold border border-cyan-200 bg-white text-cyan-800 hover:bg-cyan-50 transition-all px-3 py-1.5 rounded-lg flex items-center gap-2 shadow-2xs outline-none"
            >
              <Settings2 className="h-3.5 w-3.5 text-cyan-700" />
              <span>案例自动提取配置</span>
              <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-bold border ${
                config.enabled ? "pill-ok" : "pill-neutral"
              }`}>
                {config.enabled ? "● 自动运行中" : "○ 已暂停"}
              </span>
              {showConfig ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </button>
          </div>
        </div>

        {/* Config Panel */}
        {showConfig && (
          <div className="border-t border-cyan-200/60 mt-4 pt-4 animate-slideDown">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-cyan-200/60 pb-3 mb-4">
              <h3 className="text-xs font-bold text-cyan-900">案例自动提取配置</h3>
              <div className="flex items-center gap-3 bg-white py-1.5 px-3.5 rounded-lg border border-slate-200 shadow-3xs">
                <span className="text-xs font-bold text-slate-700">启用每日自动生成案例</span>
                <button
                  type="button"
                  onClick={() => setConfig({ ...config, enabled: !config.enabled })}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out outline-none ${
                    config.enabled ? "bg-cyan-700" : "bg-slate-200"
                  }`}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm ring-0 transition duration-200 ease-in-out ${
                      config.enabled ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 max-w-4xl">
              <div className="space-y-1">
                <span className="block text-2xs font-bold text-cyan-800 uppercase tracking-wider">阶段案例天数</span>
                <input
                  className="w-full rounded-lg border border-cyan-200 bg-white px-3.5 py-2 text-sm text-slate-700 focus:border-cyan-600 focus:ring-4 focus:ring-cyan-100 outline-none transition-all"
                  value={config.case_days}
                  placeholder="3,7,14,21,28"
                  onChange={(event) => setConfig({ ...config, case_days: event.target.value })}
                />
              </div>
            </div>
            <div className="mt-4 flex items-center gap-3 border-t border-cyan-200/60 pt-3">
              <Button
                type="button"
                onClick={saveConfig}
                disabled={saveBusy}
                className="bg-cyan-700 hover:bg-cyan-800 text-white font-semibold transition-colors duration-200 px-4 shadow-2xs h-8.5 flex items-center gap-1.5"
              >
                {saveBusy ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                <span>保存配置</span>
              </Button>
              {saveMessage && <span className="text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-100 px-2 py-1 rounded-md">{saveMessage}</span>}
              <span className="text-xs font-medium text-slate-500">
                系统会在 3/7/14/21/28 天自动扫描阶段聊天，生成优秀案例草稿，运营侧只负责审核入库。
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Stats block */}
      <div className="border-b border-cyan-100 bg-cyan-50/20 px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="grid gap-6 sm:grid-cols-3">
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">已入库优秀案例</div>
              <div className="mt-1 text-xl font-bold text-cyan-800">{caseApproved.length} 个</div>
            </div>
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">覆盖服务阶段</div>
              <div className="mt-1 text-xl font-bold text-slate-900">{config.case_days ? config.case_days.split(",").length : 0} 个阶段</div>
            </div>
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider">检索触发场景</div>
              <div className="mt-1 text-sm font-semibold text-slate-700">
                群聊检测到焦虑情绪/身体不适
              </div>
            </div>
          </div>
          <div className="text-xs text-cyan-900 bg-cyan-50/70 border border-cyan-100 px-3 py-2 rounded-lg font-medium max-w-lg leading-relaxed">
            <strong>智能检索机制：</strong>当群聊中出现用户消极、质疑或身体不适时，AI 引擎会自动在下方已入库案例中进行语义匹配，并提取最贴切的真实成功案例，用于情感承接与共鸣鼓励。
          </div>
        </div>
      </div>

      {/* Main 2-column layout */}
      <div className="grid gap-6 p-6 xl:grid-cols-[1.7fr_1.3fr] bg-white">
        {/* Left Column: Drafts queue with Tab switch */}
        <div className="space-y-4">
          <div className="flex border-b border-slate-200">
            <button
              type="button"
              onClick={() => setActiveTab("pending")}
              className={`pb-3 text-sm font-semibold transition-all relative px-2 mr-8 outline-none ${
                activeTab === "pending"
                  ? "text-cyan-700 font-bold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
            >
              待审核标杆案例
              <span className={`ml-1.5 px-1.5 py-0.5 text-2xs rounded-full ${activeTab === "pending" ? "pill-info" : "pill-neutral"}`}>
                {casePending.length}
              </span>
              {activeTab === "pending" && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-cyan-700 rounded-full" />
              )}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("approved")}
              className={`pb-3 text-sm font-semibold transition-all relative px-2 outline-none ${
                activeTab === "approved"
                  ? "text-emerald-600 font-bold"
                  : "text-slate-400 hover:text-slate-600"
              }`}
            >
              已审核历史
              <span className={`ml-1.5 px-1.5 py-0.5 text-2xs rounded-full ${activeTab === "approved" ? "pill-ok" : "pill-neutral"}`}>
                {caseApproved.length}
              </span>
              {activeTab === "approved" && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-full" />
              )}
            </button>
          </div>

          <div className="min-h-[350px]">
            {activeTab === "pending" ? (
              casePending.length > 0 ? (
                <div className="space-y-5 max-h-[650px] overflow-y-auto pr-1.5 animate-fadeIn">
                  {casePending.map((draft) => (
                    <div key={draft.id} className="rounded-xl border border-slate-200/80 p-5 bg-slate-50/20 shadow-2xs hover:border-slate-300 transition-all">
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-md bg-cyan-50 text-cyan-800 border border-cyan-100 px-2 py-0.5 text-xs font-semibold">
                            优秀案例
                          </span>
                          {draft.category && (
                            <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600 border border-slate-200/50">
                              {draft.category}
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-slate-400 font-medium">{draft.created_at}</span>
                      </div>
                      
                      {draft.reason && (
                        <div className="mb-3 text-xs text-amber-950 bg-amber-50/40 border border-amber-100/60 p-2.5 rounded-lg">
                          <span className="font-semibold text-amber-800">入选原因：</span>
                          {draft.reason}
                        </div>
                      )}
                      {draft.evidence && (
                        <div className="mb-4 rounded-lg bg-slate-50 border border-slate-100 p-3 text-xs leading-relaxed text-slate-600">
                          <span className="font-semibold text-slate-500 block mb-1">聊天对话证据：</span>
                          <div className="whitespace-pre-wrap">{draft.evidence}</div>
                        </div>
                      )}

                      <KnowledgeDraftActions
                        draftId={draft.id}
                        draftType="case"
                        initialTitle={draft.title}
                        initialQuestion={draft.question}
                        initialAnswer={draft.answer}
                        initialCategory={draft.category}
                        initialTags={draft.tags}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 py-16 text-center text-slate-400 animate-fadeIn">
                  <Award className="mx-auto mb-3 h-9 w-9 text-cyan-700" />
                  <p className="text-sm font-medium">暂无待审核优秀案例</p>
                  <p className="text-xs text-slate-400 mt-1">系统未自动生成新的候选金牌案例</p>
                </div>
              )
            ) : (
              <div className="max-h-[650px] space-y-3 overflow-y-auto pr-1.5 animate-fadeIn">
                {caseApproved.slice(0, 30).map((draft) => (
                  <div key={draft.id} className="rounded-lg border border-slate-200/80 p-4 text-sm bg-white hover:shadow-2xs transition-all">
                    <div className="flex items-start justify-between gap-3">
                      <div className="font-semibold text-slate-800 leading-relaxed">{draft.title}</div>
                      <span className="shrink-0 rounded-full px-2 py-0.5 text-2xs font-semibold bg-purple-50 text-purple-700">
                        案例
                      </span>
                    </div>
                    <div className="mt-2 flex items-center justify-between text-2xs text-slate-400 font-medium">
                      <span>分类：{draft.category || "未分类"}</span>
                      <span>已同步至 {draft.published_to || "知识向量块"}</span>
                    </div>
                  </div>
                ))}
                {caseApproved.length === 0 && (
                  <div className="rounded-xl border border-dashed border-slate-200 py-16 text-center text-slate-400">
                    <p className="text-sm font-medium">暂无已入库案例历史</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Case Base card list */}
        <div className="border-t border-slate-100 pt-6 xl:border-t-0 xl:border-l xl:border-slate-100 xl:pl-6 xl:pt-0">
          <div className="space-y-4">
            <div className="border-b border-slate-100 pb-3 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-800">已入库优秀案例库</h3>
                <p className="mt-1 text-xs text-slate-500 leading-relaxed">
                  展示由 AI 自动生成并经人工批准上线的金牌营养师服务参考案例。
                </p>
              </div>
              <span className="rounded-md bg-purple-50 text-purple-700 border border-purple-100 px-2 py-0.5 text-xs font-semibold">
                {caseApproved.length} 个案例
              </span>
            </div>

            <div className="max-h-[520px] space-y-4 overflow-y-auto pr-1">
              {caseApproved.map((draft) => (
                <ExcellentCaseCard key={draft.id} draft={draft} />
              ))}
              {caseApproved.length === 0 && (
                <div className="rounded-xl border border-dashed border-slate-200 py-12 text-center text-sm text-slate-400 bg-slate-50/20">
                  暂无任何已入库优秀案例，请在左侧“待审核标杆案例”中审核批准。
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function ExcellentCaseCard({ draft }: { draft: KnowledgeDraft }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="rounded-xl border border-cyan-100 bg-cyan-50/10 p-4 shadow-3xs hover:border-cyan-200 hover:bg-cyan-50/20 transition-all duration-200">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="rounded-md bg-cyan-100 text-cyan-800 px-1.5 py-0.5 text-2xs font-bold border border-cyan-200/60">
              标杆案例
            </span>
            {draft.category && (
              <span className="rounded-md bg-slate-100 text-slate-650 px-1.5 py-0.5 text-2xs font-semibold border border-slate-200/50">
                {draft.category}
              </span>
            )}
          </div>
          <h4 className="text-sm font-bold text-slate-800 leading-snug pt-1">{draft.title}</h4>
        </div>
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="text-xs font-semibold text-purple-600 hover:text-purple-700 shrink-0 select-none outline-none"
        >
          {isOpen ? "收起详情" : "展开详情"}
        </button>
      </div>

      <div className={`space-y-3.5 overflow-hidden transition-all duration-300 ${isOpen ? "max-h-[1000px] mt-4 pt-3.5 border-t border-purple-100/50" : "max-h-0"}`}>
        <div className="space-y-1">
          <div className="text-[10px] font-bold text-purple-700/60 uppercase tracking-wider flex items-center gap-1">
            <Compass className="h-3 w-3" />
            <span>案例适用场景与背景</span>
          </div>
          <p className="text-xs text-slate-650 leading-relaxed font-normal whitespace-pre-wrap pl-2.5 border-l border-purple-200">
            {draft.question}
          </p>
        </div>

        <div className="space-y-1">
          <div className="text-[10px] font-bold text-amber-700/60 uppercase tracking-wider flex items-center gap-1">
            <Sparkles className="h-3 w-3" />
            <span>金牌辅导过程与成效</span>
          </div>
          <p className="text-xs text-slate-700 leading-relaxed font-normal whitespace-pre-wrap pl-2.5 border-l border-amber-250">
            {draft.answer}
          </p>
        </div>

        {draft.reason && (
          <div className="text-2xs text-slate-500 bg-slate-50 border border-slate-100 p-2 rounded-lg leading-relaxed">
            <span className="font-semibold text-slate-700">采纳要点：</span>
            {draft.reason}
          </div>
        )}
      </div>

      {draft.tags && draft.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100/50 pt-2.5">
          {draft.tags.map((tag, idx) => (
            <span
              key={idx}
              className="rounded-full bg-purple-50/55 text-purple-650 border border-purple-100/40 px-2 py-0.5 text-3xs font-semibold"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
