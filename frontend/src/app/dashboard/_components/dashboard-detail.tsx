import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft, FileText, MessageSquareText } from "lucide-react";
import { StructuredCheckin } from "@/components/structured-checkin";
import type { CheckinParsed } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";

export type DashboardCaseRef = {
  service_case_id: number;
  case_no: string;
  user_id?: number | null;
  user_name?: string | null;
  user_phone?: string | null;
  group_id: string;
  group_name?: string | null;
};

export type DashboardMessageEvent = {
  event_id: string;
  message_id?: number | null;
  content: string;
  occurred_at?: string | null;
  reason?: string | null;
  evidence?: string | null;
  level?: string | null;
  ai_reply_message_id?: number | null;
  ai_reply_content?: string | null;
  ai_reply_created_at?: string | null;
};

export type DashboardCaseMessageEvents = {
  case: DashboardCaseRef;
  events: DashboardMessageEvent[];
};

export type DashboardRiskReason = {
  level?: string | null;
  reason?: string | null;
  evidence?: string | null;
};

export type DashboardCaseInsightItem = {
  case: DashboardCaseRef;
  risk_level?: string | null;
  idle_days?: number | null;
  last_inbound_at?: string | null;
  recall_count?: number;
  last_recall_at?: string | null;
  service_day?: number | null;
  started_at?: string | null;
  reasons: DashboardRiskReason[];
};

export type DashboardCheckinEvent = {
  checkin_id: number;
  checkin_date: string;
  content: string;
  parsed_json?: CheckinParsed | null;
  created_at: string;
};

export type DashboardCaseCheckins = {
  case: DashboardCaseRef;
  checkins: DashboardCheckinEvent[];
};

export type DashboardPendingDocument = {
  document_id: number;
  document_type: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type DashboardCasePendingDocuments = {
  case: DashboardCaseRef;
  documents: DashboardPendingDocument[];
};

const LEVEL_LABELS: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  reviewing: "待审核",
  approved: "已审核",
  sent: "已发送",
  archived: "未发送",
};

export function DetailHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle: string;
}) {
  return (
    <div>
      <Link href="/dashboard" className="inline-flex min-h-10 items-center gap-2 text-sm font-semibold text-cyan-700 hover:underline">
        <ArrowLeft className="h-4 w-4" />
        返回数据看板
      </Link>
      <h1 className="mt-3 page-heading">{title}</h1>
      <p className="page-subtitle">{subtitle}</p>
    </div>
  );
}

export function MessageEventList({
  items,
  emptyText,
  showAiReply = false,
}: {
  items: DashboardCaseMessageEvents[];
  emptyText: string;
  showAiReply?: boolean;
}) {
  if (!items.length) {
    return <div className="panel p-12 text-center text-slate-500">{emptyText}</div>;
  }
  return (
    <div className="grid gap-4">
      {items.map((item) => (
        <CaseSection key={item.case.service_case_id} caseRef={item.case}>
          <div className="divide-y divide-slate-100">
            {item.events.map((event) => (
              <div key={event.event_id} className="p-5">
                <EventBadges event={event} />
                <QuoteBlock title="用户原话" content={event.content || event.evidence || "未保存原话"} />
                {(event.reason || event.evidence) && (
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                    {event.reason && <InfoBox tone="amber" title="识别原因" content={event.reason} />}
                    {event.evidence && <InfoBox tone="rose" title="模型标注证据" content={event.evidence} />}
                  </div>
                )}
                {showAiReply && event.ai_reply_content && (
                  <div className="mt-3">
                    <QuoteBlock title="AI 拟回复" content={event.ai_reply_content} muted />
                  </div>
                )}
              </div>
            ))}
          </div>
        </CaseSection>
      ))}
    </div>
  );
}

