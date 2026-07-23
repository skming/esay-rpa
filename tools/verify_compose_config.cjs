#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const REQUIRED_SNIPPETS = [
  'services:',
  'postgres:',
  'redis:',
  'minio:',
  'backend:',
  './database/init.sql:/docker-entrypoint-initdb.d/001-init.sql:ro',
  'DATABASE_URL: postgresql+asyncpg://rpa:rpa@postgres:5432/rpa',
  'RPA_FLOW_STORE_BACKEND: sqlalchemy',
  'RPA_TASK_STORE_BACKEND: sqlalchemy',
  'RPA_SCHEDULE_STORE_BACKEND: sqlalchemy',
  'RPA_TASK_QUEUE_BACKEND: redis',
  'RPA_ARTIFACT_STORE_BACKEND: minio',
  'condition: service_healthy',
  '"8765:8765"',
  'volumes:'
];

function main() {
  const composePath = path.resolve(process.argv[2] || 'docker-compose.yml');
  const content = fs.readFileSync(composePath, 'utf8');
  const missing = REQUIRED_SNIPPETS.filter((snippet) => !content.includes(snippet));
  const healthcheckCount = (content.match(/healthcheck:/g) || []).length;
  const result = {
    composePath,
    requiredChecks: REQUIRED_SNIPPETS.length,
    missing,
    healthcheckCount,
    passed: missing.length === 0 && healthcheckCount >= 3
  };

  console.log(JSON.stringify(result, null, 2));
  if (!result.passed) {
    process.exit(1);
  }
}

main();
