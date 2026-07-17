import { api } from "@/lib/api";
import type { PopulationDistribution, PopulationPortrait } from "@/lib/api";
import { AlertTriangle, ClipboardCheck, HeartPulse, PieChart, UsersRound } from "lucide-react";
import { DistributionGroupPanel } from "./distribution-group-panel";
import { DistributionPanel } from "./distribution-panel";
import { GeographyPanel } from "./geography-panel";
import { RetryButton } from "./retry-button";

const HEALTH_DISTRIBUTION_KEYS = new Set([
  "health_domain",
  "health_condition",
  "medication_category",
  "special_stage",
  "pending_condition",
  "allergy_category",
]);
const PRIMARY_DISTRIBUTION_KEYS = new Set(["customer_type", "weight_loss_band"]);
const NEED_DISTRIBUTION_KEYS = new Set(["main_need", "expected_change"]);
const HABIT_DISTRIBUTION_KEYS = new Set(["diet_pattern", "sleep_problem", "sleep_quality"]);

function topBucket(distribution: PopulationDistribution | undefined) {
  if (!distribution || distribution.buckets.length === 0) return null;
  return distribution.buckets.reduce((top, bucket) => (bucket.count > top.count ? bucket : top));
}

function percentage(count: number, total: number) {
  return total > 0 ? Math.round((count * 100) / total) : 0;
}

function sectionForDistribution(key: string) {
  if (NEED_DISTRIBUTION_KEYS.has(key)) return "#customer-needs";
  if (HABIT_DISTRIBUTION_KEYS.has(key)) return "#habit-profile";
  if (PRIMARY_DISTRIBUTION_KEYS.has(key)) return "#service-strategy";
  if (HEALTH_DISTRIBUTION_KEYS.has(key)) return "#health-profile";
  return "#body-profile";
}

function formatSnapshotTime(date: Date) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function SectionHeading({ id, title, description }: { id: string; title: string; description: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-1 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h2 id={id} className="text-base font-bold tracking-tight text-slate-900">{title}</h2>
        <p className="mt-0.5 max-w-[72ch] text-sm leading-6 text-slate-600">{description}</p>
      </div>
    </div>
  );
}

function PortraitHeader({ totalUsers, snapshotTime }: { totalUsers?: number; snapshotTime?: string }) {
  return (
    <div className="page-header">
      <div className="page-header-row">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-cyan-50 text-cyan-700">
            <PieChart className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h1 className="page-heading">群体画像</h1>
            <p className="page-subtitle">
              {typeof totalUsers === "number"
                ? `基于 ${totalUsers} 位未删除、未退款用户实时聚合，先看服务重点，再下钻到具体用户。`
                : "从客户结构、健康资料、基础体型和地域分布理解当前服务人群。"}
            </p>
          </div>
        </div>
        {snapshotTime && (
          <span className="status-pill pill-neutral shrink-0 self-start">本次统计 {snapshotTime}</span>
        )}
      </div>
    </div>
  );
}

function PortraitSummary({ portrait }: { portrait: PopulationPortrait }) {
  const customerDistribution = portrait.distributions.find((dist) => dist.key === "customer_type");
  const healthDistribution = portrait.distributions.find((dist) => dist.key === "health_domain");
  const customer = topBucket(customerDistribution);
  const health = topBucket(healthDistribution);
  const largestGap = portrait.distributions.reduce<PopulationDistribution | null>((current, dist) => {
    if (!current || dist.unknown > current.unknown) return dist;
    return current;
  }, null);
  const hasGap = Boolean(largestGap && largestGap.unknown > 0);

  const items = [
    {
      label: "主力人群",
      value: customer?.label ?? "客户分型待补",
      meta: customer
        ? `${customer.count} 人 · 占全部用户 ${percentage(customer.count, portrait.total_users)}%`
        : "暂无可用的客户分型",
      href: "#service-strategy",
      icon: UsersRound,
    },
    {
      label: "主要健康关注",
      value: health?.label ?? "健康资料待积累",
      meta: health
        ? `${health.count} 人 · 点击查看对应用户`
        : "当前没有已确认的健康领域",
      href: "#health-profile",
      icon: HeartPulse,
    },
    {
      label: hasGap ? "首要数据缺口" : "资料完整度",
      value: hasGap && largestGap ? `${largestGap.label}待补` : "主要维度已采集",
      meta: hasGap && largestGap
        ? `${largestGap.unknown} 人未采集 · 占全部用户 ${percentage(largestGap.unknown, portrait.total_users)}%`
        : "当前维度没有未采集用户",
      href: largestGap ? sectionForDistribution(largestGap.key) : "#body-profile",
      icon: ClipboardCheck,
    },
  ];

  return (
    <section className="panel overflow-hidden" aria-labelledby="portrait-summary-title">
      <div className="flex flex-col gap-1 border-b border-slate-200/70 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div>
          <h2 id="portrait-summary-title" className="panel-title">运营快照</h2>
          <p className="panel-subtitle">先看最值得注意的三件事，点击即可跳到对应证据。</p>
        </div>
        <span className="status-pill pill-info shrink-0 self-start">{portrait.total_users} 位用户</span>
      </div>
      <div className="divide-y divide-slate-200 sm:grid sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <a
              key={item.label}
              href={item.href}
              className="group flex min-h-28 items-start gap-3 px-5 py-4 transition-colors duration-150 hover:bg-slate-50/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500 sm:px-6"
            >
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-cyan-50 text-cyan-800 transition-colors group-hover:bg-cyan-100">
                <Icon className="h-4 w-4" aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <span className="block text-xs font-semibold text-slate-500">{item.label}</span>
                <span className="mt-1 block text-base font-bold leading-6 text-slate-900">{item.value}</span>
                <span className="mt-1 block text-sm leading-5 text-slate-600">{item.meta}</span>
              </span>
            </a>
          );
        })}
      </div>
    </section>
  );
}

