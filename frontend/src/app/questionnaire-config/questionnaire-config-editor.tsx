"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { API_BASE } from "@/lib/api";

type Template = {
  name: string;
  fields: Record<string, unknown>[];
};

export function QuestionnaireConfigEditor({ template }: { template: Template }) {
  const [value, setValue] = useState(JSON.stringify(template.fields, null, 2));
  const [message, setMessage] = useState("");

  async function save() {
    setMessage("");
    let fields: unknown;
    try {
      fields = JSON.parse(value);
    } catch {
      setMessage("JSON 格式不正确");
      return;
    }
    if (!Array.isArray(fields)) {
      setMessage("根节点必须是数组");
      return;
    }
    const response = await fetch(`${API_BASE}/questionnaire-template`, {
      method: "PUT",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({ name: template.name, fields }),
    });
    setMessage(response.ok ? "已保存" : await response.text());
  }

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm text-zinc-500">当前 {template.fields.length} 个条目。修改 JSON 后保存。</div>
        <Button type="button" onClick={save}>保存问卷配置</Button>
      </div>
      <Textarea className="min-h-[640px] font-mono text-xs" value={value} onChange={(event) => setValue(event.target.value)} />
      {message && <p className="mt-3 text-sm text-zinc-600">{message}</p>}
    </section>
  );
}
