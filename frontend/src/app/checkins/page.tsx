import Link from "next/link";
import { api, ServiceCase, Checkin } from "@/lib/api";

export default async function CheckinsPage() {
  const cases = await api<ServiceCase[]>("/service-cases").catch(() => []);
  const rows = await Promise.all(
    cases.map(async (item) => ({
      serviceCase: item,
      checkins: await api<Checkin[]>(`/service-cases/${item.id}/checkins`).catch(() => []),
    })),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">打卡记录</h1>
        <p className="mt-1 text-sm text-zinc-500">按服务单查看用户每日打卡。</p>
      </div>
      <div className="grid gap-4">
        {rows.map(({ serviceCase, checkins }) => (
          <section key={serviceCase.id} className="rounded-lg border border-zinc-200 bg-white p-4">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h2 className="font-medium">{serviceCase.user?.name ?? serviceCase.case_no}</h2>
                <p className="text-sm text-zinc-500">{serviceCase.group_id}</p>
              </div>
              <Link className="text-sm hover:underline" href={`/cases/${serviceCase.id}`}>服务单详情</Link>
            </div>
            <div className="grid gap-2 text-sm">
              {checkins.slice(0, 7).map((checkin) => (
                <div key={checkin.id} className="rounded-md border border-zinc-200 px-3 py-2">
                  <span className="mr-3 text-zinc-500">{checkin.checkin_date}</span>
                  {checkin.content}
                </div>
              ))}
              {checkins.length === 0 && <p className="text-zinc-500">暂无打卡</p>}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
