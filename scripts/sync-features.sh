#!/usr/bin/env bash
# Sync feature files from angzarr core repository.
#
# This script downloads the client feature files from the angzarr core repo
# and places them in the features/ directory for testing.

set -euo pipefail

REPO="angzarr-io/angzarr"
BRANCH="${1:-main}"
FEATURES_DIR="features"

echo "Syncing feature files from $REPO ($BRANCH)..."

# Create features directory if it doesn't exist
mkdir -p "$FEATURES_DIR"

# Download feature files using GitHub API
curl -sL "https://api.github.com/repos/$REPO/contents/features/client?ref=$BRANCH" | \
    jq -r '.[] | select(.type == "file") | .download_url' | \
    while read -r url; do
        filename=$(basename "$url")
        echo "  Downloading $filename..."
        curl -sL "$url" -o "$FEATURES_DIR/$filename"
    done

echo "Done. Feature files synced to $FEATURES_DIR/"
