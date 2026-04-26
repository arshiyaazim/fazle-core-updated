#!/bin/bash
# Sprint commit workflow — stage, commit, push to develop
# Usage: ./scripts/git-sprint.sh [commit-message]
# Default message: "Sprint updates"

set -e

COMMIT_MSG="${1:-Sprint updates}"
BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ "$BRANCH" != "develop" ]; then
    echo "❌ Not on develop branch (currently on $BRANCH). Aborting."
    exit 1
fi

echo "📝 Staging changes..."
git add -p

if git diff --cached --quiet; then
    echo "⚠️  No changes staged. Exiting."
    exit 0
fi

echo "💾 Committing: $COMMIT_MSG"
git commit -m "$COMMIT_MSG"

echo "🚀 Pushing to origin/develop..."
git push origin develop

echo "✅ Done!"
