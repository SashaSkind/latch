from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_project_config_registers_required_latch_stdio_server() -> None:
    config = tomllib.loads(
        (ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    )

    server = config["mcp_servers"]["latch"]
    assert server["command"] == "/bin/sh"
    assert server["enabled"] is True
    assert server["required"] is True
    assert server["enabled_tools"] == ["latch_execute_batch"]
    assert server["default_tools_approval_mode"] == "writes"
    assert "codex_runtime.mcp_server" in server["args"][1]


def test_latch_skill_prefers_the_first_class_mcp_tool() -> None:
    skill = (
        ROOT / ".codex" / "skills" / "latch-runtime" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "latch_execute_batch" in skill
    assert "Call `latch_execute_batch`" in skill
    assert "If the MCP server is unavailable" in skill
