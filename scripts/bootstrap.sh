#!/usr/bin/env bash
# Phase 0 bootstrap: build gerion0/libgpod + python-gpod bindings on macOS arm64.
#
# Installs native deps via Homebrew, clones libgpod into vendor/, builds with
# meson, and installs everything (native libs + python bindings) into a project
# venv at $REPO_ROOT/.venv — brew's system Python is never modified.
#
# Idempotent: re-running updates the clone and rebuilds.
#
# MacPorts alternative: see README.md. This script targets Homebrew.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor"
VENV_DIR="$REPO_ROOT/.venv"
LIBGPOD_REPO="https://github.com/gerion0/libgpod.git"
LIBGPOD_DIR="$VENDOR_DIR/libgpod"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "macOS only"
[[ "$(uname -m)" == "arm64" ]] || log "warning: not arm64 ($(uname -m)); proceeding anyway"
command -v brew >/dev/null 2>&1 || die "Homebrew not found; install from https://brew.sh/"

BREW_PREFIX="$(brew --prefix)"
BREW_PY="$BREW_PREFIX/bin/python3"
[[ -x "$BREW_PY" ]] || die "expected brew python3 at $BREW_PY — run: brew install python"

export PKG_CONFIG_PATH="$BREW_PREFIX/lib/pkgconfig:$BREW_PREFIX/share/pkgconfig:${PKG_CONFIG_PATH:-}"
# sqlite and libxml2 are keg-only in Homebrew — expose their pkgconfig explicitly.
for keg in sqlite libxml2; do
  if [[ -d "$BREW_PREFIX/opt/$keg/lib/pkgconfig" ]]; then
    PKG_CONFIG_PATH="$BREW_PREFIX/opt/$keg/lib/pkgconfig:$PKG_CONFIG_PATH"
  fi
done
export PKG_CONFIG_PATH

log "Installing Homebrew deps"
brew install \
  pkg-config meson ninja swig \
  glib libplist sqlite gdk-pixbuf libxml2 \
  pygobject3 ffmpeg

log "Creating project venv at $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
  "$BREW_PY" -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet mutagen

log "Cloning/updating libgpod (gerion0 fork) into $LIBGPOD_DIR"
mkdir -p "$VENDOR_DIR"
if [[ ! -d "$LIBGPOD_DIR/.git" ]]; then
  git clone "$LIBGPOD_REPO" "$LIBGPOD_DIR"
else
  git -C "$LIBGPOD_DIR" fetch --quiet
  git -C "$LIBGPOD_DIR" reset --hard --quiet origin/HEAD
fi

# Homebrew ships libplist's pkg-config file as libplist-2.0.pc; upstream
# meson.build uses the unversioned name `libplist`, which only resolves on
# distros (Debian/Fedora) that also install a compat libplist.pc.
if grep -q "dependency('libplist')" "$LIBGPOD_DIR/meson.build"; then
  sed -i '' "s/dependency('libplist')/dependency('libplist-2.0')/" "$LIBGPOD_DIR/meson.build"
fi

# bindings/python/meson.build computes the install dir as
#   py_module = get_option('prefix') + py3_inst.get_path('purelib') / project_name
# On macOS Homebrew Python (and any config where sysconfig returns an absolute
# purelib), that produces `$PREFIX$PREFIX/lib/.../gpod` — a nested absolute
# path. Drop the redundant `prefix +` so it installs to the real purelib.
if grep -q "get_option('prefix') + py3_inst.get_path('purelib')" "$LIBGPOD_DIR/bindings/python/meson.build"; then
  sed -i '' "s|get_option('prefix') + py3_inst.get_path('purelib')|py3_inst.get_path('purelib')|" \
    "$LIBGPOD_DIR/bindings/python/meson.build"
fi

log "Configuring libgpod build (prefix=$VENV_DIR)"
cd "$LIBGPOD_DIR"
rm -rf build
# Put venv's python first so meson's find_installation('python3') picks it.
# python.install_env=prefix makes meson compute install paths relative to
# --prefix rather than from absolute sysconfig paths (needed because
# bindings/python/meson.build does `prefix + purelib`).
PATH="$VENV_DIR/bin:$BREW_PREFIX/bin:$PATH" meson setup build \
  --prefix="$VENV_DIR" \
  -Dpython.install_env=prefix \
  -Dudev=disabled \
  -Dios=disabled \
  -Dsgutils=disabled \
  -Dsysinfo-ng=disabled \
  -Dmono=disabled \
  -Dtaglib=disabled \
  -Ddoc=disabled \
  -Dtest=false

log "Building libgpod"
meson compile -C build

log "Installing libgpod into $VENV_DIR"
meson install -C build

log "Verifying import from venv"
"$VENV_PY" -c "import gpod; print('gpod', gpod.version, 'from', gpod.__file__)"
"$VENV_PY" - <<'PY'
import gpod
try:
    gpod.Database('/nonexistent/path')
except Exception as e:
    print(f'expected error on nonexistent DB: {type(e).__name__}: {e}')
else:
    raise SystemExit('BUG: Database(nonexistent) did not raise')
PY

log "Done. Activate the venv with: source $VENV_DIR/bin/activate"
