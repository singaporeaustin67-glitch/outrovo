#!/usr/bin/env bash
# Outrovo setup — fills in every free credential in one pass.
# Usage:  bash setup.sh
# Requires: python3, curl.
set -uo pipefail

cd "$(dirname "$0")"
ENV_FILE=".env"
touch "$ENV_FILE"

get() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-; }

echo "== Outrovo setup =="

LLM_KEY="$(get LLM_API_KEY)"
if [ -n "$LLM_KEY" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 \
    -H "Authorization: Bearer $LLM_KEY" https://api.groq.com/openai/v1/models)
  if [ "$code" = "200" ]; then echo "LLM key: valid"; else echo "LLM key: REJECTED ($code)"; fi
else
  echo "LLM key missing — free signup: https://console.groq.com/keys (set LLM_API_KEY in .env)"
fi

GH="$(get GITHUB_TOKEN)"
if [ -n "$GH" ]; then
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 -H "Authorization: Bearer $GH" https://api.github.com/user)
  if [ "$code" = "200" ]; then echo "GitHub token: valid"; else echo "GitHub token: REJECTED ($code)"; fi
else
  echo "GitHub token missing. 20s to create (no scopes needed):"
  echo "  https://github.com/settings/tokens/new"
fi

TV="$(get TAVILY_API_KEY)"
if [ -n "$TV" ]; then echo "Tavily key: set"; else echo "Tavily key missing — free key: https://app.tavily.com (set TAVILY_API_KEY in .env)"; fi

if [ -z "$(get SMTP_HOST)" ]; then
  echo "SMTP (optional, enables outreach sending): Gmail app password (smtp.gmail.com:587)"
  echo "  or Resend free SMTP -> SMTP_HOST/PORT/USER/PASS/FROM_EMAIL in .env"
fi

echo
echo "== Values to paste into Render -> outrovo -> Environment =="
for k in LLM_BASE_URL LLM_API_KEY LLM_MODEL GITHUB_TOKEN TAVILY_API_KEY SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS FROM_EMAIL FROM_NAME; do
  v="$(get "$k")"
  if [ -n "$v" ]; then
    case "$k" in
      LLM_BASE_URL|LLM_MODEL|SMTP_HOST|SMTP_PORT|FROM_NAME|FROM_EMAIL) printf "%s=%s\n" "$k" "$v" ;;
      *) printf "%s=%s (masked; see .env)\n" "$k" "${v:0:6}..." ;;
    esac
  fi
done
echo
echo "Then: Render -> Manual Deploy -> Deploy latest commit."
