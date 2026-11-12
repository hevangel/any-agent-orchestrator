# Prerequisites and first-run setup

The orchestrator needs uv, playwright-cli (which needs Node.js), Google Chrome and herdr on the machine it runs on. **The first time an agent works in this repository on a machine, it checks for each one and installs whatever is missing**, before running anything else. Later runs only re-check when a command fails because a tool is missing.

| Tool | Used for | Check |
| --- | --- | --- |
| **uv** | Every Python command: the `aao` package, tests, tools | `uv --version` |
| **Node.js + npm** | Installing playwright-cli | `node --version` (18 or newer) |
| **playwright-cli** | Driving chat websites ([web_interfaces.md](web_interfaces.md)) | `playwright-cli --version` |
| **Google Chrome** | The browser playwright-cli drives (`AAO_WEB_BROWSER=chrome`) | see below |
| **herdr** | Running and controlling CLI agents ([machines.md](machines.md)) | `herdr --version` |

## Install

Install for the current user where possible. If an install needs administrator rights (`sudo`, a UAC prompt), stop and ask the human to run it; do not escalate on your own.

### uv

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh                                   # Linux, macOS, WSL
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" # Windows
```

Then, in the repository: `uv sync`.

### playwright-cli

Needs Node.js; install Node from nodejs.org or the OS package manager if `node` is missing.

```sh
npm install -g @playwright/cli@latest
```

### Google Chrome

playwright-cli uses the installed Chrome rather than bundled Chromium, because Google sign-in and some anti-bot checks reject automation-flagged Chromium. Check, then install if missing:

| OS | Check | Install |
| --- | --- | --- |
| Windows | `Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"` | `winget install --id Google.Chrome -e` |
| macOS | `test -d "/Applications/Google Chrome.app"` | `brew install --cask google-chrome` |
| Linux | `google-chrome --version` | Google's `.deb`/`.rpm` from google.com/chrome (needs `sudo`: ask the human) |

Keep `AAO_WEB_BROWSER=chrome` in `.env` (it is the default in `.env.example`).

### herdr

```sh
curl -fsSL https://herdr.dev/install.sh | sh     # Linux, macOS, WSL (same command as agents.json)
```

On Windows, follow the instructions at <https://herdr.dev>; there is no scripted installer in `agents.json` yet. Update with `herdr update`.

## Rules

- **Always run Python through uv**: `uv run <command>`, `uv sync`, `uv add <package>`, `uv tool install <tool>`. Never call `python`, `pip` or `pip install` directly, and never create a virtualenv by hand. The one exception is what the collectors send to remote machines: `probe.py` and `usage_scan.py` are piped to `python3` over SSH, where uv is not installed. That is why they must stay standard-library only.
- After installing, re-run the check command to confirm the tool is on `PATH`; open a new shell if the installer changed `PATH`.
- Installing a tool never includes logging in. Web logins are done once by a human (`uv run aao web login <site>`); agent CLIs are logged in by their owner.
- Report what you installed, and which version, in your first progress comment or reply.
- **Docker is optional.** It is not a prerequisite and is never installed automatically; install it only if the human asks for the container. The Docker image already has every tool ([Dockerfile](../Dockerfile)), so inside the container there is nothing to install. Code must not require Docker: the `docker` driver mode and its e2e cases skip when the image is not built.
