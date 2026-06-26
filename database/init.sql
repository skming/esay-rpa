-- Easy RPA 自动化平台 PostgreSQL 初始化脚本
-- PostgreSQL 15+
--
-- 当前应用未发布，不保留旧 schema 兼容逻辑；此脚本面向新库初始化。
-- SQLAlchemy 模型为了兼容 SQLite 使用 VARCHAR(36) 保存 ID，这里保持一致，
-- 避免 PostgreSQL UUID 类型和应用层字符串绑定出现驱动差异。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS rpa_flows (
  id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
  name VARCHAR(120) NOT NULL,
  version VARCHAR(32) NOT NULL DEFAULT 'v1.0.0',
  description TEXT,
  definition JSONB NOT NULL DEFAULT '{}'::jsonb,
  input_variables JSONB NOT NULL DEFAULT '[]'::jsonb,
  status VARCHAR(24) NOT NULL DEFAULT 'draft',
  folder_path VARCHAR(500) NOT NULL DEFAULT '默认目录',
  last_run_status VARCHAR(24),
  last_run_at TIMESTAMPTZ,
  snapshots JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rpa_flows_status_check CHECK (status IN ('draft', 'active', 'paused', 'disabled', 'archived')),
  CONSTRAINT rpa_flows_last_run_status_check CHECK (
    last_run_status IS NULL OR last_run_status IN ('queued', 'running', 'success', 'stopped', 'error')
  )
);

CREATE TABLE IF NOT EXISTS rpa_tasks (
  id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
  flow_id VARCHAR(36) REFERENCES rpa_flows(id) ON DELETE SET NULL,
  schedule_id VARCHAR(36),
  flow_name VARCHAR(120) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'run',
  status VARCHAR(24) NOT NULL DEFAULT 'queued',
  target_url TEXT NOT NULL DEFAULT '',
  selector TEXT NOT NULL DEFAULT '',
  fetcher VARCHAR(24) NOT NULL DEFAULT 'static',
  extract_mode VARCHAR(24) NOT NULL DEFAULT 'text',
  timeout_ms INTEGER NOT NULL DEFAULT 30000,
  request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  progress_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  result_payload JSONB,
  artifacts_payload JSONB NOT NULL DEFAULT '[]'::jsonb,
  variables_payload JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_message TEXT,
  input_prompt TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rpa_tasks_mode_check CHECK (mode IN ('run', 'debug')),
  CONSTRAINT rpa_tasks_status_check CHECK (status IN ('queued', 'running', 'success', 'stopped', 'error')),
  CONSTRAINT rpa_tasks_fetcher_check CHECK (fetcher IN ('static', 'dynamic', 'stealthy')),
  CONSTRAINT rpa_tasks_extract_mode_check CHECK (extract_mode IN ('text', 'html', 'attribute', 'count', 'table', 'similar', 'by_text')),
  CONSTRAINT rpa_tasks_timeout_check CHECK (timeout_ms BETWEEN 1000 AND 300000)
);

CREATE TABLE IF NOT EXISTS rpa_task_logs (
  id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
  task_id VARCHAR(36) NOT NULL REFERENCES rpa_tasks(id) ON DELETE CASCADE,
  level VARCHAR(16) NOT NULL,
  message TEXT NOT NULL,
  detail TEXT,
  node_id VARCHAR(120),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rpa_task_logs_level_check CHECK (level IN ('info', 'success', 'running', 'warn', 'error', 'input'))
);

CREATE TABLE IF NOT EXISTS rpa_task_variables (
  id VARCHAR(180) PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL REFERENCES rpa_tasks(id) ON DELETE CASCADE,
  flow_id VARCHAR(36) REFERENCES rpa_flows(id) ON DELETE SET NULL,
  name VARCHAR(120) NOT NULL,
  scope VARCHAR(16) NOT NULL,
  type VARCHAR(24) NOT NULL,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rpa_task_variables_scope_check CHECK (scope IN ('全局', '循环', '局部')),
  CONSTRAINT rpa_task_variables_type_check CHECK (type IN ('String', 'Integer', 'Boolean', 'List', 'Dict'))
);

