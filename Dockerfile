FROM ubuntu:24.04

# Empty = install every agent in agents.json. Comma-separated tags (core,tier2,byok,free-tokens) limit the install.
ARG AGENT_TAGS=""

ENV DEBIAN_FRONTEND=noninteractive
# Install locations used by the agent installers (Kilo: ~/.kilo/bin, OpenCode: ~/.opencode/bin)
ENV PATH="/root/.local/bin:/root/.kilo/bin:/root/.opencode/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq unzip xz-utils gnupg tmux python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu's packaged Node.js is too old for several npm-based agents, so use the NodeSource LTS build
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI: the GitHub Project orchestration layer is driven through `gh`
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# uv for Python tooling (the aao dashboard package and reference projects under external/ use it),
# plus clanker-usage, the primary subscription-quota source for the Quotas dashboard
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && /root/.local/bin/uv tool install clanker-usage

# playwright-cli drives web chat UIs; install Chromium plus its OS libraries with the
# playwright-core version bundled inside @playwright/cli so the two always match.
RUN npm install -g @playwright/cli \
    && PWCORE="$(find "$(npm root -g)/@playwright/cli" -path '*playwright-core/cli.js' | head -1)" \
    && node "$PWCORE" install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/any-agent-orchestrator
COPY agents.json install_agents.sh update_agents.sh ./
# Both scripts are on PATH: `docker compose run --rm agents update_agents.sh`
ENV PATH="/opt/any-agent-orchestrator:${PATH}"

# Agents write per-install state (machine IDs, backups) during install; remove it so the image carries no identity.
# Credentials are never baked in: docker-compose.yml bind-mounts them from the host at run time.
RUN chmod +x install_agents.sh update_agents.sh \
    && ./install_agents.sh --tags "${AGENT_TAGS}" \
    && rm -rf /root/.claude.json /root/.claude/backups /root/.claude/sessions /root/.claude/downloads

# Some installers put the program itself inside the directory that docker-compose.yml bind-mounts
# for credentials (Codex: ~/.codex/packages); the mount would hide it. Move those trees to
# /opt/agent-installs and rewrite absolute paths and symlinks that point at them. (Hermes keeps its
# program, Python and dependency environment all in ~/.hermes, so that directory is not mounted at all.)
RUN <<'EOF'
set -eu
relocate() {
    src="$1"; dst="/opt/agent-installs/$2"
    [ -e "$src" ] || return 0
    mkdir -p "$(dirname "$dst")" && mv "$src" "$dst"
    for link in $(find /root/.local/bin /opt/agent-installs -type l); do
        target="$(readlink "$link")"
        case "$target" in "$src"*) ln -sfn "$dst${target#"$src"}" "$link" ;; esac
    done
    grep -rlI --exclude-dir=node_modules -F "$src" /root/.local/bin /opt/agent-installs 2>/dev/null \
        | xargs -r sed -i "s#$src#$dst#g"
}
relocate /root/.codex/packages codex/packages
codex --version >/dev/null
hermes chat --help >/dev/null
EOF

COPY docker-entrypoint.sh /usr/local/bin/aao-entrypoint
RUN chmod +x /usr/local/bin/aao-entrypoint

WORKDIR /workspace
ENTRYPOINT ["aao-entrypoint"]
CMD ["bash"]
