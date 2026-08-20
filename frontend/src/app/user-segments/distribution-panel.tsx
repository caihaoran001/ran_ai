"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronDown, Phone, RotateCw, X } from "lucide-react";
import { api } from "@/lib/api";
import type { PopulationDistribution, ServiceCase } from "@/lib/api";

// 默认最多展示的分桶数（与年龄段等固定 5 桶的维度保持一致高度）；超出的收起，可点开。
const DEFAULT_VISIBLE_BUCKETS = 5;

// 分型标签配色，与单人画像页顶部标签保持一致。
const CUSTOMER_TYPE_COLORS: Record<string, string> = {
  疾病管理型: "bg-rose-500",
  减重攻坚型: "bg-amber-500",
  健康投资型: "bg-emerald-500",
};

function barColor(distKey: string, label: string): string {
  if (distKey === "customer_type" && CUSTOMER_TYPE_COLORS[label]) {
    return CUSTOMER_TYPE_COLORS[label];
  }
  return "bg-cyan-600";
}

const STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  questionnaire_submitted: "已提交问卷",
  generating: "文件生成中",
  documents_generated: "已生成文档",
  reviewing: "审核中",
  approved: "已批准",
  in_service: "服务中",
  completed: "阶段已完结",
  refunded: "已退款",
};

export function DistributionPanel({
  dist,
  embedded = false,
}: {
  dist: PopulationDistribution;
  embedded?: boolean;
}) {
  const denom = dist.total + dist.unknown; // 展示占比的分母含未采集，避免误导
  // 当前下钻选中的分桶标签（null 表示抽屉关闭）。
  const [activeBucket, setActiveBucket] = useState<string | null>(null);
  // 分桶过多时默认收起，只显示前若干条，避免页面某一块过长、与相邻框高度不齐。
  const [expanded, setExpanded] = useState(false);
  const overflow = dist.buckets.length - DEFAULT_VISIBLE_BUCKETS;
  const visibleBuckets = expanded ? dist.buckets : dist.buckets.slice(0, DEFAULT_VISIBLE_BUCKETS);

  const header = (
    <div className="min-w-0">
      <h2 id={`distribution-title-${dist.key}`} className="panel-title">
        {dist.label}
      </h2>
      <p className="panel-subtitle">{dist.description}</p>
    </div>
  );

  const status = (
    <span className="status-pill pill-neutral shrink-0 self-start">
      {`有数据 ${dist.total}`}
      {dist.multi_select ? " · 可多选" : ""}
      {dist.unknown > 0 ? ` · 未采集 ${dist.unknown}` : " · 已完整"}
    </span>
  );

  const buckets = (
    <div className="space-y-3">
      {dist.buckets.length === 0 ? (
        <div className="empty-state">暂无有效样本</div>
      ) : (
        visibleBuckets.map((bucket) => {
          const pct = denom > 0 ? Math.round((bucket.count * 100) / denom) : 0;
          return (
            <button
              type="button"
              key={bucket.label}
              onClick={() => setActiveBucket(bucket.label)}
              className="grid min-h-12 w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 rounded-lg px-2 py-2 text-left transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 focus-visible:ring-offset-1 sm:grid-cols-[8rem_minmax(0,1fr)_6.5rem]"
              title={`查看「${bucket.label}」的用户`}
              aria-label={`查看${bucket.label}用户，共${bucket.count}人，占全部纳入用户${pct}%`}
            >
              <div className="min-w-0 whitespace-normal break-words text-sm font-medium leading-5 text-slate-700 sm:col-start-1 sm:row-start-1">
                {bucket.label}
              </div>
              <div className="shrink-0 text-right text-sm tabular-nums text-slate-600 sm:col-start-3 sm:row-start-1">
                {bucket.count} 人 · {pct}%
              </div>
              <div className="relative col-span-2 row-start-2 h-2.5 overflow-hidden rounded-full bg-slate-100 sm:col-span-1 sm:col-start-2 sm:row-start-1 sm:h-5 sm:rounded-md">
                <div
                  className={`h-full rounded-[inherit] ${barColor(dist.key, bucket.label)}`}
                  style={{ width: `${Math.max(pct, bucket.count > 0 ? 2 : 0)}%` }}
                />
              </div>
            </button>
          );
        })
      )}
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center justify-center gap-1 rounded-md py-1.5 text-sm font-semibold text-cyan-700 transition-colors hover:bg-cyan-50"
        >
          {expanded ? "收起" : `展开全部 ${dist.buckets.length} 项`}
          <ChevronDown className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
        </button>
      )}
    </div>
  );

  return (
    <>
      {embedded ? (
        <section className="min-w-0" aria-labelledby={`distribution-title-${dist.key}`}>
          <div className="flex min-w-0 flex-col gap-3 border-b border-slate-200 pb-4 sm:flex-row sm:items-start sm:justify-between">
            {header}
            {status}
          </div>
          <div className="pt-4">{buckets}</div>
        </section>
      ) : (
        <section className="panel overflow-hidden" aria-labelledby={`distribution-title-${dist.key}`}>
          <div className="panel-header-clean sm:flex-row sm:items-start sm:justify-between">
            {header}
            {status}
          </div>
          <div className="panel-body">{buckets}</div>
        </section>
      )}

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

export function MembersDrawer({
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
  const [reloadKey, setReloadKey] = useState(0);
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    requestAnimationFrame(() => closeButtonRef.current?.focus());

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(
        drawerRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [onClose]);

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
  }, [distKey, bucketLabel, reloadKey]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-slate-900/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="population-members-drawer-title"
        aria-describedby="population-members-drawer-status"
        className="relative flex h-full w-full max-w-md flex-col bg-white shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold text-slate-400">{distLabel}</p>
            <h3 id="population-members-drawer-title" className="text-break text-lg font-bold text-slate-900">{bucketLabel}</h3>
            <p id="population-members-drawer-status" className="mt-0.5 text-sm text-slate-500" aria-live="polite">
              {loading ? "加载中…" : `共 ${members.length} 张服务单`}
            </p>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500"
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
            <div className="empty-state" role="alert">
              <p>{error}</p>
              <button
                type="button"
                className="admin-button-secondary"
                onClick={() => setReloadKey((value) => value + 1)}
              >
                <RotateCw className="h-4 w-4" aria-hidden="true" />
                重新加载
              </button>
            </div>
          ) : members.length === 0 ? (
            <div className="empty-state">该分桶下暂无服务单</div>
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
            {user?.name ?? "待识别用户"}
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
