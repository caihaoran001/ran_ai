import { NextRequest, NextResponse } from "next/server";

const SERVER_API_BASE_URL = process.env.SERVER_API_BASE_URL ?? "http://localhost:8000/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// 短链入口：用户访问 /s/{code} → 解析出站内目标路径 → 重定向。
// 返回相对路径 Location，让浏览器基于用户当前访问的域名解析；
// 不要用 new URL(target, request.url)——容器内 request.url 的 host 是内部地址(0.0.0.0:3000)，会跳错。
function redirectTo(path: string): NextResponse {
  // 只允许站内绝对路径，防开放重定向(//evil.com 这类协议相对外链)
  const safe = path.startsWith("/") && !path.startsWith("//") ? path : "/";
  return new NextResponse(null, { status: 307, headers: { Location: safe } });
}

// 解析失败兜底：返回公开的提示页(本路由在 /s/ 白名单内，不会被 middleware 拦去登录)。
// 关键：绝不 redirectTo("/")——首页要登录，会把点链接的匿名用户甩到登录页。
// 页面带自动重试(几秒后重新打开当前短链)，扛过后端部署/抖动窗口。
function fallbackPage(): NextResponse {
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>链接打开中…</title>
<style>
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:#f7f8fa;color:#333;display:flex;min-height:100vh;align-items:center;justify-content:center;}
  .card{max-width:340px;padding:32px 24px;text-align:center;}
  .t{font-size:17px;font-weight:600;margin-bottom:10px;}
  .d{font-size:14px;line-height:1.6;color:#666;margin-bottom:20px;}
  .btn{display:inline-block;padding:10px 28px;background:#10a37f;color:#fff;border-radius:22px;font-size:15px;text-decoration:none;border:none;}
</style>
</head>
<body>
  <div class="card">
    <div class="t">链接正在打开…</div>
    <div class="d">网络繁忙，正在为你重试。<br/>若没有自动跳转，请点击下方按钮重新打开。</div>
    <a class="btn" href="">重新打开</a>
  </div>
  <script>
    // 3 秒后自动重新打开当前短链(此时后端通常已恢复)
    setTimeout(function(){ location.reload(); }, 3000);
  </script>
</body>
</html>`;
  return new NextResponse(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

// 解析短码：对短暂失败(后端部署/连接抖动)重试，避免偶发把用户弹去登录。
async function resolveTarget(code: string): Promise<string | null> {
  const url = `${SERVER_API_BASE_URL}/short-links/${encodeURIComponent(code)}`;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        return typeof data.target_path === "string" ? data.target_path : null;
      }
      // 4xx(短码不存在/过期)是确定性失败，重试无意义，直接放弃
      if (res.status >= 400 && res.status < 500) {
        return null;
      }
    } catch {
      // 网络/连接异常：落到下面的退避重试
    }
    if (attempt < 2) {
      await new Promise((r) => setTimeout(r, 300 * (attempt + 1)));
    }
  }
  return null;
}

export async function GET(_request: NextRequest, { params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const target = await resolveTarget(code);
  if (target) {
    return redirectTo(target);
  }
  // 解析失败：给公开提示页(带自动重试)，绝不跳首页/登录页
  return fallbackPage();
}
