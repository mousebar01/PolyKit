"""Read-only API for bundled Agent Skills."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.agent_skills import AgentSkillError, get_agent_skill, list_agent_skills, read_agent_skill_resource


router = APIRouter(prefix="/agent-skills", tags=["agent-skills"])


@router.get("")
async def list_skills():
    """Return lightweight skill metadata for progressive discovery."""

    try:
        return {
            "schema_version": 1,
            "skills": list_agent_skills(),
        }
    except (AgentSkillError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not load bundled Agent Skills: {exc}") from exc


@router.get("/{name}")
async def get_skill(name: str):
    """Load the full SKILL.md instructions only after a skill is selected."""

    try:
        return get_agent_skill(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent Skill was not found") from exc
    except AgentSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not load Agent Skill: {exc}") from exc


@router.get("/{name}/resources/{resource_path:path}")
async def read_skill_resource(name: str, resource_path: str):
    """Read one bounded UTF-8 resource; scripts are returned as text, never executed."""

    try:
        return read_agent_skill_resource(name, resource_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent Skill resource was not found") from exc
    except AgentSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Agent Skill resource: {exc}") from exc


__all__ = ["router"]
