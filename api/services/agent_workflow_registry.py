"""Registry for built-in Agent workflow definitions."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from schemas.agent_workflow import AgentWorkflowDefinition


class AgentWorkflowDefinitionError(ValueError):
    pass


_BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "resources" / "agent_workflows"


@lru_cache(maxsize=1)
def _definitions() -> dict[str, AgentWorkflowDefinition]:
    result: dict[str, AgentWorkflowDefinition] = {}
    if not _BUILTIN_ROOT.is_dir():
        return result
    for path in sorted(_BUILTIN_ROOT.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            definition = AgentWorkflowDefinition.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise AgentWorkflowDefinitionError(f"Invalid Agent workflow definition {path.name}: {exc}") from exc
        if definition.id in result:
            raise AgentWorkflowDefinitionError(f"Duplicate Agent workflow id: {definition.id}")
        result[definition.id] = definition
    return result


def list_agent_workflows() -> list[AgentWorkflowDefinition]:
    return list(_definitions().values())


def get_agent_workflow(workflow_id: str) -> AgentWorkflowDefinition:
    key = str(workflow_id or "").strip()
    definition = _definitions().get(key)
    if definition is None:
        raise AgentWorkflowDefinitionError(f"Unknown Agent workflow: {key!r}")
    return definition


def clear_agent_workflow_cache() -> None:
    _definitions.cache_clear()


__all__ = [
    "AgentWorkflowDefinitionError",
    "clear_agent_workflow_cache",
    "get_agent_workflow",
    "list_agent_workflows",
]
