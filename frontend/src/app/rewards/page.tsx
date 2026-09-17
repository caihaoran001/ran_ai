import Link from "next/link";
import type { ReactNode } from "react";
import { CheckCircle2, ChevronDown, FileText, Gift, MapPin, MessageSquareQuote, PackageOpen, Settings, Ticket, Truck } from "lucide-react";
import {
  api,
  type Day28BenefitGrantCase,
  type RewardAdminBatch,
  type RewardAdminCase,
  type RewardAdminList,
  type RewardCheckinDay,
  type RewardShipment,
} from "@/lib/api";
import { ShipmentAction } from "./shipment-action";
import { CheckinRewardSwitch } from "./checkin-reward-switch";
import { CheckinRewardTierConfig } from "./checkin-reward-tier-config";
import { Day28BenefitConfig } from "./day28-benefit-config";

const statusCopy: Record<string, { label: string; className: string }> = {
  pending_shipment: {
    label: "待发货",
    className: "border-amber-200 bg-amber-50 text-amber-800",
  },
  missing_address: {
    label: "待补地址",
    className: "border-rose-200 bg-rose-50 text-rose-800",
  },
  waiting_choice: {
    label: "待用户选择",
    className: "border-sky-200 bg-sky-50 text-sky-800",
  },
  accumulating: {
    label: "继续累积",
    className: "border-violet-200 bg-violet-50 text-violet-800",
  },
  shipped: {
    label: "已发货",
    className: "border-emerald-200 bg-emerald-50 text-emerald-800",
  },
  qualified: {
    label: "已达标",
    className: "border-slate-200 bg-slate-50 text-slate-700",
  },
};

