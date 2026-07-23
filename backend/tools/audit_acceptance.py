from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Status = Literal["passed", "incomplete", "missing", "blocked_by_external_dependency"]

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

REQUIRED_COMPOSE_SNIPPETS = [
    "services:",
    "postgres:",
    "redis:",
    "minio:",
    "backend:",
    "./database/init.sql:/docker-entrypoint-initdb.d/001-init.sql:ro",
    "DATABASE_URL: postgresql+asyncpg://rpa:rpa@postgres:5432/rpa",
    "RPA_FLOW_STORE_BACKEND: sqlalchemy",
    "RPA_TASK_STORE_BACKEND: sqlalchemy",
    "RPA_SCHEDULE_STORE_BACKEND: sqlalchemy",
    "RPA_TASK_QUEUE_BACKEND: redis",
    "RPA_ARTIFACT_STORE_BACKEND: minio",
    "condition: service_healthy",
    '"8765:8765"',
    "volumes:",
]

MAC_RELEASE_REQUIRED_ENV = [
    "CSC_LINK",
    "CSC_KEY_PASSWORD",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
    "APPLE_TEAM_ID",
]


@dataclass(frozen=True)
class AuditCheck:
    id: str
    title: str
    status: Status
    criteria: list[str]
    evidence: list[str]
    observed: dict[str, Any]
    notes: list[str]


def main() -> None:
    args = parse_args()
    checks = build_checks()
    payload = build_report(checks)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.enforce and payload["summary"]["overall_status"] != "passed":
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计 Easy RPA 当前验收证据，不把 smoke 结果误判为正式通过。")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "output" / "bench" / "acceptance-audit.json",
        help="写入统一验收报告的路径。",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="存在 incomplete/missing/blocked_by_external_dependency 时返回非 0 退出码。",
    )
    return parser.parse_args()


def build_checks() -> list[AuditCheck]:
    return [
        audit_frontend_component_contract(),
        audit_load_and_cron(),
        audit_selector_regression(),
        audit_static_monitor_smoke(),
        audit_static_monitor_7d(),
        audit_anti_bot_smoke(),
        audit_anti_bot_real_targets(),
        audit_compose_static_contract(),
        audit_compose_runtime(),
        audit_macos_startup(),
        audit_windows_linux_startup(),
        audit_macos_signed_release(),
    ]


def build_report(checks: list[AuditCheck]) -> dict[str, Any]:
    counts: dict[Status, int] = {
        "passed": 0,
        "incomplete": 0,
        "missing": 0,
        "blocked_by_external_dependency": 0,
    }
    for check in checks:
        counts[check.status] += 1

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "project_root": str(PROJECT_ROOT),
        "summary": {
            "total": len(checks),
            **counts,
            "overall_status": "passed" if counts["passed"] == len(checks) else "not_fully_accepted",
        },
        "checks": [asdict(check) for check in checks],
    }


def audit_frontend_component_contract() -> AuditCheck:
    source_root = PROJECT_ROOT / "src"
    package_json = read_json(PROJECT_ROOT / "package.json") or {}
    raw_controls = find_raw_controls(source_root)
    radix_dependencies = [
        name
        for name in (package_json.get("dependencies") or {})
        if isinstance(name, str) and name.startswith("@radix-ui/")
    ]
    has_context_menu = "@radix-ui/react-context-menu" in (package_json.get("dependencies") or {})
    status: Status = "passed" if not raw_controls and has_context_menu and len(radix_dependencies) >= 5 else "incomplete"

    return AuditCheck(
        id="frontend_modular_shadcn_contract",
        title="前端模块化与 shadcn/Radix 组件优先",
        status=status,
        criteria=[
            "业务组件不直接使用原生 button/input/select/textarea 控件。",
            "基础控件集中在 src/components/ui，并使用 Radix/shadcn 风格封装。",
            "画布右键菜单使用 Radix ContextMenu 封装而不是手写 fixed 浮层。",
        ],
        evidence=[rel(PROJECT_ROOT / "src"), rel(PROJECT_ROOT / "package.json")],
        observed={
            "raw_controls_outside_ui": raw_controls,
            "radix_dependency_count": len(radix_dependencies),
            "has_radix_context_menu": has_context_menu,
        },
        notes=[] if status == "passed" else ["仍存在业务层原生控件或缺少必要 Radix 依赖。"],
    )