export default async function UserSegmentsPage() {
  let portrait: PopulationPortrait | null = null;
  try {
    portrait = await api<PopulationPortrait>("/dashboard/population-portrait");
  } catch {
    portrait = null;
  }

  const snapshotTime = formatSnapshotTime(new Date());

  if (!portrait) {
    return (
      <div className="page-stack">
        <PortraitHeader />
        <div className="empty-state" role="alert">
          <AlertTriangle className="h-7 w-7 text-amber-600" aria-hidden="true" />
          <div>
            <p className="font-bold text-slate-900">群体画像加载失败</p>
            <p className="mt-1 max-w-xl leading-6 text-slate-600">
              这次没有拿到统计数据，页面不会用“0 位用户”代替真实结果。请重新加载；如果仍失败，再检查后端服务。
            </p>
          </div>
          <RetryButton />
        </div>
      </div>
    );
  }

  const healthDistributions = portrait.distributions.filter((dist) => HEALTH_DISTRIBUTION_KEYS.has(dist.key));
  const primaryDistributions = portrait.distributions.filter((dist) => PRIMARY_DISTRIBUTION_KEYS.has(dist.key));
  const needDistributions = portrait.distributions.filter((dist) => NEED_DISTRIBUTION_KEYS.has(dist.key));
  const habitDistributions = portrait.distributions.filter((dist) => HABIT_DISTRIBUTION_KEYS.has(dist.key));
  const bodyDistributions = portrait.distributions.filter(
    (dist) => !HEALTH_DISTRIBUTION_KEYS.has(dist.key)
      && !PRIMARY_DISTRIBUTION_KEYS.has(dist.key)
      && !NEED_DISTRIBUTION_KEYS.has(dist.key)
      && !HABIT_DISTRIBUTION_KEYS.has(dist.key),
  );

  return (
    <div className="page-stack">
      <PortraitHeader totalUsers={portrait.total_users} snapshotTime={snapshotTime} />

      {portrait.total_users === 0 ? (
        <div className="empty-state">
          <UsersRound className="h-7 w-7 text-slate-400" aria-hidden="true" />
          <div>
            <p className="font-bold text-slate-900">当前统计范围内暂无用户</p>
            <p className="mt-1 leading-6 text-slate-600">新用户建档且最新服务单未退款后，会自动进入这里。</p>
          </div>
        </div>
      ) : (
        <>
          <PortraitSummary portrait={portrait} />

          {needDistributions.length > 0 && (
            <DistributionGroupPanel
              id="customer-needs"
              title="客户共同诉求"
              description="汇总用户明确选择的首要问题和 28 天期待；这里只呈现可稳定统计的结构化表达，不把每人的自由文本深层需求强行拼成结论。"
              distributions={needDistributions}
            />
          )}

          {primaryDistributions.length > 0 && (
            <section id="service-strategy" className="scroll-mt-6 space-y-3" aria-labelledby="service-strategy-title">
              <SectionHeading
                id="service-strategy-title"
                title="服务策略画像"
                description="先判断主要客户类型和最终减重层级；点击任一分布即可查看对应用户。"
              />
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {primaryDistributions.map((dist) => (
                  <DistributionPanel key={dist.key} dist={dist} />
                ))}
              </div>
            </section>
          )}

          {habitDistributions.length > 0 && (
            <DistributionGroupPanel
              id="habit-profile"
              title="饮食与睡眠习惯"
              description="饮食模式、常见睡眠问题和主观睡眠质量集中查看；补剂习惯暂不纳入，避免与当前产品使用情况混淆。"
              distributions={habitDistributions}
            />
          )}

          {healthDistributions.length > 0 && (
            <DistributionGroupPanel
              id="health-profile"
              title="健康资料"
              description="查看已确认健康情况、长期用药、特殊阶段、待确认项与过敏史。"
              distributions={healthDistributions}
            />
          )}

          {bodyDistributions.length > 0 && (
            <DistributionGroupPanel
              id="body-profile"
              title="基础与体型"
              description="年龄、BMI、体脂率和腰高比集中查看；体脂率和腰围可能尚未填报。"
              distributions={bodyDistributions}
            />
          )}

          {portrait.geography && <GeographyPanel geography={portrait.geography} />}

          <details className="panel group overflow-hidden">
            <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-5 py-3 text-sm font-bold text-slate-800 marker:hidden hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500 sm:px-6">
              <span>统计口径</span>
              <span className="text-xs font-semibold text-slate-500 group-open:hidden">展开查看</span>
              <span className="hidden text-xs font-semibold text-slate-500 group-open:inline">收起</span>
            </summary>
            <div className="grid gap-4 border-t border-slate-200 px-5 py-4 text-sm leading-6 text-slate-600 sm:grid-cols-2 sm:px-6">
              <div>
                <p className="font-bold text-slate-800">纳入范围</p>
                <p>每位用户只取最新未删除服务单；最新服务单已确认退款则不纳入。</p>
              </div>
              <div>
                <p className="font-bold text-slate-800">数据来源</p>
                <p>问卷固定选项、服务单健康资料和 AI 画像分别贡献可用维度；没有 AI 画像仍计入总人数。</p>
              </div>
              <div>
                <p className="font-bold text-slate-800">比例口径</p>
                <p>普通分布均以全部纳入用户为分母；地域下钻到城市后改为显示该省内部占比。</p>
              </div>
              <div>
                <p className="font-bold text-slate-800">多选与缺失</p>
                <p>诉求、睡眠问题和部分健康资料可多选，分桶人数之和可能超过总人数；缺失信息统一归入“未采集”。</p>
              </div>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
