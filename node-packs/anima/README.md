# Anima (official Diffusers)

This official PolyKit node pack exposes CircleStone Labs' Anima 2B model as a
native text-to-image node. It uses the Hugging Face Diffusers conversion and
the existing isolated node-pack runner; it does not start ComfyUI or call a
hosted generation API.

1. Open **Models → Anima → Setup/Repair** to create the isolated Python
   environment.
2. Accept the Anima model license on Hugging Face and configure a Hub token if
   required by your account.
3. Download `circlestone-labs/Anima-Base-v1.0-Diffusers` from the Models page.
4. Connect a `Text` node to `anima/generate`, then connect its image output to
   the editor's `Preview` node or to `polykit.image_output` in a server/API workflow.

The Base checkpoint is intended for anime/illustration work, not photorealism.
The adapter follows the model-card quality prefix and exposes the recommended
30–50 step / CFG 4–5 range. Native transparency is not claimed by Anima; a
future, separately evaluated alpha-matte node can be added after the image
quality baseline is accepted.

License note: Anima's model license is non-commercial. Review the upstream
terms before shipping it in a commercial product or hosted service.
