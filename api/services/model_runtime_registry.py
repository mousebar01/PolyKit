"""Model runtime registry and lifecycle manager.

Node-pack inventory and filesystem root ownership live outside this module.
ModelRuntimeRegistry is responsible for executable model instances, active-model
selection, generation lifecycle and accelerator-memory reclamation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from services.generators.base import BaseGenerator
from services.model_pack_subprocess import ModelPackSubprocess, _venv_python
from services.runtime_paths import runtime_paths


EXECUTOR = os.environ.get("POLYKIT_EXECUTOR", "cuda").strip().lower() or "cuda"
_DEFAULT_IDLE_UNLOAD_SECONDS = 5 * 60


def _model_location(manifest: dict, model_id: str) -> str:
    """Resolve a manifest's shared weight directory without allowing traversal."""
    raw = str(manifest.get("model_location") or model_id).strip()
    parts = Path(raw).parts
    if (
        not raw
        or Path(raw).is_absolute()
        or not parts
        or any(part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
    ):
        return model_id
    return raw


def _idle_unload_seconds() -> float:
    raw = os.environ.get("POLYKIT_IDLE_UNLOAD_SECONDS", str(_DEFAULT_IDLE_UNLOAD_SECONDS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULT_IDLE_UNLOAD_SECONDS)
    return value if value == 0 or value > 0 else float(_DEFAULT_IDLE_UNLOAD_SECONDS)


print(f"[Registry] MODELS_DIR     = {runtime_paths.models}")
print(f"[Registry] WORKSPACE_DIR  = {runtime_paths.workspace}")
print(f"[Registry] NODE_PACKS_DIR = {runtime_paths.node_packs}")


def _discover_node_packs() -> Dict[str, Tuple[type, dict, Path]]:
    """Discover executable model node packs from the configured runtime dir."""
    result: Dict[str, Tuple[type, dict, Path]] = {}
    node_packs_dir = runtime_paths.node_packs

    if not node_packs_dir.exists():
        print(f"[Registry] WARNING: NODE_PACKS_DIR not found: {node_packs_dir}")
        return result

    for pack_dir in sorted(node_packs_dir.iterdir()):
        if not pack_dir.is_dir() or pack_dir.name.startswith("."):
            continue
        if (pack_dir / ".polykit-incomplete").exists():
            print(f"[Registry] Skipping '{pack_dir.name}': install has not completed")
            continue

        manifest_path = pack_dir / "manifest.json"
        generator_path = pack_dir / "generator.py"
        if not manifest_path.exists():
            print(f"[Registry] Skipping '{pack_dir.name}': missing manifest.json")
            continue
        if not generator_path.exists():
            print(f"[Registry] Skipping '{pack_dir.name}': missing generator.py")
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("type", "model") != "model":
                print(
                    f"[Registry] Skipping '{pack_dir.name}': type "
                    f"'{manifest.get('type')}' is not handled by this registry"
                )
                continue

            pack_id = manifest["id"]
            class_name = manifest["generator_class"]
            nodes = [n for n in manifest.get("nodes", []) if n.get("id")]

            has_venv = _venv_python(pack_dir).exists()
            has_build_vendor = (pack_dir / "build_vendor.py").exists()
            vendor_built = (pack_dir / "vendor").exists()
            env_isolated = manifest.get("env", "shared") == "isolated"
            subprocess_mode = has_venv or env_isolated or (has_build_vendor and not vendor_built)

            cls_or_none = None
            if not subprocess_mode:
                module_name = f"node_packs.{pack_id}.generator"
                spec = importlib.util.spec_from_file_location(module_name, generator_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError(f"Could not load generator module for '{pack_id}'")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                cls_or_none = getattr(module, class_name)

            if nodes:
                for node in nodes:
                    node_manifest = {
                        **manifest,
                        "id": f"{pack_id}/{node['id']}",
                        "pack_id": pack_id,
                        "node_id": node["id"],
                        "name": node.get("name", node["id"]),
                        "hf_repo": node.get("hf_repo", ""),
                        "download_check": node.get("download_check", ""),
                        "hf_skip_prefixes": node.get("hf_skip_prefixes", []),
                        "hf_include_prefixes": node.get("hf_include_prefixes", []),
                        "params_schema": node.get("params_schema", manifest.get("params_schema", [])),
                        "input": node.get("input", "image"),
                        "inputs": node.get("inputs", manifest.get("inputs")),
                        "output": node.get("output", "mesh"),
                    }
                    full_id = f"{pack_id}/{node['id']}"
                    result[full_id] = (cls_or_none, node_manifest, pack_dir)
                    if subprocess_mode:
                        if has_venv:
                            print(f"[Registry] Loaded subprocess node: {full_id}")
                        elif env_isolated:
                            print(f"[Registry] Node '{full_id}' needs setup (isolated env, venv missing)")
                        else:
                            print(f"[Registry] Node '{full_id}' needs setup (venv missing)")
                    else:
                        print(f"[Registry] Loaded node: {full_id} ({class_name})")
            else:
                result[pack_id] = (cls_or_none, manifest, pack_dir)
                if subprocess_mode:
                    if has_venv:
                        print(f"[Registry] Loaded subprocess node pack: {pack_id}")
                    elif env_isolated:
                        print(f"[Registry] Node pack '{pack_id}' needs setup (isolated env, venv missing)")
                    else:
                        print(f"[Registry] Node pack '{pack_id}' needs setup (venv missing)")
                else:
                    print(f"[Registry] Loaded node pack: {pack_id} ({class_name})")
        except Exception as exc:
            print(f"[Registry] ERROR loading node pack '{pack_dir.name}': {exc}")

    return result


class ModelRuntimeRegistry:
    """Own loaded model runtimes, not application storage configuration."""

    def __init__(self) -> None:
        self._generators: Dict[str, BaseGenerator] = {}
        self._manifests: Dict[str, dict] = {}
        self._errors: Dict[str, str] = {}
        self._active_id: str = "fake" if EXECUTOR == "fake" else os.environ.get("SELECTED_MODEL_ID", "sf3d")
        self._lifecycle_lock = threading.RLock()
        self._active_runs: set[str] = set()
        self._idle_unload_timer: threading.Timer | None = None
        self.idle_unload_seconds = _idle_unload_seconds()

    def initialize(self) -> None:
        from services.official_packs import sync_official_packs

        paths = runtime_paths.snapshot()
        try:
            sync_official_packs(paths.node_packs)
        except Exception as exc:
            print(f"[Registry] WARNING: official pack sync failed: {exc}")

        if EXECUTOR == "fake":
            from services.fake_generator import FakeGenerator

            self._generators["fake"] = FakeGenerator(paths.models, paths.workspace)
            self._manifests["fake"] = {
                "id": "fake",
                "name": FakeGenerator.DISPLAY_NAME,
                "description": "Deterministic CPU-only test artifact; not a model benchmark.",
                "version": "1.0.0",
                "vram_gb": 0,
                "executor": "fake",
            }
            print("[Registry] Using explicit fake CPU executor")
            return

        node_packs = _discover_node_packs()
        for model_id, entry in node_packs.items():
            cls, manifest, pack_dir = entry
            try:
                model_location = _model_location(manifest, model_id)
                model_root = paths.models / model_location
                if cls is None:
                    if not _venv_python(pack_dir).exists():
                        raise RuntimeError(
                            "venv not found — node pack needs setup. "
                            "Click 'Repair' on the Models page to run setup.py."
                        )
                    gen = ModelPackSubprocess(pack_dir, manifest)
                    gen.model_dir = model_root
                    gen.outputs_dir = paths.workspace
                else:
                    gen = cls(model_root, paths.workspace)
                    gen.hf_repo = manifest.get("hf_repo", "")
                    gen.hf_skip_prefixes = manifest.get("hf_skip_prefixes", [])
                    gen.download_check = manifest.get("download_check", "")
                    gen._params_schema = manifest.get("params_schema", [])

                self._generators[model_id] = gen
                self._manifests[model_id] = manifest
                self._errors.pop(model_id, None)
            except Exception as exc:
                msg = f"Failed to instantiate generator '{model_id}': {exc}"
                print(f"[Registry] ERROR: {msg}")
                self._errors[model_id] = msg

        if not self._generators:
            print("[Registry] WARNING: No node packs found.")
            return

        if self._active_id not in self._generators:
            fallback = next(iter(self._generators))
            print(
                f"[Registry] WARNING: SELECTED_MODEL_ID='{self._active_id}' is unknown. "
                f"Falling back to '{fallback}'."
            )
            self._active_id = fallback

        print(f"[Registry] Active model  : {self._active_id}")
        print(f"[Registry] All models    : {list(self._generators.keys())}")

    def reload(self) -> None:
        with self._lifecycle_lock:
            if self._active_runs:
                raise RuntimeError("Cannot reload node packs while a generation is running")
            print("[Registry] Reloading node packs…")
            for gen in self._generators.values():
                try:
                    gen.unload()
                except Exception:
                    pass
            self._generators.clear()
            self._manifests.clear()
            self._errors.clear()
            self.initialize()
            print("[Registry] Reload complete.")

    def load_errors(self) -> Dict[str, str]:
        return dict(self._errors)

    def manifests(self) -> Dict[str, dict]:
        """Read-only snapshot used by catalog/presentation layers."""
        return dict(self._manifests)

    def model_ids(self) -> list[str]:
        return list(self._generators.keys())

    def get_active(self) -> BaseGenerator:
        gen = self._generators[self._active_id]
        if not gen.is_loaded():
            if not gen.is_downloaded():
                if not isinstance(gen, ModelPackSubprocess):
                    gen._auto_download()
            gen.load()
        return gen

    def get_generator(self, model_id: str) -> BaseGenerator:
        if model_id not in self._generators:
            raise ValueError(
                f"Unknown model ID: '{model_id}'. Available: {list(self._generators.keys())}"
            )
        return self._generators[model_id]

    def get_manifest(self, model_id: str) -> dict:
        if model_id not in self._manifests:
            raise KeyError(f"No manifest for model ID: '{model_id}'")
        return self._manifests[model_id]

    def switch_model(self, model_id: str, *, allow_during_generation: bool = False) -> None:
        with self._lifecycle_lock:
            if self._active_runs and not allow_during_generation:
                raise RuntimeError("Cannot switch models while a generation is running")
            if model_id not in self._generators:
                raise ValueError(
                    f"Unknown model ID: '{model_id}'. Available: {list(self._generators.keys())}"
                )
            if model_id != self._active_id:
                if self._active_id in self._generators:
                    self._generators[self._active_id].unload()
                self._active_id = model_id

    def has_active_generation(self) -> bool:
        with self._lifecycle_lock:
            return bool(self._active_runs)

    def cancel_generation(self, job_id: str) -> bool:
        """Best-effort hard stop behind the model-runtime boundary.

        Cooperative cancellation is still driven by the job cancellation event.
        Isolated node-pack processes can additionally be stopped immediately
        without RunCoordinator reaching into their private subprocess fields.
        """
        with self._lifecycle_lock:
            if job_id not in self._active_runs:
                return False
            gen = self._generators.get(self._active_id)
            if isinstance(gen, ModelPackSubprocess):
                try:
                    gen.stop()
                except Exception:
                    return False
            return True

    def active_status(self) -> dict:
        if self._active_id not in self._generators:
            return {
                "id": self._active_id,
                "name": "",
                "downloaded": False,
                "loaded": False,
            }
        gen = self._generators[self._active_id]
        manifest = self._manifests[self._active_id]
        return {
            "id": self._active_id,
            "name": manifest.get("name", gen.DISPLAY_NAME),
            "downloaded": gen.is_downloaded(),
            "loaded": gen.is_loaded(),
            "executor": manifest.get("executor", "cuda"),
        }

    def all_status(self) -> list:
        result = []
        for model_id, gen in self._generators.items():
            manifest = self._manifests[model_id]
            result.append({
                "id": model_id,
                "name": manifest.get("name", gen.DISPLAY_NAME),
                "description": manifest.get("description", ""),
                "version": manifest.get("version", ""),
                "vram_gb": manifest.get("vram_gb", gen.VRAM_GB),
                "hf_repo": manifest.get("hf_repo", ""),
                "tags": manifest.get("tags", []),
                "input": manifest.get("input"),
                "inputs": manifest.get("inputs"),
                "output": manifest.get("output"),
                "downloaded": gen.is_downloaded(),
                "loaded": gen.is_loaded(),
                "active": model_id == self._active_id,
            })
        return result

    def params_schema(self, model_id: Optional[str] = None) -> list:
        target_id = model_id or self._active_id
        if target_id not in self._generators:
            raise KeyError(target_id)
        return self._generators[target_id].params_schema()

    def begin_generation(self, job_id: str) -> None:
        with self._lifecycle_lock:
            self._active_runs.add(job_id)
            self._cancel_idle_unload_locked()

    def end_generation(self, job_id: str) -> None:
        with self._lifecycle_lock:
            self._active_runs.discard(job_id)
            if not self._active_runs:
                self._schedule_idle_unload_locked()

    def _cancel_idle_unload_locked(self) -> None:
        timer = self._idle_unload_timer
        self._idle_unload_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_idle_unload_locked(self) -> None:
        self._cancel_idle_unload_locked()
        if self.idle_unload_seconds <= 0:
            return
        timer = threading.Timer(self.idle_unload_seconds, self._unload_if_idle)
        timer.daemon = True
        self._idle_unload_timer = timer
        timer.start()

    def _unload_if_idle(self) -> None:
        with self._lifecycle_lock:
            self._idle_unload_timer = None
            if self._active_runs:
                return
            if not any(gen.is_loaded() for gen in self._generators.values()):
                return
            print(
                f"[Registry] Idle for {self.idle_unload_seconds:g}s; "
                "unloading models to release accelerator memory."
            )
            self.unload_all()

    def update_paths(
        self,
        models_dir: Optional[Path],
        workspace_dir: Optional[Path],
        node_packs_dir: Optional[Path] = None,
    ) -> None:
        """Apply runtime-root changes and rebind loaded model runtimes."""
        with self._lifecycle_lock:
            if self._active_runs:
                raise RuntimeError("Cannot update paths while a generation is running")

            old_node_packs = runtime_paths.node_packs.resolve()
            if models_dir is not None:
                self.unload_all()

            paths = runtime_paths.update(
                models_dir=models_dir,
                workspace_dir=workspace_dir,
                node_packs_dir=node_packs_dir,
            )

            if models_dir is not None:
                for model_id, gen in self._generators.items():
                    manifest = self._manifests.get(model_id, {})
                    model_location = _model_location(manifest, model_id)
                    gen.model_dir = paths.models / model_location

            if workspace_dir is not None:
                for gen in self._generators.values():
                    gen.outputs_dir = paths.workspace

            if node_packs_dir is not None and paths.node_packs.resolve() != old_node_packs:
                self.reload()

    def unload_all(self, *, allow_during_generation: bool = False) -> None:
        with self._lifecycle_lock:
            if self._active_runs and not allow_during_generation:
                raise RuntimeError("Cannot unload models while a generation is running")
            self._cancel_idle_unload_locked()
            for gen in self._generators.values():
                if isinstance(gen, ModelPackSubprocess):
                    gen.stop()
                else:
                    gen.unload()


model_runtime_registry = ModelRuntimeRegistry()

__all__ = ["ModelRuntimeRegistry", "model_runtime_registry"]
