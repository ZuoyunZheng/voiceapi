/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  webpack: (config) => {
    config.resolve.fallback = {
      "buffer": require.resolve("buffer/"),
    };
    return config;
  },
  output: 'standalone',
  server: {
    host: '0.0.0.0',
  },
  compiler: {
    // Enables the styled-components SWC transform
    styledComponents: true
  }
}

module.exports = nextConfig