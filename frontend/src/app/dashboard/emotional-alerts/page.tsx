import Link from "next/link";
import { AlertTriangle, ArrowLeft, MessageSquareText } from "lucide-react";
import { api } from "@/lib/api";
import { formatChinaShortDateTime } from "@/lib/format";

type EmotionalAlertMessage = {
  message_id: number;
  content: string;
  detected_at: string;
  level?: string | null;
  reason?: string | null;
  evidence?: string | null;
};

type EmotionalAlertCaseItem = {
  service_case_id: number;
  case_no: string;
  user_id?: number | null;
  user_name?: string | null;
  group_id: string;
  group_name?: string | null;
  messages: EmotionalAlertMessage[];
};

const LEVEL_LABELS: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

export default async function EmotionalAlertsPage() {
  const items = await api<EmotionalAlertCaseItem[]>("/dashboard/emotional-alerts").catch(() => []);
  const messageCount = items.reduce((sum, item) => sum + item.messages.length, 0);

  return (
    <div className="page-stack">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <Link href="/dashboard" className="inline-flex items-center gap-2 text-sm font-semibold text-cyan-700 hover:underline">
            <ArrowLeft className="h-4 w-4" />
            返回数据看板
          </Link>
          <h1 className="mt-3 page-heading">今日情绪预警</h1>
          <p className="page-subtitle">
            今日共有 {items.length} 个服务单、{messageCount} 条用户消息被识别为需要关注的情绪信号。
          </p>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="panel p-12 text-center text-slate-500">今天暂无情绪预警。</div>
      ) : (
        <div className="grid gap-4">
          {items.map((item) => (
            <section key={item.service_case_id} className="panel overflow-hidden">
              <div className="flex flex-col gap-3 border-b border-slate-100 p-5 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0">
                  <Link href={`/cases/${item.service_case_id}`} className="text-lg font-bold text-slate-950 hover:text-cyan-700">
                    {item.user_name || "未命名用户"}
                  </Link>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs font-semibold text-slate-500">
                    <span>{item.case_no}</span>
                    <span>{item.group_name || item.group_id}</span>
                  </div>
                </div>
                <Link
                  href={`/cases/${item.service_case_id}`}
                  className="inline-flex items-center justify-center rounded-lg border border-cyan-200 px-3 py-2 text-sm font-semibold text-cyan-800 hover:bg-cyan-50"
                >
                  查看服务单
                </Link>
              </div>

              <div className="divide-y divide-slate-100">
                {item.messages.map((message) => (
                  <div key={message.message_id} className="p-5">
                    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs font-semibold">
                      <span className="inline-flex items-center gap-1 rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-rose-700">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        情绪等级：{LEVEL_LABELS[message.level || ""] || message.level || "未标注"}
                      </span>
                      <span className="text-slate-400">{formatChinaShortDateTime(message.detected_at)}</span>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                      <div className="mb-2 flex items-center gap-2 text-xs font-bold text-slate-500">
                        <MessageSquareText className="h-4 w-4" />
                        用户原话
                      </div>
                      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-900">{message.content}</p>
                    </div>
                    {(message.reason || message.evidence) && (
                      <div className="mt-3 grid gap-3 md:grid-cols-2">
                        {message.reason && (
                          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                            <div className="mb-1 text-xs font-bold text-amber-700">识别原因</div>
                            {message.reason}
                          </div>
                        )}
                        {message.evidence && (
                          <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
                            <div className="mb-1 text-xs font-bold text-rose-700">模型标注证据</div>
                            {message.evidence}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
