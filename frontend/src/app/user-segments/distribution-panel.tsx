"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Phone, X } from "lucide-react";
import { api } from "@/lib/api";
import type { PopulationDistribution, ServiceCase } from "@/lib/api";

// 分型标签配色，与单人画像页顶部标签保持一致。
const CUSTOMER_TYPE_COLORS: Record<string, string> = {
  疾病管理型: "bg-rose-500",
  减重攻坚型: "bg-amber-500",
  健康投资型: "bg-emerald-500",
};

function barColor(distKey: string, label: string, index: number): string {
  if (distKey === "customer_type" && CUSTOMER_TYPE_COLORS[label]) {
    return CUSTOMER_TYPE_COLORS[label];
  }
  const palette = ["bg-cyan-500", "bg-sky-500", "bg-indigo-500", "bg-violet-500", "bg-fuchsia-500"];
  return palette[index % palette.length];
}

const STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  questionnaire_submitted: "已提交问卷",
  generating: "文件生成中",
  documents_generated: "已生成文档",
  reviewing: "审核中",
  approved: "已批准",
  in_service: "服务中",
  completed: "已毕业",
  refunded: "已退款",
};

export function DistributionPanel({ dist }: { dist: PopulationDistribution }) {
  const denom = dist.total + dist.unknown; // 展示占比的分母含未采集，避免误导
  const maxCount = Math.max(1, ...dist.buckets.map((b) => b.count));
  // 当前下钻选中的分桶标签（null 表示抽屉关闭）。
  const [activeBucket, setActiveBucket] = useState<string | null>(null);

  return (
    <>
      <section className="panel overflow-hidden">
        <div className="panel-header-clean">
          <div>
            <h2 className="panel-title">{dist.label}</h2>
            <p className="panel-subtitle">{dist.description}</p>
          </div>
          <span className="status-pill pill-neutral">
            有效 {dist.total}
            {dist.unknown > 0 ? ` · 未采集 ${dist.unknown}` : ""}
          </span>
        </div>
        <div className="panel-body space-y-3">
          {dist.buckets.length === 0 ? (
            <div className="empty-state">暂无有效样本</div>
          ) : (
            dist.buckets.map((bucket, index) => {
              const pct = denom > 0 ? Math.round((bucket.count * 100) / denom) : 0;
              const widthPct = Math.round((bucket.count * 100) / maxCount);
              return (
                <button
                  type="button"
                  key={bucket.label}
                  onClick={() => setActiveBucket(bucket.label)}
                  className="flex w-full items-center gap-3 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-slate-50"
                  title={`查看「${bucket.label}」的用户`}
                >
                  <div className="w-32 shrink-0 truncate text-sm text-slate-700">{bucket.label}</div>
                  <div className="relative h-6 flex-1 overflow-hidden rounded-md bg-slate-100">
                    <div
                      className={`h-full rounded-md ${barColor(dist.key, bucket.label, index)}`}
                      style={{ width: `${Math.max(widthPct, bucket.count > 0 ? 4 : 0)}%` }}
                    />
                  </div>
                  <div className="w-24 shrink-0 text-right text-sm tabular-nums text-slate-600">
                    {bucket.count} 人 · {pct}%
                  </div>
                </button>
              );
            })
          )}
        </div>
      </section>

      {activeBucket !== null && (
        <MembersDrawer
          distKey={dist.key}
          distLabel={dist.label}
          bucketLabel={activeBucket}
          onClose={() => setActiveBucket(null)}
        />
      )}
    </>
  );
}

function MembersDrawer({
  distKey,
  distLabel,
  bucketLabel,
  onClose,
}: {
  distKey: string;
  distLabel: string;
  bucketLabel: string;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [members, setMembers] = useState<ServiceCase[]>([]);
  const [error, setError] = useState<string | null>(null);

  const escHandler = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener("keydown", escHandler);
    return () => document.removeEventListener("keydown", escHandler);
  }, [escHandler]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ key: distKey, bucket: bucketLabel });
    api<ServiceCase[]>(`/dashboard/population-portrait/members?${params.toString()}`)
      .then((data) => {
        if (!cancelled) setMembers(data);
      })
      .catch(() => {
        if (!cancelled) setError("加载用户列表失败，请重试");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [distKey, bucketLabel]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-slate-900/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside className="relative flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold text-slate-400">{distLabel}</p>
            <h3 className="text-break text-lg font-bold text-slate-900">{bucketLabel}</h3>
            <p className="mt-0.5 text-sm text-slate-500">
              {loading ? "加载中…" : `共 ${members.length} 位用户`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="关闭"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-14 animate-pulse rounded-lg bg-slate-100" />
              ))}
            </div>
          ) : error ? (
            <div className="empty-state">{error}</div>
          ) : members.length === 0 ? (
            <div className="empty-state">该分桶下暂无用户</div>
          ) : (
            <ul className="space-y-2">
              {members.map((sc) => (
                <MemberRow key={sc.id} serviceCase={sc} />
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}

function MemberRow({ serviceCase }: { serviceCase: ServiceCase }) {
  const user = serviceCase.user;
  const statusLabel = STATUS_LABELS[serviceCase.status] || serviceCase.status;
  const inner = (
    <div className="flex items-center gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-cyan-200 bg-cyan-50 text-base font-black text-cyan-900">
        {(user?.name ?? "待").charAt(0)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-break text-sm font-bold text-slate-900">
            {user?.name ?? "待填写问卷用户"}
          </span>
          {serviceCase.risk_level === "high" && (
            <span className="status-pill border-orange-200 bg-orange-50 text-orange-700">高风险</span>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-500">
          {user?.phone && (
            <span className="flex items-center gap-1">
              <Phone className="h-3 w-3 text-slate-400" />
              {user.phone}
            </span>
          )}
          <span>{statusLabel}</span>
          {(serviceCase.group_name || serviceCase.group_id) && (
            <span className="max-w-40 truncate">群: {serviceCase.group_name || serviceCase.group_id}</span>
          )}
        </div>
      </div>
      {user && <ArrowRight className="h-4 w-4 shrink-0 text-cyan-600" />}
    </div>
  );

  if (!user) {
    return (
      <li className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">{inner}</li>
    );
  }
  return (
    <li>
      <Link
        href={`/users/${user.id}`}
        className="block rounded-lg border border-slate-200 bg-white px-3 py-2.5 transition-colors hover:border-cyan-300 hover:bg-cyan-50/40"
      >
        {inner}
      </Link>
    </li>
  );
}
