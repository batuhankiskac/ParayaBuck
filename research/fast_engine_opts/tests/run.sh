#!/bin/sh
# pytest for research/fast_engine_opts.
#
# Same venv split as research/batch_screen/tests/run.sh: the framework needs numba, which lives
# only in the research venv, while pytest lives only in the project venv.
set -e
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
RES_VENV=${RES_VENV:-$HOME/.venvs/pb-research}
if [ -z "${PROJ_VENV:-}" ]; then
  for cand in "$ROOT/../ParayaBuck/.venv" "$HOME/Desktop/ParayaBuck/.venv" "$ROOT/.venv"; do
    if [ -x "$cand/bin/python" ]; then PROJ_VENV=$cand; break; fi
  done
fi
: "${PROJ_VENV:?set PROJ_VENV to the project venv (it has pytest)}"
SITE=$("$RES_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
cd "$ROOT"
PYTHONPATH="$SITE:$ROOT/src:$ROOT" "$PROJ_VENV/bin/python" -m pytest "$ROOT/research/fast_engine_opts/tests" "$@"
