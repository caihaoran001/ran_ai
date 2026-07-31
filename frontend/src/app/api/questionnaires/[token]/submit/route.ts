import { NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";
// POST(C 端首次提交)走常驻 ingress，避开 backend 重启期的 502；PATCH(运营重生成)仍走 backend。
// 未配 SERVER_INGRESS_BASE_URL(如本地 dev 无 ingress)时回退 backend。
const SERVER_INGRESS_BASE_URL =
  process.env.SERVER_INGRESS_BASE_URL ?? SERVER_API_BASE_URL;

// POST=首次提交问卷；PATCH=运营编辑已提交问卷并重新生成。两者转发逻辑一致，仅方法/目标进程不同。
async function forward(
  request: Request,
  params: Promise<{ token: string }>,
  method: "POST" | "PATCH",
) {
  const { token } = await params;
  const body = await request.text();
  const template = new URL(request.url).searchParams.get("template");
  const suffix = template ? `?template=${encodeURIComponent(template)}` : "";
  const headers: Record<string, string> = { "content-type": "application/json" };
  // PATCH = 运营编辑已提交问卷并重新生成,属管理操作,转发时带 admin key;POST(用户首次提交)保持公开不带
  if (method === "PATCH") {
    headers["x-admin-api-key"] = process.env.BACKEND_ADMIN_API_KEY ?? "change-me-internal-api-key";
  }
  // POST 走 ingress(只落库+置待生成信号)，PATCH 走 backend(带 admin key、就地重生成)
  const baseUrl = method === "POST" ? SERVER_INGRESS_BASE_URL : SERVER_API_BASE_URL;
  const response = await fetch(`${baseUrl}/questionnaires/${token}/submit${suffix}`, {
    method,
    headers,
    body,
  });

  const text = await response.text();
  return new NextResponse(text, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ token: string }> },
) {
  return forward(request, params, "POST");
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ token: string }> },
) {
  return forward(request, params, "PATCH");
}
