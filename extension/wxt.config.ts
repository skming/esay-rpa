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
    // Chrome 用公开密钥给解压加载的扩展分配稳定 ID，桌面端据此自动识别构建产物。
    key: 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApY5upUGCM0geaIcPjXPhnWpm1EB/MdV6Zibodc/a0eQ3JTOqSyYP77eN89vU4OR+8yOfpNRGrO2z0+mndRfhgYFszZ+YDV0wa/6sNL8j5PnibgqWizrWgjaMk3AxnUFfz77OrBYewZ2q8ybZYeu9atEc3dX5quheLw3/Vszv5t3Bf7UoER2o5PEVyfvIm/m5TqbjHyPhmrPmZqSvotpW07nmbe6Z44TR/1YabO5CkxUVkpotkhx1i8IK1fkifyypouz4lIw103Djg35+no7ClS3j3LPeFxrW0BqjPZVeWjqtcNtkFxUD4dx5YNnPuFBaHYsb3r7og0ZNBev7dba5swIDAQAB',
    permissions: ['activeTab', 'scripting', 'debugger', 'alarms', 'tabs', 'tabGroups'],
    host_permissions: ['<all_urls>'],
  },
});
