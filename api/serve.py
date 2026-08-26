#!/usr/bin/env python3
"""Standalone headless PolyKit server entry point.

Run from the repository root with ``python api/serve.py``. All state and
generated artifacts are owned by this FastAPI process and the configured
directories.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the PolyKit headless FastAPI server")
    parser.add_argument("--host", default=os.environ.get("POLYKIT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("POLYKIT_PORT", "8765") or "8765"))
    polykit_home = Path.home() / ".polykit"
    parser.add_argument("--models-dir", default=os.environ.get("MODELS_DIR", str(polykit_home / "models")))
    parser.add_argument("--workspace-dir", default=os.environ.get("WORKSPACE_DIR", str(polykit_home / "workspace")))
    parser.add_argument("--workflows-dir", default=os.environ.get("WORKFLOWS_DIR", str(polykit_home / "workflows")))
    parser.add_argument("--node-packs-dir", default=os.environ.get("NODE_PACKS_DIR", str(polykit_home / "node-packs")))
    parser.add_argument("--state-db", default=os.environ.get("POLYKIT_STATE_DB"), help="SQLite path for persisted run state")
    parser.add_argument("--web-dir", default=os.environ.get("POLYKIT_WEB_DIR"), help="Built Web UI directory")
    parser.add_argument("--cors-origins", default=os.environ.get("POLYKIT_CORS_ORIGINS"), help="Comma-separated browser origins allowed to call the API")
    parser.add_argument("--model", dest="selected_model_id")
    parser.add_argument("--executor", choices=("cuda", "fake"), default=os.environ.get("POLYKIT_EXECUTOR", "cuda"))
    parser.add_argument(
        "--idle-unload-seconds",
        type=float,
        default=(float(os.environ["POLYKIT_IDLE_UNLOAD_SECONDS"])
                 if os.environ.get("POLYKIT_IDLE_UNLOAD_SECONDS") is not None else None),
        help="Unload loaded models after this many idle seconds; 0 disables automatic unloading.",
    )
    parser.add_argument("--hf-token")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload for development")
    parser.add_argument("--log-level", default="info")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < (3, 10):
        print("PolyKit headless server requires Python 3.10 or newer.", file=sys.stderr)
        return 2
    if args.executor not in {"cuda", "fake"}:
        print(f"Unsupported POLYKIT_EXECUTOR={args.executor!r}; expected 'cuda' or 'fake'.", file=sys.stderr)
        return 2
    values = {
        "MODELS_DIR": args.models_dir,
        "WORKSPACE_DIR": args.workspace_dir,
        "WORKFLOWS_DIR": args.workflows_dir,
        "NODE_PACKS_DIR": args.node_packs_dir,
        "SELECTED_MODEL_ID": args.selected_model_id,
        "POLYKIT_EXECUTOR": args.executor,
        "POLYKIT_IDLE_UNLOAD_SECONDS": (
            str(args.idle_unload_seconds) if args.idle_unload_seconds is not None else None
        ),
        "HUGGING_FACE_HUB_TOKEN": args.hf_token,
        "HF_TOKEN": args.hf_token,
        "POLYKIT_HEADLESS": "1",
        "POLYKIT_STATE_DB": args.state_db,
        "POLYKIT_WEB_DIR": args.web_dir,
        "POLYKIT_CORS_ORIGINS": args.cors_origins,
        "POLYKIT_API_URL": f"http://127.0.0.1:{args.port}",
    }
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value

    try:
        import uvicorn
    except ImportError as exc:
        print("PolyKit server dependencies are missing. Run `uv sync` from the repository root first.", file=sys.stderr)
        print(f"Detail: {exc}", file=sys.stderr)
        return 2

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
