import type { NextConfig } from "next";

const config: NextConfig = {
  output: "export",
  poweredByHeader: false,
  images: { unoptimized: true },
};
export default config;
