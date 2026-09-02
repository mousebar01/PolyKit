#!/usr/bin/env python3
"""PolyKit automation CLI.

This is a thin stdlib-only HTTP client for the authoritative FastAPI control
plane. It intentionally contains no Agent runtime, planner, or workflow state
machine. Product logic stays in FastAPI services, Workflow Engine, validators,
and Node Packs.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

DEFAULT_API_URL = os.environ.get("POLYKIT_API_URL", "http://127.0.0.1:8765").rstrip("/")


class CliError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _api_json(
    api_url: str,
    method: str,
    path: str,
    payload: Any | None = None,
    *,
    timeout: float = 30.0,
) -> Any:
    body = _json_bytes(payload) if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{api_url}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CliError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CliError(f"Cannot connect to PolyKit API at {api_url}: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError("PolyKit API returned non-JSON data") from exc


def _multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----polykit-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _api_multipart(
    api_url: str,
    path: str,
    *,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    timeout: float = 30.0,
) -> Any:
    if not file_path.is_file():
        raise CliError(f"File not found: {file_path}")
    body, content_type = _multipart_body(fields, file_field, file_path)
    request = Request(
        f"{api_url}{path}",
        data=body,
        headers={"Accept": "application/json", "Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise CliError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise CliError(f"Cannot connect to PolyKit API at {api_url}: {exc.reason}") from exc
    return json.loads(raw or b"{}")


def _load_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid JSON in {path}: {exc}") from exc


def _dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False))


def _world_path(world_id: str) -> str:
    value = world_id.strip()
    if not value:
        raise CliError("world_id is required")
    return quote(value, safe="")


def cmd_health(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "GET", "/health")


def cmd_doctor(args: argparse.Namespace) -> Any:
    checks: dict[str, Any] = {}
    for name, path in (
        ("health", "/health/ready"),
        ("paths", "/settings/paths"),
        ("models", "/model/all"),
    ):
        try:
            checks[name] = {"ok": True, "value": _api_json(args.api_url, "GET", path)}
        except CliError as exc:
            checks[name] = {"ok": False, "error": str(exc)}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}


def cmd_model_list(args: argparse.Namespace) -> Any:
    models = _api_json(args.api_url, "GET", "/model/all")
    if args.downloaded:
        models = [item for item in models if isinstance(item, dict) and item.get("downloaded")]
    return models


def cmd_model_switch(args: argparse.Namespace) -> Any:
    query = urlencode({"model_id": args.model_id})
    return _api_json(args.api_url, "POST", f"/model/switch?{query}")


def cmd_model_unload(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", "/model/unload-all")


def cmd_workflow_list(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "GET", "/workflow-definitions")


def cmd_run_status(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "GET", f"/workflow-runs/{quote(args.run_id, safe='')}")


def cmd_run_inspect(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "GET", f"/workflow-runs/{quote(args.run_id, safe='')}/inspect")


def cmd_run_cancel(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", f"/workflow-runs/{quote(args.run_id, safe='')}/cancel")


def cmd_run_execute(args: argparse.Namespace) -> Any:
    payload = _load_json(args.file)
    return _api_json(args.api_url, "POST", "/workflow-runs/execute", payload, timeout=30.0)


def cmd_asset_from_image(args: argparse.Namespace) -> Any:
    fields = {
        "remesh": args.remesh,
        "collection": args.collection,
        "enable_texture": str(args.texture).lower(),
        "texture_resolution": str(args.texture_resolution),
    }
    optional = {
        "model_id": args.model_id,
        "workflow_id": args.workflow_id,
        "world_id": args.world_id,
        "proto_id": args.proto_id,
        "node_id": args.proto_id,
    }
    fields.update({key: value for key, value in optional.items() if value})
    if args.params:
        params = _load_json(args.params)
        if not isinstance(params, dict):
            raise CliError("--params must point to a JSON object")
        fields["params"] = json.dumps(params, ensure_ascii=False)
    return _api_multipart(
        args.api_url,
        "/workflow-runs/from-image",
        fields=fields,
        file_field="image",
        file_path=Path(args.image),
    )


def cmd_asset_from_text(args: argparse.Namespace) -> Any:
    payload = {
        "prompt": args.prompt,
        "image_model_id": args.image_model_id,
        "mesh_model_id": args.mesh_model_id,
        "enable_texture": not args.no_texture,
        "enable_optimize": not args.no_optimize,
        "target_faces": args.target_faces,
        "collection": args.collection,
        "workflow_id": args.workflow_id or None,
        "world_id": args.world_id or None,
        "proto_id": args.proto_id or None,
        "image_params": _load_json(args.image_params) if args.image_params else {},
        "mesh_params": _load_json(args.mesh_params) if args.mesh_params else {},
        "texture_params": _load_json(args.texture_params) if args.texture_params else {},
    }
    return _api_json(args.api_url, "POST", "/workflow-runs/text-to-asset", payload)


def cmd_image_generate(args: argparse.Namespace) -> Any:
    params = _load_json(args.params) if args.params else {}
    payload = {
        "schema_version": 1,
        "workflow_id": args.workflow_id or None,
        "prompt": {
            "text": {"class_type": "polykit.text", "inputs": {"text": args.prompt}},
            "image": {
                "class_type": args.model_id,
                "inputs": {"text": ["text", "text"], "params": params},
            },
            "output": {
                "class_type": "polykit.image_output",
                "inputs": {"image": ["image", "image"]},
            },
        },
        "output_node_id": "output",
        "collection": args.collection,
    }
    return _api_json(args.api_url, "POST", "/workflow-runs/execute", payload)


def cmd_image_remove_background(args: argparse.Namespace) -> Any:
    payload = {
        "schema_version": 1,
        "workflow_id": args.workflow_id or None,
        "prompt": {
            "image": {
                "class_type": "polykit.image",
                "inputs": {"image": {"kind": "workspace_path", "path": args.workspace_path}},
            },
            "cutout": {
                "class_type": "image-background-remover/remove-background",
                "inputs": {"image": ["image", "image"], "params": {"model": args.model}},
            },
            "output": {
                "class_type": "polykit.image_output",
                "inputs": {"image": ["cutout", "image"]},
            },
        },
        "output_node_id": "output",
        "collection": args.collection,
    }
    return _api_json(args.api_url, "POST", "/workflow-runs/execute", payload)


def cmd_mesh_decimate(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", "/optimize/mesh", {"path": args.path, "target_faces": args.target_faces})


def cmd_mesh_smooth(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", "/optimize/smooth", {"path": args.path, "iterations": args.iterations})


def cmd_mesh_import(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", "/optimize/import-by-path", {"path": args.path})


def cmd_asset_search_external(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", "/workspace-library/providers/polyhaven/search", {
        "query": args.query,
        "category": args.category or None,
        "limit": args.limit,
        "refresh": args.refresh,
    })


def cmd_asset_import_external(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "POST", "/workspace-library/providers/polyhaven/import", {
        "asset_id": args.asset_id,
        "resolution": args.resolution,
    }, timeout=180.0)


def cmd_settings_paths(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "GET", "/settings/paths")


def cmd_world_create(args: argparse.Namespace) -> Any:
    payload = {
        key: value
        for key, value in {
            "name": args.name,
            "prompt": args.prompt,
            "parent_world_id": args.parent_world_id,
        }.items()
        if value
    }
    return _api_json(args.api_url, "POST", "/workspace-library/worlds", payload)


def cmd_world_get(args: argparse.Namespace) -> Any:
    return _api_json(args.api_url, "GET", f"/workspace-library/worlds/{_world_path(args.world_id)}")


def cmd_world_save(args: argparse.Namespace) -> Any:
    document = _load_json(args.file)
    if not isinstance(document, dict):
        raise CliError("World file must contain a JSON object")
    if document.get("id") != args.world_id:
        raise CliError("World document id must exactly match world_id")
    return _api_json(
        args.api_url,
        "PUT",
        f"/workspace-library/worlds/{_world_path(args.world_id)}",
        document,
    )


def cmd_world_compile_scene(args: argparse.Namespace) -> Any:
    plan = _load_json(args.file)
    if not isinstance(plan, dict):
        raise CliError("Scene plan file must contain a JSON object")
    payload = {"plan": plan, "solve": not args.no_solve, "resolve_assets": args.resolve_assets}
    return _api_json(
        args.api_url,
        "POST",
        f"/workspace-library/worlds/{_world_path(args.world_id)}/scene-plan",
        payload,
    )


def cmd_world_find_assets(args: argparse.Namespace) -> Any:
    payload = {
        "query": args.query,
        "category": args.category or None,
        "limit": args.limit,
        "meshesOnly": True,
    }
    return _api_json(args.api_url, "POST", "/workspace-library/search", payload)


def cmd_world_compose(args: argparse.Namespace) -> Any:
    payload = {
        "collection": args.collection,
        "output_name": args.output_name,
        "allow_missing": args.allow_missing,
    }
    return _api_json(
        args.api_url,
        "POST",
        f"/workspace-library/worlds/{_world_path(args.world_id)}/compose",
        payload,
    )


def cmd_world_build_structure(args: argparse.Namespace) -> Any:
    payload = {
        "building_id": args.building_id or None,
        "collection": args.collection,
        "render_preview": not args.no_preview,
    }
    return _api_json(
        args.api_url,
        "POST",
        f"/workspace-library/worlds/{_world_path(args.world_id)}/build-structure",
        payload,
    )


def cmd_world_validate(args: argparse.Namespace) -> Any:
    return _api_json(
        args.api_url,
        "POST",
        f"/workspace-library/worlds/{_world_path(args.world_id)}/validate",
        {"capability": args.capability, "run_id": args.run_id or None},
    )


def cmd_world_attach_asset(args: argparse.Namespace) -> Any:
    payload = {
        "workspace_path": args.workspace_path,
        "workflow_id": args.workflow_id or None,
        "run_id": args.run_id or None,
        "concept_image": args.concept_image or None,
    }
    world_id = _world_path(args.world_id)
    proto_id = quote(args.proto_id, safe="")
    return _api_json(
        args.api_url,
        "POST",
        f"/workspace-library/worlds/{world_id}/artifacts/{proto_id}",
        payload,
    )


def _set_handler(parser: argparse.ArgumentParser, handler) -> None:
    parser.set_defaults(handler=handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polykit", description="PolyKit HTTP automation CLI")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="FastAPI base URL")
    sub = parser.add_subparsers(dest="command", required=True)

    _set_handler(sub.add_parser("health", help="Read server health"), cmd_health)
    _set_handler(sub.add_parser("doctor", help="Check server, paths, and model catalog"), cmd_doctor)

    model = sub.add_parser("model", help="Model runtime operations").add_subparsers(dest="model_command", required=True)
    model_list = model.add_parser("list")
    model_list.add_argument("--downloaded", action="store_true")
    _set_handler(model_list, cmd_model_list)
    model_switch = model.add_parser("switch")
    model_switch.add_argument("model_id")
    _set_handler(model_switch, cmd_model_switch)
    _set_handler(model.add_parser("unload"), cmd_model_unload)

    workflow = sub.add_parser("workflow", help="Editable workflow definitions").add_subparsers(dest="workflow_command", required=True)
    _set_handler(workflow.add_parser("list"), cmd_workflow_list)

    run = sub.add_parser("workflow-run", help="Canonical Workflow Run operations").add_subparsers(dest="run_command", required=True)
    for name, handler in (("status", cmd_run_status), ("inspect", cmd_run_inspect), ("cancel", cmd_run_cancel)):
        item = run.add_parser(name)
        item.add_argument("run_id")
        _set_handler(item, handler)
    execute = run.add_parser("execute", help="Submit a WorkflowExecutionRequest JSON file")
    execute.add_argument("file")
    _set_handler(execute, cmd_run_execute)

    asset = sub.add_parser("asset", help="Asset generation workflows").add_subparsers(dest="asset_command", required=True)
    from_image = asset.add_parser("from-image")
    from_image.add_argument("image")
    from_image.add_argument("--model-id", default="")
    from_image.add_argument("--remesh", choices=("quad", "triangle", "none"), default="quad")
    from_image.add_argument("--texture", action="store_true")
    from_image.add_argument("--texture-resolution", type=int, default=1024)
    from_image.add_argument("--collection", default="Workflows")
    from_image.add_argument("--workflow-id", default="")
    from_image.add_argument("--world-id", default="")
    from_image.add_argument("--proto-id", default="")
    from_image.add_argument("--params", help="JSON object file")
    _set_handler(from_image, cmd_asset_from_image)

    from_text = asset.add_parser("from-text")
    from_text.add_argument("prompt")
    from_text.add_argument("--image-model-id", default="anima/generate")
    from_text.add_argument("--mesh-model-id", default="trellis2/generate")
    from_text.add_argument("--no-texture", action="store_true")
    from_text.add_argument("--no-optimize", action="store_true")
    from_text.add_argument("--target-faces", type=int, default=100000)
    from_text.add_argument("--collection", default="Workflows")
    from_text.add_argument("--workflow-id", default="")
    from_text.add_argument("--world-id", default="")
    from_text.add_argument("--proto-id", default="")
    from_text.add_argument("--image-params")
    from_text.add_argument("--mesh-params")
    from_text.add_argument("--texture-params")
    _set_handler(from_text, cmd_asset_from_text)

    external_search = asset.add_parser("search-external", help="Read-only Poly Haven model metadata search")
    external_search.add_argument("query")
    external_search.add_argument("--category", default="")
    external_search.add_argument("--limit", type=int, default=5)
    external_search.add_argument("--refresh", action="store_true")
    _set_handler(external_search, cmd_asset_search_external)

    external_import = asset.add_parser("import-external", help="Explicitly import one Poly Haven model")
    external_import.add_argument("asset_id")
    external_import.add_argument("--resolution", choices=("1k", "2k", "4k", "8k"), default="2k")
    _set_handler(external_import, cmd_asset_import_external)

    image = sub.add_parser("image", help="Image workflow helpers").add_subparsers(dest="image_command", required=True)
    image_generate = image.add_parser("generate")
    image_generate.add_argument("prompt")
    image_generate.add_argument("--model-id", default="anima/generate")
    image_generate.add_argument("--params")
    image_generate.add_argument("--collection", default="Workflows")
    image_generate.add_argument("--workflow-id", default="")
    _set_handler(image_generate, cmd_image_generate)
    bg = image.add_parser("remove-background")
    bg.add_argument("workspace_path")
    bg.add_argument("--model", default="isnet-anime")
    bg.add_argument("--collection", default="Workflows")
    bg.add_argument("--workflow-id", default="")
    _set_handler(bg, cmd_image_remove_background)

    mesh = sub.add_parser("mesh", help="Mesh processing helpers").add_subparsers(dest="mesh_command", required=True)
    decimate = mesh.add_parser("decimate")
    decimate.add_argument("path")
    decimate.add_argument("target_faces", type=int)
    _set_handler(decimate, cmd_mesh_decimate)
    smooth = mesh.add_parser("smooth")
    smooth.add_argument("path")
    smooth.add_argument("--iterations", type=int, default=1)
    _set_handler(smooth, cmd_mesh_smooth)
    mesh_import = mesh.add_parser("import")
    mesh_import.add_argument("path")
    _set_handler(mesh_import, cmd_mesh_import)

    settings = sub.add_parser("settings", help="Server settings").add_subparsers(dest="settings_command", required=True)
    _set_handler(settings.add_parser("paths"), cmd_settings_paths)

    world = sub.add_parser("world", help="World domain operations").add_subparsers(dest="world_command", required=True)
    create = world.add_parser("create")
    create.add_argument("--name", default="")
    create.add_argument("--prompt", default="")
    create.add_argument("--parent-world-id", default="")
    _set_handler(create, cmd_world_create)
    get = world.add_parser("get")
    get.add_argument("world_id")
    _set_handler(get, cmd_world_get)
    save = world.add_parser("save")
    save.add_argument("world_id")
    save.add_argument("file")
    _set_handler(save, cmd_world_save)
    compile_scene = world.add_parser("compile-scene")
    compile_scene.add_argument("world_id")
    compile_scene.add_argument("file", help="ScenePlan JSON")
    compile_scene.add_argument("--no-solve", action="store_true")
    compile_scene.add_argument("--resolve-assets", action="store_true")
    _set_handler(compile_scene, cmd_world_compile_scene)
    find_assets = world.add_parser("find-assets")
    find_assets.add_argument("query")
    find_assets.add_argument("--category", default="")
    find_assets.add_argument("--limit", type=int, default=5)
    _set_handler(find_assets, cmd_world_find_assets)
    compose = world.add_parser("compose")
    compose.add_argument("world_id")
    compose.add_argument("--collection", default="Scenes")
    compose.add_argument("--output-name", default="scene")
    compose.add_argument("--allow-missing", action="store_true")
    _set_handler(compose, cmd_world_compose)
    build = world.add_parser("build-structure")
    build.add_argument("world_id")
    build.add_argument("--building-id", default="")
    build.add_argument("--collection", default="Scenes")
    build.add_argument("--no-preview", action="store_true")
    _set_handler(build, cmd_world_build_structure)
    validate = world.add_parser("validate")
    validate.add_argument("world_id")
    validate.add_argument("capability", choices=(
        "world.spec.validate",
        "world.blockout.validate",
        "world.construction.validate",
        "world.gameplay.validate",
        "world.final.validate",
    ))
    validate.add_argument("--run-id", default="")
    _set_handler(validate, cmd_world_validate)
    attach = world.add_parser("attach-asset")
    attach.add_argument("world_id")
    attach.add_argument("proto_id")
    attach.add_argument("workspace_path")
    attach.add_argument("--workflow-id", default="")
    attach.add_argument("--run-id", default="")
    attach.add_argument("--concept-image", default="")
    _set_handler(attach, cmd_world_attach_asset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
        _dump(result)
        return 0
    except (CliError, OSError, ValueError) as exc:
        _dump({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
