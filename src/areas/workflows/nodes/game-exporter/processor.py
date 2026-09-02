"""Engine import bundles for game-runtime handoff.

The bundle is deliberately an interchange package, not a native Unity
``.unitypackage`` or Unreal ``.uasset``.  Those binary assets are created by
the target editor and cannot be truthfully produced without that editor.  The
processor therefore emits a copy of the input mesh as its primary output and
publishes a zip + JSON manifest as sidecars for a deterministic handoff.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+", "_", value)
    return value.strip("_").lower()[:48] or "asset"


def _safe_folder(value: Any, default: str, root: str) -> str:
    raw = str(value or default).replace("\\", "/").strip().strip("/")
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or parts[0] != root:
        return default
    return "/".join(parts)


def _stable_guid(data: bytes, target: str) -> str:
    # Unity GUIDs are 32 lower-case hexadecimal characters.  Deriving it from
    # the source bytes makes repeated exports merge cleanly in source control.
    return hashlib.sha256(target.encode("ascii") + b"\0" + data).hexdigest()[:32]


def _zip_write(zf: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    zf.writestr(info, payload)


def _unity_meta(guid: str, extension: str) -> str:
    # These settings are intentionally conservative; the Unity editor may
    # update them when the project's serialization version differs.
    return "\n".join([
        "fileFormatVersion: 2",
        f"guid: {guid}",
        "ModelImporter:",
        "  serializedVersion: 22200",
        "  fileIDToRecycleName: {}",
        "  meshCompression: 0",
        "  isReadable: 0",
        "  optimizeMeshForGPU: 1",
        "  keepQuads: 0",
        "  weldVertices: 1",
        "  importBlendShapes: 1",
        "  swapUVChannels: 0",
        "  generateSecondaryUV: 0",
        "  useFileUnits: 1",
        "  importVisibility: 1",
        "  importCameras: 1",
        "  importLights: 1",
        "  preserveHierarchy: 0",
        "  sortHierarchyByName: 1",
        "  animationType: 0",
        "  humanoidOversampling: 1",
        "  avatarSetup: 0",
        "  addHumanoidExtraRootOnlyWhenUsingAvatar: 0",
        "  importAnimation: 1",
        "  bakeIK: 0",
        "  resampleCurves: 1",
        "  legacyGenerateAnimations: 4",
        "  motionNodeName: 0",
        "  rigImportErrors: []",
        "  rigImportWarnings: []",
        "  animationRetargetingWarnings: []",
        "  animationDoRetargetingWarnings: 0",
        "  importConstraints: 0",
        "  importBlendShapeNormals: 0",
        "  animationCompression: 1",
        "  animationRotationError: 0.5",
        "  animationPositionError: 0.5",
        "  animationScaleError: 0.5",
        "  materials:",
        "    materialImportMode: 1",
        "    materialLocation: 1",
        "    materialName: 0",
        "    materialSearch: 1",
        "    materialSearchStartsWith: 1",
        "    materialSearchByName: 1",
        "    materialSearchByDistance: 1",
        "    materialLocation: 1",
        "  meshes:",
        "    lODScreenPercentages: []",
        f"  sourceExtension: .{extension}",
        "  userData: PolyKit import bundle; review scale, rig, and materials in the target project.",
        "  assetBundleName: ",
        "  assetBundleVariant: ",
        "",
    ])


def _readme(target: str, source_name: str, native: bool) -> str:
    if target == "unity":
        importer = "Unity's built-in ModelImporter can read this format." if native else "Install a glTF importer package before importing this file; Unity does not read glTF natively."
        return "\n".join([
            "# PolyKit Unity import bundle",
            "",
            f"Source asset: `{source_name}`",
            "",
            importer,
            "The `.meta` file is included for stable project identity. Unity creates the final imported asset in the target project's Library/ database; this zip is not a native `.unitypackage`.",
            "Review scale, materials, rig type, and generated colliders after import.",
            "",
        ])
    return "\n".join([
        "# PolyKit Unreal import bundle",
        "",
        f"Source asset: `{source_name}`",
        "",
        "Copy the asset into the project Content folder or extract this bundle, then import it from the Unreal Editor.",
        "The JSON manifest records the intended import settings; Unreal creates the final `.uasset` and cooked data in the target project.",
        "Review scale, normals, materials, skeleton assignment, and collision after import.",
        "",
    ])


def _build_bundle(
    input_path: Path,
    workspace_dir: Path,
    target: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    source_bytes = input_path.read_bytes()
    source_name = _slug(input_path.stem) + (input_path.suffix.lower() or ".mesh")
    extension = input_path.suffix.lower().lstrip(".") or "mesh"
    native_unity = extension in {"fbx", "obj", "dae", "3ds"}
    if target == "unity":
        folder = _safe_folder(params.get("asset_folder"), "Assets/PolyKit", "Assets")
        archive_tag = "unity-import"
        asset_path = f"{folder}/{source_name}"
        meta_path = f"{asset_path}.meta"
        guid = _stable_guid(source_bytes, target)
        manifest = {
            "schemaVersion": 1,
            "kind": "polykit.unity-import-bundle",
            "target": "unity",
            "nativeAsset": False,
            "source": {"name": input_path.name, "format": extension, "bytes": len(source_bytes), "sha256": hashlib.sha256(source_bytes).hexdigest()},
            "bundlePath": asset_path,
            "metaPath": meta_path,
            "guid": guid,
            "importer": "Unity ModelImporter" if native_unity else "third-party glTF importer required",
            "limitations": ["The target Unity editor creates the final imported asset and Library database entry."],
        }
        files = {asset_path: source_bytes, meta_path: _unity_meta(guid, extension).encode("utf-8")}
    elif target == "unreal":
        folder = _safe_folder(params.get("content_folder"), "Content/PolyKit", "Content")
        archive_tag = "unreal-import"
        asset_path = f"{folder}/{source_name}"
        manifest = {
            "schemaVersion": 1,
            "kind": "polykit.unreal-import-bundle",
            "target": "unreal",
            "nativeAsset": False,
            "source": {"name": input_path.name, "format": extension, "bytes": len(source_bytes), "sha256": hashlib.sha256(source_bytes).hexdigest()},
            "bundlePath": asset_path,
            "importSettings": {
                "combineMeshes": True,
                "importMorphTargets": True,
                "normalImportMethod": "ImportNormalsAndTangents",
                "materialSearchLocation": "Local",
                "autoGenerateCollision": bool(params.get("auto_generate_collision", False)),
            },
            "limitations": ["The target Unreal Editor creates the final .uasset and cooked data."],
        }
        files = {asset_path: source_bytes}
    else:
        raise ValueError(f"unsupported engine target: {target}")

    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    files[f"{folder}/PolyKitImportManifest.json"] = manifest_bytes
    if bool(params.get("include_readme", True)):
        files[f"{folder}/README.md"] = _readme(target, source_name, native_unity).encode("utf-8")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    primary = workspace_dir / f"{token}_{archive_tag}{input_path.suffix.lower() or '.glb'}"
    archive = workspace_dir / f"{token}_{archive_tag}.zip"
    report_path = workspace_dir / f"{token}_{archive_tag}.json"
    shutil.copy2(input_path, primary)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            _zip_write(zf, name, files[name])
    manifest["archive"] = archive.name
    manifest["primaryMesh"] = primary.name
    report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(primary),
        "sidecars": [str(archive), str(report_path)],
        "metadata": {
            "evidence_kind": f"{target}-import-bundle",
            "schema_version": 1,
            "target": target,
            "archive": archive.name,
            "report": report_path.name,
            "source_format": extension,
            "native_editor_asset": False,
        },
    }


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    input_raw = input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    workspace_dir = Path(str(data.get("workspaceDir") or ""))
    node_id = str(params.get("_node_id") or "")
    target = "unity" if node_id == "unity-import-bundle" else "unreal" if node_id == "unreal-import-bundle" else ""
    if input_path is None or not input_path.is_file():
        error(f"game-exporter: input mesh not found: {input_raw}")
        return
    if not target:
        error(f"game-exporter: unsupported node '{node_id}'")
        return
    try:
        progress(10, "Reading source mesh…")
        progress(45, f"Building {target.title()} import manifest…")
        result = _build_bundle(input_path, workspace_dir, target, params)
        progress(90, "Writing import bundle sidecars…")
        progress(100, "Import bundle ready")
        emit({"type": "done", "result": result})
    except Exception as exc:
        error(f"game-exporter: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"game-exporter: {exc}")