def audit_load_and_cron() -> AuditCheck:
    path = BACKEND_ROOT / "storage" / "bench" / "load-test.json"
    data = read_json(path)
    criteria = [
        "task_count >= 100 且 create_concurrency >= 100。",
        "任务全部创建成功、全部完成、失败数和超时数为 0。",
        "Cron 最大误差 <= 5000ms，且至少有 1 个 Cron 样本。",
    ]
    if data is None:
        return missing_check("load_100_concurrency_cron", "100 并发与 Cron 误差", criteria, path)

    passed = (
        int(data.get("task_count", 0)) >= 100
        and int(data.get("create_concurrency", 0)) >= 100
        and data.get("successful_creates") == data.get("task_count")
        and data.get("completed_tasks") == data.get("task_count")
        and int(data.get("failed_creates", -1)) == 0
        and int(data.get("failed_tasks", -1)) == 0
        and int(data.get("timed_out_tasks", -1)) == 0
        and int(data.get("cron_samples", 0)) > 0
        and float(data.get("cron_max_error_ms", 999999)) <= 5000
    )
    return AuditCheck(
        id="load_100_concurrency_cron",
        title="100 并发与 Cron 误差",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path)],
        observed=pick(data, ["task_count", "create_concurrency", "successful_creates", "completed_tasks", "failed_creates", "failed_tasks", "timed_out_tasks", "cron_samples", "cron_max_error_ms"]),
        notes=[] if passed else ["现有 load-test.json 未满足全部并发或 Cron 验收条件。"],
    )


def audit_selector_regression() -> AuditCheck:
    path = BACKEND_ROOT / "storage" / "bench" / "selector-regression.json"
    data = read_json(path)
    criteria = [
        "recommended_selector_survival_rate >= css_in_js_survival_target，默认目标 0.85。",
        "relocation_rate >= adaptive_relocation_target，默认目标 0.80。",
        "total_cases > 0 且 passed=true。",
    ]
    if data is None:
        return missing_check("selector_css_in_js_regression", "Selector 存活率与自动重定位", criteria, path)

    survival_target = float(data.get("css_in_js_survival_target", 0.85))
    relocation_target = float(data.get("adaptive_relocation_target", 0.8))
    passed = (
        bool(data.get("passed"))
        and int(data.get("total_cases", 0)) > 0
        and float(data.get("recommended_selector_survival_rate", 0)) >= survival_target
        and float(data.get("relocation_rate", 0)) >= relocation_target
    )
    return AuditCheck(
        id="selector_css_in_js_regression",
        title="Selector 存活率与自动重定位",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path)],
        observed=pick(data, ["total_cases", "recommended_selector_survival_rate", "relocation_rate", "css_in_js_survival_target", "adaptive_relocation_target", "passed"]),
        notes=[] if passed else ["Selector 回归结果未达到设计方案阈值。"],
    )


def audit_static_monitor_smoke() -> AuditCheck:
    path = BACKEND_ROOT / "storage" / "monitor" / "static-success-smoke-summary.json"
    data = read_json(path)
    criteria = [
        "短周期 smoke 至少有 1 条记录。",
        "success_rate >= 0.99。",
        "该项只证明工具链可运行，不证明 7 天成功率。",
    ]
    if data is None:
        return missing_check("static_monitor_smoke", "静态页面监控 smoke", criteria, path)

    passed = bool(data.get("passed")) and int(data.get("records", 0)) >= 1 and float(data.get("success_rate", 0)) >= 0.99
    return AuditCheck(
        id="static_monitor_smoke",
        title="静态页面监控 smoke",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path)],
        observed=pick(data, ["records", "success_count", "failure_count", "success_rate", "threshold", "duration_hours", "passed"]),
        notes=["smoke 不能替代连续 7 天验收。"],
    )


