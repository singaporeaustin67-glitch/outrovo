#!/usr/bin/env bash
# Verifies Outrovo production end-to-end. Usage: bash verify.sh [BASE_URL]
# Defaults to the Render deployment. Run after pasting env vars into Render.
BASE="${1:-https://outrovo-psgn.onrender.com}"
pass=0; fail=0
check() {
  if [ "$2" = "$3" ]; then echo "  PASS  $1"; pass=$((pass+1)); else echo "  FAIL  $1 (want $2, got $3)"; fail=$((fail+1)); fi
}
H=$(curl -s --max-time 30 "$BASE/api/health")
echo "Health:"
check "websearch enabled"   "True" "$(echo "$H" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sources"]["websearch"])')"
check "github enabled"      "True" "$(echo "$H" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sources"]["github"])')"
check "outreach configured" "True" "$(echo "$H" | python3 -c 'import json,sys;print(json.load(sys.stdin)["outreach"]["sending_configured"])')"
echo "Live search (local business -> needs websearch):"
R=$(curl -s --max-time 240 -X POST "$BASE/api/search" -H "Content-Type: application/json" -d '{"query":"coffee shop owners in Austin"}')
check "returns results" "True" "$(echo "$R" | python3 -c 'import json,sys;print(bool(json.load(sys.stdin)["results"]))')"
echo "Outreach followups endpoint:"
check "followups reachable" "True" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/api/outreach/followups" | grep -q 200 && echo True || echo False)"
echo; echo "== $pass passed, $fail failed =="
