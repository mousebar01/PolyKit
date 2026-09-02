"""Normalize authored outdoor environment contracts without changing legacy worlds.

New World documents record the terrain compiler generation they were authored
for. When such a world first receives an environment, this module converts the
expressive planner vocabulary into the compact runtime contract consumed by the
existing browser and production terrain compilers.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


TERRAIN_COMPILER_VERSION = 2
TERRAIN_LANDFORMS = frozenset({
    "mountain",
    "hills",
    "plains",
    "dunes",
    "canyon",
    "volcanic",
    "mesa",
})
TERRAIN_SURFACES = frozenset({
    "rock",
    "grass",
    "sand",
    "snow",
    "forest",
    "swamp",
    "beach",
    "water",
})
TERRAIN_COVERAGES = frozenset({"world", "local"})


def _semantic_name(value: Any, *, label: str, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{label} must be one of: {choices}")
    return normalized


def _authoring_terrain_version(world: Mapping[str, Any]) -> int | None:
    authoring = world.get("authoring")
    if not isinstance(authoring, Mapping):
        return None
    value = authoring.get("terrain_version")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_world_environment_contract(world: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one new-generation World environment while preserving legacy worlds.

    Worlds without ``authoring.terrain_version`` are intentionally returned
    unchanged. For Terrain Compiler v2 worlds:

    * ``terrainVersion`` defaults to 2 on the environment;
    * optional ``region.landform`` selects the geometry profile and is compiled
      into the existing ``region.kind`` field;
    * optional ``region.surface`` remains semantic/material information and does
      not alter elevation;
    * a single authored region defaults to ``coverage=world`` so ordinary scenes
      are one complete terrain domain rather than a local mask plus background;
    * multiple authored regions default to ``coverage=local`` unless one is
      explicitly declared as the world/base region;
    * at most one region may use ``coverage=world``;
    * regions without either a compiled ``kind`` or explicit ``landform`` use
      the conservative ``plains`` geometry profile.

    This keeps ``kind`` as a compatibility/runtime field while allowing new
    planners to express combinations such as mountain + snow or hills + forest.
    Rivers remain the existing top-level terrain modifier contract.
    """

    result = dict(world)
    if _authoring_terrain_version(result) != TERRAIN_COMPILER_VERSION:
        return result

    runtime = result.get("runtime")
    if not isinstance(runtime, Mapping):
        return result
    build = runtime.get("build")
    if not isinstance(build, Mapping):
        return result
    environment = build.get("environment")
    if not isinstance(environment, Mapping):
        return result

    normalized_environment = dict(environment)
    raw_version = normalized_environment.get("terrainVersion", normalized_environment.get("terrain_version"))
    if raw_version is None:
        normalized_environment["terrainVersion"] = TERRAIN_COMPILER_VERSION
    else:
        if isinstance(raw_version, bool):
            raise ValueError("terrainVersion must be 2 for a Terrain Compiler v2 world")
        try:
            parsed_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("terrainVersion must be 2 for a Terrain Compiler v2 world") from exc
        if parsed_version != TERRAIN_COMPILER_VERSION:
            raise ValueError("terrainVersion must be 2 for a Terrain Compiler v2 world")
        normalized_environment["terrainVersion"] = TERRAIN_COMPILER_VERSION
        normalized_environment.pop("terrain_version", None)

    raw_regions = normalized_environment.get("regions")
    if isinstance(raw_regions, list):
        regions: list[Any] = []
        world_coverage_count = 0
        default_coverage = "world" if len(raw_regions) == 1 else "local"
        for index, raw_region in enumerate(raw_regions):
            if not isinstance(raw_region, Mapping):
                regions.append(raw_region)
                continue
            region = dict(raw_region)
            landform = _semantic_name(
                region.get("landform"),
                label=f"regions[{index}].landform",
                allowed=TERRAIN_LANDFORMS,
            )
            surface = _semantic_name(
                region.get("surface"),
                label=f"regions[{index}].surface",
                allowed=TERRAIN_SURFACES,
            )
            coverage = _semantic_name(
                region.get("coverage"),
                label=f"regions[{index}].coverage",
                allowed=TERRAIN_COVERAGES,
            ) or default_coverage
            region["coverage"] = coverage
            if coverage == "world":
                world_coverage_count += 1
            if landform is not None:
                region["landform"] = landform
                region["kind"] = landform
            elif not isinstance(region.get("kind"), str) or not str(region.get("kind")).strip():
                region["kind"] = "plains"
            if surface is not None:
                region["surface"] = surface
            regions.append(region)
        if world_coverage_count > 1:
            raise ValueError("regions may contain at most one coverage=world region")
        normalized_environment["regions"] = regions

    build_copy = dict(build)
    build_copy["environment"] = normalized_environment
    runtime_copy = dict(runtime)
    runtime_copy["build"] = build_copy
    result["runtime"] = runtime_copy
    return result


__all__ = [
    "TERRAIN_COMPILER_VERSION",
    "TERRAIN_COVERAGES",
    "TERRAIN_LANDFORMS",
    "TERRAIN_SURFACES",
    "normalize_world_environment_contract",
]
