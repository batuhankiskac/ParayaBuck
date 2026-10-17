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
if [ -z "${PROJ_VENV:-}" ]; then
  # this suite usually runs from a git worktree, which is not next to the main checkout: try the
  # layouts a worktree can have before giving up.
  for cand in "$ROOT/../ParayaBuck/.venv" "$HOME/Desktop/ParayaBuck/.venv" "$ROOT/.venv"; do
    if [ -x "$cand/bin/python" ]; then PROJ_VENV=$cand; break; fi
  done
fi
: "${PROJ_VENV:?set PROJ_VENV to the project venv (it has pytest)}"
SITE=$("$RES_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
cd "$ROOT"
PYTHONPATH="$SITE:$ROOT/src:$ROOT" "$PROJ_VENV/bin/python" -m pytest "$ROOT/research/batch_screen/tests" "$@"
