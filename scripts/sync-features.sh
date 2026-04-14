#!/bin/bash
# Sync feature files from angzarr-project submodule

FEATURES="angzarr-project/features/client"

if [ ! -d "$FEATURES" ]; then
    echo "Error: Cannot find features at $FEATURES"
    echo "Ensure angzarr-project submodule is initialized"
    exit 1
fi

mkdir -p features
cp -r "$FEATURES/"*.feature features/ 2>/dev/null || true
echo "Features synced from $FEATURES"
