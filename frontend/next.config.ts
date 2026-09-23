import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // prod 스테이지에서 .next/standalone 을 복사하므로 반드시 켜져 있어야 한다.
  output: "standalone",
};

export default nextConfig;
