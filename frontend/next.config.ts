import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  agentRules: false,
  experimental: { proxyTimeout: 240000, proxyClientMaxBodySize: "100mb" },
  turbopack: { root: process.cwd() },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_URL || "http://127.0.0.1:5000"}/api/:path*`,
      },
    ];
  },
};
export default config;
