import re
from pathlib import Path


_UNSAFE_COLLECTION = re.compile(r'[/:*?"<>|\\]')


def normalize_collection(value: str) -> str:
    """Keep generated outputs inside one safe workspace collection."""
    collection = str(value or "").strip()
    if (
        not collection
        or collection in {".", ".."}
        or len(collection) > 80
        or _UNSAFE_COLLECTION.search(collection)
    ):
        return "Default"
    return collection


def resolve_workspace_path(workspace_dir: Path, raw_path: str) -> Path:
    """Resolve a workspace-relative path without allowing traversal.

    The API exposes workspace files to a browser and to the CLI.  Never use a
    user-provided path with ``workspace_dir / raw_path`` directly: ``..`` and
    symlinks can escape the workspace root.
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("workspace path must be a non-empty relative path")

    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError("workspace path must be relative")

    root = workspace_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("workspace path escapes the workspace directory") from exc
    return resolved
