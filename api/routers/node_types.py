"""Node type catalog API.

The server-side Node Catalog is the single metadata source for built-in, model,
and process nodes.  The Web editor consumes this endpoint instead of duplicating
Node Pack manifest interpretation on the client.
"""
from fastapi import APIRouter

from services.node_catalog import get_node_definitions

router = APIRouter(tags=["nodes"])


@router.get("/node_types")
async def node_types():
    """Return all executable node definitions (builtin, model, process)."""
    return {"nodes": [definition.to_dict() for definition in get_node_definitions()]}
