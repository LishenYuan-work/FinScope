import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Render runs the standalone server from the Docker image. Vercel should
  // use its native Next.js output so its deployment finalizer can collect the
  // regular build trace files.
  ...(process.env.VERCEL ? {} : { output: "standalone" as const }),
  poweredByHeader: false,
};

export default nextConfig;
