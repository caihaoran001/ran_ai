import type { NextConfig } from "next";

function publicHostname() {
  try {
    return new URL(process.env.FRONTEND_PUBLIC_BASE_URL || "").hostname;
  } catch {
    return "";
  }
}

const lanHostname = publicHostname();

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: lanHostname ? [lanHostname] : [],
  experimental: {
    // 客户端路由缓存复用时长：站内跳回访问过的页面时直接还原上一页，不再重新向服务端要一遍。
    // 取 5 分钟是运营侧的选择（审一批文件常来回切十几次）。注意它对全站生效，不只后退：
    // 审核控制台有实时监听（待审数一变就重取）+ 手动刷新按钮兜底；数据看板、AI 运行这类
    // 纯服务端页面在 5 分钟内回访会看到缓存值，需要最新数就按浏览器刷新（F5 绕过此缓存）。
    staleTimes: { dynamic: 300, static: 300 },
  },
};

export default nextConfig;
