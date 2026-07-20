"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { AlertTriangle, ExternalLink, LoaderCircle, RefreshCw } from "lucide-react";
import { api, Checkin, Message, ServiceCase, UserDocument } from "@/lib/api";

type LoadStatus = "idle" | "loading" | "success" | "error";

type DetailState = {
  status: LoadStatus;
  checkins: Checkin[];
  messages: Message[];
  documents: UserDocument[];
  failed: string[];
};

const INITIAL_STATE: DetailState = {
  status: "idle",
  checkins: [],
  messages: [],
  documents: [],
  failed: [],
};

export function UserRecordDetails({
  serviceCase,
  serviceCases,
}: {
  serviceCase: ServiceCase;
  serviceCases: ServiceCase[];
}) {
  const [state, setState] = useState<DetailState>(INITIAL_STATE);

  const load = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", failed: [] }));
    const results = await Promise.allSettled([
      api<Checkin[]>(`/service-cases/${serviceCase.id}/checkins`),
      api<Message[]>(`/service-cases/${serviceCase.id}/messages?limit=5`),
      api<UserDocument[]>(`/service-cases/${serviceCase.id}/documents`),
    ]);
    const failed: string[] = [];
    const checkins = results[0].status === "fulfilled" ? results[0].value : (failed.push("打卡"), []);
    const messages = results[1].status === "fulfilled" ? results[1].value : (failed.push("聊天"), []);
    const documents = results[2].status === "fulfilled" ? results[2].value : (failed.push("交付文件"), []);
    setState({
      status: failed.length === 3 ? "error" : "success",
      checkins,
      messages,
      documents,
      failed,
    });
  }, [serviceCase.id]);

  return (
    <details
      className="panel group overflow-hidden"
      onToggle={(event) => {
        if (event.currentTarget.open && state.status === "idle") void load();
      }}
    >
      <summary className="panel-header-clean flex min-h-16 cursor-pointer list-none select-none items-center justify-between gap-4 marker:hidden">
        <div className="min-w-0">
          <h2 className="panel-title">资料与记录</h2>
          <p className="panel-subtitle">最近打卡、聊天、运营信号、交付文件与历史服务单，展开时读取。</p>
        </div>
        <span className="shrink-0 text-xs font-semibold text-cyan-700 group-open:hidden">查看记录</span>
        <span className="hidden shrink-0 text-xs font-semibold text-cyan-700 group-open:inline">收起</span>
      </summary>

      <div className="panel-body">
        {state.status === "loading" && (
          <div className="flex min-h-36 items-center justify-center gap-2 text-sm font-medium text-slate-600" aria-live="polite">
            <LoaderCircle className="h-4 w-4 animate-spin text-cyan-700" />
            正在读取运营记录
          </div>
        )}

        {state.status === "error" && (
          <LoadError failed={state.failed} onRetry={load} />
        )}

        {state.status === "success" && (
          <div className="space-y-7">
            {state.failed.length > 0 && <LoadError failed={state.failed} onRetry={load} compact />}

            <section>
              <SectionHeading title="运营信号" />
              <div className="grid gap-3 sm:grid-cols-3">
                <Info label="转人工记录" value={reasonText(serviceCase.intervention_reasons)} />
                <Info label="续购意向" value={intentText(serviceCase.renewal_intent_flags)} />
                <Info label="退款 / 退出意向" value={intentText(serviceCase.refund_intent_flags)} />
              </div>
            </section>

            <section>
              <SectionHeading title="打卡记录" note={`${uniqueCheckinDays(state.checkins)} 个有效打卡日`} />
              {state.checkins.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <Info label="体重变化" value={weightTrend(state.checkins)} />
                  <Info label="最近睡眠" value={state.checkins[0]?.parsed_json?.sleep || "未记录"} />
                  <Info label="最近情绪" value={state.checkins[0]?.parsed_json?.mood || "未记录"} />
                  <Info label="最近饮水" value={state.checkins[0]?.parsed_json?.water || "未记录"} />
                  <Info label="最近运动" value={state.checkins[0]?.parsed_json?.exercise || "未记录"} />
                  <Info label="最近打卡日期" value={state.checkins[0]?.checkin_date || "未记录"} />
                </div>
              ) : (
                <InlineEmpty>当前服务单还没有打卡记录。</InlineEmpty>
              )}
            </section>

            <section>
              <SectionHeading title="最近聊天" note="最多显示最近 5 条" />
              {state.messages.length > 0 ? (
                <div className="space-y-2">
                  {state.messages.map((message) => (
                    <div key={message.id} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm leading-6">
                      <span className="font-bold text-slate-600">{senderLabel(message)}：</span>
                      <span className="text-break whitespace-pre-wrap text-slate-800">{message.content}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <InlineEmpty>当前服务单还没有聊天记录。</InlineEmpty>
              )}
            </section>

            <section>
              <SectionHeading title="交付文件摘要" />
              {state.documents.length > 0 ? (
                <DocumentHighlights
                  energyDocument={state.documents.find((item) => item.document_type === "energy_restart_prescription")}
                  executionDocument={state.documents.find((item) => item.document_type === "day28_execution_standard")}
                />
              ) : (
                <InlineEmpty>当前服务单还没有可展示的交付文件。</InlineEmpty>
              )}
            </section>

            <section>
              <SectionHeading title="历史服务单" note={`${serviceCases.length} 个`} />
              <div className="grid gap-2 sm:grid-cols-2">
                {serviceCases.map((item) => (
                  <Link
                    key={item.id}
                    className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-800 transition-colors hover:border-cyan-300 hover:bg-slate-50 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-600"
                    href={`/cases/${item.id}`}
                  >
                    <span className="text-break">{item.case_no} · {statusLabel(item)}</span>
                    <ExternalLink className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                  </Link>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
    </details>
  );
}

function LoadError({
  failed,
  onRetry,
  compact = false,
}: {
  failed: string[];
  onRetry: () => Promise<void>;
  compact?: boolean;
}) {
  return (
    <div className={`rounded-xl border border-amber-200 bg-amber-50 ${compact ? "px-4 py-3" : "p-5"}`} role="alert">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="flex items-center gap-2 text-sm font-bold text-amber-900">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {failed.join("、")}暂时无法读取
          </p>
          <p className="mt-1 text-sm leading-6 text-amber-800">这不代表没有记录，可重试或直接进入服务单核对。</p>
        </div>
        <button
          type="button"
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-amber-300 bg-white px-3 text-sm font-bold text-amber-900 transition-colors hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600"
          onClick={() => void onRetry()}
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          重新读取
        </button>
      </div>
    </div>
  );
}

function SectionHeading({ title, note }: { title: string; note?: string }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-200 pb-2">
      <h3 className="text-sm font-bold text-slate-900">{title}</h3>
      {note && <span className="text-xs text-slate-500">{note}</span>}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-slate-50 px-3 py-2.5">
      <div className="text-xs font-semibold text-slate-500">{label}</div>
      <div className="text-break mt-1 text-sm font-semibold leading-6 text-slate-900">{value}</div>
    </div>
  );
}

function InlineEmpty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-sm text-slate-600">{children}</div>;
}

function DocumentHighlights({
  energyDocument,
  executionDocument,
}: {
  energyDocument?: UserDocument;
  executionDocument?: UserDocument;
}) {
  const oneSentence = blockText(energyDocument, "one_sentence");
  const currentState = blockText(energyDocument, "current_state");
  const successPath = blockText(energyDocument, "success_path");
  const nonNegotiable = blockText(energyDocument, "non_negotiable");
  const principle = blockText(executionDocument, "principle");
  const dailyCheckinItems = blockItems(executionDocument, "daily_checkin");
  const dailyCheckin = dailyCheckinItems.length > 0
    ? dailyCheckinItems
    : splitChecklist(blockText(executionDocument, "daily_checkin"));
  const items = [
    ["一句话结论", oneSentence],
    ["当前身体状态", currentState],
    ["成功路径", successPath],
    ["最不能打折的一件事", nonNegotiable],
    ["执行总原则", principle],
  ].filter((item) => item[1]);

  if (items.length === 0 && dailyCheckin.length === 0) {
    return <InlineEmpty>交付文件存在，但当前模板没有可提取的摘要。</InlineEmpty>;
  }
  return (
    <div className="space-y-3">
      {items.map(([title, value]) => (
        <div key={title} className="grid gap-1 border-b border-slate-100 pb-3 sm:grid-cols-[9rem_1fr] sm:gap-4">
          <div className="text-xs font-semibold text-slate-500">{title}</div>
          <div className="text-break whitespace-pre-wrap text-sm leading-6 text-slate-800">{value}</div>
        </div>
      ))}
      {dailyCheckin.length > 0 && (
        <div className="grid gap-1 sm:grid-cols-[9rem_1fr] sm:gap-4">
          <div className="text-xs font-semibold text-slate-500">每日打卡项</div>
          <ul className="space-y-1 text-sm leading-6 text-slate-800">
            {dailyCheckin.map((item) => <li key={item}>· {item}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function blockByKey(document: UserDocument | undefined, key: string) {
  const blocks = Array.isArray(document?.content_json?.blocks) ? document.content_json.blocks : [];
  return blocks.find((block) => block.key === key);
}

function blockText(document: UserDocument | undefined, key: string) {
  const block = blockByKey(document, key);
  return typeof block?.content === "string" ? block.content.trim() : "";
}

function blockItems(document: UserDocument | undefined, key: string) {
  const block = blockByKey(document, key);
  return Array.isArray(block?.items) ? block.items.map((item) => String(item).trim()).filter(Boolean) : [];
}

function splitChecklist(value: string) {
  return value
    .split(/\n|[；;]/)
    .map((item) => item.replace(/^[\s\d.、)）\-·]+/, "").trim())
    .filter(Boolean);
}

function uniqueCheckinDays(checkins: Checkin[]) {
  return new Set(checkins.map((item) => item.checkin_date).filter(Boolean)).size;
}

function weightTrend(checkins: Checkin[]) {
  const weights = checkins
    .map((item) => item.parsed_json?.weight || "")
    .map((value) => Number(String(value).match(/\d+(\.\d+)?/)?.[0]))
    .filter((value) => Number.isFinite(value));
  if (weights.length < 2) return weights.length === 1 ? `${weights[0]}` : "未记录";
  const first = weights[weights.length - 1];
  const last = weights[0];
  return `${first} → ${last}（${(last - first).toFixed(1)}）`;
}

function senderLabel(message: Message) {
  if (message.sender_type === "bot") return "燃人.AI";
  if (message.sender_type === "staff") return message.display_sender_name || "班主任";
  if (message.direction === "outbound") return "运营";
  return message.display_sender_name || "客户";
}

function reasonText(flags?: Array<Record<string, string>> | null) {
  if (!flags?.length) return "无当前记录";
  return flags
    .slice(-2)
    .map((item) => item.evidence ? `${item.reason || item.code}（${item.evidence}）` : item.reason || item.code)
    .join("；");
}

function intentText(flags?: Array<Record<string, string | boolean>> | null) {
  if (!flags?.length) return "无当前记录";
  const latest = flags[flags.length - 1];
  return typeof latest.evidence === "string" && latest.evidence ? latest.evidence : "已识别";
}

function statusLabel(serviceCase: ServiceCase) {
  const labels: Record<string, string> = {
    created: "已创建",
    questionnaire_submitted: "已提交问卷",
    generating: "文件生成中",
    documents_generated: "文件已生成",
    reviewing: "审核中",
    approved: "已批准",
    in_service: "服务中",
    completed: "阶段已完结",
    refunded: "已退款",
  };
  if (serviceCase.status === "in_service" && serviceCase.service_continued_at) return "续服中";
  return labels[serviceCase.status] || serviceCase.status;
}
