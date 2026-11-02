#!/usr/bin/env bash
# One-time, run by a human with admin on the repo: branch protection for main.
# One non-author approving review, the sdlc-gate check required, stale approvals dismissed,
# no force pushes, no deletions.
#
# Usage: bash protect_main.sh owner/repo [branch=main] [check=gate]
set -euo pipefail
REPO="${1:?owner/repo}"
BRANCH="${2:-main}"
CHECK="${3:-gate}"
gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" --input - <<JSON
{
  "required_status_checks": { "strict": true, "contexts": ["$CHECK"] },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true
}
JSON
echo "$BRANCH protected on $REPO: 1 non-author review, '$CHECK' required, no force pushes."
