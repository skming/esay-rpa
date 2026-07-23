import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'wxt';

// See https://wxt.dev/api/config.html
export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  vite: () => ({
    plugins: [tailwindcss()],
  }),
  manifest: {
    name: 'Easy RPA',
    description: '自动化浏览器插件',
    permissions: ['activeTab', 'scripting', 'debugger', 'alarms', 'storage', 'tabs', 'tabGroups'],
    host_permissions: ['<all_urls>'],
  },
});
