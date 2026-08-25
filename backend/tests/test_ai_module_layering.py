"""AI 编排层的模块分层必须无环。

护栏/阶段机/工具事件这几层是靠「谁能 import 谁」撑起来的：ai_guard_state 是零依赖底座，
ai_guards / ai_tool_events 只在 TYPE_CHECKING 下引用它、运行期不反向依赖上层。一旦有人在
模块顶层加一条反向 import，import 期就会撞成循环崩掉整个后端——而这种崩溃只在真正 import
到那条路径时才出现，平时测试可能一路绿到线上。这里用 ast 静态扫顶层运行期 import 钉住这件事，
不引入 import-linter/mypy（一条本地规则不值一个新依赖）。

只算「模块加载时真的会执行」的 import：TYPE_CHECKING 块永不执行、函数内的惰性 import 本就是
用来破环的，都不计入分层边。
"""
from __future__ import annotations

import ast
from pathlib import Path

_SERVICES = Path(__file__).resolve().parent.parent / "app" / "services"


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _module_load_imports(body: list[ast.stmt]) -> list[ast.Import | ast.ImportFrom]:
    """收集在模块加载时会真正执行的 import：跳过 TYPE_CHECKING 块与函数/类体内的惰性 import。"""
    found: list[ast.Import | ast.ImportFrom] = []
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.append(node)
        elif isinstance(node, ast.If):
            if _is_type_checking(node.test):
                continue
            found += _module_load_imports(node.body)
            found += _module_load_imports(node.orelse)
        elif isinstance(node, ast.Try):
            found += _module_load_imports(node.body)
            for handler in node.handlers:
                found += _module_load_imports(handler.body)
            found += _module_load_imports(node.orelse)
            found += _module_load_imports(node.finalbody)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            found += _module_load_imports(node.body)
        # FunctionDef / AsyncFunctionDef / ClassDef 内部的 import 是运行时惰性引入，不构成加载期分层边
    return found


def _in_scope_modules() -> dict[str, Path]:
    """作用域：app/services 下的 ai_*.py 及 ai_tools 包（含其 __init__）。返回 {点名: 路径}。"""
    modules: dict[str, Path] = {}
    for path in _SERVICES.glob("ai_*.py"):
        modules[f"app.services.{path.stem}"] = path
    for path in (_SERVICES / "ai_tools").glob("*.py"):
        stem = "ai_tools" if path.stem == "__init__" else f"ai_tools.{path.stem}"
        modules[f"app.services.{stem}"] = path
    return modules


def _resolve_from(node: ast.ImportFrom, self_name: str) -> str | None:
    """把 from-import 的基准模块解析成绝对点名（含相对 import）。"""
    if node.level == 0:
        return node.module
    base = self_name.rsplit(".", node.level)[0]
    return f"{base}.{node.module}" if node.module else base


def _edges(self_name: str, tree: ast.Module, in_scope: set[str]) -> set[str]:
    targets: set[str] = set()
    for imp in _module_load_imports(tree.body):
        if isinstance(imp, ast.Import):
            for alias in imp.names:
                if alias.name in in_scope:
                    targets.add(alias.name)
        else:
            base = _resolve_from(imp, self_name)
            if base is None:
                continue
            # `from app.services import ai_x` —— 被导入名本身就是子模块
            for alias in imp.names:
                candidate = f"{base}.{alias.name}"
                if candidate in in_scope:
                    targets.add(candidate)
            if base in in_scope:
                targets.add(base)
    targets.discard(self_name)
    return targets


def _build_graph() -> dict[str, set[str]]:
    modules = _in_scope_modules()
    in_scope = set(modules)
    graph: dict[str, set[str]] = {}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        graph[name] = _edges(name, tree, in_scope)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)

    def visit(node: str, stack: list[str]) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in sorted(graph[node]):
            if color[nxt] == GRAY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                cycle = visit(nxt, stack)
                if cycle:
                    return cycle
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            cycle = visit(node, [])
            if cycle:
                return cycle
    return None


def test_ai_module_layering_is_acyclic() -> None:
    cycle = _find_cycle(_build_graph())
    assert cycle is None, "AI 模块出现加载期循环 import：" + " -> ".join(cycle)


def test_guard_state_is_a_zero_dependency_base() -> None:
    """底座只能被依赖，不能反向依赖任何 AI 模块——否则 TYPE_CHECKING 那条分层线形同虚设。"""
    graph = _build_graph()
    assert graph["app.services.ai_guard_state"] == set()
