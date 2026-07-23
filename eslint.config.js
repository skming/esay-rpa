import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: ['dist', 'dist-electron', 'release', 'backend', 'extension', 'coverage', '*.cjs'],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // 全仓已改为渲染期按前值调整 state / 派生 state / key 重置，不再有 effect 内同步 setState。
      // 剩余 3 处 disable 均已就地注明是 await 之后的 setState（规则不区分 await 边界）
      'react-hooks/set-state-in-effect': 'error',
      // 组件必须来自常量表或静态引用，不能由函数返回——那样 React 无法确认组件身份稳定
      'react-hooks/static-components': 'error',
      // 项目已开 tsconfig strict 且全仓零 any，保持该基线
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
);
