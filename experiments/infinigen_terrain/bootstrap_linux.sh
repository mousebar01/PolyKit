#!/usr/bin/env bash
set -euo pipefail

# Clone the exact Infinigen revision used by this experiment. The terrain
# feature still needs Infinigen's documented native Linux dependencies; this
# script deliberately does not sudo-install system packages.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${INFINIGEN_DIR:-${ROOT}/.external/infinigen}"
ENV_NAME="${INFINIGEN_CONDA_ENV:-infinigen-polykit}"
PIN="3f58bb886bb1bda681d41240344fe3126ac0e9bd"
MODE="${1:---clone-only}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[bootstrap] missing command: $1" >&2
    exit 1
  }
}

need git
mkdir -p "$(dirname "${DEST}")"

if [[ ! -d "${DEST}/.git" ]]; then
  echo "[bootstrap] cloning Infinigen -> ${DEST}"
  git clone https://github.com/princeton-vl/infinigen.git "${DEST}"
fi

cd "${DEST}"
git fetch --tags origin
git checkout --detach "${PIN}"
git submodule update --init --recursive

echo "[bootstrap] Infinigen pinned at $(git rev-parse HEAD)"

if [[ "${MODE}" == "--clone-only" ]]; then
  cat <<'EOF'

Clone complete.

Infinigen Terrain CPU currently requires Linux native build dependencies.
Install the dependencies from Infinigen's Installation.md, then run:

  bash experiments/infinigen_terrain/bootstrap_linux.sh --install

EOF
  exit 0
fi

if [[ "${MODE}" != "--install" ]]; then
  echo "usage: $0 [--clone-only|--install]" >&2
  exit 2
fi

need conda
for cmd in cmake g++; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[bootstrap] warning: ${cmd} is missing; terrain native build will likely fail" >&2
  fi
done

if ! conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "[bootstrap] creating conda env ${ENV_NAME}"
  conda create -y -n "${ENV_NAME}" python=3.11
fi

echo "[bootstrap] installing Infinigen legacy terrain feature set"
env \
  INFINIGEN_MINIMAL_INSTALL=False \
  INFINIGEN_INSTALL_TERRAIN=True \
  conda run -n "${ENV_NAME}" python -m pip install -e ".[infinigen1]"

cat <<EOF

Infinigen terrain environment installed.

Activate it with:
  conda activate ${ENV_NAME}

Then from the PolyKit repository run:
  PYTHONPATH="${ROOT}" python -m experiments.infinigen_terrain.generate \\
    --preset multi_mountains --resolution 1024 --seed 73

EOF
