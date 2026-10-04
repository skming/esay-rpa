-- Easy RPA PostgreSQL 新库初始化脚本
-- PostgreSQL 15+
--
-- 项目未发布，不提供旧 schema 迁移；本文件需与 SQLAlchemy 持久化模型保持一致。

CREATE TABLE IF NOT EXISTS rpa_flows (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  version VARCHAR(32) NOT NULL DEFAULT 'v1.0.0',
  description TEXT,
  definition JSONB NOT NULL,
  input_variables JSONB NOT NULL DEFAULT '[]'::jsonb,
  acceptance_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
  revision INTEGER NOT NULL DEFAULT 1,
  status VARCHAR(24) NOT NULL DEFAULT 'draft',
  folder_path VARCHAR(500) NOT NULL DEFAULT '默认目录',
  default_browser_executor VARCHAR(24) NOT NULL DEFAULT 'playwright',
  last_run_status VARCHAR(24),
  last_run_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_flow_versions (
  flow_id VARCHAR(36) NOT NULL REFERENCES rpa_flows(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL,
  version VARCHAR(32) NOT NULL,
  description TEXT,
  definition JSONB NOT NULL,
  input_variables JSONB NOT NULL DEFAULT '[]'::jsonb,
  acceptance_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
  saved_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (flow_id, revision)
);

CREATE TABLE IF NOT EXISTS rpa_tasks (
  id VARCHAR(36) PRIMARY KEY,
  flow_id VARCHAR(36),
  schedule_id VARCHAR(36),
  flow_name VARCHAR(120) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'run',
  status VARCHAR(24) NOT NULL DEFAULT 'queued',
  flow_revision INTEGER,
  definition_digest VARCHAR(64),
  acceptance_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
  run_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  progress_payload JSONB NOT NULL,
  result_payload JSONB,
  execution_evidence_payload JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_message TEXT,
  confirmation_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_task_logs (
  id VARCHAR(36) PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL REFERENCES rpa_tasks(id) ON DELETE CASCADE,
  level VARCHAR(16) NOT NULL,
  message TEXT NOT NULL,
  detail TEXT,
  node_id VARCHAR(120),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_task_variables (
  id VARCHAR(180) PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL REFERENCES rpa_tasks(id) ON DELETE CASCADE,
  name VARCHAR(120) NOT NULL,
  category VARCHAR(24) NOT NULL DEFAULT 'flow',
  sensitive BOOLEAN NOT NULL DEFAULT false,
  scope VARCHAR(16) NOT NULL,
  type VARCHAR(24) NOT NULL,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_artifacts (
  id VARCHAR(36) PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL REFERENCES rpa_tasks(id) ON DELETE CASCADE,
  artifact_type VARCHAR(32) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  storage_url TEXT NOT NULL,
  content_type VARCHAR(120) NOT NULL,
  size_bytes BIGINT NOT NULL DEFAULT 0,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_schedules (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  cron_expression VARCHAR(120) NOT NULL,
  timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
  enabled BOOLEAN NOT NULL DEFAULT true,
  task_payload JSONB NOT NULL,
  last_run_at TIMESTAMPTZ,
  next_run_at TIMESTAMPTZ,
  last_task_id VARCHAR(36),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rpa_model_catalog (
  id VARCHAR(120) PRIMARY KEY,
  sort_order INTEGER NOT NULL DEFAULT 0,
  data JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_rpa_flows_updated_at
  ON rpa_flows(updated_at);
CREATE INDEX IF NOT EXISTS ix_rpa_flow_versions_flow_saved
  ON rpa_flow_versions(flow_id, saved_at);
CREATE INDEX IF NOT EXISTS ix_rpa_tasks_updated_at
  ON rpa_tasks(updated_at);
CREATE INDEX IF NOT EXISTS ix_rpa_tasks_flow_updated
  ON rpa_tasks(flow_id, updated_at);
CREATE INDEX IF NOT EXISTS ix_rpa_tasks_schedule_created
  ON rpa_tasks(schedule_id, created_at);
CREATE INDEX IF NOT EXISTS ix_rpa_tasks_rate
  ON rpa_tasks(status, updated_at, flow_id);
CREATE INDEX IF NOT EXISTS ix_rpa_task_logs_task_created
  ON rpa_task_logs(task_id, created_at);
CREATE INDEX IF NOT EXISTS ix_rpa_task_variables_task_name
  ON rpa_task_variables(task_id, name);
CREATE INDEX IF NOT EXISTS ix_rpa_artifacts_task_id
  ON rpa_artifacts(task_id);
CREATE INDEX IF NOT EXISTS ix_rpa_schedules_enabled_next
  ON rpa_schedules(enabled, next_run_at);

INSERT INTO rpa_flows (
  id,
  name,
  version,
  description,
  definition,
  input_variables,
  acceptance_contract,
  revision,
  status,
  folder_path,
  default_browser_executor
)
VALUES (
  '00000000-0000-0000-0000-000000000101',
  '订单自动处理',
  'v3.0.2',
  '默认演示流程：使用 Scrapling 采集目标页面并输出运行日志。',
  '{
    "nodes": [
      {"id": "start", "type": "start"},
      {"id": "n1", "type": "browser.fetch", "targetUrl": "https://quotes.toscrape.com/", "selector": ".quote .text::text", "outputVariable": "quotes"},
      {"id": "end", "type": "end"}
    ],
    "edges": [
      {"source": "start", "target": "n1"},
      {"source": "n1", "target": "end"}
    ]
  }'::jsonb,
  '[]'::jsonb,
  '{
    "requirements": [
      {"id": "quotes-required", "description": "采集页面中的名言文本", "sourceKind": "product_default"}
    ],
    "deliverables": [
      {"id": "quotes", "variable": "quotes", "kind": "scalar", "requirementIds": ["quotes-required"]}
    ]
  }'::jsonb,
  1,
  'active',
  '默认目录',
  'playwright'
)
ON CONFLICT (id) DO UPDATE
SET
  name = EXCLUDED.name,
  version = EXCLUDED.version,
  description = EXCLUDED.description,
  definition = EXCLUDED.definition,
  input_variables = EXCLUDED.input_variables,
  acceptance_contract = EXCLUDED.acceptance_contract,
  revision = EXCLUDED.revision,
  status = EXCLUDED.status,
  folder_path = EXCLUDED.folder_path,
  default_browser_executor = EXCLUDED.default_browser_executor,
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
    "flowRevision": 1,
    "acceptanceContract": {
      "requirements": [
        {"id": "quotes-required", "description": "采集页面中的名言文本", "sourceKind": "product_default"}
      ],
      "deliverables": [
        {"id": "quotes", "variable": "quotes", "kind": "scalar", "requirementIds": ["quotes-required"]}
      ]
    },
    "variables": {},
    "sensitiveVariables": [],
    "timeoutMs": 30000,
    "scope": "full",
    "failureStrategy": "stop",
    "screenshot": true,
    "concurrency": 1,
    "browserExecutor": "playwright"
  }'::jsonb
)
ON CONFLICT (id) DO NOTHING;
