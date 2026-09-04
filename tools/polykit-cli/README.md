# PolyKit CLI

`polykit.py` is a stdlib-only automation client for the same FastAPI control plane used by the Web UI. It is not an Agent runtime and contains no product logic of its own.

Start PolyKit first, then run:

```bash
python tools/polykit-cli/polykit.py health
python tools/polykit-cli/polykit.py doctor
```

Use `--api-url` or `POLYKIT_API_URL` for a remote/headless server.

## Workflow Runs

```bash
python tools/polykit-cli/polykit.py workflow-run status <run-id>
python tools/polykit-cli/polykit.py workflow-run inspect <run-id>
python tools/polykit-cli/polykit.py workflow-run cancel <run-id>
python tools/polykit-cli/polykit.py workflow-run execute workflow-request.json
```

`inspect` is read-only and returns the persisted node/event/artifact/evidence timeline. It never advances or retries a run.

## Assets and images

```bash
# Image generation keeps the generated mesh by default.
python tools/polykit-cli/polykit.py asset from-image ./chair.png --texture
# Simplification is explicit when a face budget is desired.
python tools/polykit-cli/polykit.py asset from-image ./chair.png --texture --optimize --target-faces 250000
python tools/polykit-cli/polykit.py asset from-text "stylized wooden chair"
python tools/polykit-cli/polykit.py image generate "isolated low-poly lantern"
python tools/polykit-cli/polykit.py image remove-background Workflows/lantern.png
python tools/polykit-cli/polykit.py asset search-external "wooden chair" --category furniture
python tools/polykit-cli/polykit.py asset import-external Chair_01 --resolution 1k
```

External search is read-only. Import is explicit, downloads the selected
Poly Haven glTF bundle through FastAPI, publishes a GLB, and records CC0
attribution/provenance in the workspace.

## World domain

```bash
python tools/polykit-cli/polykit.py world create --name Cabin --prompt "small playable winter cabin"
python tools/polykit-cli/polykit.py world get <world-id>
python tools/polykit-cli/polykit.py world compile-scene <world-id> scene-plan.json
python tools/polykit-cli/polykit.py world build-structure <world-id>
python tools/polykit-cli/polykit.py world validate <world-id> world.construction.validate --run-id <run-id>
python tools/polykit-cli/polykit.py world compose <world-id>
python tools/polykit-cli/polykit.py world attach-asset <world-id> chair Worlds/chair.glb --run-id <run-id>
```

The CLI only translates command-line arguments to HTTP requests. World validation, scene planning, Workflow execution, Node Pack dispatch, artifacts, and persistence remain server-owned.
