/** @type {import('next').NextConfig} */

// Backend origin the proxy forwards to. In docker this is the service name.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // "standalone" produces a minimal, self-contained server bundle for a small
  // production Docker image (Stage 1 deployment target).
  output: "standalone",
  // Proxy /api/* to the backend so the browser sees a single origin. This keeps
  // the auth cookies first-party with SameSite=Lax in both dev and prod, and
  // lets the OAuth redirect_uri live under the frontend host.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
