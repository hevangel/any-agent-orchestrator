#!/usr/bin/env bash
# Container entrypoint: link host credential files that live next to a SQLite database.
#
# OpenCode keeps auth.json beside opencode.db; SQLite fails on Windows bind mounts, so the data
# directory is a named volume and only auth.json comes from the host (mounted at /host-creds).
# A symlink, not a copy, so a login or token refresh inside the container is written back to the host.
set -euo pipefail

link_host_file() {
    local host="$1" target="$2"
    [ -f "$host" ] || return 0
    mkdir -p "$(dirname "$target")"
    ln -sfn "$host" "$target"
}

link_host_file /host-creds/opencode/auth.json /root/.local/share/opencode/auth.json
# last-used model, which `opencode run` / `kilo run` fall back to
link_host_file /host-creds/opencode-state/model.json /root/.local/state/opencode/model.json
link_host_file /host-creds/kilo-state/model.json /root/.local/state/kilo/model.json

exec "$@"
