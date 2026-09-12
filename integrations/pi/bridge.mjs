import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { CallToolResultSchema } from "@modelcontextprotocol/sdk/types.js";

const NAMES = new Set(["platform_reference", "platform_query", "inbox_submit", "algorithm_start", "algorithm_stop"]);

/** A session owns one child and its confirmation tokens. Calls are never retried. */
export class Bridge {
  constructor({ python = process.env.EMFORGE_PYTHON || "python",
    cwd = process.env.EMFORGE_SOURCE, connection = process.env.EMFORGE_CONNECTION } = {}) {
    if (!connection) throw new Error("Set EMFORGE_CONNECTION to a local endpoint/token JSON file");
    this.options = { python, cwd, connection };
    this.client = null;
    this.connecting = null;
  }

  async connect() {
    if (this.client) return this.client;
    if (this.connecting) return this.connecting;
    this.connecting = this.open();
    try { return await this.connecting; }
    finally { this.connecting = null; }
  }

  async open() {
    const { python, cwd, connection } = this.options;
    const client = new Client({ name: "emforge-pi", version: "0.1.0" });
    const transport = new StdioClientTransport({ command: python, cwd,
      args: ["-m", "emforge", "platform-mcp", "--transport", "stdio", "--connection", connection],
      env: { PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" }, stderr: "pipe" });
    // Drain diagnostics without leaking local paths/configuration into a model transcript.
    transport.stderr?.on("data", () => {});
    client.onclose = () => { if (this.client === client) this.client = null; };
    try {
      await client.connect(transport, { timeout: 15000 });
      this.client = client;
      return client;
    } catch {
      await client.close().catch(() => {});
      throw new Error("emforge MCP startup failed; check Python, emforge[mcp] and the connection file");
    }
  }

  async list() {
    const client = await this.connect();
    const { tools } = await client.listTools();
    const accepted = tools.filter(tool => NAMES.has(tool.name));
    if (accepted.length !== NAMES.size) throw new Error("Incompatible emforge MCP tool catalog; update the local proxy");
    return accepted;
  }

  async call(name, args, signal) {
    signal?.throwIfAborted();
    if (!NAMES.has(name)) throw new Error("Unknown emforge tool");
    const client = await this.connect();
    const result = CallToolResultSchema.parse(await client.callTool(
      { name, arguments: args }, CallToolResultSchema, { signal, timeout: 45000 }));
    if (result.isError) {
      const message = result.content.filter(c => c.type === "text").map(c => c.text).join("\n");
      throw new Error(message || "emforge tool failed");
    }
    return result;
  }

  async close() {
    if (this.connecting) await this.connecting.catch(() => {});
    const client = this.client;
    this.client = null;
    await client?.close();
  }
}
