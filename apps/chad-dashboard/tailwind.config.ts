import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: [
    './app/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
    // Workspace package — its primitive components ship Tailwind classes that
    // need to be in the dashboard's compiled CSS.
    '../../packages/genui-renderer/src/**/*.{ts,tsx}',
    '../../packages/genui-renderer/dist/**/*.{js,mjs}',
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;
