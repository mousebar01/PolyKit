# Blender MCP integration

PolyKit keeps Blender MCP as an **independent development/authoring integration**.
It is not part of the product control plane and is not required by the Web UI,
FastAPI World API, CLI, or WorkflowRun lifecycle.

The project-level `.mcp.json` currently contains only the Blender server entry:

```json
{
  "mcpServers": {
    "blender": {
      "command": "uvx",
      "args": ["blender-mcp"]
    }
  }
}
```

A compatible external MCP client may use that configuration when interactive
Blender authoring is useful. PolyKit itself does not start an embedded chat
runtime or expose a PolyKit MCP server.

## Product boundary

```text
Web / CLI / automation
        ↓ HTTP
      FastAPI
        ↓
Workflow Engine / Node Packs
        ↓
workspace artifacts

external MCP client ── optional ──> Blender MCP ──> Blender
```

The two paths are intentionally separate. Blender MCP may help a developer or
external tool inspect or author a scene, but product execution and persistence
remain owned by FastAPI and WorkflowRun.

When a Blender result becomes a PolyKit asset, publish it into the server-owned
workspace through normal workflow or asset APIs. Browser code must not connect
directly to Blender or depend on an MCP session being alive.

## Workflow-backed Blender

For deterministic product generation, use the bundled `blender-scene/build`
process node and the canonical WorkflowRun path rather than relying on an
interactive MCP conversation. See
[`blender-scene-workflow.md`](blender-scene-workflow.md).
