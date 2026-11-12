#!/usr/bin/env bash
# Install AI CLI tools listed in agents.json (Linux / macOS / WSL)
#
# Usage:
#   ./install_agents.sh                     # install every agent
#   ./install_agents.sh --tags core,byok    # install agents carrying any of these tags
#   ./install_agents.sh --dry-run           # print the install commands without running them
#
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
    echo "  -t, --tags   Only install agents with at least one of these tags (default: all)"
    echo "      --dry-run  Print the install commands without running them"
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

echo -e "${CYAN}=== Installing AI CLI Tools${TAGS:+ (tags: $TAGS)} ===${NC}"
echo ""

FAILED=()

# Each agent is read on fd 3 so installers that read stdin cannot consume the list
while IFS=$'\t' read -r -u 3 name install_cmd; do
    echo -e "${YELLOW}Installing $name...${NC}"
    if run_command "$install_cmd"; then
        echo -e "${GREEN}✓ $name installed${NC}"
    else
        echo -e "${RED}! Error installing $name${NC}"
        FAILED+=("$name")
    fi
    echo ""
done 3< <(jq -r --argjson want "$WANT_JSON" '
    .agents[]
    | select(($want | length) == 0 or any(.tags[]; . as $t | any($want[]; . == $t)))
    | [.name, .install.unix]
    | @tsv
' "$CATALOG" | tr -d '\r')

# Add ~/.local/bin to PATH in shell config if missing
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [[ -f "$rc" ]] && ! grep -q 'local/bin' "$rc"; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
        echo -e "${GREEN}✓ Added ~/.local/bin to PATH in $rc${NC}"
    fi
done
echo ""

echo -e "${CYAN}=== Installation Complete ===${NC}"
if (( ${#FAILED[@]} > 0 )); then
    echo -e "${RED}Failed: $(IFS=', '; echo "${FAILED[*]}")${NC}"
    echo -e "${YELLOW}Please restart your terminal or run: source ~/.bashrc (or ~/.zshrc)${NC}"
    exit 1
fi
echo -e "${YELLOW}Please restart your terminal or run: source ~/.bashrc (or ~/.zshrc)${NC}"
