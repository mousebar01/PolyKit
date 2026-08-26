"""Managed setup for PolyKit's official Hunyuan3D-Part node pack.

This script intentionally does not vendor Tencent Hunyuan3D-Part or its model
weights.  It installs a pinned MIT integration adapter, then delegates platform
and CUDA dependency setup to that adapter. Model weights remain a separate
Models-page download.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parent
PROVIDER_ROOT = PACK_ROOT / "provider"
PROVIDER_REVISION = "48b9ee3540bf7a85bcb7eb982f748d0fe14195a8"
PROVIDER_ZIP = (
    "https://github.com/DrHepa/Hunyuan3D-Part-modly-extension/"
    f"archive/{PROVIDER_REVISION}.zip"
)
REVISION_FILE = PROVIDER_ROOT / ".polykit-provider-revision"
PROVIDER_COMPAT_SENTINEL = (
    "# polykit-compat-patches: v4 git-2.25, json-stdout, linux-amd64-wheels, readiness-native-deps, uv"
)
PROVIDER_COMPAT_SENTINEL_PREFIX = "# polykit-compat-patches:"


def _read_payload() -> dict:
    if len(sys.argv) < 2:
        return {}
    raw = sys.argv[1]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hunyuan3D-Part setup expected a JSON setup payload") from exc
    return value if isinstance(value, dict) else {}


def _safe_extract(archive: Path, destination: Path) -> Path:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        roots: set[str] = set()
        for member in members:
            parts = Path(member.filename).parts
            if not parts:
                continue
            roots.add(parts[0])
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise RuntimeError(f"Unsafe provider archive member: {member.filename}") from exc
        if len(roots) != 1:
            raise RuntimeError("Unexpected Hunyuan3D-Part provider archive layout")
        zf.extractall(destination)
        return destination / next(iter(roots))


def _provider_is_current() -> bool:
    if not (PROVIDER_ROOT / "generator.py").is_file():
        return False
    if not (PROVIDER_ROOT / "setup.py").is_file():
        return False
    if not REVISION_FILE.is_file():
        return False
    try:
        return REVISION_FILE.read_text(encoding="utf-8").strip() == PROVIDER_REVISION
    except OSError:
        return False


def _install_provider_source() -> None:
    if _provider_is_current():
        print(f"[hunyuan3d-part] provider {PROVIDER_REVISION[:8]} already present")
        return

    print(f"[hunyuan3d-part] fetching provider {PROVIDER_REVISION[:8]} ...")
    with tempfile.TemporaryDirectory(prefix="polykit-hunyuan-provider-") as td:
        temp_root = Path(td)
        archive = temp_root / "provider.zip"
        request = urllib.request.Request(PROVIDER_ZIP, headers={"User-Agent": "PolyKit"})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)

        extracted = _safe_extract(archive, temp_root / "extract")
        staging = PACK_ROOT / ".provider-staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(
            extracted,
            staging,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "venv"),
        )
        (staging / ".polykit-provider-revision").write_text(
            PROVIDER_REVISION + "\n",
            encoding="utf-8",
        )

        old = PACK_ROOT / ".provider-old"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        if PROVIDER_ROOT.exists():
            PROVIDER_ROOT.rename(old)
        try:
            staging.rename(PROVIDER_ROOT)
        except Exception:
            if old.exists() and not PROVIDER_ROOT.exists():
                old.rename(PROVIDER_ROOT)
            raise
        finally:
            shutil.rmtree(old, ignore_errors=True)

    print("[hunyuan3d-part] provider source ready")


def _provider_compat_replacements() -> list[tuple[str, str]]:
    """Literal (old, new) pairs patched into the pinned provider setup.py."""
    return [
        (
            """def pip_run(venv_dir: Path, *args: str) -> None:
    subprocess.run(subprocess_command(venv_python(venv_dir), \"-m\", \"pip\", *args), check=True)
""",
            """def _uv_executable() -> str:
    configured = os.environ.get(\"POLYKIT_UV\")
    if configured:
        if Path(configured).is_file():
            return configured
        resolved = shutil.which(configured)
    else:
        resolved = shutil.which(\"uv\")
    if resolved:
        return resolved
    raise RuntimeError(
        \"PolyKit requires uv to install Node Pack dependencies. Install it from \"
        \"https://docs.astral.sh/uv/getting-started/installation/ and run setup again.\"
    )


def pip_run(venv_dir: Path, *args: str) -> None:
    uv = _uv_executable()
    if not args:
        raise ValueError(\"package manager command requires an operation\")
    uv_args = [
        \"--reinstall\" if arg == \"--force-reinstall\" else
        \"--no-cache\" if arg == \"--no-cache-dir\" else arg
        for arg in args[1:]
        if not (args[0] == \"uninstall\" and arg in {\"-y\", \"--yes\"})
    ]
    command = [uv, \"pip\", args[0], \"--python\", str(venv_python(venv_dir)), *uv_args]
    subprocess.run(command, check=True)
""",
        ),
        (
            '    "pymeshlab",\n]',
            '    "pymeshlab",\n    "socksio",\n]',
        ),
        (
            "    pip_run(venv_dir, *command)\n\n\ndef _native_build_artifact_root(extension_dir: Path) -> Path:",
            """    pip_run(venv_dir, *command)


def _extract_json_object(text: str):
    raw = (text or "").strip()
    if not raw:
        raise json.JSONDecodeError("Expecting value", raw, 0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(raw[start:end + 1])


def _git_version() -> tuple[int, int]:
    try:
        output = subprocess.run(
            ["git", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except Exception:
        return (0, 0)
    match = re.search(r"git version (\\d+)\\.(\\d+)", output)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _native_build_artifact_root(extension_dir: Path) -> Path:""",
        ),
        (
            """def _is_linux_arm64(system_name: str, machine: str) -> bool:
    return system_name == "Linux" and normalize_machine(machine) == "arm64"


def _is_windows_amd64(system_name: str, machine: str) -> bool:
    return system_name == "Windows" and normalize_machine(machine) == "x86_64"
""",
            """def _is_linux_arm64(system_name: str, machine: str) -> bool:
    return system_name == "Linux" and normalize_machine(machine) == "arm64"


def _is_linux_amd64(system_name: str, machine: str) -> bool:
    return system_name == "Linux" and normalize_machine(machine) == "x86_64"


def _is_windows_amd64(system_name: str, machine: str) -> bool:
    return system_name == "Windows" and normalize_machine(machine) == "x86_64"
""",
        ),
        (
            """    elif _is_windows_amd64(system_name, normalized_machine):
        torch_plan = torch_install_plan(
            system_name=system_name,
            machine=normalized_machine,
            py_tag=py_tag,
            gpu_sm=gpu_sm,
            cuda_version=cuda_version,
        )
        desired_cuda_label = str(torch_plan.get("label") or "")
        lane = f"windows-amd64-{desired_cuda_label}-prebuilt"
        state = SUPPORTED
        reason = (
            "Windows AMD64 uses prebuilt PyG wheels matching the selected PyTorch/CUDA lane plus a matching spconv CUDA wheel; "
            "runtime inference still depends on the managed import smoke passing on the target host."
        )
""",
            """    elif _is_windows_amd64(system_name, normalized_machine):
        torch_plan = torch_install_plan(
            system_name=system_name,
            machine=normalized_machine,
            py_tag=py_tag,
            gpu_sm=gpu_sm,
            cuda_version=cuda_version,
        )
        desired_cuda_label = str(torch_plan.get("label") or "")
        lane = f"windows-amd64-{desired_cuda_label}-prebuilt"
        state = SUPPORTED
        reason = (
            "Windows AMD64 uses prebuilt PyG wheels matching the selected PyTorch/CUDA lane plus a matching spconv CUDA wheel; "
            "runtime inference still depends on the managed import smoke passing on the target host."
        )
    elif _is_linux_amd64(system_name, normalized_machine):
        torch_plan = torch_install_plan(
            system_name=system_name,
            machine=normalized_machine,
            py_tag=py_tag,
            gpu_sm=gpu_sm,
            cuda_version=cuda_version,
        )
        desired_cuda_label = str(torch_plan.get("label") or "")
        lane = f"linux-amd64-{desired_cuda_label}-prebuilt"
        state = SUPPORTED
        reason = (
            "Linux AMD64 uses prebuilt PyG wheels matching the selected PyTorch/CUDA lane plus a matching spconv CUDA wheel."
        )
""",
        ),
        (
            """    if state == SUPPORTED and _is_windows_amd64(system_name, normalized_machine):
        install_strategy = "windows-amd64-prebuilt-wheels"
""",
            """    if state == SUPPORTED and _is_windows_amd64(system_name, normalized_machine):
        install_strategy = "windows-amd64-prebuilt-wheels"
""",
        ),
        (
            """        automatic_install_supported = True
    elif state == SUPPORTED:
        install_strategy = "linux-arm64-source-build"
""",
            """        automatic_install_supported = True
    elif state == SUPPORTED and _is_linux_amd64(system_name, normalized_machine):
        install_strategy = "linux-amd64-prebuilt-wheels"
        torch_plan = torch_install_plan(
            system_name=system_name,
            machine=normalized_machine,
            py_tag=py_tag,
            gpu_sm=gpu_sm,
            cuda_version=cuda_version,
        )
        install_steps = _linux_amd64_native_install_steps(torch_plan)
        message = (
            "Linux AMD64 native runtime uses prebuilt wheels and may be installed automatically during managed setup."
        )
        next_action = "Run managed setup/repair again; it will install Linux prebuilt native wheels and re-probe readiness."
        plan_status = "planned"
        automatic_install_supported = True
    elif state == SUPPORTED:
        install_strategy = "linux-arm64-source-build"
""",
        ),
        (
            """def _windows_amd64_native_install_steps(torch_plan: dict[str, Any]) -> tuple[NativeInstallStep, ...]:
""",
            """def _linux_amd64_native_install_steps(torch_plan: dict[str, Any]) -> tuple[NativeInstallStep, ...]:
    pyg_plan = _windows_pyg_wheel_plan(torch_plan)
    pyg_suffix = pyg_plan["suffix"]
    pyg_index_url = pyg_plan["index_url"]
    spconv_requirement = _windows_spconv_requirement(pyg_plan["cuda_label"])
    reason = (
        "Linux AMD64 uses PyG prebuilt wheels matching the managed PyTorch/CUDA install plan "
        f"({pyg_plan['torch_version']}+{pyg_plan['cuda_label']}) instead of source builds."
    )
    return (
        NativeInstallStep(
            package="torch_scatter",
            requirement=f"torch-scatter=={TORCH_SCATTER_VERSION}+{pyg_suffix}",
            strategy="pip-prebuilt-wheel",
            status="planned",
            pip_args=(
                "install",
                f"torch-scatter=={TORCH_SCATTER_VERSION}+{pyg_suffix}",
                "-f",
                pyg_index_url,
                "--no-cache-dir",
            ),
            reason=reason,
        ),
        NativeInstallStep(
            package="torch_cluster",
            requirement=f"torch-cluster=={TORCH_CLUSTER_VERSION}+{pyg_suffix}",
            strategy="pip-prebuilt-wheel",
            status="planned",
            pip_args=(
                "install",
                f"torch-cluster=={TORCH_CLUSTER_VERSION}+{pyg_suffix}",
                "-f",
                pyg_index_url,
                "--no-cache-dir",
            ),
            reason=reason,
        ),
        NativeInstallStep(
            package="spconv",
            requirement=spconv_requirement,
            strategy="pip-prebuilt-wheel",
            status="planned",
            pip_args=("install", spconv_requirement, "--no-cache-dir"),
            reason=reason,
        ),
    )


def _windows_amd64_native_install_steps(torch_plan: dict[str, Any]) -> tuple[NativeInstallStep, ...]:
""",
        ),
        (
            '    if plan.install_strategy != "windows-amd64-prebuilt-wheels":\n        return {}',
            '    if plan.install_strategy not in {"windows-amd64-prebuilt-wheels", "linux-amd64-prebuilt-wheels"}:\n        return {}',
        ),
        (
            '        and plan.install_strategy == "windows-amd64-prebuilt-wheels"\n',
            '        and plan.install_strategy in {"windows-amd64-prebuilt-wheels", "linux-amd64-prebuilt-wheels"}\n',
        ),
        (
            """    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    subprocess.run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--sparse",
            "--depth",
            "1",
            "--branch",
            repo_ref,
            repo_url,
            str(runtime_root),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(runtime_root), "sparse-checkout", "set", "P3-SAM", "XPart/partgen"],
        check=True,
    )
""",
            """    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    # Git 2.25 rejects combining --sparse with --filter=blob:none. Use a
    # shallow clone there, and keep sparse checkout on newer git.
    git_version = _git_version()
    clone_cmd = ["git", "clone", "--depth", "1", "--branch", repo_ref, repo_url, str(runtime_root)]
    if git_version >= (2, 27):
        clone_cmd = [
            "git",
            "clone",
            "--filter=blob:none",
            "--sparse",
            "--depth",
            "1",
            "--branch",
            repo_ref,
            repo_url,
            str(runtime_root),
        ]
    subprocess.run(clone_cmd, check=True)
    if git_version >= (2, 27):
        subprocess.run(
            ["git", "-C", str(runtime_root), "sparse-checkout", "set", "P3-SAM", "XPart/partgen"],
            check=True,
        )
""",
        ),
        (
            "    payload = json.loads(result.stdout.strip())\n    payload[\"python_exe\"] = payload.get(\"python_exe\") or str(python_exe)",
            "    payload = _extract_json_object(result.stdout)\n    payload[\"python_exe\"] = payload.get(\"python_exe\") or str(python_exe)",
        ),
        (
            "    raw_probe = json.loads(result.stdout.strip() or \"{}\")\n",
            "    raw_probe = _extract_json_object(result.stdout or \"{}\")\n",
        ),
        (
            "def _managed_host_support(\n    *,\n    fallback_context: Any,\n    runtime_verification: dict[str, Any],\n    has_venv: bool,\n) -> tuple[dict[str, Any], dict[str, Any]]:",
            "def _managed_host_support(\n    *,\n    fallback_context: Any,\n    runtime_verification: dict[str, Any],\n    has_venv: bool,\n    native_import_results: dict[str, bool] | None = None,\n) -> tuple[dict[str, Any], dict[str, Any]]:",
        ),
        (
            '    required_dependencies = {\n        module: module not in set(runtime_verification.get("missing") or [])\n        for module in ("numpy", "torch", "trimesh")\n    }',
            '    required_dependencies = {\n        module: module not in set(runtime_verification.get("missing") or [])\n        for module in ("numpy", "torch", "trimesh")\n    }\n'
            "    # The base probe never imports native wheels, so they would always\n"
            "    # look missing here; trust the dedicated native-runtime evidence.\n"
            "    for module, present in (native_import_results or {}).items():\n"
            '        if module not in ("numpy", "torch", "trimesh"):\n'
            "            required_dependencies[module] = bool(present)",
        ),
        (
            "    host, support = _managed_host_support(\n        fallback_context=context,\n        runtime_verification=runtime_verification,\n        has_venv=has_venv,\n    )\n    generator = Hunyuan3DPartGenerator(project_root=project_root, runtime_context=context)",
            "    generator = Hunyuan3DPartGenerator(project_root=project_root, runtime_context=context)",
        ),
        (
            "    native_runtime = native_runtime or run_native_runtime_phase(\n        venv_dir=venv_dir,\n        managed_python=managed_python,\n        system_name=system_name,\n        machine=machine,\n        py_tag=py_tag,\n        gpu_sm=gpu_sm,\n        cuda_version=cuda_version,\n        native_mode=native_mode,\n    )\n    native_dependency_report = build_native_dependency_report(native_runtime=native_runtime)",
            "    native_runtime = native_runtime or run_native_runtime_phase(\n        venv_dir=venv_dir,\n        managed_python=managed_python,\n        system_name=system_name,\n        machine=machine,\n        py_tag=py_tag,\n        gpu_sm=gpu_sm,\n        cuda_version=cuda_version,\n        native_mode=native_mode,\n    )\n"
            "    native_probe_payload = native_runtime.get(\"probe\")\n"
            "    native_probe_imports = native_probe_payload.get(\"imports\") if isinstance(native_probe_payload, dict) else {}\n"
            "    native_import_results = {\n"
            "        name: bool((entry or {}).get(\"ready\"))\n"
            "        for name, entry in (native_probe_imports or {}).items()\n"
            "    }\n"
            "    for module, present in context.support.dependencies.items():\n"
            "        native_import_results.setdefault(module, bool(present))\n"
            "    host, support = _managed_host_support(\n"
            "        fallback_context=context,\n"
            "        runtime_verification=runtime_verification,\n"
            "        has_venv=has_venv,\n"
            "        native_import_results=native_import_results,\n"
            "    )\n"
            "    native_dependency_report = build_native_dependency_report(native_runtime=native_runtime)",
        ),
    ]


def _apply_provider_compat_patches() -> None:
    """Patch the pinned provider so Linux x86_64 hosts can finish setup.

    The MIT adapter currently:
    - uses ``git clone --sparse --filter`` which Git 2.25 rejects
    - parses probe JSON from stdout that pymeshlab pollutes
    - only auto-installs native wheels on Windows / Linux ARM64
    - imports Gradio under a SOCKS proxy without ``socksio``
    - reports native wheels as missing in readiness because the host-support
      probe only covers the base packages
    - routes the provider's pip-compatible install helper through uv

    Each replacement is skipped when its result is already present, so a
    provider copy patched by an older sentinel version upgrades in place.
    """
    provider_setup = PROVIDER_ROOT / "setup.py"
    if not provider_setup.is_file():
        raise RuntimeError("Hunyuan3D-Part provider setup.py is missing after bootstrap")
    text = provider_setup.read_text(encoding="utf-8")
    # Drop marker lines left by older patch-set versions so the current
    # sentinel is the only one after this run.
    while text.startswith(PROVIDER_COMPAT_SENTINEL_PREFIX):
        remainder = text.split("\n", 1)
        if len(remainder) != 2:
            text = ""
            break
        text = remainder[1]
    if PROVIDER_COMPAT_SENTINEL in text:
        print("[hunyuan3d-part] provider compatibility patches already applied")
        return

    replacements = _provider_compat_replacements()
    original = text
    for old, new in replacements:
        if new in text:
            # Already applied by an older patch run; keep the upgrade path
            # working for providers patched before the sentinel changed.
            continue
        if old not in text:
            raise RuntimeError(
                "Hunyuan3D-Part provider setup.py changed; compatibility patch "
                f"no longer matches:\n{old[:180]}"
            )
        text = text.replace(old, new, 1)

    if "import re\n" not in text.split("from dataclasses", 1)[0]:
        text = text.replace("import json\n", "import json\nimport re\n", 1)

    text = PROVIDER_COMPAT_SENTINEL + "\n" + text
    if text == original:
        return
    provider_setup.write_text(text, encoding="utf-8")
    print("[hunyuan3d-part] applied provider compatibility patches")


def _run_provider_setup(payload: dict) -> None:
    provider_setup = PROVIDER_ROOT / "setup.py"
    if not provider_setup.is_file():
        raise RuntimeError("Hunyuan3D-Part provider setup.py is missing after bootstrap")

    source_python = str(payload.get("python_exe") or sys.executable)
    provider_payload = dict(payload)
    # The provider code itself stays under provider/, but all managed runtime
    # state belongs to the official pack root so ModelPackSubprocess finds venv/
    # and official-pack sync leaves it untouched.
    provider_payload["ext_dir"] = str(PACK_ROOT)
    provider_payload["pack_dir"] = str(PACK_ROOT)
    provider_payload["python_exe"] = source_python

    command = [
        source_python,
        str(provider_setup),
        "setup",
        json.dumps(provider_payload),
    ]
    print("[hunyuan3d-part] installing isolated runtime dependencies ...")
    result = subprocess.run(
        command,
        cwd=str(PROVIDER_ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Hunyuan3D-Part provider setup failed with exit code {result.returncode}"
        )


def main() -> int:
    payload = _read_payload()
    accelerator = str(payload.get("accelerator") or "")
    if accelerator and accelerator != "cuda":
        raise RuntimeError(
            "Hunyuan3D-Part P3-SAM currently requires an NVIDIA CUDA runtime. "
            f"Detected accelerator: {accelerator}."
        )

    _install_provider_source()
    _apply_provider_compat_patches()
    _run_provider_setup(payload)

    venv_python = PACK_ROOT / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not venv_python.is_file():
        raise RuntimeError(f"Managed Hunyuan3D-Part venv was not created at {venv_python}")

    print("[hunyuan3d-part] setup complete")
    print("[hunyuan3d-part] download P3-SAM weights from the Models page before running the node")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
