#!/usr/bin/env bash
# Phase 0 bootstrap: build gerion0/libgpod + python-gpod bindings on macOS arm64.
#
# Installs native deps via Homebrew, creates a uv-managed venv at $REPO_ROOT/.venv
# (using brew's Python so the meson-built bindings link against the same ABI),
# syncs PyPI deps via `uv sync`, then clones libgpod into vendor/, builds with
# meson, and installs the native lib + python bindings into the same venv.
#
# Idempotent: re-running updates the clone and rebuilds. `--inexact` on
# `uv sync` preserves the externally-installed gpod bindings on subsequent
# syncs.
#
# Requires: Homebrew, uv (https://docs.astral.sh/uv/).

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
command -v uv   >/dev/null 2>&1 || die "uv not found; install via 'brew install uv' or see https://docs.astral.sh/uv/"

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
  glib libplist sqlite gdk-pixbuf libxml2 libusb \
  pygobject3 ffmpeg

log "Creating uv-managed venv at $VENV_DIR (python=$BREW_PY)"
if [[ ! -d "$VENV_DIR" ]]; then
  uv venv --python "$BREW_PY" "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"

log "Syncing PyPI deps via uv (--inexact preserves the gpod bindings on re-runs)"
(cd "$REPO_ROOT" && uv sync --inexact)

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

# python-gpod's Database._load_gtkpod_extended_info uses `_itdb_file` as a
# str, but itdb_get_itunesdb_path returns bytes. Without decoding, the
# computed `.ext` path is literally `b'/.../iTunesDB'.ext` — so gtkpod extended
# info (where we store per-track sha1 for dedupe) never loads back. Patch it.
python_ipod="$LIBGPOD_DIR/bindings/python/ipod.py"
if ! grep -q "isinstance(itdb_file, bytes)" "$python_ipod"; then
  python3 - <<PY
import re, pathlib
p = pathlib.Path("$python_ipod")
src = p.read_text()
old = (
    '    def _load_gtkpod_extended_info(self):\n'
    '        """Read extended information from a gtkpod .ext file."""\n'
    '        itdbext_file = "%s.ext" % (self._itdb_file)\n\n'
    '        if os.path.exists(self._itdb_file) and os.path.exists(itdbext_file):\n'
    '            gtkpod.parse(itdbext_file, self, self._itdb_file)\n'
)
new = (
    '    def _load_gtkpod_extended_info(self):\n'
    '        """Read extended information from a gtkpod .ext file."""\n'
    '        itdb_file = self._itdb_file\n'
    '        if isinstance(itdb_file, bytes):\n'
    '            itdb_file = itdb_file.decode("UTF-8")\n'
    '        itdbext_file = "%s.ext" % itdb_file\n\n'
    '        if os.path.exists(itdb_file) and os.path.exists(itdbext_file):\n'
    '            gtkpod.parse(itdbext_file, self, itdb_file)\n'
)
assert old in src, "ipod.py layout changed; rewrite the patch"
p.write_text(src.replace(old, new))
PY
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
  -Dsysinfo-ng=enabled \
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

log "Done. Drive the CLI via: uv run ipodsync doctor   (or: source $VENV_DIR/bin/activate)"
