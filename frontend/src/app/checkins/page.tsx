import Link from "next/link";
import { api, ServiceCase, Checkin } from "@/lib/api";

export default async function CheckinsPage() {
  const cases = await api<ServiceCase[]>("/service-cases").catch(() => []);
  const recentCheckins = await api<Checkin[]>("/checkins?limit=100").catch(() => []);
  const checkinsByCase = new Map<number, Checkin[]>();
  for (const checkin of recentCheckins) {
    if (!checkin.service_case_id) continue;
    const items = checkinsByCase.get(checkin.service_case_id) ?? [];
    items.push(checkin);
    checkinsByCase.set(checkin.service_case_id, items);
  }
  const rows = cases.map((item) => ({
    serviceCase: item,
    checkins: checkinsByCase.get(item.id) ?? [],
  }));

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">打卡记录</h1>
            <p className="page-subtitle">按服务单查看最近 100 条打卡，便于运营快速判断执行连续性。</p>
          </div>
          <span className="status-pill border-cyan-200 bg-cyan-50 text-cyan-800">
            最近 {recentCheckins.length} 条
          </span>
        </div>
      </div>
      <div className="grid gap-4">
        {rows.map(({ serviceCase, checkins }) => (
          <section key={serviceCase.id} className="panel overflow-hidden">
            <div className="panel-header-clean flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <h2 className="text-break font-semibold text-slate-950">{serviceCase.user?.name ?? serviceCase.case_no}</h2>
                <p className="text-break mt-1 text-sm text-slate-600">{serviceCase.group_id}</p>
              </div>
              <Link className="admin-button-secondary text-cyan-800" href={`/cases/${serviceCase.id}`}>服务单详情</Link>
            </div>
            <div className="grid gap-2 p-4 text-sm sm:p-5">
              {checkins.slice(0, 7).map((checkin) => (
                <div key={checkin.id} className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
                  <div className="mb-2 text-xs font-bold text-slate-600">{checkin.checkin_date}</div>
                  <StructuredCheckin checkin={checkin} />
                </div>
              ))}
              {checkins.length === 0 && <p className="empty-state py-6">暂无打卡</p>}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function StructuredCheckin({ checkin }: { checkin: Checkin }) {
  const parsed = checkin.parsed_json;
  if (!parsed) return <span>{checkin.content}</span>;
  const product = parsed.product_schedule || {};
  const rows = [
    ["早C", product.morning_c],
    ["午B", product.noon_b],
    ["晚A", product.night_a],
    ["饮水", parsed.water],
    ["睡眠", parsed.sleep],
    ["情绪", parsed.mood],
    ["运动", parsed.exercise],
    ["体重", parsed.weight],
  ].filter(([, value]) => value);
  if (rows.length === 0) return <span>{checkin.content}</span>;
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {rows.map(([label, value]) => (
        <span key={label} className="text-break rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
          <span className="font-bold text-slate-600">{label}：</span>{value}
        </span>
      ))}
    </div>
  );
}
