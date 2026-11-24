#!/usr/bin/env bash
# Run Copilot CLI with a prompt file, on the TRIGGERING USER's own Copilot seat.
#
# Usage: aine/scripts/run_copilot.sh work/prompt.md [label]
#
# 1. Gets the user's 8-hour Copilot token from the AINE Forge proxy (needs AINE_TICKET,
#    AINE_PROXY_URL, AINE_PROXY_SECRET) and masks it in the log.
# 2. Runs Copilot WITHOUT the proxy secret, URL or ticket in its environment, so the AI
#    can't call Confluence itself.
# 3. Appends the transcript to work/aine-session.md.
set -euo pipefail
prompt_file="$1"
label="${2:-copilot}"
mkdir -p work

token=$(python3 aine/scripts/aine_confluence.py copilot-token)
echo "::add-mask::$token"

model_args=()
[ -n "${COPILOT_MODEL:-}" ] && model_args=(--model "$COPILOT_MODEL")

{
  echo ""
  echo "## ${label} — $(date -u +%FT%TZ)"
  echo ""
} >> work/aine-session.md

env -u AINE_PROXY_SECRET -u AINE_PROXY_URL -u AINE_TICKET -u CONF_TOKEN -u CONF_EMAIL \
  COPILOT_GITHUB_TOKEN="$token" \
  copilot -p "$(cat "$prompt_file")" --allow-all-tools "${model_args[@]}" 2>&1 | tee -a work/aine-session.md
exit "${PIPESTATUS[0]}"
