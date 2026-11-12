#!/usr/bin/env bash
# Update AI CLI tools listed in agents.json (Linux / macOS / WSL)
#
# Usage:
#   ./update_agents.sh                     # update every installed agent
#   ./update_agents.sh --tags core,byok    # update installed agents carrying any of these tags
#   ./update_agents.sh --dry-run           # print the update commands without running them
#
# An agent is only updated when its binary (the "binary" field in agents.json) is on PATH.
# Requires jq to read agents.json.

set -uo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$SCRIPT_DIR/agents.json"

TAGS=""
DRY_RUN=0

usage() {
    echo "Usage: $0 [--tags tag1,tag2] [--dry-run]"
    echo "  -t, --tags   Only update agents with at least one of these tags (default: all)"
    echo "      --dry-run  Print the update commands without running them"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--tags)
            if [[ $# -lt 2 ]]; then
                echo -e "${RED}--tags needs a value${NC}"
                usage
                exit 1
            fi
            TAGS="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown argument: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

if ! command -v jq &>/dev/null; then
    echo -e "${RED}jq is required to read agents.json (e.g. sudo apt-get install jq)${NC}"
    exit 1
fi

if [[ ! -f "$CATALOG" ]]; then
    echo -e "${RED}Catalog not found: $CATALOG${NC}"
    exit 1
fi

# Turn "core,byok" into a JSON array and reject tags that no agent carries.
# jq on Windows writes CRLF, so every jq output is piped through tr to strip \r.
WANT_JSON="$(jq -nc --arg tags "$TAGS" '$tags | split(",") | map(select(length > 0))' | tr -d '\r')"
UNKNOWN="$(jq -r --argjson want "$WANT_JSON" '
    [.agents[].tags[]] as $known
    | [$want[] | select(. as $w | any($known[]; . == $w) | not)]
    | join(", ")
' "$CATALOG" | tr -d '\r')"
if [[ -n "$UNKNOWN" ]]; then
    KNOWN="$(jq -r '[.agents[].tags[]] | unique | join(", ")' "$CATALOG" | tr -d '\r')"
    echo -e "${RED}Unknown tag(s): $UNKNOWN (known tags: $KNOWN)${NC}"
    exit 1
fi

# Ensure ~/.local/bin is on PATH for this session
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

run_command() {
    if (( DRY_RUN )); then
        echo "  [dry-run] $1"
        return 0
    fi
    ( eval "$1" )
}

echo -e "${CYAN}=== Updating AI CLI Tools${TAGS:+ (tags: $TAGS)} ===${NC}"
echo ""

UPDATED=()
SKIPPED=()
FAILED=()

# Each agent is read on fd 3 so update commands that read stdin cannot consume the list
while IFS=$'\t' read -r -u 3 name binary update_cmd; do
    echo -e "${YELLOW}Updating $name...${NC}"
    if ! command -v "$binary" &>/dev/null; then
        echo -e "${YELLOW}- $name not installed ('$binary' not on PATH), skipping${NC}"
        SKIPPED+=("$name")
    elif run_command "$update_cmd"; then
        echo -e "${GREEN}✓ $name updated${NC}"
        UPDATED+=("$name")
    else
        echo -e "${RED}! Error updating $name${NC}"
        FAILED+=("$name")
    fi
    echo ""
done 3< <(jq -r --argjson want "$WANT_JSON" '
    .agents[]
    | select(($want | length) == 0 or any(.tags[]; . as $t | any($want[]; . == $t)))
    | [.name, .binary, .update.unix]
    | @tsv
' "$CATALOG" | tr -d '\r')

echo -e "${CYAN}=== Update Complete ===${NC}"
echo -e "Updated: ${#UPDATED[@]}  Skipped (not installed): ${#SKIPPED[@]}  Failed: ${#FAILED[@]}"
if (( ${#FAILED[@]} > 0 )); then
    echo -e "${RED}Failed: $(IFS=', '; echo "${FAILED[*]}")${NC}"
fi
echo -e "${YELLOW}Please restart your terminal for changes to take effect.${NC}"
if (( ${#FAILED[@]} > 0 )); then
    exit 1
fi
