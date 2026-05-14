"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { API_BASE } from "@/lib/api";

export function NewCaseForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [groupId, setGroupId] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    await fetch(`${API_BASE}/service-cases`, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({ group_id: groupId, user: { name, phone: phone || null } }),
    });
    setName("");
    setGroupId("");
    setPhone("");
    setBusy(false);
    router.refresh();
  }

  return (
    <form onSubmit={submit} className="grid gap-3 rounded-lg border border-zinc-200 bg-white p-4 md:grid-cols-[1fr_1fr_1fr_auto]">
      <Input placeholder="用户姓名，可等问卷提交后自动填充" value={name} onChange={(event) => setName(event.target.value)} />
      <Input placeholder="企业微信群 group_id" value={groupId} onChange={(event) => setGroupId(event.target.value)} required />
      <Input placeholder="手机号，可选" value={phone} onChange={(event) => setPhone(event.target.value)} />
      <Button disabled={busy}>{busy ? "创建中" : "创建服务单"}</Button>
    </form>
  );
}
