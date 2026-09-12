"""The agent launcher passes paths as argv and leaves existing user configuration alone."""
import json
import tomllib

from scripts.launch_agent import launch_command


def test_codex_overrides_roundtrip_spaces_quotes_and_unicode(tmp_path):
    source = tmp_path / "中文 space's"
    python = source / "python.exe"
    connection = source / "connection.json"
    argv = launch_command("codex", source, python, connection, tmp_path)
    settings = tomllib.loads("\n".join(argv[i+1] for i, arg in enumerate(argv) if arg == "-c"))
    mcp = settings["mcp_servers"]["emforge"]
    assert mcp["command"] == str(python)
    assert mcp["args"][-1] == str(connection)
    assert mcp["cwd"] == str(source)


def test_claude_configuration_has_no_token_or_permission_overrides(tmp_path):
    argv = launch_command("claude", tmp_path, tmp_path / "python.exe", tmp_path / "private.json", tmp_path)
    config = json.loads((tmp_path / "claude-mcp.json").read_text(encoding="utf-8"))
    assert set(config) == {"mcpServers"}
    assert config["mcpServers"]["emforge"]["args"][-1].endswith("private.json")
    assert "--mcp-config" in argv and "--dangerously-skip-permissions" not in argv


def test_claude_variadic_config_does_not_consume_prompt(tmp_path):
    argv = launch_command("claude", tmp_path, tmp_path / "python", tmp_path / "private.json", tmp_path,
                          extra=["-p", "Query the fleet"])
    assert argv[1:3] == ["-p", "Query the fleet"]
    assert argv[-2:] == ["--mcp-config", str(tmp_path / "claude-mcp.json")]
