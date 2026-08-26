"""Model execution boundary for one prepared generator invocation.

Generators historically expose ``outputs_dir`` as mutable instance state. The
server is currently single-accelerator/serialized, but callers should not own
or leak that mutable state. This adapter scopes the output directory to one
invocation and restores the generator afterwards, providing a migration path
toward fully stateless generator APIs.
"""
from __future__ import annotations

import inspect
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from services.model_runtime_registry import model_runtime_registry


ProgressCallback = Optional[Callable[[int, str], None]]


def execute_model(
    model_id: str,
    primary_input: Any,
    params: dict,
    output_dir: Path,
    progress_cb: ProgressCallback = None,
    cancel_event: Optional[threading.Event] = None,
):
    """Execute one model invocation with request-scoped output state."""
    model_runtime_registry.switch_model(model_id, allow_during_generation=True)
    generator = model_runtime_registry.get_active()
    previous_output_dir = getattr(generator, "outputs_dir", None)
    generator.outputs_dir = Path(output_dir)
    try:
        supports_cancel = "cancel_event" in inspect.signature(generator.generate).parameters
        if supports_cancel:
            return generator.generate(primary_input, params, progress_cb, cancel_event)
        return generator.generate(primary_input, params, progress_cb)
    finally:
        generator.outputs_dir = previous_output_dir
