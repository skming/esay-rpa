#!/usr/bin/env node

const REQUIRED_BY_PLATFORM = {
  darwin: ['CSC_LINK', 'CSC_KEY_PASSWORD', 'APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID'],
  win32: ['CSC_LINK', 'CSC_KEY_PASSWORD'],
  linux: []
};

function main() {
  const platform = process.argv[2] || process.platform;
  const required = REQUIRED_BY_PLATFORM[platform];
  if (required === undefined) {
    throw new Error(`不支持的平台: ${platform}`);
  }

  const missing = required.filter((name) => typeof process.env[name] !== 'string' || process.env[name].trim() === '');
  const result = {
    platform,
    required,
    missing,
    passed: missing.length === 0
  };
  console.log(JSON.stringify(result, null, 2));
  if (!result.passed) {
    process.exit(1);
  }
}

main();
