from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class ApiModel(BaseModel):
    """Base model with camelCase aliases and strict extra-field rejection."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


RunMode = Literal["run", "debug"]
RunScope = Literal["full", "from-selection", "selected-only"]
RunFailureStrategy = Literal["stop", "continue", "retry"]
# "extension" 借助用户已打开的真实浏览器窗口执行（见 /extension），无法无人值守调度。
BrowserExecutorKind = Literal["playwright", "extension"]
DebugControlCommand = Literal["continue", "step-over", "step-into"]
FetcherType = Literal["static", "dynamic", "stealthy"]
ExtractMode = Literal["text", "html", "attribute", "count", "table", "similar", "by_text"]
TaskStatus = Literal["queued", "running", "success", "stopped", "error", "paused_for_human"]
TaskLogLevel = Literal["info", "success", "running", "warn", "error", "input"]
ScheduleStatus = Literal["enabled", "disabled"]
ArtifactType = Literal["script", "screenshot", "report", "dataset", "log"]
FlowStatus = Literal["draft", "active", "paused", "disabled", "archived"]
RuntimeVariableType = Literal["String", "Integer", "Boolean", "List", "Dict"]
RuntimeVariableScope = Literal["全局", "循环", "局部"]
RuntimeVariableCategory = Literal["flow", "environment", "credential"]
DeliverableKind = Literal["table", "document", "file", "scalar"]
RequirementSourceKind = Literal["user", "product_default"]
ComparisonOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]


class HealthResponse(ApiModel):
    status: Literal["ok"]
    service: str


class CodeGenerateRequest(ApiModel):
    flow_name: str = Field(default="未命名流程", min_length=1, max_length=120)
    target_url: HttpUrl | None = None
    selector: str | None = Field(default=None, min_length=1, max_length=500)
    fetcher: FetcherType = "static"
    extract_mode: ExtractMode = "text"
    attribute: str | None = Field(default=None, max_length=120)
    text_query: str | None = Field(default=None, max_length=500)
    adaptive: bool = False
    auto_save: bool = False
    flow_definition: dict[str, object] | None = None

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @model_validator(mode="after")
    def validate_generation_target(self) -> "CodeGenerateRequest":
        if self.flow_definition is None:
            raise ValueError("flowDefinition 不能为空")
        return self

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


class DateRangeAssertion(ApiModel):
    field: str = Field(min_length=1, max_length=120)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)


class AllowedValuesAssertion(ApiModel):
    field: str = Field(min_length=1, max_length=120)
    values: list[str] = Field(min_length=1, max_length=100)


class RequirementClause(ApiModel):
    id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    source_kind: RequirementSourceKind = "user"
    source_quote: str | None = Field(default=None, max_length=1000)
    source_turn_id: str | None = Field(default=None, max_length=120)
    confidence: float = Field(default=1.0, ge=0, le=1)
    confirmed: bool = True

    @model_validator(mode="after")
    def validate_source(self) -> "RequirementClause":
        if self.source_kind == "user" and not (self.source_quote or "").strip():
            raise ValueError("用户需求条款必须提供 sourceQuote 原文")
        if not self.confirmed:
            raise ValueError("未确认的推断不能进入验收契约")
        return self


class NumericRangeAssertion(ApiModel):
    field: str = Field(min_length=1, max_length=120)
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "NumericRangeAssertion":
        if self.minimum is None and self.maximum is None:
            raise ValueError("数值范围至少需要 minimum 或 maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum 不能大于 maximum")
        return self


class FieldFormatAssertion(ApiModel):
    field: str = Field(min_length=1, max_length=120)
    format: Literal["integer", "decimal", "email", "url", "date", "datetime", "non_empty"]


class CrossFieldAssertion(ApiModel):
    left_field: str = Field(min_length=1, max_length=120)
    operator: ComparisonOperator
    right_field: str = Field(min_length=1, max_length=120)


class SortAssertion(ApiModel):
    field: str = Field(min_length=1, max_length=120)
    direction: Literal["asc", "desc"]


class AggregateAssertion(ApiModel):
    field: str | None = Field(default=None, max_length=120)
    operation: Literal["count", "sum", "avg", "min", "max"]
    operator: ComparisonOperator = "eq"
    expected: float
    tolerance: float = Field(default=0, ge=0)


class DeliverableContract(ApiModel):
    id: str = Field(min_length=1, max_length=120)
    variable: str = Field(min_length=1, max_length=120)
    kind: DeliverableKind
    required: bool = True
    min_rows: int | None = Field(default=None, ge=0)
    max_rows: int | None = Field(default=None, ge=0)
    required_fields: list[str] = Field(default_factory=list, max_length=100)
    date_ranges: list[DateRangeAssertion] = Field(default_factory=list, max_length=20)
    allowed_values: list[AllowedValuesAssertion] = Field(default_factory=list, max_length=20)
    unique_by: list[str] = Field(default_factory=list, max_length=20)
    min_chars: int | None = Field(default=None, ge=0)
    required_terms: list[str] = Field(default_factory=list, max_length=100)
    forbidden_terms: list[str] = Field(default_factory=list, max_length=100)
    source_variables: list[str] = Field(default_factory=list, max_length=100)
    extensions: list[str] = Field(default_factory=list, max_length=20)
    min_bytes: int | None = Field(default=None, ge=0)
    requirement_ids: list[str] = Field(default_factory=list, max_length=100)
    numeric_ranges: list[NumericRangeAssertion] = Field(default_factory=list, max_length=20)
    field_formats: list[FieldFormatAssertion] = Field(default_factory=list, max_length=20)
    cross_field_assertions: list[CrossFieldAssertion] = Field(default_factory=list, max_length=20)
    sort_assertions: list[SortAssertion] = Field(default_factory=list, max_length=10)
    aggregate_assertions: list[AggregateAssertion] = Field(default_factory=list, max_length=20)
    expected_count_variable: str | None = Field(default=None, max_length=120)
    minimum_coverage_ratio: float = Field(default=1.0, gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "DeliverableContract":
        if self.min_rows is not None and self.max_rows is not None and self.min_rows > self.max_rows:
            raise ValueError("minRows 不能大于 maxRows")
        return self


class FlowAcceptanceContract(ApiModel):
    requirements: list[RequirementClause] = Field(default_factory=list, max_length=100)
    deliverables: list[DeliverableContract] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_requirement_references(self) -> "FlowAcceptanceContract":
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirements.id 不能重复")
        known = set(requirement_ids)
        unknown = sorted({
            requirement_id
            for deliverable in self.deliverables
            for requirement_id in deliverable.requirement_ids
            if requirement_id not in known
        })
        if unknown:
            raise ValueError(f"deliverable 引用了不存在的 requirementIds: {unknown}")
        return self


class VariableEvidence(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    type: RuntimeVariableType
    char_count: int = Field(ge=0)
    item_count: int | None = Field(default=None, ge=0)
    digest: str | None = Field(default=None, max_length=64)
    comparable: bool = True
    producer_node_id: str | None = Field(default=None, max_length=120)


class NodeExecutionEvidence(ApiModel):
    node_id: str = Field(min_length=1, max_length=120)
    node_type: str = Field(min_length=1, max_length=120)
    inputs: list[VariableEvidence] = Field(default_factory=list)
    outputs: list[VariableEvidence] = Field(default_factory=list)
    unchanged_pairs: list[str] = Field(default_factory=list)
    status: Literal["success", "error"] = "success"
    duration_ms: int = Field(default=0, ge=0)
    browser_url: str | None = Field(default=None, max_length=4000)
    selector: str | None = Field(default=None, max_length=1000)
    match_count: int | None = Field(default=None, ge=0)


class FlowCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="v1.0.0", min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=1000)
    definition: dict[str, object] = Field(default_factory=dict)
    input_variables: list["RuntimeVariableSnapshot"] = Field(default_factory=list)
    acceptance_contract: FlowAcceptanceContract = Field(default_factory=FlowAcceptanceContract)
    status: FlowStatus = "draft"
    folder_path: str = Field(default="默认目录", max_length=500)
    default_browser_executor: BrowserExecutorKind = "playwright"


class FlowUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    version: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=1000)
    definition: dict[str, object] | None = None
    input_variables: list["RuntimeVariableSnapshot"] | None = None
    acceptance_contract: FlowAcceptanceContract | None = None
    status: FlowStatus | None = None
    folder_path: str | None = Field(default=None, max_length=500)
    default_browser_executor: BrowserExecutorKind | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> "FlowUpdateRequest":
        if all(
            v is None
            for v in [
                self.name,
                self.version,
                self.description,
                self.definition,
                self.input_variables,
                self.acceptance_contract,
                self.status,
                self.folder_path,
                self.default_browser_executor,
            ]
        ):
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
    acceptance_contract: FlowAcceptanceContract = Field(default_factory=FlowAcceptanceContract)
    revision: int = Field(default=1, ge=1)
    saved_at: datetime


class FlowSnapshot(ApiModel):
    flow_id: str
    name: str
    version: str
    description: str | None = None
    definition: dict[str, object]
    input_variables: list["RuntimeVariableSnapshot"] = Field(default_factory=list)
    acceptance_contract: FlowAcceptanceContract = Field(default_factory=FlowAcceptanceContract)
    revision: int = Field(default=1, ge=1)
    status: FlowStatus
    folder_path: str = "默认目录"
    default_browser_executor: BrowserExecutorKind = "playwright"
    last_run_status: str | None = None
    last_run_at: datetime | None = None
    success_rate_30d: int | None = None  # None 表示近 30 天无已完成任务，样本不足，非 0%
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
    browser_executor: BrowserExecutorKind = "playwright"

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
        if len(value) > 100:  # 上限防止超大请求体拖慢校验/执行，正常流程远用不到这么多变量
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
    flow_revision: int | None = Field(default=None, ge=1)
    definition_digest: str | None = Field(default=None, max_length=64)
    acceptance_contract: FlowAcceptanceContract = Field(default_factory=FlowAcceptanceContract)
    sensitive_variables: list[str] = Field(default_factory=list, max_length=100)
    variables: dict[str, object] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    scope: RunScope = "full"
    start_node_id: str | None = Field(default=None, max_length=120)
    failure_strategy: RunFailureStrategy = "stop"
    screenshot: bool = True
    concurrency: int = Field(default=1, ge=1, le=20)
    browser_executor: BrowserExecutorKind = "playwright"
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
    category: RuntimeVariableCategory = "flow"
    name: str = Field(min_length=1, max_length=120)
    sensitive: bool = False  # True 时日志和 UI 中需脱敏显示
    type: RuntimeVariableType
    value: str
    scope: RuntimeVariableScope = "全局"

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type_case(cls, value: object) -> object:
        """`string` 与 `String` 是同一个类型。

        大小写敏感只会让调用方（尤其是 AI）在建流程时踩 400，而首字母大写这件事
        本身不携带任何语义——在入口归一化，比在提示词里反复叮嘱可靠。
        """
        if not isinstance(value, str):
            return value
        canonical = {option.lower(): option for option in get_args(RuntimeVariableType)}
        return canonical.get(value.strip().lower(), value)


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
    structured: list[object] | None = None


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
    """任务完整状态，API 返回并持久化到任务存储。"""

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
    flow_revision: int | None = Field(default=None, ge=1)
    definition_digest: str | None = Field(default=None, max_length=64)
    acceptance_contract: FlowAcceptanceContract = Field(default_factory=FlowAcceptanceContract)
    execution_evidence: list[NodeExecutionEvidence] = Field(default_factory=list)
    run_config: RunConfigSnapshot = Field(default_factory=RunConfigSnapshot)
    error: str | None = None
    input_prompt: str | None = None
    human_takeover_message: str | None = None
    human_takeover_resume_mode: str | None = None   # "next_node" | "current_node"


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
    last_error: str | None = None


def _is_safe_variable_name(value: str) -> bool:
    """合法字符：字母、数字、_、.、-，且首字符须为字母或下划线。"""
    if not value or len(value) > 120:
        return False
    if not (value[0].isalpha() or value[0] == "_"):
        return False
    return all(char.isalnum() or char in {"_", ".", "-"} for char in value)