def audit_static_monitor_7d() -> AuditCheck:
    path = BACKEND_ROOT / "storage" / "monitor" / "static-success-7d-summary.json"
    data = read_json(path)
    criteria = [
        "records >= 10080。",
        "duration_hours >= 168。",
        "success_rate >= 0.99 且 passed=true。",
    ]
    if data is None:
        smoke_path = BACKEND_ROOT / "storage" / "monitor" / "static-success-smoke-summary.json"
        return AuditCheck(
            id="static_monitor_7d",
            title="静态页面 7 天成功率",
            status="incomplete",
            criteria=criteria,
            evidence=[rel(smoke_path)] if smoke_path.exists() else [],
            observed={"expected_evidence": rel(path)},
            notes=["当前只有短周期 smoke 证据，尚未真实跑满连续 7 天。"],
        )

    passed = (
        bool(data.get("passed"))
        and bool(data.get("enough_records"))
        and bool(data.get("enough_duration"))
        and int(data.get("records", 0)) >= 10080
        and float(data.get("duration_hours", 0)) >= 168
        and float(data.get("success_rate", 0)) >= 0.99
    )
    return AuditCheck(
        id="static_monitor_7d",
        title="静态页面 7 天成功率",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path)],
        observed=pick(data, ["records", "duration_hours", "success_rate", "threshold", "enough_records", "enough_duration", "passed"]),
        notes=[] if passed else ["7 天监控汇总存在，但尚未满足完整验收条件。"],
    )


def audit_anti_bot_smoke() -> AuditCheck:
    path = BACKEND_ROOT / "storage" / "bench" / "anti-bot-smoke.json"
    data = read_json(path)
    criteria = [
        "smoke 至少 1 次尝试。",
        "success_rate >= 0.90。",
        "category=smoke，该项不证明 Cloudflare/DataDome 真实成功率。",
    ]
    if data is None:
        return missing_check("anti_bot_smoke", "反爬评测工具 smoke", criteria, path)

    categories = set((data.get("by_category") or {}).keys())
    passed = bool(data.get("passed")) and int(data.get("total_attempts", 0)) >= 1 and float(data.get("success_rate", 0)) >= 0.9 and categories == {"smoke"}
    return AuditCheck(
        id="anti_bot_smoke",
        title="反爬评测工具 smoke",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path)],
        observed=pick(data, ["targets", "attempts_per_target", "total_attempts", "success_rate", "threshold", "passed"]) | {"categories": sorted(categories)},
        notes=["smoke 使用公开静态页面，不能替代授权反爬目标评测。"],
    )


def audit_anti_bot_real_targets() -> AuditCheck:
    path = BACKEND_ROOT / "storage" / "bench" / "anti-bot-benchmark.json"
    data = read_json(path)
    criteria = [
        "使用授权 Cloudflare 和 DataDome 目标清单。",
        "每个目标 attempts_per_target >= 10。",
        "全局 success_rate >= 0.90 且 passed=true。",
    ]
    if data is None:
        return AuditCheck(
            id="anti_bot_real_targets",
            title="Cloudflare/DataDome 真实反爬成功率",
            status="blocked_by_external_dependency",
            criteria=criteria,
            evidence=[rel(BACKEND_ROOT / "config" / "anti_bot_targets.example.json")],
            observed={"expected_evidence": rel(path)},
            notes=["缺少合法授权目标清单和多轮真实评测产物，当前环境不能替代测试方授权目标。"],
        )

    categories = set((data.get("by_category") or {}).keys())
    required_categories = {"cloudflare", "datadome"}
    passed = (
        bool(data.get("passed"))
        and int(data.get("attempts_per_target", 0)) >= 10
        and float(data.get("success_rate", 0)) >= 0.9
        and required_categories.issubset(categories)
    )
    return AuditCheck(
        id="anti_bot_real_targets",
        title="Cloudflare/DataDome 真实反爬成功率",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path)],
        observed=pick(data, ["targets", "attempts_per_target", "total_attempts", "success_rate", "threshold", "passed"]) | {"categories": sorted(categories)},
        notes=[] if passed else ["真实反爬评测产物存在，但目标类型、采样次数或成功率尚未覆盖完整验收。"],
    )


