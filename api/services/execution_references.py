"""Reference semantics shared by execution graph validation and caching.

Legacy plans encode a reference as ``[node_id, output_name]``. Batch-capable
inputs may contain those pairs recursively, for example ``mesh=[[a, mesh],
[b, mesh]]``. Historically the runtime resolved those recursively while graph
analysis and cache signatures only saw top-level pairs, which could schedule a
consumer before its real dependencies or reuse stale cached output.

Nested legacy references inside ``params`` are intentionally not inferred:
ordinary parameter arrays such as ``["indoor", "wood"]`` are structurally
indistinguishable from the legacy reference pair. A future schema version can
use an explicit ``$ref`` object when parameter-level references are required.
A direct top-level params reference remains recognized for backwards
compatibility.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any


Reference = tuple[str, str]


def is_legacy_reference(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(part, str) for part in value)
    )


def _walk(value: Any) -> Iterator[Reference]:
    if is_legacy_reference(value):
        yield (value[0], value[1])
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk(item)


def iter_input_references(input_name: str, value: Any) -> Iterator[Reference]:
    """Yield references carried by one node input.

    Direct references are always supported. Recursive legacy inference is
    enabled for normal capability inputs (including batch mesh/image inputs)
    but disabled below ``params`` because two-string parameter lists are valid
    literals and cannot be distinguished from the legacy pair format.
    """

    if is_legacy_reference(value):
        yield (value[0], value[1])
        return
    if input_name == "params":
        return
    yield from _walk(value)


def referenced_node_ids(input_name: str, value: Any) -> tuple[str, ...]:
    """Return stable unique upstream node ids for dependency accounting."""

    return tuple(dict.fromkeys(node_id for node_id, _output_name in iter_input_references(input_name, value)))


__all__ = [
    "Reference",
    "is_legacy_reference",
    "iter_input_references",
    "referenced_node_ids",
]
