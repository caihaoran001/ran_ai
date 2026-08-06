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
    // 后退/前进复用客户端路由缓存 30 秒：从文件详情返回审核控制台时直接还原上一页，
    // 不再重新向服务端要一遍列表。列表的实时性由待审计数的实时监听兜住（数字一变就重取），
    // 所以这 30 秒不会让运营看到过期的待审队列。
    staleTimes: { dynamic: 30, static: 180 },
  },
};

export default nextConfig;