CREATE TABLE IF NOT EXISTS rpa_schedules (
  id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
  name VARCHAR(120) NOT NULL,
  cron_expression VARCHAR(120) NOT NULL,
  timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
  enabled BOOLEAN NOT NULL DEFAULT true,
  task_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_run_at TIMESTAMPTZ,
  next_run_at TIMESTAMPTZ,
  last_task_id VARCHAR(36) REFERENCES rpa_tasks(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_artifacts (
  id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
  task_id VARCHAR(36) NOT NULL REFERENCES rpa_tasks(id) ON DELETE CASCADE,
  flow_id VARCHAR(36) REFERENCES rpa_flows(id) ON DELETE SET NULL,
  artifact_type VARCHAR(32) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  storage_url TEXT NOT NULL,
  content_type VARCHAR(120) NOT NULL DEFAULT 'application/octet-stream',
  size_bytes BIGINT NOT NULL DEFAULT 0,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rpa_artifacts_type_check CHECK (artifact_type IN ('script', 'screenshot', 'report', 'dataset', 'log')),
  CONSTRAINT rpa_artifacts_size_check CHECK (size_bytes >= 0)
);

CREATE INDEX IF NOT EXISTS idx_rpa_flows_status ON rpa_flows(status);
CREATE INDEX IF NOT EXISTS idx_rpa_flows_folder_path_updated_at ON rpa_flows(folder_path, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_rpa_tasks_flow_id_created_at ON rpa_tasks(flow_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rpa_tasks_schedule_id_created_at ON rpa_tasks(schedule_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rpa_tasks_status_created_at ON rpa_tasks(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rpa_task_logs_task_id_created_at ON rpa_task_logs(task_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_rpa_task_variables_task_id_name ON rpa_task_variables(task_id, name);
CREATE INDEX IF NOT EXISTS idx_rpa_task_variables_flow_id_name ON rpa_task_variables(flow_id, name);
CREATE INDEX IF NOT EXISTS idx_rpa_schedules_enabled_next_run_at ON rpa_schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_rpa_artifacts_task_id_created_at ON rpa_artifacts(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rpa_artifacts_flow_id_created_at ON rpa_artifacts(flow_id, created_at DESC);

INSERT INTO rpa_flows (
  id,
  name,
  version,
  description,
  definition,
  input_variables,
  status,
  folder_path
)
VALUES (
  '00000000-0000-0000-0000-000000000101',
  '订单自动处理',
  'v3.0.2',
  '默认演示流程：使用 Scrapling 采集目标页面并输出运行日志。',
  '{
    "nodes": [
      {"id": "start", "type": "start"},
      {"id": "n1", "type": "browser.fetch", "targetUrl": "https://quotes.toscrape.com/", "selector": ".quote .text::text"},
      {"id": "end", "type": "end"}
    ],
    "edges": [
      {"source": "start", "target": "n1"},
      {"source": "n1", "target": "end"}
    ]
  }'::jsonb,
  '[]'::jsonb,
  'active',
  '默认目录'
)
ON CONFLICT (id) DO UPDATE
SET
  name = EXCLUDED.name,
  version = EXCLUDED.version,
  description = EXCLUDED.description,
  definition = EXCLUDED.definition,
  input_variables = EXCLUDED.input_variables,
  status = EXCLUDED.status,
  folder_path = EXCLUDED.folder_path,
  updated_at = now();

INSERT INTO rpa_schedules (id, name, cron_expression, timezone, enabled, task_payload)
VALUES (
  '00000000-0000-0000-0000-000000000201',
  '每日订单采集演示',
  '0 9 * * *',
  'Asia/Shanghai',
  false,
  '{
    "flowName": "订单自动处理",
    "flowId": "00000000-0000-0000-0000-000000000101",
    "targetUrl": "https://quotes.toscrape.com/",
    "selector": ".quote .text::text",
    "fetcher": "static",
    "extractMode": "text",
    "timeoutMs": 30000
  }'::jsonb
)
ON CONFLICT (id) DO NOTHING;