def audit_compose_static_contract() -> AuditCheck:
    path = PROJECT_ROOT / "docker-compose.yml"
    criteria = [
        "Compose 包含 PostgreSQL、Redis、MinIO、backend 服务。",
        "backend 启用 SQLAlchemy、Redis 队列和 MinIO artifact store。",
        "至少 3 个 healthcheck，且包含持久化挂载和端口映射。",
    ]
    if not path.exists():
        return missing_check("compose_static_contract", "Docker Compose 静态合约", criteria, path)

    content = path.read_text(encoding="utf-8")
    missing = [snippet for snippet in REQUIRED_COMPOSE_SNIPPETS if snippet not in content]
    healthcheck_count = len(re.findall(r"\bhealthcheck:", content))
    passed = not missing and healthcheck_count >= 3
    return AuditCheck(
        id="compose_static_contract",
        title="Docker Compose 静态合约",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(path), rel(PROJECT_ROOT / "tools" / "verify_compose_config.cjs")],
        observed={"missing_snippets": missing, "healthcheck_count": healthcheck_count},
        notes=[] if passed else ["Compose 静态配置仍缺少必要服务、环境变量或健康检查。"],
    )


def audit_compose_runtime() -> AuditCheck:
    path = PROJECT_ROOT / "output" / "bench" / "docker-compose-runtime.json"
    data = read_json(path)
    criteria = [
        "docker compose config 成功。",
        "docker compose build backend 成功。",
        "postgres/redis/minio/backend 启动成功，/api/health 和 /api/queue 返回成功。",
    ]
    if data is not None:
        passed = bool(data.get("passed"))
        return AuditCheck(
            id="compose_runtime",
            title="Docker Compose 实机运行",
            status="passed" if passed else "incomplete",
            criteria=criteria,
            evidence=[rel(path)],
            observed=data,
            notes=[] if passed else ["Docker 实机运行产物存在，但未全部通过。"],
        )

    if shutil.which("docker") is None:
        return AuditCheck(
            id="compose_runtime",
            title="Docker Compose 实机运行",
            status="blocked_by_external_dependency",
            criteria=criteria,
            evidence=[],
            observed={"docker_cli": "missing", "expected_evidence": rel(path)},
            notes=["当前机器没有 Docker CLI，只能完成静态合约检查，不能证明容器可构建和运行。"],
        )

    return AuditCheck(
        id="compose_runtime",
        title="Docker Compose 实机运行",
        status="incomplete",
        criteria=criteria,
        evidence=[],
        observed={"docker_cli": "available", "expected_evidence": rel(path)},
        notes=["检测到 Docker CLI，但缺少实机运行报告。"],
    )


def audit_macos_startup() -> AuditCheck:
    candidate_paths = [
        PROJECT_ROOT / "output" / "bench" / "electron-startup-release-config.json",
        PROJECT_ROOT / "output" / "bench" / "electron-startup.json",
    ]
    data_path = next((path for path in candidate_paths if path.exists()), None)
    criteria = [
        "macOS arm64 目录包可启动。",
        "冷启动平均耗时 <= 3000ms。",
        "样本 platform=darwin，arch=arm64，packaged=true。",
    ]
    if data_path is None:
        return missing_check("electron_macos_startup", "Electron macOS 冷启动", criteria, candidate_paths[0])

    data = read_json(data_path) or {}
    samples = data.get("samples") if isinstance(data.get("samples"), list) else []
    sample_scope_ok = bool(samples) and all(
        sample.get("platform") == "darwin" and sample.get("arch") == "arm64" and bool(sample.get("packaged"))
        for sample in samples
        if isinstance(sample, dict)
    )
    passed = bool(data.get("passed")) and float(data.get("averageMs", 999999)) <= 3000 and sample_scope_ok
    return AuditCheck(
        id="electron_macos_startup",
        title="Electron macOS 冷启动",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(data_path)],
        observed=pick(data, ["runs", "averageMs", "p95Ms", "maxAverageMs", "passed", "measuredAt"]) | {"sample_scope_ok": sample_scope_ok},
        notes=[] if passed else ["冷启动报告存在，但平台范围或耗时阈值未满足。"],
    )


