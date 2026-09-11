from __future__ import annotations

import pytest
from openpyxl import load_workbook

from app.services.file_action_runner import FileActionRunner, apply_file_result_variables
from app.services.runtime_variables import RuntimeVariableStore


async def test_excel_read_csv_writes_output_variables(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id,total\nA001,42\nA002,64\n", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({})

    node = {
        "type": "excel.read",
        "path": "orders.csv",
        "column": "order_id",
        "outputVariable": "order_ids",
        "firstValueVariable": "first_order_id",
        "countVariable": "row_count",
    }
    result = await runner.run(
        node,
        variables,
        timeout_ms=1000,
    )
    apply_file_result_variables(node, result, variables)

    assert result.count == 2
    assert result.values == ["A001", "A002"]
    snapshots = {variable.name: variable for variable in variables.snapshots()}
    assert snapshots["order_ids"].value == '["A001", "A002"]'
    assert snapshots["first_order_id"].value == "A001"
    assert snapshots["row_count"].value == "2"


async def test_excel_read_csv_accepts_response_and_status_variable_aliases(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id,total\nA001,42\nA002,64\n", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({})
    node = {
        "type": "excel.read",
        "path": "orders.csv",
        "column": "order_id",
        "responseVariable": "order_ids",
        "statusVariable": "row_count",
    }

    result = await runner.run(node, variables, timeout_ms=1000)
    saved = apply_file_result_variables(node, result, variables)

    assert saved == ["order_ids", "row_count"]
    assert variables.get("order_ids") == ["A001", "A002"]
    assert variables.get("row_count") == 2


async def test_excel_write_dict_rows_writes_headers_and_aligned_values(tmp_path) -> None:
    runner = FileActionRunner(tmp_path)
    variables = RuntimeVariableStore.from_initial(
        {
            "rows": [
                {"序号": "1", "合约编号": "Y001", "门店名称": "门店A"},
                {"序号": "2", "合约编号": "Y002", "门店名称": "门店B"},
            ]
        }
    )

    await runner.run({"type": "excel.write", "path": "contracts.xlsx", "rows": "${var.rows}"}, variables, timeout_ms=1000)

    workbook = load_workbook(tmp_path / "contracts.xlsx", read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    assert rows == [
        ("序号", "合约编号", "门店名称"),
        ("1", "Y001", "门店A"),
        ("2", "Y002", "门店B"),
    ]


async def test_file_write_rejects_path_traversal(tmp_path) -> None:
    runner = FileActionRunner(tmp_path)
    variables = RuntimeVariableStore.from_initial({})

    try:
        await runner.run({"type": "file.write", "path": "../escape.txt", "content": "unsafe"}, variables, timeout_ms=1000)
    except ValueError as exc:
        assert "超出" in str(exc)
    else:
        raise AssertionError("文件节点必须拒绝目录穿越路径")


async def test_file_read_resolves_template_path(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.txt").write_text("done", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({"name": "report"})

    node = {"type": "file.read", "path": "${var.name}.txt", "outputVariable": "content"}
    result = await runner.run(node, variables, timeout_ms=1000)
    apply_file_result_variables(node, result, variables)

    assert result.values == ["done"]
    snapshots = {variable.name: variable for variable in variables.snapshots()}
    assert snapshots["content"].value == '["done"]'


async def test_file_copy_move_delete_and_list(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("hello", encoding="utf-8")
    (workspace / "keep.log").write_text("skip", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({})

    copy_result = await runner.run({"type": "file.copy", "path": "source.txt", "targetPath": "archive/source.txt"}, variables, timeout_ms=1000)
    assert copy_result.values == [str(workspace / "archive/source.txt")]
    assert (workspace / "archive/source.txt").read_text(encoding="utf-8") == "hello"

    move_result = await runner.run({"type": "file.move", "path": "archive/source.txt", "targetPath": "done/source.txt"}, variables, timeout_ms=1000)
    assert move_result.values == [str(workspace / "done/source.txt")]
    assert not (workspace / "archive/source.txt").exists()
    assert (workspace / "done/source.txt").exists()

    list_result = await runner.run({"type": "file.list", "path": ".", "pattern": "*.txt", "outputVariable": "files", "countVariable": "file_count"}, variables, timeout_ms=1000)
    apply_file_result_variables({"outputVariable": "files", "countVariable": "file_count"}, list_result, variables)
    assert list_result.values == ["source.txt"]
    assert variables.get("file_count") == 1

    delete_result = await runner.run({"type": "file.delete", "path": "source.txt"}, variables, timeout_ms=1000)
    assert delete_result.count == 1
    assert not (workspace / "source.txt").exists()


async def test_deleterow_uses_row_index_the_catalog_advertises(tmp_path) -> None:
    # 删掉的必须是 rowIndex 指的那行：删错行不会报错，用户只能靠比对数据才发现。
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id\nA001\nA002\nA003\n", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({})

    await runner.run(
        {"type": "excel.deleterow", "path": "orders.csv", "rowIndex": 2},
        variables,
        timeout_ms=1000,
    )

    assert (workspace / "orders.csv").read_text(encoding="utf-8-sig") == "order_id\nA001\nA003\n"


async def test_deleterow_without_a_row_index_refuses_to_guess(tmp_path) -> None:
    # 删行是破坏性的：键名写错时宁可报错停住，也不能默认删掉某一行。
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id\nA001\nA002\n", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({})

    with pytest.raises(ValueError, match="rowIndex"):
        await runner.run({"type": "excel.deleterow", "path": "orders.csv"}, variables, timeout_ms=1000)

    assert (workspace / "orders.csv").read_text(encoding="utf-8") == "order_id\nA001\nA002\n"


async def test_deleterow_resolves_a_templated_row_index(tmp_path) -> None:
    # 循环里删行时行号来自变量，int("${var.i}") 会直接抛 ValueError。
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id\nA001\nA002\nA003\n", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({"target_row": "1"})

    await runner.run(
        {"type": "excel.deleterow", "path": "orders.csv", "rowIndex": "${var.target_row}"},
        variables,
        timeout_ms=1000,
    )

    assert (workspace / "orders.csv").read_text(encoding="utf-8-sig") == "order_id\nA002\nA003\n"


async def test_deleterow_still_honours_the_legacy_index_key(tmp_path) -> None:
    # 存量流程里行号存在 index 上，换键名不能让它们改删别的行。
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders.csv").write_text("order_id\nA001\nA002\nA003\n", encoding="utf-8")
    runner = FileActionRunner(workspace)
    variables = RuntimeVariableStore.from_initial({})

    await runner.run(
        {"type": "excel.deleterow", "path": "orders.csv", "index": 3},
        variables,
        timeout_ms=1000,
    )

    assert (workspace / "orders.csv").read_text(encoding="utf-8-sig") == "order_id\nA001\nA002\n"
