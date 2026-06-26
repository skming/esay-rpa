from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def to_camel(value: str) -> str:
    """Convert snake_case field names to camelCase for JSON serialisation."""
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class ApiModel(BaseModel):
    """Base model with camelCase aliases and strict extra-field rejection."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


RunMode = Literal["run", "debug"]
RunScope = Literal["full", "from-selection", "selected-only"]
RunFailureStrategy = Literal["stop", "continue", "retry"]
DebugControlCommand = Literal["continue", "step-over", "step-into"]
FetcherType = Literal["static", "dynamic", "stealthy"]
ExtractMode = Literal["text", "html", "attribute", "count", "table", "similar", "by_text"]
TaskStatus = Literal["queued", "running", "success", "stopped", "error"]
TaskLogLevel = Literal["info", "success", "running", "warn", "error", "input"]
ScheduleStatus = Literal["enabled", "disabled"]
ArtifactType = Literal["script", "screenshot", "report", "dataset", "log"]
FlowStatus = Literal["draft", "active", "paused", "disabled", "archived"]
RuntimeVariableType = Literal["String", "Integer", "Boolean", "List", "Dict"]
RuntimeVariableScope = Literal["全局", "循环", "局部"]
RuntimeVariableCategory = Literal["flow", "environment", "credential"]


class HealthResponse(ApiModel):
    status: Literal["ok"]
    service: str


class CodeGenerateRequest(ApiModel):
    flow_name: str = Field(default="未命名流程", min_length=1, max_length=120)
    target_url: HttpUrl
    selector: str = Field(min_length=1, max_length=500)
    fetcher: FetcherType = "static"
    extract_mode: ExtractMode = "text"
    attribute: str | None = Field(default=None, max_length=120)
    text_query: str | None = Field(default=None, max_length=500)
    adaptive: bool = False
    auto_save: bool = False

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("selector 不能为空")
        return normalized

    @field_validator("attribute")
    @classmethod
    def validate_attribute(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if any(char in normalized for char in ['"', "'", " ", ">", "<"]):
            raise ValueError("attribute 只能使用安全属性名")
        return normalized


class GeneratedScript(ApiModel):
    filename: str
    language: Literal["python"] = "python"
    dependencies: list[str]
    content: str


class AnalyzeSiteRequest(ApiModel):
    target_url: HttpUrl
    selector: str | None = Field(default=None, min_length=1, max_length=500)
    fetcher: FetcherType = "static"
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    max_candidates: int = Field(default=8, ge=1, le=20)

    @field_validator("selector")
    @classmethod
    def validate_optional_selector(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized


class SelectorCandidate(ApiModel):
    selector: str
    match_count: int = Field(ge=0)
    sample_text: str
    stability_score: int = Field(ge=0, le=100)
    reasons: list[str]


class SelectorCheck(ApiModel):
    selector: str
    match_count: int = Field(ge=0)
    sample_texts: list[str]
    stable: bool


class SiteAnalysisResult(ApiModel):
    url: str
    title: str | None = None
    fetcher: FetcherType
    has_css_in_js: bool
    risk_level: Literal["low", "medium", "high"]
    warnings: list[str]
    checked_selector: SelectorCheck | None = None
    candidates: list[SelectorCandidate]


class FlowCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="v1.0.0", min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=1000)
    definition: dict[str, object] = Field(default_factory=dict)
    input_variables: list["RuntimeVariableSnapshot"] = Field(default_factory=list)
    status: FlowStatus = "draft"
    folder_path: str = Field(default="默认目录", max_length=500)


class FlowUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    version: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=1000)
    definition: dict[str, object] | None = None
    input_variables: list["RuntimeVariableSnapshot"] | None = None
    status: FlowStatus | None = None
    folder_path: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> "FlowUpdateRequest":
        if all(v is None for v in [self.name, self.version, self.description, self.definition, self.input_variables, self.status, self.folder_path]):
            raise ValueError("至少提供一个需要更新的字段")
        return self


class FlowMoveRequest(ApiModel):
    folder_path: str = Field(min_length=1, max_length=500)


class FlowStatusPatchRequest(ApiModel):
    status: FlowStatus


class FlowVersionSnapshot(ApiModel):
    version: str
    description: str | None = None
    definition: dict[str, object]
    input_variables: list["RuntimeVariableSnapshot"] = Field(default_factory=list)
    saved_at: datetime


class FlowSnapshot(ApiModel):
    flow_id: str
    name: str
    version: str
    description: str | None = None
    definition: dict[str, object]
    input_variables: list["RuntimeVariableSnapshot"] = Field(default_factory=list)
    status: FlowStatus
    folder_path: str = "默认目录"
    last_run_status: str | None = None
    last_run_at: datetime | None = None
    success_rate_30d: int | None = None
    created_at: datetime
    updated_at: datetime
    snapshots: list[FlowVersionSnapshot] = Field(default_factory=list)


class FlowRunRequest(ApiModel):
    mode: RunMode = "run"
    variables: dict[str, object] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    scope: RunScope = "full"
    start_node_id: str | None = Field(default=None, max_length=120)
    failure_strategy: RunFailureStrategy = "stop"
    screenshot: bool = True
    concurrency: int = Field(default=1, ge=1, le=20)

    @field_validator("start_node_id")
    @classmethod
    def validate_start_node_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 100:
            raise ValueError("variables 最多支持 100 个变量")

        normalized: dict[str, object] = {}
        for key, raw_value in value.items():
            name = key.strip()
            if not _is_safe_variable_name(name):
                raise ValueError(f"变量名不合法: {key}")
            normalized[name] = raw_value
        return normalized


class DebugControlRequest(ApiModel):
    command: DebugControlCommand


class UserInputRequest(ApiModel):
    value: str = Field(default="", max_length=4096)


class RunTaskRequest(ApiModel):
    mode: RunMode = "run"
    flow_id: str | None = Field(default=None, max_length=36)
    schedule_id: str | None = Field(default=None, max_length=36)
    flow_definition: dict[str, object] | None = None
    variables: dict[str, object] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    scope: RunScope = "full"
    start_node_id: str | None = Field(default=None, max_length=120)
    failure_strategy: RunFailureStrategy = "stop"
    screenshot: bool = True
    concurrency: int = Field(default=1, ge=1, le=20)
    # Legacy code-gen fields — optional when running a saved flow by flow_id
    flow_name: str = Field(default="未命名流程", min_length=1, max_length=120)
    target_url: HttpUrl | None = None
    selector: str | None = Field(default=None, min_length=1, max_length=500)
    fetcher: FetcherType = "static"
    extract_mode: ExtractMode = "text"
    attribute: str | None = Field(default=None, max_length=120)
    text_query: str | None = Field(default=None, max_length=500)
    adaptive: bool = False
    auto_save: bool = False

    @field_validator("start_node_id")
    @classmethod
    def validate_start_node_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 100:
            raise ValueError("variables 最多支持 100 个变量")

        normalized: dict[str, object] = {}
        for key, raw_value in value.items():
            name = key.strip()
            if not _is_safe_variable_name(name):
                raise ValueError(f"变量名不合法: {key}")
            normalized[name] = raw_value
        return normalized

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @field_validator("attribute")
    @classmethod
    def validate_attribute(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if any(char in normalized for char in ['"', "'", " ", ">", "<"]):
            raise ValueError("attribute 只能使用安全属性名")
        return normalized


class RunConfigSnapshot(ApiModel):
    """Immutable record of the execution parameters used for a task run."""

    scope: RunScope = "full"
    start_node_id: str | None = Field(default=None, max_length=120)
    failure_strategy: RunFailureStrategy = "stop"
    screenshot: bool = True
    concurrency: int = Field(default=1, ge=1, le=20)

    @classmethod
    def from_request(cls, request: RunTaskRequest) -> "RunConfigSnapshot":
        return cls(
            scope=request.scope,
            start_node_id=request.start_node_id,
            failure_strategy=request.failure_strategy,
            screenshot=request.screenshot,
            concurrency=request.concurrency,
        )


class RuntimeProgress(ApiModel):
    current_step: int = Field(ge=0)
    total_steps: int = Field(ge=1)
    percent: int = Field(ge=0, le=100)
    elapsed_ms: int = Field(ge=0)


class RuntimeVariableSnapshot(ApiModel):
    """Single variable value captured at a point in time during a task run."""

    category: RuntimeVariableCategory = "flow"
    name: str = Field(min_length=1, max_length=120)
    # When True the value should be masked in logs and the UI.
    sensitive: bool = False
    type: RuntimeVariableType
    value: str
    scope: RuntimeVariableScope = "全局"


class QueueStats(ApiModel):
    backend: Literal["memory", "redis"]
    concurrency: int = Field(ge=1)
    queued_count: int = Field(ge=0)
    active_count: int = Field(ge=0)
    active_task_ids: list[str]
    started: bool


class TaskLogEntry(ApiModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    level: TaskLogLevel
    message: str
    detail: str | None = None
    node_id: str | None = Field(default=None, max_length=120)


class ScrapeResult(ApiModel):
    url: str
    selector: str
    count: int
    values: list[str]


class ArtifactSnapshot(ApiModel):
    artifact_id: str
    task_id: str
    artifact_type: ArtifactType
    filename: str
    storage_url: str
    content_type: str
    size_bytes: int = Field(ge=0)
    created_at: datetime
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ArtifactContent(ApiModel):
    artifact: ArtifactSnapshot
    content: str


class TaskSnapshot(ApiModel):
    """Complete state of a task, returned by the API and persisted in the task store."""

    task_id: str
    flow_id: str | None = None
    schedule_id: str | None = None
    flow_name: str
    status: TaskStatus
    mode: RunMode
    progress: RuntimeProgress
    created_at: datetime
    updated_at: datetime
    result: ScrapeResult | None = None
    artifacts: list[ArtifactSnapshot] = Field(default_factory=list)
    variables: list[RuntimeVariableSnapshot] = Field(default_factory=list)
    run_config: RunConfigSnapshot = Field(default_factory=RunConfigSnapshot)
    error: str | None = None
    input_prompt: str | None = None


class ScheduleCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    cron_expression: str = Field(min_length=9, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    enabled: bool = True
    task: RunTaskRequest

    @field_validator("cron_expression")
    @classmethod
    def validate_cron_expression(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if len(normalized.split()) not in {5, 6}:
            raise ValueError("cron_expression 必须是 5 或 6 段 Cron 表达式")
        return normalized


class ScheduleUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    cron_expression: str | None = Field(default=None, min_length=9, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    task: RunTaskRequest | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> "ScheduleUpdateRequest":
        if self.name is None and self.cron_expression is None and self.timezone is None and self.enabled is None and self.task is None:
            raise ValueError("至少提供一个需要更新的字段")
        return self

    @field_validator("cron_expression")
    @classmethod
    def validate_cron_expression(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if len(normalized.split()) not in {5, 6}:
            raise ValueError("cron_expression 必须是 5 或 6 段 Cron 表达式")
        return normalized


class ScheduleSnapshot(ApiModel):
    schedule_id: str
    name: str
    cron_expression: str
    timezone: str
    status: ScheduleStatus
    task: RunTaskRequest
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_task_id: str | None = None


def _is_safe_variable_name(value: str) -> bool:
    """Return True when the name is a valid RPA variable identifier (letters, digits, _, ., -)."""
    if not value or len(value) > 120:
        return False
    if not (value[0].isalpha() or value[0] == "_"):
        return False
    return all(char.isalnum() or char in {"_", ".", "-"} for char in value)
