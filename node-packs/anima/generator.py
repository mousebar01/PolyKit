"""Native Diffusers adapter for the official CircleStone Anima model.

The adapter deliberately keeps model execution inside PolyKit's isolated node
pack protocol. It does not start ComfyUI, call a hosted API, or hide weights in
the pack's virtual environment.
"""
from __future__ import annotations

import inspect
import threading
from pathlib import Path
from typing import Callable, Optional

from services.asset_names import output_name
from services.generators.base import BaseGenerator


class AnimaGenerator(BaseGenerator):
    MODEL_ID = "anima/generate"
    DISPLAY_NAME = "Anima (official Diffusers)"
    VRAM_GB = 8

    def load(self) -> None:
        if self._model is not None:
            return
        if not self.is_downloaded():
            raise RuntimeError(
                "Anima weights are not downloaded. Download the official "
                "Diffusers model from the Models page first."
            )

        import torch

        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        model_path = str(self.model_dir)
        try:
            from diffusers import ModularPipeline

            pipe = ModularPipeline.from_pretrained(model_path)
            # The modular index records the Hub repo as each component's
            # source.  We deliberately keep all weights in PolyKit's shared
            # model directory, so override that source with the local root
            # when materialising components (otherwise the pipeline object is
            # created but ``text_encoder`` and friends remain ``None``).
            pipe.load_components(
                pretrained_model_name_or_path=model_path,
                dtype=dtype,
            )
        except (ImportError, AttributeError):
            # Older Diffusers releases expose the converted model through the
            # regular pipeline entry point. Keep this fallback explicit so the
            # official modular path remains the default.
            from diffusers import DiffusionPipeline

            pipe = DiffusionPipeline.from_pretrained(model_path, torch_dtype=dtype)

        if torch.cuda.is_available():
            pipe.to("cuda")
        else:
            pipe.to("cpu")
        self._model = pipe

    def _device(self):
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"

    def _generator(self, seed: int):
        import torch

        device = self._device()
        return torch.Generator(device=device).manual_seed(int(seed))

    def generate(
        self,
        image_bytes: bytes | None,
        params: dict,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        del image_bytes  # Anima is a text-to-image node; the text travels in params.
        self._check_cancelled(cancel_event)
        if self._model is None:
            self.load()

        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Anima requires a non-empty text prompt")

        # These are the quality/safety prefixes recommended by the official
        # model card. The caller can still write Danbooru tags or natural prose.
        positive = f"masterpiece, best quality, score_7, safe, {prompt}"
        negative = str(
            params.get("negative_prompt")
            or "lowres, bad anatomy, bad hands, text, watermark, logo, jpeg artifacts"
        )
        steps = max(1, min(60, int(params.get("steps", 32))))
        guidance = max(1.0, min(8.0, float(params.get("guidance_scale", 4.5))))
        width = max(512, min(1536, int(params.get("width", 1024))))
        height = max(512, min(1536, int(params.get("height", 1024))))
        seed = int(params.get("seed", -1))
        generator = self._generator(seed) if seed >= 0 else None

        if progress_cb:
            progress_cb(5, "Preparing Anima prompt")
        self._check_cancelled(cancel_event)

        kwargs = {
            "prompt": positive,
            "negative_prompt": negative,
            "height": height,
            "width": width,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
        }
        if generator is not None:
            kwargs["generator"] = generator

        # Diffusers versions differ in callback support. Only pass a callback
        # when the loaded pipeline advertises the corresponding argument.
        try:
            signature = inspect.signature(self._model.__call__)
        except (TypeError, ValueError):
            signature = None
        if signature and "callback_on_step_end" in signature.parameters:
            def _step_end(_pipe, _step, _timestep, callback_kwargs):
                self._check_cancelled(cancel_event)
                if progress_cb:
                    progress_cb(10 + round(80 * (_step + 1) / steps), "Generating illustration")
                return callback_kwargs

            kwargs["callback_on_step_end"] = _step_end
            kwargs["callback_on_step_end_tensor_inputs"] = []
        elif signature and "callback" in signature.parameters:
            def _callback(_step, _timestep, _latents):
                self._check_cancelled(cancel_event)
                if progress_cb:
                    progress_cb(10 + round(80 * (_step + 1) / steps), "Generating illustration")

            kwargs["callback"] = _callback
            kwargs["callback_steps"] = 1

        result = self._model(**kwargs)
        self._check_cancelled(cancel_event)
        images = getattr(result, "images", None)
        if not images:
            raise RuntimeError("Anima Diffusers pipeline returned no image")
        image = images[0]

        output_dir = Path(self.outputs_dir or self.model_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = str(params.get("filename_stem") or "anima")
        output_path = output_dir / output_name(stem, tag="anima", ext=".png")
        image.save(output_path, format="PNG")
        if progress_cb:
            progress_cb(100, "Illustration ready")
        return output_path
