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
};

export default nextConfig;
