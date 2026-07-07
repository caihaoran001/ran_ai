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
      <div>
        <h1 className="page-heading">打卡记录</h1>
        <p className="page-subtitle">按服务单查看最近 100 条打卡。</p>
      </div>
      <div className="grid gap-4">
        {rows.map(({ serviceCase, checkins }) => (
          <section key={serviceCase.id} className="panel p-4 sm:p-5">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <h2 className="text-break font-semibold text-slate-950">{serviceCase.user?.name ?? serviceCase.case_no}</h2>
                <p className="text-break mt-1 text-sm text-slate-600">{serviceCase.group_id}</p>
              </div>
              <Link className="inline-flex min-h-10 items-center justify-center rounded-lg border border-cyan-200 px-3 text-sm font-semibold text-cyan-800 hover:bg-cyan-50" href={`/cases/${serviceCase.id}`}>服务单详情</Link>
            </div>
            <div className="grid gap-2 text-sm">
              {checkins.slice(0, 7).map((checkin) => (
                <div key={checkin.id} className="rounded-lg border border-slate-200 px-3 py-2.5">
                  <div className="mb-2 text-xs font-semibold text-slate-500">{checkin.checkin_date}</div>
                  <StructuredCheckin checkin={checkin} />
                </div>
              ))}
              {checkins.length === 0 && <p className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-slate-500">暂无打卡</p>}
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
        <span key={label} className="text-break rounded-lg bg-slate-50 px-2.5 py-1.5">
          <span className="text-slate-500">{label}：</span>{value}
        </span>
      ))}
    </div>
  );
}
