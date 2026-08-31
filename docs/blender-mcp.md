# Blender MCP integration

PolyKit can expose the official Blender MCP server to the embedded Agent. The
project-level `.mcp.json` entry is intentionally lazy: it starts the server
only when a session uses a Blender tool, and points the official add-on at the
Blender instance on the EasyTier peer `my4060` (`10.144.144.2:9876`).

The connection has two hops:

```text
Agent → uvx → official blender-mcp (stdio) → EasyTier TCP → Blender add-on (9876)
```

The MCP package is constrained to `mcp<2` because the pinned official server
revision still imports the MCP 1.x `FastMCP` API. The `uvx` cache keeps the
server outside the PolyKit repository; no Blender MCP source is copied into the
product.

## Blender-side setup

In Blender on `my4060`, enable the official Blender MCP add-on and start its
socket server. Its host must be reachable through EasyTier (not only
`localhost`); port `9876` is the default. The server should accept connections
from `10.144.144.1`.

## Verification

From the PolyKit host:

```bash
nc -vz 10.144.144.2 9876
curl http://127.0.0.1:8765/agent/mcp
```

The first command checks the raw Blender add-on socket. The second confirms
that the embedded Agent sees a `blender` MCP server. The actual Blender tools
are started lazily by the Agent, so opening the configuration does not modify
the Blender scene.

## Boundary

Blender MCP is an interactive authoring and rendering tool for the Agent. The
PolyKit FastAPI service remains the owner of workflow runs, job state, and
workspace artifacts. When a Blender result becomes a product asset, it should
be saved or copied into the server-owned workspace and then registered through
the normal workflow/asset APIs; the browser does not connect to Blender
directly.

For a complete runnable example, see
[`blender-scene-workflow.md`](blender-scene-workflow.md). It uses the same MCP
bridge from a FastAPI process node and publishes a GLB, Blender file, and PNG
preview into the workspace.
