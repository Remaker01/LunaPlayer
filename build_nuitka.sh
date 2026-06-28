#!/usr/bin/env sh

set -eu

CONDA_ENV="smallplayer"
OUTPUT_DIR="dist/nuitka-fixed"
CACHE_DIR=".nuitka-cache"
KEEP_BUILD_DIR=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --conda-env)
            CONDA_ENV="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --cache-dir)
            CACHE_DIR="$2"
            shift 2
            ;;
        --keep-build-dir)
            KEEP_BUILD_DIR=1
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_ROOT"

RESOLVED_OUTPUT_DIR="$PROJECT_ROOT/$OUTPUT_DIR"
RESOLVED_CACHE_DIR="$PROJECT_ROOT/$CACHE_DIR"

if ! command -v conda >/dev/null 2>&1; then
    echo "Conda is not available in PATH." >&2
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/main.py" ]; then
    echo "main.py was not found in the project root." >&2
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/resources/icon.ico" ]; then
    echo "resources/icon.ico was not found." >&2
    exit 1
fi

mkdir -p "$RESOLVED_CACHE_DIR"

if [ -d "$RESOLVED_OUTPUT_DIR" ]; then
    rm -rf "$RESOLVED_OUTPUT_DIR"
fi

# ── OS-specific Nuitka options ──────────────────────────────────
UNAME_S=$(uname -s)
case "$UNAME_S" in
    Linux*)
        ICON_OPT="--linux-icon=resources/icon.png"
        OUTPUT_FILENAME="LunaPlayer"
        ;;
    Darwin*)
        ICON_OPT="--macos-app-icon=resources/icon.png"
        OUTPUT_FILENAME="LunaPlayer"
        ;;
    CYGWIN*|MINGW*|MSYS*)
        ICON_OPT="--windows-icon-from-ico=resources/icon.ico"
        OUTPUT_FILENAME="LunaPlayer.exe"
        WIN_EXTRA="--windows-console-mode=disable"
        ;;
    *)
        ICON_OPT=""
        OUTPUT_FILENAME="LunaPlayer"
        WIN_EXTRA=""
        ;;
esac

set -- \
    run -n "$CONDA_ENV" \
    python -m nuitka \
    --standalone \
    --enable-plugin=pyside6 \
    --include-module=av.utils \
    --include-data-dir=resources=resources \
    $ICON_OPT \
    --product-name=LunaPlayer \
    --company-name=LunaPlayer \
    --file-description="LunaPlayer - 音乐播放器" \
    --file-version=1.0.0.0 \
    --product-version=1.0.0.0 \
    "--output-filename=$OUTPUT_FILENAME" \
    "--output-dir=$OUTPUT_DIR"

# Windows-only flags go after the base arguments.
if [ -n "${WIN_EXTRA:-}" ]; then
    set -- "$@" "$WIN_EXTRA"
fi

if [ "$KEEP_BUILD_DIR" -eq 0 ]; then
    set -- "$@" --remove-output
fi

set -- "$@" main.py

echo "Building LunaPlayer with Nuitka..."
echo "  Conda env : $CONDA_ENV"
echo "  Output dir: $RESOLVED_OUTPUT_DIR"
echo "  Cache dir : $RESOLVED_CACHE_DIR"

NUITKA_CACHE_DIR="$RESOLVED_CACHE_DIR" conda "$@"

DIST_DIR="$RESOLVED_OUTPUT_DIR/main.dist"
if [ ! -d "$DIST_DIR" ]; then
    echo "Build finished without producing $DIST_DIR." >&2
    exit 1
fi

echo
echo "Build completed successfully."
echo "Executable: $DIST_DIR/$OUTPUT_FILENAME"
