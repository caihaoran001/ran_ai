import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { sessionUsername } from "@/lib/auth";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

async function proxy(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const upstreamUrl = new URL(`${SERVER_API_BASE_URL}/${path.join("/")}`);
  const requestUrl = new URL(request.url);
  upstreamUrl.search = requestUrl.search;

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");
  if (contentType) headers.set("content-type", contentType);
  if (accept) headers.set("accept", accept);
  headers.set("x-admin-api-key", process.env.BACKEND_ADMIN_API_KEY ?? "change-me-internal-api-key");
  const operator = await sessionUsername(request.cookies.get("session_token")?.value);
  if (operator) headers.set("x-admin-operator", operator);

  const method = request.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();
  const response = await fetch(upstreamUrl, {
    method,
    headers,
    body,
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  for (const key of ["content-type", "content-disposition"]) {
    const value = response.headers.get(key);
    if (value) responseHeaders.set(key, value);
  }

  return new NextResponse(response.body, {
    status: response.status,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