def audit_windows_linux_startup() -> AuditCheck:
    windows_path = PROJECT_ROOT / "output" / "bench" / "electron-startup-windows.json"
    linux_path = PROJECT_ROOT / "output" / "bench" / "electron-startup-linux.json"
    windows_data = read_json(windows_path)
    linux_data = read_json(linux_path)
    criteria = [
        "Windows 安装包/目录包可启动并生成冷启动报告。",
        "Linux AppImage/目录包可启动并生成冷启动报告。",
        "各平台平均冷启动 <= 3000ms。",
    ]
    if windows_data is None or linux_data is None:
        return AuditCheck(
            id="electron_windows_linux_startup",
            title="Electron Windows/Linux 冷启动",
            status="blocked_by_external_dependency",
            criteria=criteria,
            evidence=[rel(path) for path in (windows_path, linux_path) if path.exists()],
            observed={
                "windows_report": rel(windows_path) if windows_path.exists() else None,
                "linux_report": rel(linux_path) if linux_path.exists() else None,
            },
            notes=["当前工作区只存在 macOS arm64 冷启动证据，Windows/Linux 需要对应平台或 CI 产物。"],
        )

    passed = bool(windows_data.get("passed")) and bool(linux_data.get("passed")) and float(windows_data.get("averageMs", 999999)) <= 3000 and float(linux_data.get("averageMs", 999999)) <= 3000
    return AuditCheck(
        id="electron_windows_linux_startup",
        title="Electron Windows/Linux 冷启动",
        status="passed" if passed else "incomplete",
        criteria=criteria,
        evidence=[rel(windows_path), rel(linux_path)],
        observed={"windows": pick(windows_data, ["runs", "averageMs", "passed"]), "linux": pick(linux_data, ["runs", "averageMs", "passed"])},
        notes=[] if passed else ["Windows/Linux 冷启动报告存在，但未全部满足阈值。"],
    )


def audit_macos_signed_release() -> AuditCheck:
    criteria = [
        "提供 CSC_LINK/CSC_KEY_PASSWORD 和 Apple Developer 公证变量。",
        "执行 electron:dist:signed 成功。",
        "保留签名、公证后的 macOS 产物和校验输出。",
    ]
    missing_env = [name for name in MAC_RELEASE_REQUIRED_ENV if os.environ.get(name, "").strip() == ""]
    signed_report = PROJECT_ROOT / "output" / "bench" / "macos-signed-release.json"
    data = read_json(signed_report)
    if data is not None:
        passed = bool(data.get("passed"))
        return AuditCheck(
            id="macos_signed_notarized_release",
            title="macOS 签名与公证发布",
            status="passed" if passed else "incomplete",
            criteria=criteria,
            evidence=[rel(signed_report)],
            observed=data,
            notes=[] if passed else ["签名/公证报告存在，但未成功完成。"],
        )

    return AuditCheck(
        id="macos_signed_notarized_release",
        title="macOS 签名与公证发布",
        status="blocked_by_external_dependency" if missing_env else "incomplete",
        criteria=criteria,
        evidence=[rel(PROJECT_ROOT / "electron-builder.config.cjs"), rel(PROJECT_ROOT / "tools" / "verify_release_env.cjs"), rel(PROJECT_ROOT / "tools" / "notarize-mac.cjs")],
        observed={"missing_env": missing_env, "expected_evidence": rel(signed_report)},
        notes=[
            "当前缺少 Apple Developer/CSC 凭据，不能执行真实签名和公证。"
            if missing_env
            else "签名环境变量存在，但缺少签名/公证结果报告。"
        ],
    )


def missing_check(check_id: str, title: str, criteria: list[str], expected_path: Path) -> AuditCheck:
    return AuditCheck(
        id=check_id,
        title=title,
        status="missing",
        criteria=criteria,
        evidence=[],
        observed={"expected_evidence": rel(expected_path)},
        notes=["缺少可审计产物。"],
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def pick(data: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: data.get(key) for key in keys if key in data}


def find_raw_controls(source_root: Path) -> list[str]:
    if not source_root.exists():
        return [f"{rel(source_root)}:missing"]

    pattern = re.compile(r"</?(button|input|select|textarea)(?:\s|>)")
    findings: list[str] = []
    for path in sorted(source_root.rglob("*.tsx")):
        if "node_modules" in path.parts or path.parent == source_root / "components" / "ui":
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            findings.append(rel(path))
    return findings


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
