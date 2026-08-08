import { DetailHeader } from "../_components/dashboard-detail";
import { OutcomeTrendPanel } from "./outcome-trend-panel";

export default function OutcomeTrendsPage() {
  return (
    <div className="page-stack">
      <DetailHeader
        title="客户成效月度趋势"
        subtitle="按服务开始月份追踪承接效率、用户参与、服务结果和商业信号。第7天指标只比较已经走到第7天的人，第28天指标同理，避免新批次因为服务尚未走完而被误判。"
      />
      <OutcomeTrendPanel />
    </div>
  );
}
