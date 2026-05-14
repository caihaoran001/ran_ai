import { api } from "@/lib/api";
import { QuestionnaireConfigEditor } from "./questionnaire-config-editor";

type Template = {
  name: string;
  fields: Record<string, unknown>[];
};

export default async function QuestionnaireConfigPage() {
  const template = await api<Template>("/questionnaire-template");
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">问卷配置</h1>
        <p className="mt-1 text-sm text-zinc-500">这里保存后，新发给用户的问卷链接会使用更新后的题目。</p>
      </div>
      <QuestionnaireConfigEditor template={template} />
    </div>
  );
}
