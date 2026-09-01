# Semantic scene planning

PolyKit now has a small server-side compiler inspired by EmbodiedGen's
`layout` and `spatial-computing` skills. It is intentionally additive: model
generation still runs through `/workflow-runs/*`, while the compiler owns only
the semantic plan and deterministic transforms.

## Flow

```text
Agent / MCP
  -> ScenePlan JSON
  -> POST /workspace-library/worlds/{world_id}/scene-plan
  -> relation validation + optional asset lookup + deterministic layout
  -> WorldDocument.scene_plan + WorldDocument.instances
```

## Agent translation contract

自然语言任务不要直接写成文件名或坐标。Agent 应先做两步翻译，再调用
`polykit_world_compile_scene`：

1. **对象拆解**：提取一个 room/background、承载物（context）、关键物体
   （hero/manipulated）和少量环境物（distractor）。每个对象分配稳定的英文
   `id`，把中文名、同义词放进 `name`/`aliases`，并给出米制 `size`。
2. **关系树**：只输出有意义的关系边。房间和地面用 `floor`/`in_room`，表面用
   `on`，容器用 `inside`，相对位置用 `near`/`beside`/`away_from`/`overlooking`。
   不要让同一个对象拥有互相矛盾的支撑关系；不要把对象名当作文件路径。

这对应 EmbodiedGen `LayoutDesigner` 的 disassemble + hierarchy 两阶段，但
LLM 决策留在现有 Agent，服务端只负责校验、检索、布局和持久化。若对象没有高
置信度的本地资产，Agent 应调用 `polykit_generate_text_asset`，等待完成后再把
相对路径写回对象的 `asset.workspacePath`。

Create a world first, then compile a plan:

```json
{
  "plan": {
    "sceneKind": "indoor",
    "prompt": "A small cabin with a stove and a chair",
    "seed": 23,
    "bounds": {"width": 8, "depth": 8, "height": 3},
    "objects": [
      {"id": "room", "name": "Cabin room", "role": "room", "size": [6, 3, 6]},
      {"id": "stove", "name": "Wood stove", "role": "hero", "size": [1, 1.5, 1]}
    ],
    "relations": [
      {"subject": "stove", "type": "floor", "object": "room"}
    ]
  },
  "solve": true,
  "resolve_assets": false
}
```

The compiler supports `floor`, `on`, `inside`, `in_room`, `near`, `beside`,
`away_from`, and `overlooking`. Support relations establish a contact plane or
containing volume; spatial relations are then solved relative to their target.
Relations may include `distance`, `tolerance`, `clearance`, and a cardinal
`side`. It uses object dimensions, a seeded placement order, and 2D footprint
checks. It is not a physics engine or a navmesh generator.

Every solved plan receives `metadata.layoutQuality`. The server audits scene
bounds, support contact, containment, all declared spatial relations, and
pairwise footprint overlap. The audit is camera-independent: a plan does not
pass merely because a single preview angle hides an intersection. A mesh-aware
backend can add tighter hull/collider checks later while keeping the same
ScenePlan contract.

ScenePlan uses the right-handed Y-up coordinates shared by Three.js and glTF:
`position` is `[x, ground_y, z]`, with the second component as vertical height.
Instance rotations use Three.js/ScenePlan XYZ Euler radians. Instance positions
are ground/contact points; object sizes use the same scene units as the asset
workflow. The composition node fits each source mesh to its semantic size,
centres it in X/Z, and keeps its lowest vertex at the contact point.
The composition node accepts Blender-Z-up placement vectors only when its
`coordinate_system` parameter is explicitly set to `Blender-Z-up`; the default
is `glTF-Y-up`. This prevents a Blender viewport vector from silently placing
parts underground in the exported scene.

When `resolve_assets` is enabled, the server searches `Workflows/` for mesh
assets using names, aliases, categories, and optional `*.asset.json` sidecars.
Low-confidence matches are left unresolved so the Agent can call a local
generation workflow instead.  For an unresolved prop or set-dressing object,
the caller may then use the read-only `polykit_asset_search_external` fallback
against Poly Haven's public API.  Import is an explicit second step with
`polykit_asset_import_external`; the server downloads and verifies the selected
glTF bundle, publishes a workspace GLB, and keeps the provider/license
provenance in an asset sidecar.  External candidates are not mixed into the
local library index, and provider calls never run in the browser.

Poly Haven assets are CC0.  Live API use must retain clear Poly Haven
attribution and a unique User-Agent; PolyKit records both on imported assets.

The same operations are exposed through MCP:

- `polykit_world_compile_scene`
- `polykit_world_find_assets`
- `polykit_asset_search_external` (read-only Poly Haven fallback)
- `polykit_asset_import_external` (explicit Poly Haven download/import)
- `polykit_world_compose_scene`

For an unresolved object, the reference project's asset-creator chain is
available as one canonical workflow submission:

```text
POST /workflow-runs/text-to-asset
  text → anima/generate → image-background-remover → trellis2/generate
      → trellis2/refine (optional) → mesh-optimizer (optional) → polykit.output
```

The matching MCP tool is `polykit_generate_text_asset`. It returns the normal
workflow `run_id`; polling and workspace publication use the existing
`/workflow-runs/{run_id}` contract.

After object assets are resolved, `polykit_world_compose_scene` (or
`POST /workspace-library/worlds/{world_id}/compose`) compiles the plan into a
single `scene-composer/compose` batch node. The resulting GLB is published by
the normal `polykit.output` sink, so the asset library and Three.js viewer read
the same workspace artifact. Missing assets fail by default; callers may opt
into `allow_missing` for an intentional partial preview.

The existing world, workflow, asset, and renderer contracts remain unchanged.
