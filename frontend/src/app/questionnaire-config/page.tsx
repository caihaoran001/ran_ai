import { api } from "@/lib/api";
import { QuestionnaireConfigEditor } from "./questionnaire-config-editor";

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
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">问卷配置</h1>
        <p className="mt-1 text-sm text-zinc-500">
          配置入营问卷、每日打卡问卷、阶段复盘问卷，以及机器人定时发送文案。
        </p>
      </div>
      <QuestionnaireConfigEditor config={config} />
    </div>
  );
}
