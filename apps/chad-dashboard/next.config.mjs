/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow the local genui-renderer package to be transpiled
  transpilePackages: ['@chad-fleet/genui-renderer'],
};

export default nextConfig;