function formatDate(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatDateOnly(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function batchLabel(batch: RewardAdminBatch) {
  if (batch.kind === "current") {
    return batch.started_on ? `当前批次（${batch.started_on} 开启）` : "当前批次（未开启）";
  }
  if (batch.kind === "round") {
    const closedOn = batch.closed_at ? formatDateOnly(batch.closed_at) : "—";
    return `历史批次（${batch.started_on ?? "—"} 开启 · ${closedOn} 关闭）`;
  }
  return batch.reward_created_on
    ? `更早批次（系统未记录开启/关闭时间 · 奖励产生于 ${batch.reward_created_on}）`
    : "更早批次（系统未记录开启/关闭时间）";
}

function batchPlaceholder(batch: RewardAdminBatch, rewardsEnabled: boolean) {
  if (batch.kind !== "current") return "本批暂无记录";
  if (!batch.started_on || !rewardsEnabled) return "当前未开启；开启后新达标客户会显示在这里";
  return "本轮还没有客户达标";
}

function RewardCaseCard({ row, batchClosed }: { row: RewardAdminCase; batchClosed: boolean }) {
  const status = statusCopy[row.operational_status] ?? statusCopy.qualified;
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header-clean flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-extrabold text-slate-950">{row.user_name}</h2>
            <span className={`status-pill ${status.className}`}>{status.label}</span>
          </div>
          <p className="text-break mt-1 text-sm text-slate-600">{row.group_name || row.group_id}</p>
          <Link href={`/cases/${row.service_case_id}`} className="mt-2 inline-block text-xs font-bold text-cyan-800 hover:underline">
            {row.case_no} · 查看服务单
          </Link>
        </div>
        <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm lg:text-right">
          <div>
            <span className="block text-xs font-semibold text-slate-500">完整打卡</span>
            <span className="font-extrabold text-slate-950">{row.complete_checkin_days} 天</span>
            {row.participating ? null : (
              <span className="block text-xs font-medium text-slate-500">
                {batchClosed ? "所属批次已关闭，只算到已发奖励需要的那几天" : "本单不参与本轮，只算到已发奖励需要的那几天"}
              </span>
            )}
          </div>
          <div>
            <span className="block text-xs font-semibold text-slate-500">尚未寄出</span>
            <span className="font-extrabold text-slate-950">{row.unshipped_rewards}</span>
          </div>
        </div>
      </div>

      <div className="grid gap-5 p-4 sm:p-5 lg:grid-cols-[minmax(15rem,0.7fr)_minmax(24rem,1.3fr)]">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
            <Gift className="h-4 w-4 text-cyan-700" />
            已获得奖励
          </h3>
          <ol className="mt-3 grid gap-2">
            {row.qualifications.map((qualification) => (
              <li key={qualification.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm">
                <span className="font-semibold text-slate-900">{qualification.reward}</span>
                <span className="shrink-0 text-xs text-slate-500">满 {qualification.tier_days} 天 · {qualification.qualified_on}</span>
              </li>
            ))}
          </ol>
        </div>
        <div>
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900">
            <Truck className="h-4 w-4 text-cyan-700" />
            寄送批次
          </h3>
          <div className="mt-3 grid gap-3">
            {row.shipments.length ? row.shipments.map((shipment) => (
              <ShipmentCard key={shipment.id} shipment={shipment} />
            )) : (
              <div className="empty-state rounded-xl border border-dashed border-slate-300 py-8">
                用户尚未选择立即寄出，当前奖励继续保留在该服务单下。
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="border-t border-slate-200 px-4 py-4 sm:px-5 sm:py-5">
        <details className="group/evidence">
          <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-4 px-4 py-3.5 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-600 [&::-webkit-details-marker]:hidden">
            <span className="min-w-0">
              <span className="block text-sm font-bold text-slate-950">完整打卡证据</span>
              <span className="mt-0.5 block text-xs leading-5 text-slate-600">
                {row.participating
                  ? `共 ${row.checkin_days.length} 天；展开后可核对日期、五项答案和用户原话`
                  : `共 ${row.checkin_days.length} 天（已发奖励对应部分）；${batchClosed ? "所属批次已关闭" : "本单不参与本轮"}，之后的完整打卡不再计入`}
              </span>
            </span>
            <ChevronDown className="h-5 w-5 shrink-0 text-slate-500 transition-transform duration-200 group-open/evidence:rotate-180 motion-reduce:transition-none" />
          </summary>
          <ol className="border-t border-slate-200">
            {row.checkin_days.map((day) => <CheckinEvidenceDay key={day.checkin_id} day={day} />)}
          </ol>
        </details>
      </div>
    </section>
  );
}

function ShipmentCard({ shipment }: { shipment: RewardShipment }) {
  const shipped = shipment.status === "shipped";
  const missingAddress = shipment.status === "missing_address";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm shadow-slate-200/40">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-bold text-slate-950">{shipment.rewards}</p>
          <p className="mt-1 text-xs text-slate-500">
            批次 #{shipment.id} · {shipped ? `发货于 ${formatDate(shipment.shipped_at)}` : `申请于 ${formatDate(shipment.requested_at)}`}
          </p>
        </div>
        <span
          className={`status-pill ${
            shipped
              ? statusCopy.shipped.className
              : missingAddress
                ? statusCopy.missing_address.className
                : statusCopy.pending_shipment.className
          }`}
        >
          {shipped ? "已发货" : missingAddress ? "信息未齐" : "待运营寄出"}
        </span>
      </div>
      <div className="mt-4 grid gap-3 rounded-lg bg-slate-50 p-3 text-sm sm:grid-cols-[9rem_10rem_1fr]">
        <div>
          <span className="block text-xs font-semibold text-slate-500">收件人</span>
          <span className="mt-1 block font-medium text-slate-900">{shipment.recipient_name || "待补"}</span>
        </div>
        <div>
          <span className="block text-xs font-semibold text-slate-500">手机号</span>
          <span className="mt-1 block font-medium text-slate-900">{shipment.recipient_phone || "待补"}</span>
        </div>
        <div>
          <span className="block text-xs font-semibold text-slate-500">详细地址</span>
          <span className="text-break mt-1 block font-medium text-slate-900">{shipment.shipping_address || "待补"}</span>
        </div>
      </div>
      {shipment.status === "pending_shipment" ? (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <ShipmentAction shipmentId={shipment.id} />
        </div>
      ) : null}
      {shipped ? (
        <p className="mt-3 flex items-center gap-1.5 text-xs font-medium text-emerald-700">
          <CheckCircle2 className="h-3.5 w-3.5" />
          由 {shipment.marked_by || "后台管理员"} 确认
        </p>
      ) : null}
    </div>
  );
}

function CheckinEvidenceDay({ day }: { day: RewardCheckinDay }) {
  return (
    <li className="border-t border-slate-200 first:border-t-0">
      <details className="group/day">
        <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-600 [&::-webkit-details-marker]:hidden">
          <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-bold text-slate-950">{day.checkin_date}</span>
            <span className="text-xs font-semibold text-slate-600">累计第 {day.cumulative_day} 天</span>
            <span className="status-pill pill-ok">完整打卡</span>
          </span>
          <ChevronDown className="h-4 w-4 shrink-0 text-slate-500 transition-transform duration-200 group-open/day:rotate-180 motion-reduce:transition-none" />
        </summary>
        <div className="grid gap-4 border-t border-slate-100 bg-slate-50/60 px-4 py-4 lg:grid-cols-[minmax(22rem,1.35fr)_minmax(18rem,0.65fr)]">
          <div>
            <p className="mb-2 flex items-center gap-2 text-xs font-bold text-slate-700">
              <FileText className="h-3.5 w-3.5 text-cyan-700" />
              当天计入的五项
            </p>
            <ol className="grid gap-2">
              {day.items.map((item) => (
                <li key={item.item_id} className="grid gap-1 rounded-lg bg-white px-3 py-2.5 text-sm ring-1 ring-slate-200 sm:grid-cols-[minmax(10rem,0.9fr)_minmax(12rem,1.1fr)] sm:gap-4">
                  <span className="text-break font-semibold text-slate-700">{item.order}. {item.question}</span>
                  <span className="text-break text-slate-950">{item.answer}</span>
                </li>
              ))}
            </ol>
          </div>
          <div>
            <p className="mb-2 flex items-center gap-2 text-xs font-bold text-slate-700">
              <MessageSquareQuote className="h-3.5 w-3.5 text-cyan-700" />
              用户原话
            </p>
            {day.source_messages.length ? (
              <div className="grid gap-2">
                {day.source_messages.map((message) => (
                  <blockquote key={message.id} className="rounded-lg bg-white px-3 py-2.5 text-sm leading-6 text-slate-800 ring-1 ring-slate-200">
                    <p className="text-break whitespace-pre-wrap">{message.content}</p>
                    <footer className="mt-1 text-xs font-medium text-slate-500">
                      消息 #{message.id} · {formatDate(message.created_at)}
                    </footer>
                  </blockquote>
                ))}
              </div>
            ) : day.original_submission ? (
              <blockquote className="rounded-lg bg-white px-3 py-2.5 text-sm leading-6 text-slate-800 ring-1 ring-slate-200">
                <p className="text-break whitespace-pre-wrap">{day.original_submission}</p>
                <footer className="mt-1 text-xs font-medium text-slate-500">原始打卡提交</footer>
              </blockquote>
            ) : (
              <p className="rounded-lg border border-dashed border-slate-300 bg-white px-3 py-3 text-sm leading-6 text-slate-600">
                该历史打卡没有可回查的群消息证据；五项答案仍来自当天打卡账本。
              </p>
            )}
          </div>
        </div>
      </details>
    </li>
  );
}

function Day28GrantLedger({ rows }: { rows: Day28BenefitGrantCase[] }) {
  return (
    <details className="panel group/grants overflow-hidden">
      <summary className="flex min-h-16 cursor-pointer list-none items-center justify-between gap-3 px-4 py-4 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-600 [&::-webkit-details-marker]:hidden sm:px-5">
        <span className="flex items-center gap-2 text-sm font-bold text-slate-900">
          <Ticket className="h-5 w-5 text-cyan-700" />
          发放记录
        </span>
        <ChevronDown className="h-5 w-5 shrink-0 text-slate-500 transition-transform duration-200 group-open/grants:rotate-180 motion-reduce:transition-none" />
      </summary>
      <div className="border-t border-slate-200">
        <p className="px-4 py-4 text-sm text-slate-600 sm:px-5">
          客户走完 28 天后助教发出的券。这里记的是「领取入口已经发到群里」——商城还没打通，
          系统看不到客户领没领、用没用，所以不会出现「已到账」。同一位客户的同一张券只会发一次。
        </p>
        {rows.length ? (
          <ol className="border-t border-slate-200">
            {rows.map((row) => (
              <li key={row.service_case_id} className="border-t border-slate-200 px-4 py-4 first:border-t-0 sm:px-5">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-bold text-slate-950">{row.user_name}</span>
                  <span className="text-break text-xs text-slate-600">{row.group_name || "—"}</span>
                  <Link
                    href={`/cases/${row.service_case_id}`}
                    className="text-xs font-bold text-cyan-800 hover:underline"
                  >
                    {row.case_no} · 查看服务单
                  </Link>
                </div>
                <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                  {row.grants.map((grant) => (
                    <li
                      key={grant.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm"
                    >
                      <span className="font-semibold text-slate-900">{grant.benefit_name}</span>
                      <span className="shrink-0 text-xs text-slate-500">
                        已发出领取入口 · {formatDate(grant.granted_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        ) : (
          <div className="empty-state border-t border-slate-200 py-10">
            还没有发出过结营权益。
          </div>
        )}
      </div>
    </details>
  );
}

function RulesDisclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="panel group/rules overflow-hidden">
      <summary className="flex min-h-16 cursor-pointer list-none items-center justify-between gap-3 px-4 py-4 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-600 [&::-webkit-details-marker]:hidden sm:px-5">
        <span className="flex items-center gap-2 text-sm font-bold text-slate-900">
          <Settings className="h-5 w-5 text-cyan-700" />
          {title}
        </span>
        <ChevronDown className="h-5 w-5 shrink-0 text-slate-500 transition-transform duration-200 group-open/rules:rotate-180 motion-reduce:transition-none" />
      </summary>
      <div className="border-t border-slate-200 p-4 sm:p-5">
        {children}
      </div>
    </details>
  );
}

export default async function RewardsPage() {
  const [rewardList, day28Grants] = await Promise.all([
    api<RewardAdminList>("/checkin-rewards").catch(() => null),
    api<Day28BenefitGrantCase[]>("/day28-benefit-grants").catch(() => []),
  ]);
  const cases = rewardList?.cases ?? [];
  const batches = rewardList?.batches ?? [];
  const rewardsEnabled = rewardList?.rewards_enabled ?? false;
  const batchesWithCases: Array<{ batch: RewardAdminBatch | null; cases: RewardAdminCase[] }> = batches.length
    ? batches.map((batch) => ({ batch, cases: cases.filter((row) => row.batch_key === batch.key) }))
    : cases.length
      ? [{ batch: null, cases }]
      : [];
  const pendingShipments = cases.flatMap((row) => row.shipments).filter((row) => row.status === "pending_shipment").length;
  const missingAddress = cases.flatMap((row) => row.shipments).filter((row) => row.status === "missing_address").length;
  const waitingChoice = cases.filter((row) => row.operational_status === "waiting_choice").length;

  return (
    <div className="page-stack">
      <header className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">奖励与权益</h1>
            <p className="page-subtitle">打卡实物奖励与结营券都在这一页：规则在上方配置，达标与发放记录在下方查看，寄出后在这里确认。</p>
          </div>
          <span className="status-pill border-cyan-200 bg-cyan-50 text-cyan-800">
            {cases.length} 位已达标用户
          </span>
        </div>
      </header>

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-extrabold text-slate-950">打卡奖励（实物）</h2>
          <p className="mt-1 text-sm leading-6 text-slate-600">
            累计完整打卡达标后寄出的实物奖励；达标资格由系统累计，收件信息由群内 Agent 记录，运营寄出后在这里确认。
          </p>
        </div>
        <CheckinRewardSwitch />
      </div>

      <RulesDisclosure title="档位配置">
        <CheckinRewardTierConfig />
      </RulesDisclosure>

      <section className="grid gap-3 sm:grid-cols-3" aria-label="打卡奖励概览">
        {[
          { label: "待发货", value: pendingShipments, icon: Truck, tone: "text-amber-700 bg-amber-50" },
          { label: "待补地址", value: missingAddress, icon: MapPin, tone: "text-rose-700 bg-rose-50" },
          { label: "待用户选择", value: waitingChoice, icon: PackageOpen, tone: "text-sky-700 bg-sky-50" },
        ].map(({ label, value, icon: Icon, tone }) => (
          <div key={label} className="panel flex items-center gap-4 p-4 sm:p-5">
            <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${tone}`}>
              <Icon className="h-5 w-5" />
            </span>
            <div>
              <p className="text-2xl font-black tabular-nums text-slate-950">{value}</p>
              <p className="text-xs font-semibold text-slate-500">{label}</p>
            </div>
          </div>
        ))}
      </section>

      <RulesDisclosure title="已达标客户明细">
        <div className="grid gap-6">
          {batchesWithCases.map((group, index) => {
            const batch = group.batch;
            const batchClosed = batch ? batch.kind !== "current" : false;
            return (
              <section key={batch?.key ?? `ungrouped-${index}`} className="grid gap-3">
                {batch ? (
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <h3 className="text-sm font-bold text-slate-900">{batchLabel(batch)}</h3>
                    <span className="status-pill pill-neutral tabular-nums">{group.cases.length} 位</span>
                  </div>
                ) : null}
                {group.cases.length ? (
                  <div className="grid gap-4">
                    {group.cases.map((row) => (
                      <RewardCaseCard key={row.service_case_id} row={row} batchClosed={batchClosed} />
                    ))}
                  </div>
                ) : batch ? (
                  <p className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 px-4 py-4 text-sm font-medium text-slate-600">
                    {batchPlaceholder(batch, rewardsEnabled)}
                  </p>
                ) : null}
              </section>
            );
          })}
          {cases.length === 0 ? (
            <section className="panel empty-state py-14">
              目前还没有达标奖励记录。
            </section>
          ) : null}
        </div>
      </RulesDisclosure>

      <div className="border-t border-slate-200 pt-7">
        <h2 className="text-xl font-extrabold text-slate-950">结营券</h2>
        <p className="mt-1 text-sm leading-6 text-slate-600">
          客户走完 28 天后助教在群里发出的领取入口；商城未打通，系统只记录「入口已发出」，同一位客户的同一张券只会发一次。
        </p>
      </div>

      <RulesDisclosure title="券配置（面额、链接与启停）">
        <Day28BenefitConfig />
      </RulesDisclosure>

      <Day28GrantLedger rows={day28Grants} />
    </div>
  );
}
