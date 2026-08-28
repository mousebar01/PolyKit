"""Small, safe workspace asset library for the Web client.

The Web client exposes a safe list/read/open contract for files already owned
by the server.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

import time
import uuid
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from services.asset_names import output_name
from services.gltf_skin import has_skin_metadata
from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection, resolve_workspace_path

router = APIRouter(prefix="/workspace-library", tags=["workspace-library"])
_ROOTS = {"Workflows"}
_SKIP_DIRS = {"tmp", "temp", "cache", "thumbnails"}
# Sidecar files that belong to a mesh and should move/delete with it.
_SIDECAR_SUFFIXES = (".landmarks.v1.json", ".world.json", ".scene.json")
_TEXT_EXTENSIONS = {"json", "txt", "md"}
_MESH_EXTENSIONS = {"glb", "gltf", "obj", "stl", "ply", "splat"}
_MOTION_EXTENSIONS = {"bvh", "npz"}
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}


class LibraryRequest(BaseModel):
    workspacePath: str = Field(min_length=1)
    sourceWorkspacePath: Optional[str] = None


class LibraryDeleteRequest(BaseModel):
    workspacePaths: list[str] = Field(min_length=1, max_length=500)


class LibraryRenameRequest(BaseModel):
    workspacePath: str = Field(min_length=1)
    newName: str = Field(min_length=1)


def _safe_path(raw: str) -> tuple[str, Path]:
    normalized = str(raw or "").replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts or parts[0] not in _ROOTS or any(part == ".." for part in parts):
        raise ValueError("Workspace library path must stay under Workflows/")
    safe = "/".join(parts)
    return safe, resolve_workspace_path(runtime_paths.workspace, safe)


def _entry(workspace_path: str, path: Path) -> dict:
    extension = path.suffix.lower().lstrip(".")
    if workspace_path.endswith(".landmarks.v1.json"):
        capability, state, preview, openable, reason = "landmarks-sidecar", "ready", "text", False, "Landmark sidecars require opening their source mesh."
    elif workspace_path.endswith(".world.json"):
        capability, state, preview, openable, reason = "generated-world", "ready", "text", False, "Generated worlds are list-only in this release."
    elif workspace_path.endswith(".scene.json"):
        capability, state, preview, openable, reason = "scene-manifest", "ready", "text", False, "Scene manifests are list-only in this release."
    elif extension in _MOTION_EXTENSIONS:
        capability, state, preview, openable, reason = "animation-motion", "ready", "binary", False, "Motion files are list-only in this release."
    elif extension in {"glb", "gltf"}:
        capability = "rigged-mesh" if has_skin_metadata(path) else "mesh"
        state, preview, openable, reason = "ready", "3d-model", True, None
    elif extension in _IMAGE_EXTENSIONS:
        capability, state, preview, openable, reason = "image", "ready", "image", True, None
    elif extension in _MESH_EXTENSIONS:
        capability, state, preview, openable, reason = "mesh", "ready", "binary", False, f".{extension} workspace assets are list-only in this release."
    elif extension in _TEXT_EXTENSIONS:
        capability, state, preview, openable, reason = None, "unsupported", "text", False, "Unsupported workspace asset."
    else:
        capability, state, preview, openable, reason = None, "unsupported", "binary", False, "Unsupported workspace asset."

    stat = path.stat()
    entry = {
        "id": f"library:{workspace_path}",
        "workspacePath": workspace_path,
        "displayName": path.name,
        "state": state,
        "previewKind": preview,
        "warnings": [],
        "openable": openable,
        "createdAt": datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime)).isoformat(),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }
    if capability:
        entry["capability"] = capability
    if reason:
        entry["nonOpenableReason"] = reason
    if capability in {"mesh", "rigged-mesh"}:
        from services.asset_thumbnails import _LIBRARY_SIZE, _RENDER_VERSION
        entry["thumbnail"] = f"/workspace-library/thumbnail?path={workspace_path}&v={_RENDER_VERSION}&size={_LIBRARY_SIZE}"
        if extension in {"glb", "gltf"}:
            from services.asset_previews import _PREVIEW_VERSION
            entry["preview"] = f"/workspace-library/preview?path={workspace_path}&v={_PREVIEW_VERSION}"
    elif capability == "image":
        # Generated images are already workspace-owned artifacts. Reuse the
        # canonical workspace URL for both cards and the detail viewer rather
        # than copying them into a second thumbnail store.
        entry["thumbnail"] = f"/workspace/{workspace_path}"
        entry["preview"] = f"/workspace/{workspace_path}"
    return entry


def _migrate_legacy_exports() -> None:
    """Move any files left in the legacy ``Exports/`` root into ``Workflows/``.

    ``Exports`` used to be a separate workspace root (distinct from the
    generated ``Workflows`` artifacts). It was merged into ``Workflows``;
    this one-time migration keeps previously exported files visible.
    """
    workspace = runtime_paths.workspace
    exports_dir = workspace / "Exports"
    workflows_dir = workspace / "Workflows"
    if not exports_dir.is_dir():
        return
    try:
        for item in sorted(exports_dir.rglob("*")):
            if item.is_file():
                rel = item.relative_to(exports_dir)
                target = workflows_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    item.rename(target)
        # remove the now-empty legacy root
        for leftover in sorted(exports_dir.rglob("*"), reverse=True):
            if leftover.is_dir():
                try:
                    leftover.rmdir()
                except OSError:
                    pass
        try:
            exports_dir.rmdir()
        except OSError:
            pass
    except OSError as exc:
        print(f"[Library] legacy Exports migration skipped: {exc}")


def _walk(root: Path, relative_root: str) -> list[dict]:
    if not root.is_dir():
        return []
    entries: list[dict] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part.startswith(".") or part.lower() in _SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if path.name.startswith(".") or path.name.endswith((".artifact.json", ".rigmeta.json")):
            continue
        workspace_path = (Path(relative_root) / relative).as_posix()
        item = _entry(workspace_path, path)
        if item["state"] != "unsupported":
            entries.append(item)
    return entries


@router.get("/thumbnail")
async def asset_thumbnail(path: str, size: int = 256):
    """Render (or return a cached) static PNG hero view for a workspace mesh.

    The library grid loads this lazily; the first request renders a server-side
    snapshot and caches it next to the workspace.
    """
    import asyncio
    from fastapi.responses import FileResponse, Response

    try:
        workspace_path, mesh_path = _safe_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not mesh_path.is_file():
        raise HTTPException(404, "Workspace asset was not found.")

    px = max(64, min(512, int(size)))
    loop = asyncio.get_running_loop()
    thumb = await loop.run_in_executor(None, _render_thumbnail, workspace_path, mesh_path, px)
    if thumb is None:
        raise HTTPException(404, "Thumbnail could not be rendered for this asset.")
    # Short cache: the URL carries a render-version param, so version bumps
    # change the URL and bust the cache; within a version, revalidation is cheap.
    # Thumbnails are static front-facing PNG views (see asset_thumbnails).
    # The URL already carries source mtime/size and render version, so this
    # artifact is immutable for its lifetime; let the browser keep it warm.
    return FileResponse(thumb, media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"})


def _render_thumbnail(workspace_path: str, mesh_path: Path, px: int):
    from services.asset_thumbnails import ensure_thumbnail
    return ensure_thumbnail(workspace_path, mesh_path, px)


@router.get("/preview")
async def asset_preview(path: str, v: int = 1):
    """Return (or generate) a lightweight textured GLB for optional detail preview.

    This endpoint remains available for clients that need an interactive detail
    view; the asset library cards use the static front-facing PNG thumbnail.
    """
    import asyncio
    from fastapi.responses import FileResponse

    try:
        workspace_path, mesh_path = _safe_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not mesh_path.is_file():
        raise HTTPException(404, "Workspace asset was not found.")

    loop = asyncio.get_running_loop()
    glb = await loop.run_in_executor(None, _generate_preview, workspace_path, mesh_path)
    if glb is None:
        raise HTTPException(404, "Preview could not be generated for this asset.")
    # Short cache: the URL carries a preview-version param, so version bumps
    # change the URL and bust the cache; within a version, revalidation is cheap.
    return FileResponse(glb, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=300"})


def _generate_preview(workspace_path: str, mesh_path: Path):
    from services.asset_previews import ensure_preview
    return ensure_preview(workspace_path, mesh_path)


@router.get("/list")
async def list_library():
    try:
        _migrate_legacy_exports()
        entries = _walk(runtime_paths.workspace / "Workflows", "Workflows")
        entries = sorted(entries, key=lambda item: item["workspacePath"])
    except OSError as exc:
        return {"success": False, "error": {"code": "list-failed", "message": str(exc)}}

    # Cards use the static thumbnail URL. Do not prewarm interactive GLBs here:
    # listing the library should stay cheap and must not trigger hidden 3D work.
    return {"success": True, "entries": entries}


@router.post("/read")
async def read_library(request: LibraryRequest):
    try:
        workspace_path, path = _safe_path(request.workspacePath)
        if not path.is_file():
            return {"success": False, "error": {"code": "not-found", "message": "Workspace asset was not found."}}
        entry = _entry(workspace_path, path)
        extension = path.suffix.lower().lstrip(".")
        if entry["previewKind"] == "3d-model":
            preview = {"kind": "3d-model", "viewerKind": extension}
        elif entry["previewKind"] == "image":
            preview = {"kind": "image", "imageUrl": f"/workspace/{workspace_path}"}
        elif entry["previewKind"] == "text":
            content = path.read_text(encoding="utf-8", errors="replace")
            preview = {"kind": "text", "content": content[:65536], "byteLength": path.stat().st_size, "truncated": len(content) > 65536}
        elif entry["previewKind"] == "binary":
            preview = {"kind": "binary", "binaryKind": extension, "byteLength": path.stat().st_size, "message": "Binary preview is unavailable."}
        else:
            preview = {"kind": "none"}
        return {"success": True, "entry": entry, "preview": preview}
    except ValueError as exc:
        return {"success": False, "error": {"code": "unsafe-path", "message": str(exc)}}
    except OSError as exc:
        return {"success": False, "error": {"code": "read-failed", "message": str(exc)}}


@router.post("/open")
async def open_library(request: LibraryRequest):
    result = await read_library(request)
    if not result.get("success"):
        return result
    entry = result["entry"]
    if request.sourceWorkspacePath:
        try:
            source_path, source = _safe_path(request.sourceWorkspacePath)
            if source_path == entry["workspacePath"] or source.suffix.lower() not in {".glb", ".gltf"} or not source.is_file():
                raise ValueError("Linked source must be an existing .glb/.gltf workspace asset.")
        except ValueError as exc:
            return {"success": False, "error": {"code": "not-openable", "message": str(exc)}}
        return {"success": True, "entry": entry}
    if not entry["openable"]:
        return {"success": False, "error": {"code": "not-openable", "message": entry.get("nonOpenableReason", "Workspace asset is not openable.")}}
    return {"success": True, "entry": entry}


# ------------------------------------------------------------------ #
# Delete / rename
# ------------------------------------------------------------------ #

def _invalidate_asset_caches(workspace_path: str, path: Path) -> None:
    """Drop cached thumbnails/previews for a workspace mesh."""
    try:
        from services.asset_previews import invalidate as invalidate_preview
        from services.asset_thumbnails import invalidate as invalidate_thumbnail
        invalidate_thumbnail(workspace_path, path)
        invalidate_preview(workspace_path, path)
    except Exception:
        pass


@router.post("/delete")
async def delete_assets(request: LibraryDeleteRequest):
    """Delete one or more workspace assets (plus their sidecars).

    Only files under ``Workflows/`` are eligible; paths that fail the safety
    check are reported as ``rejected`` and never touched.
    """
    deleted: list[str] = []
    missing: list[str] = []
    rejected: list[str] = []
    for raw in request.workspacePaths:
        try:
            workspace_path, path = _safe_path(raw)
        except ValueError:
            rejected.append(raw)
            continue
        if not path.is_file():
            missing.append(raw)
            continue
        _invalidate_asset_caches(workspace_path, path)
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(500, f"Failed to delete {workspace_path}: {exc}") from exc
        # Remove sidecar metadata that belongs to this asset.
        for suffix in _SIDECAR_SUFFIXES:
            side = path.with_name(path.stem + suffix)
            try:
                if side.is_file():
                    side.unlink()
            except OSError:
                pass
        deleted.append(workspace_path)
    return {"success": True, "deleted": deleted, "missing": missing, "rejected": rejected}


@router.post("/rename")
async def rename_asset(request: LibraryRenameRequest):
    """Rename a workspace asset (same folder), moving its sidecars along."""
    try:
        workspace_path, path = _safe_path(request.workspacePath)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "Workspace asset was not found.")

    new_name = request.newName.strip()
    if (
        not new_name
        or new_name in {".", ".."}
        or "/" in new_name
        or "\\" in new_name
        or new_name.startswith(".")
    ):
        raise HTTPException(400, "New name must be a plain filename (no folders, no leading dot).")
    if len(new_name) > 120:
        raise HTTPException(400, "New name is too long.")
    if not Path(new_name).suffix:
        new_name += path.suffix  # keep the asset format if the user omitted the extension

    if new_name == path.name:
        return {"success": True, "workspacePath": workspace_path, "displayName": path.name}

    target = path.with_name(new_name)
    if target.exists():
        raise HTTPException(409, "A file with that name already exists.")

    _invalidate_asset_caches(workspace_path, path)
    try:
        path.rename(target)
    except OSError as exc:
        raise HTTPException(500, f"Failed to rename {workspace_path}: {exc}") from exc
    for suffix in _SIDECAR_SUFFIXES:
        side = path.with_name(path.stem + suffix)
        if side.is_file():
            try:
                side.rename(target.with_name(target.stem + suffix))
            except OSError:
                pass

    new_workspace_path = f"{workspace_path.rsplit('/', 1)[0]}/{new_name}"
    return {"success": True, "workspacePath": new_workspace_path, "displayName": new_name}


# ------------------------------------------------------------------ #
# Upload
# ------------------------------------------------------------------ #

_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAX_MESH_BYTES = 1 * 1024 * 1024 * 1024  # 1 GiB — real GLBs reach hundreds of MB
_ALLOWED_MESH_EXTS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".fbx"}


@router.post("/upload")
async def upload_asset(
    file: UploadFile = File(...),
    collection: str = Form("Workflows"),
):
    """Persist an uploaded image or mesh into the workspace.

    The workflow Image / Load 3D Mesh nodes upload the picked file here once,
    then reference the returned ``workspacePath`` — so a browser temp file that
    vanishes on reload can never break a saved workflow, and the run payload can
    reference the server-side file instead of embedding base64. This is what lets
    a *remote* headless backend run workflows whose sources were picked on a
    local machine.
    """
    suffix = Path(file.filename or "").suffix.lower()
    is_image = suffix in _ALLOWED_IMAGE_EXTS
    is_mesh = suffix in _ALLOWED_MESH_EXTS
    if not is_image and not is_mesh:
        # Fall back to content-type sniffing for mislabelled filenames.
        ct = (file.content_type or "").lower()
        is_image = ct.startswith("image/")
        is_mesh = ct.startswith("model/") or ct in {"application/octet-stream"}

    if not is_image and not is_mesh:
        raise HTTPException(400, "Only image and 3D mesh uploads are supported here.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty")

    if is_image:
        if len(data) > _MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image is larger than 50 MiB")
    elif len(data) > _MAX_MESH_BYTES:
        raise HTTPException(413, "Mesh is larger than 1 GiB")

    if not is_mesh and suffix not in _ALLOWED_IMAGE_EXTS:
        suffix = ".png"

    root = normalize_collection(collection or "Workflows")
    coll_dir = runtime_paths.workspace / root
    coll_dir.mkdir(parents=True, exist_ok=True)
    dest = coll_dir / output_name(Path(file.filename or "upload").stem, ext=suffix)
    dest.write_bytes(data)

    workspace_path = f"{root}/{dest.name}"
    if is_mesh:
        # Do not make the upload wait for Open3D/EGL.  The bounded prewarm pool
        # prepares the card while the caller continues with its workflow.
        try:
            from services.asset_thumbnails import _LIBRARY_SIZE, prewarm_thumbnail
            prewarm_thumbnail(workspace_path, dest, _LIBRARY_SIZE)
        except Exception as exc:
            print(f"[Thumbnails] upload prewarm could not be queued: {exc}")
    return {
        "success": True,
        "workspacePath": workspace_path,
        "url": f"/workspace/{workspace_path}",
    }
