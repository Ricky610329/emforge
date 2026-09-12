"""Launch Pi, Codex or Claude Code with a session-local emforge MCP connection."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def launch_command(harness, source, python, connection, output, *, extra=()):
    mcp = {"command": str(python), "args": ["-m", "emforge", "platform-mcp", "--transport", "stdio",
           "--connection", str(connection)], "cwd": str(source), "env": {"PYTHONIOENCODING": "utf-8"}}
    if harness == "pi":
        package = source / "integrations" / "pi"
        cli = package / "node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
        if not cli.is_file():
            raise FileNotFoundError("Run npm ci --prefix integrations/pi first")
        return [shutil.which("node") or "node", str(cli), "-e", str(package / "extension.ts"), *extra]
    if harness == "codex":
        command = [shutil.which("codex") or "codex"]
        for key, value in mcp.items():
            if key == "env":
                command += ["-c", 'mcp_servers.emforge.env.PYTHONIOENCODING="utf-8"']
            else:
                command += ["-c", f"mcp_servers.emforge.{key}={json.dumps(value)}"]
        return command + list(extra)
    output.mkdir(parents=True, exist_ok=True)
    config = output / "claude-mcp.json"
    config.write_text(json.dumps({"mcpServers": {"emforge": mcp}}, indent=2), encoding="utf-8")
    # Claude's variadic --mcp-config consumes following positionals; keep it last.
    return [shutil.which("claude") or "claude", *extra, "--mcp-config", str(config)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", choices=["pi", "codex", "claude"])
    parser.add_argument("--connection", required=True, type=Path)
    parser.add_argument("--print-command", action="store_true", help="Show argv without starting a model")
    args, extra = parser.parse_known_args()
    source = Path(__file__).resolve().parents[1]
    connection = args.connection.resolve(strict=True)
    extra = extra[1:] if extra[:1] == ["--"] else extra
    command = launch_command(args.harness, source, sys.executable, connection, source / "local/agents", extra=extra)
    if args.print_command:
        print(json.dumps(command, ensure_ascii=False))
        return 0
    env = {**os.environ, "EMFORGE_SOURCE": str(source), "EMFORGE_PYTHON": sys.executable,
           "EMFORGE_CONNECTION": str(connection), "PYTHONIOENCODING": "utf-8"}
    return subprocess.call(command, cwd=source, env=env)


if __name__ == "__main__":
    sys.exit(main())
