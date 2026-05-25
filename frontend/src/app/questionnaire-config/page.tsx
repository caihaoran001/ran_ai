import { api } from "@/lib/api";
import { QuestionnaireConfigEditor } from "./questionnaire-config-editor";
import { RiskReplyConfig } from "./risk-reply-config";

type Template = {
  template_key: string;
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
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-zinc-900">运营规则配置</h1>
        <p className="mt-1.5 text-sm text-zinc-500">
          管理群内自动发卷策略、定时发送文案、跳过日期规则以及高风险安全拦截话术。
        </p>
      </div>
      
      <QuestionnaireConfigEditor config={config} />
      
      <div className="border-t border-zinc-200/80 pt-8">
        <RiskReplyConfig />
      </div>
    </div>
  );
}
