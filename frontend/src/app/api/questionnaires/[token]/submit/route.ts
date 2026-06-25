import { NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

// POST=首次提交问卷；PATCH=运营编辑已提交问卷并重新生成。两者转发逻辑一致，仅方法不同。
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
  const response = await fetch(`${SERVER_API_BASE_URL}/questionnaires/${token}/submit${suffix}`, {
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
