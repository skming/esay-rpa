const isCiRelease = process.env.RPA_RELEASE_SIGN === '1';

module.exports = {
  appId: 'cn.easy.rpa',
  productName: 'Easy RPA',
  directories: {
    output: 'release',
    buildResources: 'buildResources'
  },

  // Only renderer + Electron main process go into the asar.
  // Python runtime and venv live in extraResources (outside asar) so native
  // extensions and interpreter symlinks are never broken by asar path rewriting.
  files: ['dist/**/*', 'electron/**/*', 'package.json'],
  asar: true,
  compression: 'maximum',
  npmRebuild: false,

  extraResources: [
    {
      from: 'backend/app',
      to: 'backend/app',
      filter: ['**/*', '!**/__pycache__/**', '!**/*.pyc', '!**/*.pyo']
    },
    {
      from: 'backend/config',
      to: 'backend/config',
      filter: ['**/*', '!**/__pycache__/**']
    },
    {
      from: 'backend/.bundle-python',
      to: 'backend/python',
      filter: [
        '**/*',
        '!**/__pycache__/**',
        '!**/*.pyc',
        '!**/*.pyo',
        '!**/test/**',
        '!**/tests/**',
        '!**/.DS_Store',
        // strip headers and static libs that are build-time only
        '!**/*.h',
        '!**/*.a',
        '!**/*.la'
      ]
    },
    {
      from: 'backend/.venv',
      to: 'backend/.venv',
      filter: [
        '**/*',
        // interpreter & scripts — production uses backend/python, not venv bin
        '!bin/**',
        '!Scripts/**',
        // caches & build artifacts
        '!**/__pycache__/**',
        '!**/*.pyc',
        '!**/*.pyo',
        '!**/.pytest_cache/**',
        '!**/.mypy_cache/**',
        '!**/.ruff_cache/**',
        '!**/.DS_Store',
        // test frameworks — dev only
        '!**/site-packages/pytest/**',
        '!**/site-packages/_pytest/**',
        '!**/site-packages/pluggy/**',
        '!**/site-packages/iniconfig/**',
        '!**/site-packages/pytest_asyncio/**',
        // test directories inside packages
        '!**/tests/**',
        '!**/test/**',
        '!**/testing/**',
        '!**/conftest.py',
        // docs / examples — not needed at runtime
        '!**/doc/**',
        '!**/docs/**',
        '!**/examples/**',
        '!**/benchmarks/**',
        // type stubs — only used by type checkers, not at runtime
        '!**/*.pyi',
        // dist-info: keep METADATA (shows version) but drop RECORD/INSTALLER
        '!**/*.dist-info/RECORD',
        '!**/*.dist-info/INSTALLER',
        '!**/*.dist-info/direct_url.json',
        // build artefacts left in editable installs
        '!**/*.dist-info/editable_pth'
      ]
    },
    {
      from: 'backend/pyproject.toml',
      to: 'backend/pyproject.toml'
    },
    {
      from: 'backend/uv.lock',
      to: 'backend/uv.lock'
    },
    // Chrome MV3 extension for the "browser extension executor" — unsigned/unpacked, since
    // Chrome Web Store review isn't part of this project's release flow. Loaded by the user
    // via "load unpacked" (see ExtensionConfigPanel.tsx's install-assist flow); packaged path
    // matches resolveExtensionUnpackedDir()'s first candidate in electron/ipcHandlers.cjs.
    {
      from: 'extension/.output/chrome-mv3',
      to: 'extension'
    }
  ],

  // Auto-update publish target.
  // Set RPA_UPDATE_SERVER_URL=https://your-server.com/releases to enable.
  // Omit to disable update checks (default for local/unsigned builds).
  publish: process.env.RPA_UPDATE_SERVER_URL
    ? [{ provider: 'generic', url: process.env.RPA_UPDATE_SERVER_URL }]
    : null,

  mac: {
    category: 'public.app-category.developer-tools',
    hardenedRuntime: true,
    gatekeeperAssess: false,
    entitlements: 'buildResources/entitlements.mac.plist',
    entitlementsInherit: 'buildResources/entitlements.mac.plist',
    identity: isCiRelease ? undefined : null,
    icon: 'buildResources/icon.icns',
    // Ship both universal targets so the same CI job covers Intel and Apple Silicon
    target: [
      { target: 'dmg', arch: ['arm64', 'x64'] },
      { target: 'zip', arch: ['arm64', 'x64'] }
    ]
  },
  dmg: {
    sign: isCiRelease
  },
  afterPack: isCiRelease ? undefined : 'tools/adhoc-sign-mac.cjs',
  afterSign: isCiRelease ? 'tools/notarize-mac.cjs' : undefined,

  win: {
    icon: 'buildResources/icon.ico',
    target: [
      {
        target: 'nsis',
        arch: ['x64']
      }
    ]
  },
  nsis: {
    oneClick: true,
    perMachine: false,
    allowToChangeInstallationDirectory: false,
  },

  linux: {
    category: 'Development',
    icon: 'buildResources/icon.png',
    target: [
      { target: 'AppImage', arch: ['x64'] }
    ]
  }
};
