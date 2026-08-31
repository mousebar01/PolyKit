"""Thin JSON CLI for Agent Workflow Protocol v1.

The CLI intentionally contains no workflow policy. It delegates every state
transition to ``services.agent_workflow_runtime`` so FastAPI, tests, Skills and
shell usage all share one implementation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.agent_workflow_registry import list_agent_workflows  # noqa: E402
from services.agent_workflow_runtime import (  # noqa: E402
    begin_agent_workflow_step,
    cancel_agent_workflow_session,
    complete_agent_workflow_step,
    create_agent_workflow_session,
    get_agent_workflow_session,
    next_agent_workflow_action,
    pause_agent_workflow_session,
    resume_agent_workflow_session,
    wait_agent_workflow_session,
)
from services.runtime_paths import runtime_paths  # noqa: E402


def _json(value: Any, *, stream=sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


def _metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--metadata-json must decode to a JSON object")
    return parsed


def _evidence(values: list[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for value in values:
        kind, separator, ref = value.partition("=")
        if not separator or not kind.strip() or not ref.strip():
            raise ValueError("--evidence must use KIND=REF")
        result.append({"kind": kind.strip(), "ref": ref.strip()})
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-workflow",
        description="Inspect and mutate durable PolyKit Agent Workflow sessions.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Override the PolyKit workspace for this invocation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered Agent Workflow definitions.")

    start = sub.add_parser("start", help="Create a durable workflow session.")
    start.add_argument("workflow_id")
    start.add_argument("subject_kind")
    start.add_argument("subject_id")
    start.add_argument("--metadata-json")

    for name in ("get", "next", "begin", "pause", "resume", "cancel"):
        command = sub.add_parser(name, help=f"{name.title()} a workflow session.")
        command.add_argument("session_id")

    complete = sub.add_parser("complete", help="Complete the current running step.")
    complete.add_argument("session_id")
    complete.add_argument("outcome")
    complete.add_argument(
        "--evidence",
        action="append",
        default=[],
        metavar="KIND=REF",
        help="Submit required evidence. Repeat for multiple evidence refs.",
    )
    complete.add_argument("--diagnostic")

    wait = sub.add_parser("wait", help="Put the workflow in an explicit wait state.")
    wait.add_argument("session_id")
    wait.add_argument("kind", choices=("user", "run"))
    wait.add_argument("--ref")
    wait.add_argument("--reason")
    return parser


def _run(args: argparse.Namespace) -> Any:
    if args.workspace is not None:
        runtime_paths.update(workspace_dir=args.workspace)

    if args.command == "list":
        return [item.model_dump(mode="json") for item in list_agent_workflows()]
    if args.command == "start":
        return create_agent_workflow_session(
            args.workflow_id,
            subject_kind=args.subject_kind,
            subject_id=args.subject_id,
            metadata=_metadata(args.metadata_json),
        )
    if args.command == "get":
        return get_agent_workflow_session(args.session_id)
    if args.command == "next":
        return next_agent_workflow_action(args.session_id)
    if args.command == "begin":
        return begin_agent_workflow_step(args.session_id)
    if args.command == "complete":
        return complete_agent_workflow_step(
            args.session_id,
            outcome=args.outcome,
            evidence=_evidence(args.evidence),
            diagnostic=args.diagnostic,
        )
    if args.command == "wait":
        return wait_agent_workflow_session(
            args.session_id,
            kind=args.kind,
            ref=args.ref,
            reason=args.reason,
        )
    if args.command == "pause":
        return pause_agent_workflow_session(args.session_id)
    if args.command == "resume":
        return resume_agent_workflow_session(args.session_id)
    if args.command == "cancel":
        return cancel_agent_workflow_session(args.session_id)
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        result = _run(parser.parse_args(argv))
    except Exception as exc:
        _json({"error": type(exc).__name__, "message": str(exc)}, stream=sys.stderr)
        return 2
    _json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
