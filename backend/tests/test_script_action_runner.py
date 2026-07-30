from __future__ import annotations

import pytest

from app.services.runtime_variables import RuntimeVariableStore
from app.services.script_action_runner import ScriptActionRunner, apply_script_result_variables


async def test_python_script_runner_writes_output_variables(tmp_path) -> None:
    script = tmp_path / "clean.py"
    script.write_text("import os\nprint(os.environ['RPA_VARIABLES_JSON'])\n", encoding="utf-8")
    variables = RuntimeVariableStore.from_initial({"order_id": "A001"})
    runner = ScriptActionRunner(tmp_path)
    node = {
        "type": "script.python",
        "path": "clean.py",
        "outputVariable": "script_stdout",
        "statusVariable": "script_exit_code",
        "stderrVariable": "script_stderr",
        "inputVariables": ["order_id"],
    }

    result = await runner.run(node, variables, timeout_ms=5_000)
    saved = apply_script_result_variables(node, result, variables)

    assert result.exit_code == 0
    assert "order_id" in result.stdout
    assert saved == ["script_stdout", "script_exit_code", "script_stderr"]
    assert variables.get("script_exit_code") == 0
    assert "A001" in str(variables.get("script_stdout"))


async def test_python_script_runner_accepts_response_variable_alias(tmp_path) -> None:
    script = tmp_path / "clean.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    variables = RuntimeVariableStore.from_initial({})
    runner = ScriptActionRunner(tmp_path)
    node = {"type": "script.python", "path": "clean.py", "responseVariable": "script_stdout"}

    result = await runner.run(node, variables, timeout_ms=5_000)
    saved = apply_script_result_variables(node, result, variables)

    assert saved == ["script_stdout"]
    assert variables.get("script_stdout").strip() == "ok"


async def test_script_runner_rejects_path_traversal(tmp_path) -> None:
    runner = ScriptActionRunner(tmp_path)
    variables = RuntimeVariableStore()

    with pytest.raises(ValueError, match="脚本路径超出 RPA 工作目录"):
        await runner.run({"type": "script.python", "path": "../escape.py"}, variables, timeout_ms=1_000)


async def test_python_script_runner_preserves_pythonpath(tmp_path, monkeypatch) -> None:
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "custom_dependency.py").write_text("VALUE = 'from-pythonpath'\n", encoding="utf-8")
    script = tmp_path / "use_dependency.py"
    script.write_text("import custom_dependency\nprint(custom_dependency.VALUE)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(module_dir))

    runner = ScriptActionRunner(tmp_path)
    result = await runner.run({"type": "script.python", "path": "use_dependency.py"}, RuntimeVariableStore(), timeout_ms=5_000)

    assert result.exit_code == 0
    assert result.stdout == "from-pythonpath"


async def test_python_script_runner_writes_large_variables_to_file(tmp_path) -> None:
    script = tmp_path / "read_large_variables.py"
    script.write_text(
        "import json, os\n"
        "payload_path = os.environ.get('RPA_VARIABLES_FILE')\n"
        "assert payload_path, 'RPA_VARIABLES_FILE missing'\n"
        "with open(payload_path, encoding='utf-8') as f:\n"
        "    data = json.load(f)\n"
        "print(len(data['large_payload']))\n",
        encoding="utf-8",
    )
    variables = RuntimeVariableStore.from_initial({"large_payload": "x" * 80_000})
    runner = ScriptActionRunner(tmp_path)

    result = await runner.run(
        {"type": "script.python", "path": "read_large_variables.py", "inputVariables": ["large_payload"]},
        variables,
        timeout_ms=5_000,
    )

    assert result.exit_code == 0
    assert result.stdout == "80000"
