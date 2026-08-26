"""PolyKit node-pack subprocess runner.

Runs inside a node pack's own interpreter and exchanges newline-delimited JSON
with ``ModelPackSubprocess``. Existing image models keep using ``image_b64``;
mesh-primary models can use the generic ``primary_input`` envelope.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Mapping

PACK_DIR = Path(os.environ["NODE_PACK_DIR"])
MODELS_DIR = Path(os.environ.get("MODELS_DIR", Path.home() / ".polykit" / "models"))
WORKSPACE_DIR = Path(
    os.environ.get("WORKSPACE_DIR", Path.home() / ".polykit" / "workspace")
)
POLYKIT_API_DIR = os.environ.get("POLYKIT_API_DIR", "")
_MODEL_DIR_OVERRIDE = os.environ.get("MODEL_DIR", "")

if POLYKIT_API_DIR and POLYKIT_API_DIR not in sys.path:
    sys.path.insert(0, POLYKIT_API_DIR)
if str(PACK_DIR) not in sys.path:
    sys.path.insert(0, str(PACK_DIR))


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def recv():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError as exc:
            send(
                {
                    "type": "log",
                    "level": "error",
                    "message": f"Runner: invalid JSON on stdin: {exc}",
                }
            )


def load_generator(manifest: dict):
    spec = importlib.util.spec_from_file_location("generator", PACK_DIR / "generator.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load generator.py from {PACK_DIR}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generator"] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, manifest["generator_class"])


def run_prestartup_script() -> None:
    script = PACK_DIR / "prestartup_script.py"
    if not script.is_file():
        return
    print(f"[runner] Executing prestartup script: {script}")
    try:
        spec = importlib.util.spec_from_file_location("prestartup_script", script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load {script}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["prestartup_script"] = mod
        spec.loader.exec_module(mod)
    except Exception:
        print(f"[runner] prestartup_script.py FAILED:\n{traceback.format_exc()}")


def _select_node(manifest: dict, model_dir_override: str) -> dict:
    nodes = manifest.get("nodes") or []
    if nodes and model_dir_override:
        node_id = Path(model_dir_override).name
        return next((node for node in nodes if node.get("id") == node_id), nodes[0])
    return nodes[0] if nodes else {}


def _resolve_ready_schema(GenClass, node: dict, manifest: dict) -> list:
    try:
        return GenClass.params_schema()
    except Exception:
        return node.get("params_schema") or manifest.get("params_schema", [])


def _apply_manifest_metadata(gen, manifest: dict, node: dict) -> None:
    gen.hf_repo = node.get("hf_repo") or manifest.get("hf_repo", "")
    gen.hf_skip_prefixes = node.get("hf_skip_prefixes") or manifest.get(
        "hf_skip_prefixes", []
    )
    gen.download_check = node.get("download_check") or manifest.get(
        "download_check", ""
    )
    gen._params_schema = node.get("params_schema") or manifest.get("params_schema", [])


def _decode_primary_input(msg: Mapping[str, object]) -> object:
    """Decode the backward-compatible model primary-input wire format."""
    primary = msg.get("primary_input")
    if isinstance(primary, Mapping):
        kind = primary.get("kind")
        if kind == "path":
            raw_path = primary.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError("primary_input path is empty")
            return Path(raw_path)
        if kind == "none":
            return None
        raise ValueError(f"Unsupported primary_input kind: {kind!r}")

    image_b64 = msg.get("image_b64")
    if not isinstance(image_b64, str):
        raise ValueError("Generate request must include image_b64 or primary_input")
    return base64.b64decode(image_b64)


def _normalize_output_path(result: object) -> Path:
    candidate = result
    if isinstance(result, Mapping):
        for key in ("primary_mesh", "mesh", "filePath", "path", "output_path"):
            value = result.get(key)
            if value is not None:
                candidate = value
                break
    if isinstance(candidate, Path):
        return candidate
    if isinstance(candidate, str) and candidate.strip():
        return Path(candidate)
    raise TypeError(
        "Generator result must be a path or a mapping containing a mesh path; "
        f"got {type(result).__name__}"
    )


def main() -> None:
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    model_id = manifest["id"]

    run_prestartup_script()
    try:
        GenClass = load_generator(manifest)
    except Exception:
        send(
            {
                "type": "error",
                "id": None,
                "message": "Failed to load generator class",
                "traceback": traceback.format_exc(),
            }
        )
        return

    node = _select_node(manifest, _MODEL_DIR_OVERRIDE)
    send(
        {
            "type": "ready",
            "params_schema": _resolve_ready_schema(GenClass, node, manifest),
        }
    )

    model_dir = (
        Path(_MODEL_DIR_OVERRIDE) if _MODEL_DIR_OVERRIDE else MODELS_DIR / model_id
    )
    gen = GenClass(model_dir, WORKSPACE_DIR)
    _apply_manifest_metadata(gen, manifest, node)
    cancel_events: dict[str, threading.Event] = {}

    for msg in recv():
        action = msg.get("action")
        rid = msg.get("id")
        try:
            if action == "load":
                gen.load()
                send({"type": "loaded"})

            elif action == "generate":
                cancel_evt = threading.Event()
                cancel_events[rid] = cancel_evt
                primary_input = _decode_primary_input(msg)
                params = msg.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("Generate params must be an object")
                if msg.get("outputs_dir"):
                    gen.outputs_dir = Path(msg["outputs_dir"])
                    gen.outputs_dir.mkdir(parents=True, exist_ok=True)

                def progress_cb(pct: int, step: str = "") -> None:
                    send(
                        {
                            "type": "progress",
                            "id": rid,
                            "pct": pct,
                            "step": step,
                        }
                    )

                try:
                    result = gen.generate(primary_input, params, progress_cb, cancel_evt)
                    output_path = _normalize_output_path(result)
                    send(
                        {
                            "type": "done",
                            "id": rid,
                            "output_path": str(output_path),
                        }
                    )
                except Exception as exc:
                    if type(exc).__name__ == "GenerationCancelled":
                        send({"type": "cancelled", "id": rid})
                    else:
                        send(
                            {
                                "type": "error",
                                "id": rid,
                                "message": str(exc),
                                "traceback": traceback.format_exc(),
                            }
                        )
                finally:
                    cancel_events.pop(rid, None)

            elif action == "cancel":
                evt = cancel_events.get(rid)
                if evt:
                    evt.set()

            elif action == "unload":
                gen.unload()
                send({"type": "unloaded"})

            elif action == "shutdown":
                gen.unload()
                break

        except Exception:
            send(
                {
                    "type": "error",
                    "id": rid,
                    "message": "Unexpected runner error",
                    "traceback": traceback.format_exc(),
                }
            )


if __name__ == "__main__":
    main()
