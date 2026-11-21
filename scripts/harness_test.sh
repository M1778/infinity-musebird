#!/usr/bin/env bash
# Hardened harness test: does /inf-muse actually work when a real coding harness
# launches it? This is deliberately end-to-end and outside pytest: it boots a real
# `claude` process with the real MCP server, the real skill, and a real Muse session.
#
#   ./scripts/harness_test.sh              # both flows
#   ./scripts/harness_test.sh --single     # single job only
#   ./scripts/harness_test.sh --fanout     # fan-out only
#
# Requirements: `claude` on PATH, a completed `musebird login`, and a provider
# launcher in $MUSEBIRD_CLAUDE_CMD (default: `claude`, i.e. the already-configured app;
# override with e.g. `cc-switch start claude infinityrouter --`).
set -uo pipefail
cd "$(dirname "$0")/.."

FLOW="${1:-all}"
WORK="${MUSEBIRD_HARNESS_DIR:-/tmp/opencode/harness-test}"
CLAUDE_CMD="${MUSEBIRD_CLAUDE_CMD:-claude}"
MODEL="${MUSEBIRD_HARNESS_MODEL:-fable}"
PASS=0
FAIL=0

info() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

info "preflight"
command -v claude >/dev/null && ok "claude on PATH ($(claude --version 2>/dev/null | head -1))" \
  || { bad "claude not on PATH"; exit 1; }
musebird doctor >/dev/null 2>&1 && ok "musebird doctor is green" \
  || { bad "musebird doctor failed; run it for detail"; exit 1; }
claude mcp list 2>/dev/null | grep -q "musebird.*Connected" && ok "claude sees musebird MCP as Connected" \
  || bad "claude cannot connect to the musebird MCP server"
test -f "$HOME/.claude/skills/inf-muse/SKILL.md" && ok "inf-muse skill installed" \
  || bad "inf-muse skill missing"

# A throwaway git repo per run, so landing is verifiable and the user's tree is safe.
rm -rf "$WORK"; mkdir -p "$WORK/proj"
PROJ="$WORK/proj"
git -C "$PROJ" init -q -b main
printf 'seed\n' > "$PROJ/README.md"
git -C "$PROJ" -c user.name=t -c user.email=t@t add -A
git -C "$PROJ" -c user.name=t -c user.email=t@t commit -qm seed
ok "scratch repo at $PROJ"

run_flow() {
  local mode="$1" prompt="$2" tag="$3"
  info "$tag: driving a real harness turn"
  local before jobs_before
  before=$(git -C "$PROJ" status --porcelain | wc -l)
  # Only jobs created by this run count; earlier testing leaves records behind.
  jobs_before=$(ls "$HOME/.infinity-musebird/jobs/"*.json 2>/dev/null | wc -l)

  # The model must read the skill and call the tools; it is not told how.
  # `-p` has nobody to answer a permission prompt, so the MCP tools and the repo
  # edits this test needs must be granted up front. This is exactly what a user sees
  # as "it asked for permission and stopped", and it is a real integration finding.
  local allowed="mcp__musebird Bash(git *) Read Write Edit Glob Grep"
  ( cd "$PROJ" && timeout 900 $CLAUDE_CMD -p "$prompt" --model "$MODEL" \
      --allowedTools "$allowed" --permission-mode acceptEdits 2>&1 | tail -40 ) > "$WORK/$tag.log"
  echo "  --- harness output (tail) ---"
  sed 's/^/  /' "$WORK/$tag.log" | tail -18

  # Assertions are on observable state, not on what the model said.
  local jobs_after job
  jobs_after=$(ls "$HOME/.infinity-musebird/jobs/"*.json 2>/dev/null | wc -l)
  check "$tag started a NEW musebird job" '[ "'"$jobs_after"'" -gt "'"$jobs_before"'" ]'
  # Newest job by mtime is this run's.
  job=$(ls -t "$HOME/.infinity-musebird/jobs/"*.json 2>/dev/null | head -1 | xargs -r basename | sed 's/\.json$//')
  if [ -n "$job" ]; then
    local state branch
    state=$(musebird jobs show "$job" 2>/dev/null | sed -n 's/.*"state": "\([a-z_]*\)".*/\1/p' | head -1)
    branch=$(musebird jobs show "$job" 2>/dev/null | sed -n 's/.*"branch": "\([^"]*\)".*/\1/p' | head -1)
    check "$tag job reached done (state=$state)" '[ "$state" = done ]'
    if [ -n "$branch" ]; then
      ok "$tag produced a review branch: $branch"
      check "$tag branch has no deletions" \
        '[ -z "$(git -C "'"$PROJ"'" diff --diff-filter=D --name-only HEAD...'"$branch"')" ]'
      check "$tag branch contains the seed file" \
        'git -C "'"$PROJ"'" ls-tree -r --name-only "'"$branch"'" | grep -qx README.md'
    else
      bad "$tag produced no branch"
    fi
    # The deliverable must be ON THE BRANCH, because that is the contract: Muse's
    # output lands for review, not in the working tree.
    local want=""
    case "$tag" in
      single) want="hello.txt" ;;
      fanout) want="poem.txt" ;;
    esac
    if [ -n "$want" ]; then
      check "$tag deliverable $want is on the branch" \
        'git -C "'"$PROJ"'" ls-tree -r --name-only "'"$branch"'" | grep -q "'"$want"'"'
      check "$tag did not write $want into the working tree" \
        '! git -C "'"$PROJ"'" ls-files --error-unmatch "'"$want"'" >/dev/null 2>&1'
    fi
    check "$tag left the working tree untouched" \
      '[ "$(git -C "'"$PROJ"'" status --porcelain | wc -l)" = "'"$before"'" ]'
  fi
}

if [ "$FLOW" = all ] || [ "$FLOW" = --single ] || [ "$FLOW" = single ]; then
  run_flow single \
    "/inf-muse In the current directory, create a file named hello.txt containing exactly the single line: harness hello" \
    single
fi

if [ "$FLOW" = all ] || [ "$FLOW" = --fanout ] || [ "$FLOW" = fanout ]; then
  run_flow fanout \
    "/inf-muse-tokenburner In the current directory, use 3 agents to each write their own part of a three-line poem into poem.txt, then merge them" \
    fanout
  # A fan-out job should leave per-agent evidence in its landing area.
  local_landing=$(ls -dt "$PROJ"/.musebird/landing/*/ 2>/dev/null | head -1)
  if [ -n "${local_landing:-}" ]; then
    check "fan-out landed per-agent directories" \
      'ls -d "'"$local_landing"'"*/agent-* >/dev/null 2>&1 || ls -d "'"$local_landing"'"unpacked/agent-* >/dev/null 2>&1'
  fi
fi

info "result"
printf '  %d passed, %d failed\n' "$PASS" "$FAIL"
printf '  logs in %s\n' "$WORK"
[ "$FAIL" -eq 0 ] || exit 1
