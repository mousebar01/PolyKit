"""Read-only Agent Skills catalog compatible with the Agent Skills SKILL.md format.

PolyKit treats skills as procedural guidance for an Agent, not as an execution
runtime. Bundled skills may contain ``scripts/`` because the open Agent Skills
format permits them, but this module never executes those files and
``allowed-tools`` never grants permissions.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any


AGENT_SKILL_KIND = "agent-skill"
AGENT_SKILL_SCHEMA_VERSION = 1
SKILL_FILENAME = "SKILL.md"
_RESOURCE_DIRS = {"scripts", "references", "assets"}
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_TOP_LEVEL = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
_MAX_RESOURCE_BYTES = 256 * 1024
_MAX_FRONTMATTER_CHARS = 64 * 1024


class AgentSkillError(ValueError):
    """Raised when a bundled skill is invalid or cannot be read safely."""


def bundled_skills_dir() -> Path:
    """Return the reviewed, bundled skills directory.

    v1 intentionally discovers only bundled skills. Third-party installation is
    a separate trust-model problem because the Agent Skills format can include
    executable scripts.
    """

    env = os.environ.get("POLYKIT_BUNDLED_SKILLS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # api/services/agent_skills.py -> api/services -> api -> <repo>
    return (Path(__file__).resolve().parent.parent.parent / "skills").resolve()


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _unquote_scalar(value: str) -> str:
    value = _strip_inline_comment(value.strip())
    if not value:
        return ""
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        # YAML single-quoted strings escape apostrophes by doubling them.
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise AgentSkillError(f"Invalid quoted frontmatter scalar: {value}") from exc
        if not isinstance(parsed, str):
            raise AgentSkillError("Agent Skill frontmatter values must be strings")
        return parsed
    return value


def _consume_block(lines: list[str], start: int, base_indent: int, folded: bool) -> tuple[str, int]:
    values: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            values.append("")
            index += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= base_indent:
            break
        values.append(line[min(len(line), base_indent + 2):])
        index += 1
    if folded:
        paragraphs: list[str] = []
        current: list[str] = []
        for value in values:
            if value == "":
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
            else:
                current.append(value.strip())
        if current:
            paragraphs.append(" ".join(current))
        return "\n".join(paragraphs).strip(), index
    return "\n".join(values).rstrip("\n"), index


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith(" "):
            raise AgentSkillError("Unexpected indentation in Agent Skill frontmatter")
        if ":" not in raw:
            raise AgentSkillError(f"Invalid Agent Skill frontmatter line: {raw}")
        key, scalar = raw.split(":", 1)
        key = key.strip()
        scalar = scalar.strip()
        if key not in _ALLOWED_TOP_LEVEL:
            raise AgentSkillError(f"Unsupported Agent Skill frontmatter field: {key}")
        if key in result:
            raise AgentSkillError(f"Duplicate Agent Skill frontmatter field: {key}")

        if key == "metadata":
            if scalar:
                raise AgentSkillError("Agent Skill metadata must be a mapping")
            metadata: dict[str, str] = {}
            while index < len(lines):
                child = lines[index]
                if not child.strip():
                    index += 1
                    continue
                indent = len(child) - len(child.lstrip(" "))
                if indent == 0:
                    break
                if indent < 2 or ":" not in child.strip():
                    raise AgentSkillError("Invalid Agent Skill metadata mapping")
                child_key, child_value = child.strip().split(":", 1)
                child_key = child_key.strip()
                if not child_key or child_key in metadata:
                    raise AgentSkillError("Agent Skill metadata keys must be unique and non-empty")
                metadata[child_key] = _unquote_scalar(child_value)
                index += 1
            result[key] = metadata
            continue

        if scalar in {"|", ">"}:
            value, index = _consume_block(lines, index, 0, folded=scalar == ">")
            result[key] = value
        else:
            result[key] = _unquote_scalar(scalar)
    return result


def _split_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AgentSkillError("SKILL.md must start with YAML frontmatter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise AgentSkillError("SKILL.md frontmatter is missing its closing ---") from exc
    metadata = _parse_frontmatter(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return metadata, body


def _read_skill_frontmatter(skill_path: Path) -> dict[str, Any]:
    """Read only bounded YAML frontmatter and never the instruction body."""

    lines: list[str] = []
    total_chars = 0
    try:
        with skill_path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
            if not first or first.strip() != "---":
                raise AgentSkillError("SKILL.md must start with YAML frontmatter")
            for raw in handle:
                total_chars += len(raw)
                if total_chars > _MAX_FRONTMATTER_CHARS:
                    raise AgentSkillError("SKILL.md frontmatter is too large")
                line = raw.rstrip("\r\n")
                if line.strip() == "---":
                    return _parse_frontmatter(lines)
                lines.append(line)
    except UnicodeDecodeError as exc:
        raise AgentSkillError("SKILL.md frontmatter is not UTF-8 text") from exc
    raise AgentSkillError("SKILL.md frontmatter is missing its closing ---")


def _validate_metadata(skill_dir: Path, frontmatter: dict[str, Any]) -> dict[str, Any]:
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or len(name) > 64:
        raise AgentSkillError("Agent Skill name must be 1-64 lowercase letters, numbers, or hyphens")
    if name != skill_dir.name:
        raise AgentSkillError(f"Agent Skill name '{name}' must match directory '{skill_dir.name}'")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise AgentSkillError("Agent Skill description must be 1-1024 characters")

    license_value = frontmatter.get("license")
    if license_value is not None and not isinstance(license_value, str):
        raise AgentSkillError("Agent Skill license must be a string")
    compatibility = frontmatter.get("compatibility")
    if compatibility is not None and (not isinstance(compatibility, str) or not compatibility or len(compatibility) > 500):
        raise AgentSkillError("Agent Skill compatibility must be 1-500 characters")
    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()):
        raise AgentSkillError("Agent Skill metadata must map strings to strings")
    allowed_tools = frontmatter.get("allowed-tools")
    if allowed_tools is not None and not isinstance(allowed_tools, str):
        raise AgentSkillError("Agent Skill allowed-tools must be a string")

    return {
        "name": name,
        "description": description.strip(),
        "license": license_value or None,
        "compatibility": compatibility or None,
        "metadata": metadata,
        "allowed_tools": allowed_tools or None,
        # PolyKit never interprets the experimental field as an authorization grant.
        "allowed_tools_authorized": False,
    }


def _resource_entries(skill_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for dirname in sorted(_RESOURCE_DIRS):
        root = skill_dir / dirname
        if not root.is_dir():
            continue
        resolved_root = root.resolve()
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(resolved_root)
            except ValueError:
                # Do not surface symlinks that escape their declared resource directory.
                continue
            entries.append({
                "path": path.relative_to(skill_dir).as_posix(),
                "kind": dirname[:-1] if dirname.endswith("s") else dirname,
                "size": path.stat().st_size,
                "executable_by_polykit": False,
            })
    return entries


def _skill_result(skill_dir: Path, frontmatter: dict[str, Any]) -> dict[str, Any]:
    meta = _validate_metadata(skill_dir, frontmatter)
    return {
        "schema_version": AGENT_SKILL_SCHEMA_VERSION,
        "kind": AGENT_SKILL_KIND,
        **meta,
        "source": "bundled",
        "resources": _resource_entries(skill_dir),
    }


def _resolve_skill_dir(name: str, root: Path | None = None) -> tuple[Path, Path]:
    if not _NAME_RE.fullmatch(name or ""):
        raise AgentSkillError("Invalid Agent Skill name")
    resolved_root = (root or bundled_skills_dir()).resolve()
    skill_dir = (resolved_root / name).resolve()
    try:
        skill_dir.relative_to(resolved_root)
    except ValueError as exc:
        raise AgentSkillError("Invalid Agent Skill path") from exc
    if not skill_dir.is_dir():
        raise FileNotFoundError(name)
    return resolved_root, skill_dir


def _validated_skill_metadata(skill_dir: Path) -> dict[str, Any]:
    """Validate one Skill identity from frontmatter only."""

    skill_path = skill_dir / SKILL_FILENAME
    if not skill_path.is_file():
        raise AgentSkillError(f"Missing {SKILL_FILENAME} in {skill_dir.name}")
    return _validate_metadata(skill_dir, _read_skill_frontmatter(skill_path))


def load_agent_skill(skill_dir: Path, *, include_body: bool = True) -> dict[str, Any]:
    """Load and validate one skill directory."""

    skill_dir = skill_dir.resolve()
    skill_path = skill_dir / SKILL_FILENAME
    if not skill_path.is_file():
        raise AgentSkillError(f"Missing {SKILL_FILENAME} in {skill_dir.name}")
    text = skill_path.read_text(encoding="utf-8")
    frontmatter, body = _split_skill_markdown(text)
    result = _skill_result(skill_dir, frontmatter)
    if include_body:
        result["instructions"] = body
    return result


def list_agent_skills(root: Path | None = None) -> list[dict[str, Any]]:
    """Return lightweight metadata without reading Skill instruction bodies."""

    root = (root or bundled_skills_dir()).resolve()
    if not root.is_dir():
        return []
    skills: list[dict[str, Any]] = []
    for skill_dir in sorted(root.iterdir()):
        skill_path = skill_dir / SKILL_FILENAME
        if not skill_dir.is_dir() or not skill_path.is_file():
            continue
        skills.append(_skill_result(skill_dir.resolve(), _read_skill_frontmatter(skill_path)))
    return skills


def get_agent_skill(name: str, root: Path | None = None) -> dict[str, Any]:
    """Return full instructions for one bundled skill."""

    _, skill_dir = _resolve_skill_dir(name, root=root)
    return load_agent_skill(skill_dir, include_body=True)


def read_agent_skill_resource(
    name: str,
    resource_path: str,
    root: Path | None = None,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """Read a UTF-8 resource chunk after lightweight Skill identity validation.

    ``offset`` and ``limit`` are character counts. Omitting ``limit`` preserves
    the legacy full-resource API while Agent/MCP callers can request bounded
    chunks without reloading the Skill instruction body.
    """

    if offset < 0:
        raise AgentSkillError("Skill resource offset must be non-negative")
    if limit is not None and limit <= 0:
        raise AgentSkillError("Skill resource limit must be positive")

    _, skill_dir = _resolve_skill_dir(name, root=root)
    skill_meta = _validated_skill_metadata(skill_dir)
    relative = Path(resource_path)
    if relative.is_absolute() or not relative.parts or relative.parts[0] not in _RESOURCE_DIRS:
        raise AgentSkillError("Skill resources must live under scripts/, references/, or assets/")
    declared_root = (skill_dir / relative.parts[0]).resolve()
    target = (skill_dir / relative).resolve()
    try:
        target.relative_to(declared_root)
    except ValueError as exc:
        raise AgentSkillError("Skill resource escapes its declared resource directory") from exc
    if not target.is_file():
        raise FileNotFoundError(resource_path)
    size = target.stat().st_size
    if size > _MAX_RESOURCE_BYTES:
        raise AgentSkillError(f"Skill resource exceeds {_MAX_RESOURCE_BYTES} bytes")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AgentSkillError("Skill resource is not UTF-8 text") from exc

    total_chars = len(content)
    start = min(offset, total_chars)
    end = total_chars if limit is None else min(total_chars, start + limit)
    return {
        "schema_version": AGENT_SKILL_SCHEMA_VERSION,
        "kind": "agent-skill-resource",
        "skill": skill_meta["name"],
        "path": relative.as_posix(),
        "content": content[start:end],
        "offset": start,
        "next_offset": end,
        "total_chars": total_chars,
        "truncated": end < total_chars,
        "executable_by_polykit": False,
    }


__all__ = [
    "AGENT_SKILL_KIND",
    "AGENT_SKILL_SCHEMA_VERSION",
    "AgentSkillError",
    "bundled_skills_dir",
    "get_agent_skill",
    "list_agent_skills",
    "load_agent_skill",
    "read_agent_skill_resource",
]
