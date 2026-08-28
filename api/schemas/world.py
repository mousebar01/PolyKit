"""Pydantic vocabulary for server-owned world documents.

World documents are intentionally open-ended.  The Web world editor owns the
shape of the editable ``spec`` and other future fields, while the API owns the
small amount of metadata that makes a document identifiable and portable.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


WORLD_SCHEMA_VERSION = 1
WORLD_KIND = "polykit.world"


class WorldArtifact(BaseModel):
    """An optional reference to an artifact in the PolyKit workspace.

    The world store validates all artifact path spellings (including fields
    supplied through ``extra``) before writing.  Keeping this model open lets
    clients add provenance and renderer metadata without requiring a server
    migration for every new field.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    workspace_path: str | None = Field(default=None, alias="workspacePath")
    path: str | None = None
    kind: str | None = None


class WorldDocument(BaseModel):
    """A versioned, JSON-serializable world document.

    ``spec``, ``instances`` and renderer-specific fields remain open-ended on
    purpose.  ``world_id`` is optional because the URL is the authoritative
    identifier and small clients may submit only the world payload.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: Literal[1] = WORLD_SCHEMA_VERSION
    kind: Literal["polykit.world"] = WORLD_KIND
    world_id: str | None = Field(default=None, alias="worldId")
    id: str | None = None
    spec: Any = None
    # A world may index artifacts by prototype id (the Web editor's native
    # shape) or keep a legacy list.  Persistence validates paths separately,
    # so the envelope stays open to both forms.
    artifacts: Any = Field(default_factory=dict)


# A descriptive alias for callers that prefer the wire-level name.
WorldPayload = WorldDocument
