import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { createInterface } from "node:readline";
import { test } from "node:test";
import { Bridge } from "../bridge.mjs";
import { DefaultResourceLoader, SettingsManager } from "@earendil-works/pi-coding-agent";

test("real stdio MCP proxies authenticated HTTP, confirmation, results and failures", { timeout: 45000 }, async () => {
  const cwd = resolve(import.meta.dirname, "../../..");
  const python = process.env.EMFORGE_PYTHON || "python";
  const dir = await mkdtemp(join(tmpdir(), "emforge pi 空白 "));
  const fixture = spawn(python, ["-m", "tests.agent_fixture", join(dir, "root")], {
    cwd, env: { ...process.env, PYTHONIOENCODING: "utf-8" }, stdio: ["pipe", "pipe", "inherit"],
  });
  let bridge;
  try {
    const lines = createInterface({ input: fixture.stdout });
    const config = await new Promise((ok, fail) => {
      lines.once("line", line => ok(JSON.parse(line)));
      fixture.once("exit", code => fail(new Error(`Fixture exited ${code}`)));
    });
    const connection = join(dir, "connection.json");
    await writeFile(connection, JSON.stringify(config));
    bridge = new Bridge({ python, cwd, connection });
    const tools = await bridge.list();
    assert.equal(tools.length, 5);
    assert(tools.some(t => t.name === "platform_reference"));
    const call = async (name, args = {}, signal) => {
      const result = await bridge.call(name, args, signal);
      return JSON.parse(result.content[0].text);
    };
    const desc = (await call("platform_query", { operation: "description", params: { profile: "fake_f1" } })).result;
    assert.deepEqual(desc.shape, [8, 8]);
    const payload = { profile: "fake_f1", name: "anneal", run_id: "run_pi", request_id: "request_pi",
      items: [{ pattern: Array.from({ length: 8 }, () => Array(8).fill(1)) }] };
    const preview = await call("inbox_submit", payload);
    assert.equal(preview.needs_confirm, true);
    await assert.rejects(call("inbox_submit", { ...payload, run_id: "changed", confirm: preview.token }));
    const { sid } = await call("inbox_submit", { ...payload, confirm: preview.token });
    const result = (await call("platform_query", { operation: "submission_results",
      params: { profile: "fake_f1", name: "anneal", run_id: "run_pi", sid } })).result;
    assert.equal(result.length, 1);
    await assert.rejects(call("inbox_submit", { ...payload, confirm: preview.token }));
    const again = await call("inbox_submit", payload);
    assert.equal((await call("inbox_submit", { ...payload, confirm: again.token })).sid, sid);
    await assert.rejects(call("platform_query", { operation: "run_stop", params: {} }));
    await assert.rejects(call("edit_code", {}), /Unknown emforge tool/);
    await assert.rejects(call("platform_reference", {}, AbortSignal.abort()));
    await bridge.close();
    assert((await call("platform_reference")).operations.description);
    await bridge.close();
    // Load the actual TypeScript extension with Pi's public loader, not a mocked API.
    process.env.EMFORGE_PYTHON = python;
    process.env.EMFORGE_SOURCE = cwd;
    process.env.EMFORGE_CONNECTION = connection;
    const loader = new DefaultResourceLoader({ cwd: dir, agentDir: join(dir, "pi-home"),
      settingsManager: SettingsManager.inMemory(), noSkills: true, noThemes: true,
      noContextFiles: true, noPromptTemplates: true,
      additionalExtensionPaths: [resolve(import.meta.dirname, "../extension.ts")] });
    await loader.reload();
    const loaded = loader.getExtensions();
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    const extension = loaded.extensions[0];
    try {
      assert.equal(extension.tools.size, 5);
      const query = extension.tools.get("emforge_platform_query").definition;
      const result = await query.execute("pi-call", { operation: "description", params: { profile: "fake_f1" } });
      assert.deepEqual(JSON.parse(result.content[0].text).result.shape, [8, 8]);
      await assert.rejects(query.execute("pi-error", { operation: "run_stop" }));
    } finally {
      for (const shutdown of extension.handlers.get("session_shutdown")) await shutdown({ type: "session_shutdown" });
    }
    bridge = new Bridge({ python, cwd, connection });
    await writeFile(connection, JSON.stringify({ ...config, token: "wrong-test-token" }));
    await assert.rejects(bridge.call("platform_query", { operation: "platform_state" }), error =>
      !error.message.includes("wrong-test-token"));
  } finally {
    await bridge?.close();
    fixture.stdin.end();
    await new Promise(ok => fixture.exitCode !== null ? ok() : fixture.once("exit", ok));
    await rm(dir, { recursive: true, force: true });
  }
});
