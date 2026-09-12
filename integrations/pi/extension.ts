import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Bridge } from "./bridge.mjs";

/** Native Pi extension; schemas and business behavior come from emforge's MCP server. */
export default async function emforge(pi: ExtensionAPI) {
  const bridge = new Bridge();
  pi.on("session_shutdown", () => bridge.close());
  try {
    for (const tool of await bridge.list()) {
      pi.registerTool({
        name: `emforge_${tool.name}`,
        label: `emforge · ${tool.name}`,
        description: tool.description || tool.name,
        parameters: tool.inputSchema as ToolDefinition["parameters"],
        async execute(_id, params, signal) {
          const result = await bridge.call(tool.name, params, signal);
          return { content: result.content.filter(c => c.type === "text"), details: {} };
        },
      });
    }
  } catch (error) {
    await bridge.close();
    throw error;
  }
}
