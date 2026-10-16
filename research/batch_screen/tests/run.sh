#!/bin/sh
# pytest for research/batch_screen.
#
# The framework needs numba, which lives only in the research venv
# (~/.venvs/pb-research); pytest lives only in the project venv. Neither venv has both, so run
# the project venv's pytest with the research venv's site-packages prepended (pytest and its
# dependencies are pure Python and resolve from the project venv).
set -e
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
RES_VENV=${RES_VENV:-$HOME/.venvs/pb-research}
PROJ_VENV=${PROJ_VENV:-$ROOT/../ParayaBuck/.venv}
SITE=$("$RES_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
cd "$ROOT"
PYTHONPATH="$SITE:$ROOT/src:$ROOT" "$PROJ_VENV/bin/python" -m pytest "$ROOT/research/batch_screen/tests" "$@"
