import Link from "next/link";
import { api, User, ServiceCase } from "@/lib/api";
import { CaseSearchBox } from "./case-search-box";
import { UserFilterTabs, type UserFilterView } from "./user-filter-tabs";
import { SERVICE_CASE_PAGE_SIZE } from "./list-constants";
import { DeletedUserCard, ServiceCaseList } from "./service-case-list";

export default async function UsersPage({
  searchParams,
}: {
  searchParams: Promise<{ deleted?: string; active?: string; filter?: string; q?: string; group?: string }>;
}) {
  const { deleted, active, filter, q, group } = await searchParams;
  const showDeleted = deleted === "1";
  // 活跃用户 = 近 24 小时内有「用户发言」(inbound) 的服务单；仅在未看已删除时生效。
  const showActive = !showDeleted && active === "1";
  // 看板深链筛选(风险/未跟进等)只在「正常用户」下生效；「活跃用户」用固定的 active_24h 筛选。
  const dashboardFilter = showDeleted || showActive ? undefined : filter;
  const serviceCaseFilter = showActive ? "active_24h" : dashboardFilter;
  const searchQuery = showDeleted ? undefined : q?.trim();
  // 群类型筛选(新群/老群)与范围/看板筛选正交，仅服务单列表视图生效。
  const activeGroup = !showDeleted && (group === "new_group" || group === "legacy") ? group : undefined;
  // 用户列表仅「已删除」页需要；正常/活跃页只展示服务单列表，避免多打一次无用请求。
  const users = showDeleted ? await api<User[]>(`/users?deleted=true`).catch(() => []) : [];
  const caseParams = new URLSearchParams();
  if (serviceCaseFilter) caseParams.set("filter", serviceCaseFilter);
  if (searchQuery) caseParams.set("q", searchQuery);
  if (activeGroup) caseParams.set("group_type", activeGroup);
  const countQuery = caseParams.toString();
  // 首屏只取第一页：近三百条服务单全量渲染时首屏 1.96MB / 4.8 秒，其余靠"加载更多"。
  caseParams.set("limit", String(SERVICE_CASE_PAGE_SIZE));
  const [serviceCases, serviceCaseTotal] = showDeleted
    ? [[] as ServiceCase[], 0]
    : await Promise.all([
        api<ServiceCase[]>(`/service-cases?${caseParams.toString()}`).catch(() => [] as ServiceCase[]),
        api<{ total: number }>(`/service-cases/count${countQuery ? `?${countQuery}` : ""}`)
          .then((payload) => payload.total)
          .catch(() => 0),
      ]);
  const filterLabel = dashboardFilter ? SERVICE_CASE_FILTER_LABELS[dashboardFilter] : undefined;
  const currentView: UserFilterView = showDeleted ? "deleted" : showActive ? "active" : "normal";

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="page-header-row">
          <div>
            <h1 className="page-heading">
              {showDeleted ? "已删除用户" : showActive ? "活跃用户" : "正常用户"}
            </h1>
            <p className="page-subtitle">
              {showDeleted
                ? "查看已删除用户的历史记录"
                : showActive
                  ? "近 24 小时内有发言的用户"
                  : filterLabel
                    ? `当前筛选：${filterLabel}`
                    : "一个客户空间对应一个服务单"}
            </p>
          </div>
          <span className="status-pill border-slate-200 bg-slate-50 text-slate-700">
            {showDeleted ? users.length : serviceCaseTotal} 条记录
          </span>
        </div>
        <UserFilterTabs currentView={currentView} />
        {/* 给运营的说明：区分「正常用户(全部)」与「活跃用户(近 24h 发言)」两个视图的含义。 */}
        <p className="mt-2 text-xs leading-relaxed text-slate-500">
          「正常用户」显示<span className="font-semibold text-slate-700">全部</span>用户；「活跃用户」只显示<span className="font-semibold text-slate-700">近 24 小时内有发言</span>的用户，便于优先处理今天在聊的客户，页面加载也更快。
        </p>
        {!showDeleted && (
          <GroupTypeTabs
            current={activeGroup}
            keep={{ deleted, active, filter, q }}
          />
        )}
      </div>

      {!showDeleted && <CaseSearchBox />}

      {filterLabel && (
        <div className="flex flex-col gap-2 rounded-lg border border-cyan-200 bg-cyan-50 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
          <span className="text-cyan-900">已按“{filterLabel}”筛选服务单。</span>
          <Link href="/users" className="font-semibold text-cyan-800 hover:underline">
            清除筛选
          </Link>
        </div>
      )}

      <div className="grid gap-6">
        {showDeleted ? (
          users.map((user) => <DeletedUserCard key={user.id} user={user} />)
        ) : (
          <ServiceCaseList
            initialCases={serviceCases}
            total={serviceCaseTotal}
            filter={serviceCaseFilter}
            query={searchQuery}
            groupType={activeGroup}
          />
        )}
        {(showDeleted ? users.length : serviceCases.length) === 0 && (
          <div className="empty-state">
            {showDeleted
              ? "暂无已删除用户"
              : searchQuery
                ? `未找到匹配「${searchQuery}」的服务单`
                : showActive
                  ? "近 24 小时内暂无用户发言"
                  : "暂无用户"}
          </div>
        )}
      </div>
    </div>
  );
}

const SERVICE_CASE_FILTER_LABELS: Record<string, string> = {
  new_today: "今日新增用户",
  risk: "风险用户",
  unfollowed: "未跟进（2-7天）",
  renewal_followup: "续购跟进窗口",
  renewal_intent: "明确续购意向",
  refund_intent: "退款/退出意向",
  lost: "流失用户",
  in_service: "服务中",
  messaged_today: "今日有消息",
};

const GROUP_TABS = [
  { key: "", label: "全部" },
  { key: "new_group", label: "新群" },
  { key: "legacy", label: "老群" },
] as const;

function GroupTypeTabs({
  current,
  keep,
}: {
  current?: string;
  keep: { deleted?: string; active?: string; filter?: string; q?: string };
}) {
  const hrefFor = (group: string) => {
    const params = new URLSearchParams();
    if (keep.deleted === "1") params.set("deleted", "1");
    if (keep.active === "1") params.set("active", "1");
    if (keep.filter) params.set("filter", keep.filter);
    if (keep.q) params.set("q", keep.q);
    if (group) params.set("group", group);
    const qs = params.toString();
    return `/users${qs ? `?${qs}` : ""}`;
  };
  return (
    <nav
      aria-label="群类型筛选"
      className="mt-3 flex items-center gap-1 self-start rounded-lg border border-slate-200 bg-slate-50 p-1 text-sm"
    >
      {GROUP_TABS.map((tab) => {
        const active = (current ?? "") === tab.key;
        return (
          <Link
            key={tab.key || "all"}
            href={hrefFor(tab.key)}
            prefetch={false}
            aria-current={active ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              active
                ? "bg-white text-slate-950 shadow-sm"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
