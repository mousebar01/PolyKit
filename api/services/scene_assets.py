"""Semantic workspace-asset lookup for Agent-authored scene plans.

EmbodiedGen keeps an indexed asset catalogue instead of asking an LLM to
guess filenames.  PolyKit's workspace is the source of truth, so this module
provides a small deterministic index over existing GLB/GLTF and image files.
Optional ``*.asset.json`` sidecars add aliases and descriptions without
changing the binary asset contract.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from services.runtime_paths import runtime_paths
from services.scene_planner import SceneAssetRef, SceneObject, ScenePlan
from services.workspace_paths import resolve_workspace_path


_MESH_EXTENSIONS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".splat"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_SKIP_DIRS = {"tmp", "temp", "cache", "thumbnails", ".node-cache", ".artifacts"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(value.replace("_", " ").replace("-", " "))}


def _sidecar_candidates(path: Path) -> Iterable[Path]:
    yield path.with_name(f"{path.name}.asset.json")
    yield path.with_name(f"{path.stem}.asset.json")


def _read_sidecar(path: Path) -> dict[str, Any]:
    for candidate in _sidecar_candidates(path):
        try:
            if not candidate.is_file() or candidate.stat().st_size > 256 * 1024:
                continue
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, Mapping):
                return dict(value)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return {}


def _iter_assets(workspace: Path) -> Iterable[tuple[str, Path, dict[str, Any]]]:
    root = resolve_workspace_path(workspace, "Workflows")
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _MESH_EXTENSIONS | _IMAGE_EXTENSIONS:
            continue
        relative = path.relative_to(workspace).as_posix()
        if any(part.lower() in _SKIP_DIRS or part.startswith(".") for part in Path(relative).parts[:-1]):
            continue
        yield relative, path, _read_sidecar(path)


def _candidate_terms(workspace_path: str, metadata: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    stem = Path(workspace_path).stem
    names = {stem}
    aliases: set[str] = set()
    categories: set[str] = set()
    for key in ("name", "display_name", "displayName", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
    for key in ("aliases", "alias", "tags"):
        value = metadata.get(key)
        if isinstance(value, str):
            aliases.add(value.strip())
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, str, Mapping)):
            aliases.update(str(item).strip() for item in value if str(item).strip())
    for key in ("category", "categories"):
        value = metadata.get(key)
        if isinstance(value, str):
            categories.add(value.strip())
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, str, Mapping)):
            categories.update(str(item).strip() for item in value if str(item).strip())
    return names, aliases, categories


def find_asset_candidates(
    query: str,
    *,
    workspace: Path | None = None,
    category: str | None = None,
    limit: int = 5,
    meshes_only: bool = True,
) -> list[dict[str, Any]]:
    """Return deterministic semantic matches from the workspace library."""

    text = str(query or "").strip()
    if not text:
        return []
    root = workspace or runtime_paths.workspace
    query_tokens = _tokens(text)
    category_tokens = _tokens(category or "")
    results: list[dict[str, Any]] = []
    for workspace_path, path, metadata in _iter_assets(root):
        if meshes_only and path.suffix.lower() not in _MESH_EXTENSIONS:
            continue
        names, aliases, categories = _candidate_terms(workspace_path, metadata)
        name_tokens = set().union(*(_tokens(value) for value in names)) if names else set()
        alias_tokens = set().union(*(_tokens(value) for value in aliases)) if aliases else set()
        category_values = set().union(*(_tokens(value) for value in categories)) if categories else set()
        description = metadata.get("description", "")
        description_tokens = _tokens(description) if isinstance(description, str) else set()
        score = 0.0
        score += len(query_tokens & alias_tokens) * 5.0
        score += len(query_tokens & name_tokens) * 3.0
        score += len(query_tokens & category_values) * 2.0
        score += len(query_tokens & description_tokens) * 1.0
        if category_tokens:
            score += len(category_tokens & category_values) * 4.0
        if path.stem.lower() in {text.lower(), *(alias.lower() for alias in aliases)}:
            score += 8.0
        if score <= 0:
            continue
        asset_id = str(metadata.get("asset_id") or metadata.get("assetId") or f"library:{workspace_path}")
        display_name = str(metadata.get("name") or metadata.get("display_name") or path.stem)
        results.append({
            "asset_id": asset_id,
            "workspace_path": workspace_path,
            "display_name": display_name,
            "score": round(score, 3),
            "category": metadata.get("category"),
            "aliases": sorted(aliases),
            "source": metadata.get("source", "workspace-library"),
        })
    results.sort(key=lambda item: (-float(item["score"]), str(item["workspace_path"])))
    return results[: max(1, min(int(limit), 50))]


def resolve_scene_assets(plan: ScenePlan, *, workspace: Path | None = None, min_score: float = 3.0) -> ScenePlan:
    """Attach only high-confidence workspace matches to objects without assets."""

    objects: list[SceneObject] = []
    diagnostics = [item for item in plan.diagnostics if item.get("code") != "asset-resolution"]
    for obj in plan.objects:
        if obj.asset and (obj.asset.workspace_path or obj.asset.asset_id):
            objects.append(obj)
            continue
        candidates = find_asset_candidates(
            " ".join([obj.name, *obj.aliases, obj.category or ""]),
            workspace=workspace,
            category=obj.category,
            limit=1,
            meshes_only=True,
        )
        best = candidates[0] if candidates else None
        if best and float(best["score"]) >= min_score:
            objects.append(obj.model_copy(update={
                "asset": SceneAssetRef(
                    assetId=best["asset_id"],
                    workspacePath=best["workspace_path"],
                    source="workspace-library",
                )
            }))
            diagnostics.append({
                "code": "asset-resolution",
                "severity": "info",
                "object_id": obj.id,
                "asset_id": best["asset_id"],
                "workspace_path": best["workspace_path"],
                "score": best["score"],
            })
        else:
            objects.append(obj)
            diagnostics.append({
                "code": "asset-resolution",
                "severity": "info",
                "object_id": obj.id,
                "message": "No high-confidence workspace asset found; generation may be required.",
            })
    return plan.model_copy(update={"objects": objects, "diagnostics": diagnostics})


_GENERATION_ROLES = frozenset({"hero", "manipulated"})
_PROCEDURAL_ROLES = frozenset({"room", "background"})


def resolve_scene_asset_slots(
    plan: ScenePlan,
    *,
    workspace: Path | None = None,
    min_score: float = 3.0,
    include_context: bool = False,
) -> tuple[ScenePlan, list[dict[str, Any]]]:
    """Resolve scene objects through the product asset policy.

    Resolution order is intentionally conservative:
    existing binding -> procedural structure -> workspace library -> local generation.
    Context objects generate only when explicitly requested by the caller.
    """

    resolved = resolve_scene_assets(plan, workspace=workspace, min_score=min_score)
    decisions: list[dict[str, Any]] = []
    for obj in resolved.objects:
        asset = obj.asset
        if asset and asset.workspace_path:
            decisions.append({
                "object_id": obj.id,
                "mode": "library" if asset.source == "workspace-library" else "existing",
                "workspace_path": asset.workspace_path,
                "source": asset.source,
            })
            continue

        constraints = obj.constraints if isinstance(obj.constraints, dict) else {}
        policy = str(
            constraints.get("assetPolicy")
            or constraints.get("asset_policy")
            or ""
        ).strip().lower()
        procedural_hint = constraints.get("proceduralHint") or constraints.get("procedural_hint")

        if policy == "procedural" or procedural_hint or obj.role in _PROCEDURAL_ROLES:
            decisions.append({
                "object_id": obj.id,
                "mode": "procedural",
                **({"procedural_hint": str(procedural_hint)} if procedural_hint else {}),
            })
            continue

        wants_generation = (
            policy == "generate"
            or obj.role in _GENERATION_ROLES
            or (include_context and obj.role == "context")
        )
        if policy in {"library", "existing"}:
            wants_generation = False

        if wants_generation:
            decisions.append({
                "object_id": obj.id,
                "mode": "generate",
                "prompt": " ".join(
                    part for part in (obj.name, obj.description, obj.category or "") if str(part).strip()
                ).strip(),
                "size": list(obj.size),
            })
        else:
            decisions.append({
                "object_id": obj.id,
                "mode": "unresolved",
                "reason": "No matching workspace asset; generation is not enabled for this object role.",
            })
    return resolved, decisions


__all__ = ["find_asset_candidates", "resolve_scene_assets", "resolve_scene_asset_slots"]
