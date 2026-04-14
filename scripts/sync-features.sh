#!/bin/bash
# Sync feature files from angzarr-project submodule
# Features are available via symlink, this script ensures they're accessible

FEATURES="angzarr-project/features/client"

if [ ! -d "$FEATURES" ]; then
    echo "Error: Cannot find features at $FEATURES"
    echo "Ensure angzarr-project submodule is initialized"
    exit 1
fi

# Remove stale symlink or directory, replace with symlink
rm -rf features
ln -s "$FEATURES" features
echo "Features linked from $FEATURES"
