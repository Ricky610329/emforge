"""Agent launch configuration is local; credentials never enter tool output."""
import json

import pytest

from emforge.cli import main
from emforge.platform.connection import remote_from_file


def test_connection_uses_only_endpoint_and_token(tmp_path, monkeypatch):
    monkeypatch.setenv("EMFORGE_PLATFORM_TOKEN", "different-token")
    config = tmp_path / "connection.json"
    config.write_text(json.dumps({"endpoint": "http://127.0.0.1:8766", "token": "test-token",
                                  "source": "ignored", "python": "ignored"}), encoding="utf-8")
    remote = remote_from_file(config)
    assert remote.url == "http://127.0.0.1:8766"
    assert remote.token == "test-token"


@pytest.mark.parametrize("payload", [[], {}, {"endpoint": "file:///private", "token": "secret"},
    {"endpoint": "http://user:secret@localhost", "token": "secret"},
    {"endpoint": "http://localhost?secret=value", "token": "secret"},
    {"endpoint": "http://localhost", "token": ""}])
def test_invalid_config_does_not_echo_credentials(tmp_path, payload):
    config = tmp_path / "connection.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid agent connection") as error:
        remote_from_file(config)
    assert "secret" not in str(error.value)


def test_cli_stdio_uses_config_and_does_not_start_http(tmp_path, monkeypatch):
    from emforge.platform import mcp_server
    config = tmp_path / "connection.json"
    config.write_text('{"endpoint":"http://127.0.0.1:8766","token":"private"}', encoding="utf-8")
    called = []
    monkeypatch.setattr(mcp_server, "serve_stdio", lambda platform: called.append(platform))
    assert main(["platform-mcp", "--transport", "stdio", "--connection", str(config)]) == 0
    assert len(called) == 1 and called[0].token == "private"


def test_connection_cannot_silently_override_endpoint(tmp_path, capsys):
    assert main(["platform-mcp", "--connection", str(tmp_path / "unused.json"),
                 "--endpoint", "http://localhost:1234"]) == 1
    assert "--connection" in capsys.readouterr().err
