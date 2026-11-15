#!/bin/bash
# Build script for lollypop_ext C++ module
# Uses pkg-config to get compiler flags for dependencies

set -e

# Parse arguments
OUTPUT="$1"
shift
INPUTS=("$@")

# Get Python include path
PYTHON_INCLUDE=$(python3 -c "import sysconfig; print(sysconfig.get_path('include'))")

# Get compiler flags from pkg-config
PKG_CFLAGS=$(pkg-config --cflags gdk-pixbuf-2.0 gstreamer-1.0 gstreamer-pbutils-1.0)
PKG_LIBS=$(pkg-config --libs gdk-pixbuf-2.0 gstreamer-1.0 gstreamer-pbutils-1.0)

# Include directories
INCLUDE_DIRS="-I$(dirname "$0") -I$(dirname "$0")/include -I$PYTHON_INCLUDE"

# Compile
g++ -std=c++17 -O3 -fvisibility=hidden -Wall -Wextra \
    -fPIC -shared \
    $INCLUDE_DIRS \
    $PKG_CFLAGS \
    "${INPUTS[@]}" \
    $PKG_LIBS \
    -o "$OUTPUT"