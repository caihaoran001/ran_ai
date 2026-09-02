import { api } from "@/lib/api";
import { QuestionnaireConfigEditor } from "./questionnaire-config-editor";
import { StageReportConfig } from "./stage-report-config";
import { RiskReplyConfig } from "./risk-reply-config";
import { StaffFilterConfig } from "./staff-filter-config";
import { QiweAccountConfig } from "./qiwe-account-config";
import { VideoPushConfig } from "./video-push-config";

type Template = {
  template_key: string;
  language: "zh" | "en";
  name: string;
  title?: string | null;
  description?: string | null;
  fields: Record<string, unknown>[];
};

type Config = {
  templates: Template[];
  schedule: Record<string, unknown>;
};

export default async function QuestionnaireConfigPage() {
  const config = await api<Config>("/questionnaire-template/config");
  return (
    <div className="page-stack">
      <div>
        <h1 className="page-heading">运营规则配置</h1>
        <p className="page-subtitle">
          管理群内自动发卷策略、定时发送文案、跳过日期规则以及高风险安全拦截话术。
        </p>
      </div>
      
      <QuestionnaireConfigEditor config={config} />
      
      <div className="border-t border-slate-200 pt-7 grid gap-6">
        <QiweAccountConfig />
        <StageReportConfig />
        <VideoPushConfig />
        <RiskReplyConfig />
        <StaffFilterConfig />
      </div>
    </div>
  );
}
