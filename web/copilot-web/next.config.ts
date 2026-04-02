import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@copilotkit/react-core", "@copilotkit/react-ui", "@copilotkit/runtime"],
};

export default nextConfig;
