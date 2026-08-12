import Link from "next/link";
import { CheckCircle2, Gift, MapPin, PackageOpen, Truck } from "lucide-react";
import { api, type RewardAdminCase, type RewardShipment } from "@/lib/api";
import { ShipmentAction } from "./shipment-action";

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

export default async function RewardsPage() {
  const rows = await api<RewardAdminCase[]>("/checkin-rewards").catch(() => []);
  const pendingShipments = rows.flatMap((row) => row.shipments).filter((row) => row.status === "pending_shipment").length;
  const missingAddress = rows.flatMap((row) => row.shipments).filter((row) => row.status === "missing_address").length;
  const waitingChoice = rows.filter((row) => row.operational_status === "waiting_choice").length;

  return (
    <div className="page-stack">
      <header className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">奖励发货</h1>
            <p className="page-subtitle">达标资格由系统累计，地址由群内 Agent 记录；运营实际寄出后在这里确认。</p>
          </div>
          <span className="status-pill border-cyan-200 bg-cyan-50 text-cyan-800">
            {rows.length} 位已达标用户
          </span>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-3" aria-label="奖励发货概览">
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

      <div className="grid gap-4">
        {rows.map((row) => {
          const status = statusCopy[row.operational_status] ?? statusCopy.qualified;
          return (
            <section key={row.service_case_id} className="panel overflow-hidden">
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
            </section>
          );
        })}
        {rows.length === 0 ? (
          <section className="panel empty-state py-14">
            目前还没有达标奖励记录。
          </section>
        ) : null}
      </div>
    </div>
  );
}
