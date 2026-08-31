# Agent Skills

PolyKit supports a read-only catalog of bundled [Agent Skills](https://agentskills.io/specification) without making Skill files part of the execution runtime.

## Layering

```text
Agent / Skill
    ↓ procedural policy
ProductionRecipe
    ↓ compile only
WorkflowDefinition
    ↓ explicit execution
WorkflowRun
    ↓
Node Packs
    ↓
Blender / models / processors
```

A Skill answers **how an Agent should approach a class of task**. A WorkflowRun remains the only durable product execution lifecycle.

## Bundled format

Reviewed skills live under:

```text
skills/<skill-name>/
├── SKILL.md
├── scripts/       # optional, read-only to PolyKit v1
├── references/    # optional
└── assets/        # optional
```

`SKILL.md` uses Agent Skills YAML frontmatter followed by Markdown instructions. PolyKit v1 supports the current standard fields `name`, `description`, `license`, `compatibility`, `metadata`, and `allowed-tools`. `name` must match the parent directory.

PolyKit intentionally treats `allowed-tools` as metadata only. `allowed_tools_authorized` is always `false`; the field never grants shell, Blender, MCP, WorkflowRun, or other execution permission.

## Progressive disclosure

`GET /agent-skills` returns metadata and resource listings without the Markdown instruction body. `GET /agent-skills/{name}` loads the full `SKILL.md` instructions only after a caller selects the skill.

Text resources can be read through `GET /agent-skills/{name}/resources/{path}` when they are under `scripts/`, `references/`, or `assets/`. Reads are workspace-independent, path-confined, UTF-8 only, and size-bounded. Script resources are returned as text and are never executed by this API.

## Trust boundary

v1 discovers only bundled, repository-reviewed skills (or a development override through `POLYKIT_BUNDLED_SKILLS_DIR`). It does not install arbitrary third-party Skill directories.

This restriction is deliberate. The Agent Skills format permits executable `scripts/`, so third-party installation needs an explicit origin, permission, review, and update model before PolyKit should expose it as installed content.

## Example

`skills/reference-reconstruction/SKILL.md` provides production guidance for reference-driven 3D reconstruction. It routes actual work through Worlds, validators, repair scopes, ProductionRecipes, and canonical WorkflowRuns instead of scripting Blender directly.

## Design rule

Do not add retry state, stage state, artifact ownership, or task progression to Skills. If a skill needs product work to happen, it should select or compile ordinary PolyKit operations and leave execution to the existing FastAPI control plane.
