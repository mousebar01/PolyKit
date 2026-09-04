"""Asset-generation application commands shared by manual and Agent entry points."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from schemas.execution import ExecutionNode, ExecutionPlan, ExecutionSource


class GenerateAssetCommand(BaseModel):
    """Generate a mesh asset from text through explicit AI capabilities."""

    prompt: str = Field(min_length=1, max_length=20_000)
    image_model_id: str = "anima/generate"
    mesh_model_id: str = "trellis2/generate"
    enable_cutout: bool = True
    enable_texture: bool = True
    enable_optimize: bool = True
    target_faces: int = Field(default=100_000, ge=100, le=1_000_000)
    collection: str = "Workflows"
    workflow_id: Optional[str] = None
    world_id: Optional[str] = None
    proto_id: Optional[str] = None
    image_params: dict[str, Any] = Field(default_factory=dict)
    mesh_params: dict[str, Any] = Field(default_factory=dict)
    texture_params: dict[str, Any] = Field(default_factory=dict)


class GenerateAssetFromImageCommand(BaseModel):
    """Generate a mesh asset from an explicit image execution payload.

    ``image`` uses the canonical execution input shape, normally
    ``{"kind": "workspace_path", "path": ...}`` or a bounded base64 payload.
    Multipart adapters should prefer a run-owned workspace path so binary image
    data is not duplicated inside the durable execution snapshot.
    """

    image: dict[str, Any]
    mesh_model_id: str = "trellis2/generate"
    enable_texture: bool = False
    # Image-to-asset generation preserves the model output by default.  Mesh
    # simplification is an explicit opt-in capability, not an implicit step.
    enable_optimize: bool = False
    target_faces: int = Field(default=1_000_000, ge=100, le=1_000_000)
    collection: str = "Workflows"
    workflow_id: Optional[str] = None
    node_id: Optional[str] = None
    world_id: Optional[str] = None
    proto_id: Optional[str] = None
    image_name: Optional[str] = None
    mesh_params: dict[str, Any] = Field(default_factory=dict)
    texture_params: dict[str, Any] = Field(default_factory=dict)


def _refiner_id(mesh_model_id: str) -> str:
    pack_id, separator, node_id = mesh_model_id.rpartition("/")
    if not separator or node_id != "generate":
        raise ValueError(
            f"Texture refinement requires a generate capability id, got '{mesh_model_id}'"
        )
    return f"{pack_id}/refine"


def _provenance_metadata(
    *,
    world_id: str | None,
    proto_id: str | None,
    generation_mode: str,
    generator_id: str,
    node_id: str | None = None,
    image_name: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "generation_kind": "ai",
        "generation_mode": generation_mode,
        "generator_id": generator_id,
        "artifact_kind": "mesh",
    }
    for key, value in {
        "world_id": world_id,
        "proto_id": proto_id,
        "node_id": node_id,
        "image_name": image_name,
    }.items():
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    return metadata


def compile_generate_asset_plan(command: GenerateAssetCommand) -> ExecutionPlan:
    """Compile text-to-asset generation without inventing a saved Workflow."""

    image_params = dict(command.image_params)
    image_params.setdefault("filename_stem", "generated-asset")
    mesh_params = dict(command.mesh_params)
    mesh_params.setdefault("remesh", "none")
    mesh_params.setdefault("enable_texture", False)

    nodes: dict[str, ExecutionNode] = {
        "text": ExecutionNode(
            class_type="polykit.text",
            inputs={"text": command.prompt},
        ),
        "image": ExecutionNode(
            class_type=command.image_model_id,
            inputs={"text": ["text", "text"], "params": image_params},
        ),
    }

    image_node_id = "image"
    if command.enable_cutout:
        nodes["cutout"] = ExecutionNode(
            class_type="image-background-remover/remove-background",
            inputs={
                "image": ["image", "image"],
                "params": {"model": "isnet-anime"},
            },
        )
        image_node_id = "cutout"

    nodes["mesh"] = ExecutionNode(
        class_type=command.mesh_model_id,
        inputs={"image": [image_node_id, "image"], "params": mesh_params},
    )
    final_node_id = "mesh"

    if command.enable_texture:
        texture_params = dict(command.texture_params)
        texture_params.setdefault("texture_resolution", 1024)
        texture_params.setdefault("texture_size", 2048)
        texture_params.setdefault("texture_steps", 12)
        nodes["texture"] = ExecutionNode(
            class_type=_refiner_id(command.mesh_model_id),
            inputs={
                "image": [image_node_id, "image"],
                "mesh": ["mesh", "mesh"],
                "params": texture_params,
            },
        )
        final_node_id = "texture"

    if command.enable_optimize:
        nodes["optimize"] = ExecutionNode(
            class_type="mesh-optimizer/optimize",
            inputs={
                "mesh": [final_node_id, "mesh"],
                "params": {"target_faces": command.target_faces},
            },
        )
        final_node_id = "optimize"

    nodes["output"] = ExecutionNode(
        class_type="polykit.output",
        inputs={"mesh": [final_node_id, "mesh"]},
    )

    return ExecutionPlan(
        source=ExecutionSource(kind="direct", id="assets.generate.text"),
        workflow_id=command.workflow_id,
        prompt=nodes,
        output_node_id="output",
        collection=command.collection,
        metadata=_provenance_metadata(
            world_id=command.world_id,
            proto_id=command.proto_id,
            generation_mode="text",
            generator_id=command.mesh_model_id,
        ),
    )


def compile_generate_asset_from_image_plan(
    command: GenerateAssetFromImageCommand,
) -> ExecutionPlan:
    """Compile image-to-asset generation into the same execution protocol."""

    mesh_params = dict(command.mesh_params)
    mesh_params.setdefault("remesh", "none")
    mesh_params.setdefault("enable_texture", False)

    nodes: dict[str, ExecutionNode] = {
        "image": ExecutionNode(
            class_type="polykit.image",
            inputs={"image": dict(command.image)},
        ),
        "mesh": ExecutionNode(
            class_type=command.mesh_model_id,
            inputs={"image": ["image", "image"], "params": mesh_params},
        ),
    }
    final_node_id = "mesh"

    if command.enable_texture:
        texture_params = dict(command.texture_params)
        texture_params.setdefault("texture_resolution", 1024)
        nodes["texture"] = ExecutionNode(
            class_type=_refiner_id(command.mesh_model_id),
            inputs={
                "image": ["image", "image"],
                "mesh": ["mesh", "mesh"],
                "params": texture_params,
            },
        )
        final_node_id = "texture"

    if command.enable_optimize:
        nodes["optimize"] = ExecutionNode(
            class_type="mesh-optimizer/optimize",
            inputs={
                "mesh": [final_node_id, "mesh"],
                "params": {"target_faces": command.target_faces},
            },
        )
        final_node_id = "optimize"

    nodes["output"] = ExecutionNode(
        class_type="polykit.output",
        inputs={"mesh": [final_node_id, "mesh"]},
    )

    return ExecutionPlan(
        source=ExecutionSource(kind="direct", id="assets.generate.image"),
        workflow_id=command.workflow_id,
        prompt=nodes,
        output_node_id="output",
        collection=command.collection,
        metadata=_provenance_metadata(
            world_id=command.world_id,
            proto_id=command.proto_id,
            node_id=command.node_id,
            image_name=command.image_name,
            generation_mode="image",
            generator_id=command.mesh_model_id,
        ),
    )


__all__ = [
    "GenerateAssetCommand",
    "GenerateAssetFromImageCommand",
    "compile_generate_asset_from_image_plan",
    "compile_generate_asset_plan",
]