export function CaseInsightList({
  items,
  emptyText,
}: {
  items: DashboardCaseInsightItem[];
  emptyText: string;
}) {
  if (!items.length) {
    return <div className="panel p-12 text-center text-slate-500">{emptyText}</div>;
  }
  return (
    <div className="grid gap-4">
      {items.map((item) => (
        <CaseSection key={item.case.service_case_id} caseRef={item.case}>
          <div className="p-5">
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-semibold">
              {item.risk_level === "high" && (
                <span className="rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-rose-700">高风险</span>
              )}
              {typeof item.idle_days === "number" && (
                <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-amber-700">
                  已闲置 {item.idle_days} 天
                </span>
              )}
              {(item.recall_count ?? 0) > 0 && (
                <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-violet-700">
                  已召回 {item.recall_count} 次
                </span>
              )}
              {item.last_inbound_at && (
                <span className="text-slate-400">最近入站 {formatChinaShortDateTime(item.last_inbound_at)}</span>
              )}
              {item.last_recall_at && (
                <span className="text-slate-400">最近召回 {formatChinaShortDateTime(item.last_recall_at)}</span>
              )}
              {typeof item.service_day === "number" && (
                <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-emerald-700">
                  {item.service_day < 1 ? "未开始" : `服务第 ${item.service_day} 天`}
                </span>
              )}
              {item.started_at && (
                <span className="text-slate-400">起始 {formatChinaShortDateTime(item.started_at)}</span>
              )}
            </div>
            {item.reasons.length ? (
              <div className="grid gap-3 md:grid-cols-2">
                {item.reasons.map((reason, index) => (
                  <InfoBox
                    key={index}
                    tone="rose"
                    title={`风险原因${reason.level ? `（${LEVEL_LABELS[reason.level] || reason.level}）` : ""}`}
                    content={reason.reason || reason.evidence || "已标记风险，但未保存具体原因。"}
                  />
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-400">暂无更多说明，点击进入服务单查看详情。</p>
            )}
          </div>
        </CaseSection>
      ))}
    </div>
  );
}

export function CheckinDetailList({ items }: { items: DashboardCaseCheckins[] }) {
  if (!items.length) {
    return <div className="panel p-12 text-center text-slate-500">今天暂无打卡记录。</div>;
  }
  return (
    <div className="grid gap-4">
      {items.map((item) => (
        <CaseSection key={item.case.service_case_id} caseRef={item.case}>
          <div className="divide-y divide-slate-100">
            {item.checkins.map((checkin) => (
              <div key={checkin.checkin_id} className="p-5">
                <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-500">
                  <span className="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-1 text-cyan-700">
                    打卡日期：{checkin.checkin_date}
                  </span>
                  <span>{formatChinaShortDateTime(checkin.created_at)}</span>
                </div>
                <StructuredCheckin parsed={checkin.parsed_json} />
              </div>
            ))}
          </div>
        </CaseSection>
      ))}
    </div>
  );
}

export function PendingDocumentList({ items }: { items: DashboardCasePendingDocuments[] }) {
  if (!items.length) {
    return <div className="panel p-12 text-center text-slate-500">暂无待审核文件。</div>;
  }
  return (
    <div className="grid gap-4">
      {items.map((item) => (
        <CaseSection key={item.case.service_case_id} caseRef={item.case}>
          <div className="divide-y divide-slate-100">
            {item.documents.map((document) => (
              <div key={document.document_id} className="flex flex-col gap-3 p-5 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2 text-sm font-bold text-slate-950">
                    <FileText className="h-4 w-4 text-slate-400" />
                    <span className="text-break">{document.title}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                    <span>{document.document_type}</span>
                    <span>状态：{STATUS_LABELS[document.status] || document.status}</span>
                    <span>更新：{formatChinaShortDateTime(document.updated_at)}</span>
                  </div>
                </div>
                <Link
                  href={`/documents/${document.document_id}`}
                  className="inline-flex min-h-10 items-center justify-center rounded-lg border border-cyan-200 px-3 py-2 text-sm font-semibold text-cyan-800 hover:bg-cyan-50"
                >
                  审核文件
                </Link>
              </div>
            ))}
          </div>
        </CaseSection>
      ))}
    </div>
  );
}

function CaseSection({ caseRef, children }: { caseRef: DashboardCaseRef; children: ReactNode }) {
  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-slate-100 p-5 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <Link href={`/cases/${caseRef.service_case_id}`} className="text-break text-lg font-bold text-slate-950 hover:text-cyan-700">
            {caseRef.user_name || "未命名用户"}
          </Link>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs font-semibold text-slate-500">
            <span>{caseRef.case_no}</span>
            <span>{caseRef.group_name || caseRef.group_id}</span>
          </div>
        </div>
        <Link
          href={`/cases/${caseRef.service_case_id}`}
          className="inline-flex min-h-10 items-center justify-center rounded-lg border border-cyan-200 px-3 py-2 text-sm font-semibold text-cyan-800 hover:bg-cyan-50"
        >
          查看服务单
        </Link>
      </div>
      {children}
    </section>
  );
}

function EventBadges({ event }: { event: DashboardMessageEvent }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-semibold">
      {event.level && (
        <span className="rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-rose-700">
          等级：{LEVEL_LABELS[event.level] || event.level}
        </span>
      )}
      {event.occurred_at && <span className="text-slate-400">{formatChinaShortDateTime(event.occurred_at)}</span>}
    </div>
  );
}

function QuoteBlock({ title, content, muted = false }: { title: string; content: string; muted?: boolean }) {
  return (
    <div className={`rounded-lg border p-4 ${muted ? "border-slate-200 bg-white" : "border-slate-200 bg-slate-50"}`}>
      <div className="mb-2 flex items-center gap-2 text-xs font-bold text-slate-500">
        <MessageSquareText className="h-4 w-4" />
        {title}
      </div>
      <p className="text-break whitespace-pre-wrap text-sm leading-6 text-slate-900">{content}</p>
    </div>
  );
}

function InfoBox({ title, content, tone }: { title: string; content: string; tone: "amber" | "rose" }) {
  const boxClass =
    tone === "amber"
      ? "border-amber-200 bg-amber-50 text-amber-900"
      : "border-rose-200 bg-rose-50 text-rose-900";
  const titleClass = tone === "amber" ? "text-amber-700" : "text-rose-700";
  return (
    <div className={`${boxClass} text-break rounded-lg border p-3 text-sm`}>
      <div className={`mb-1 text-xs font-bold ${titleClass}`}>{title}</div>
      {content}
    </div>
  );
}
