#!/bin/sh
set -eu
# Prefer the caller's toolchain. A locally installed, ignored toolchain is also
# supported for the competition host; this script never downloads anything.
if command -v go >/dev/null 2>&1; then
    go_tool=go
elif [ -x .tools/go/bin/go ]; then
    go_tool=.tools/go/bin/go
else
    echo 'resample-go: install Go 1.23 or newer and add go to PATH' >&2
    exit 1
fi
CGO_ENABLED=0 "$go_tool" build -trimpath -ldflags='-s -w' -o bin/resample .
