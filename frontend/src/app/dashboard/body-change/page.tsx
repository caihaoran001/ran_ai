import { DetailHeader } from "../_components/dashboard-detail";
import { BodyChangePanel } from "./body-change-panel";

export default function BodyChangePage() {
  return (
    <div className="page-stack">
      <DetailHeader
        title="体重与腰围变化"
        subtitle="按 3/7/14/28 天分窗口看——前 7 天大家坚持度都还行，第二周开始分化，只看一个总数会把认真执行的人和早就放弃的人混在一起。每个数字都交代样本量、纳入门槛和剔除原因。"
      />
      <BodyChangePanel />
    </div>
  );
}
