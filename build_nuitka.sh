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
    # Remove the previous output so the standalone folder always matches
    # the current build inputs and does not keep stale DLLs around.
    rm -rf "$RESOLVED_OUTPUT_DIR"
fi

set -- \
    run -n "$CONDA_ENV" \
    python -m nuitka \
    --standalone \
    --enable-plugin=pyside6 \
    --include-module=av.utils \
    --windows-console-mode=disable \
    --include-data-dir=resources=resources \
    --windows-icon-from-ico=resources/icon.ico \
    --product-name=LunaPlayer \
    --company-name=LunaPlayer \
    --file-description="LunaPlayer - 音乐播放器" \
    --file-version=1.0.0.0 \
    --product-version=1.0.0.0 \
    --output-filename=LunaPlayer.exe \
    "--output-dir=$OUTPUT_DIR"

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
echo "Executable: $DIST_DIR/LunaPlayer.exe"
