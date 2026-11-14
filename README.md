# any-agent-orchestrator

Let one AI agent direct a whole team of other AI agents: the coding CLIs in your terminal, the chat websites in your browser, and the AI desktop apps on your computer.

You give the work to one coordinating agent. It splits the work into GitHub issues, hands each piece to whichever agent suits it, such as Claude Code, Codex, Cursor, Gemini, Grok, ChatGPT or Muse, and tracks progress on a GitHub Project board. The agents can message each other through a shared Dropbox or Box folder.

Our manifesto:

1. **AI is a fungible commodity, wherever it runs.** Choose the model and machine that best fit the work, regardless of vendor or location.
2. **The agent is an illusion: compute resources plus LLM calls.** Treat it as a resource to compose, allocate and replace.
3. **GitHub issues and Projects are the orchestration layer.** Tasks, dependencies, ownership and progress live in a shared, durable work queue.
4. **The Dropbox mailbox is the messaging layer between AIs.** Agents exchange messages through a shared folder, using the same protocol on Box when preferred.

> **Status:** early. The fleet dashboard works today; the plugin that hands out the work is not written yet. The repo also holds the agent catalog, a Docker environment, the coordination protocols and the design specs.

## The fleet dashboard

```bash
uv sync
uv run aao serve        # open http://127.0.0.1:8765/
```

One page, six tabs, refreshed in the background:

- **Projects:** each managed project's GitHub Project link, parent repository, nested submodules and external references, read from `projects/*.json`.
- **Machines:** CPU, memory and disk for this computer and every machine you've added to herdr, plus which agents are running on each. Cloud agents run by the AI labs are listed separately with their tasks.
- **Quotas:** how much of each subscription's 5-hour, weekly and monthly allowance is left, when it resets, and a warning if you're on track to run out before the reset.
- **Tokens & cost:** daily token use split into fresh input, cache writes, cache reads and output, and what that use would have cost at public API prices, per day, per model and per session.
- **Free tokens:** free tiers, credits and promotions you can use, kept current by an agent once a day.
- **API prices:** the price table behind the cost figures, with sources.

Nothing leaves your computer: the dashboard reads the agents' own log files and asks each provider for its own quota. Remote machines are read over SSH and need only Python; nothing is installed on them.

For quota numbers, install one or both of: `uv tool install clanker-usage` (Claude, Codex, Cursor, Gemini, Antigravity, Muse, Z.ai and more) and `cargo install quota-cli` (adds Copilot, Grok, Kiro). Codex quotas also come from its own logs.

## Where agents run

- **This computer**, in herdr terminal panes.
- **Your other machines over SSH.** Add them once with `herdr machine add`; every herdr command then works against them with `--machine <name>`.
- **Cloud agents from the AI labs:** Codex cloud, GitHub Copilot's coding agent, Claude Code cloud sessions and Cursor's cloud agents. They run in the provider's own machines, work from a GitHub branch and hand back a diff or pull request. They also use up your subscription, so check the Quotas tab first.

Details: [spec/machines.md](spec/machines.md).

## Your setup: `.env`

Copy `.env.example` to `.env` (it stays on your computer) and describe:

- **your machines**: which hosts, how to reach them (SSH and/or herdr) and which agents are installed on each;
- **your subscriptions**: any number of accounts per agent, free and paid. For example two Claude subscriptions, a work Codex plan and a free Z.ai key. The orchestrator uses free accounts first, then paid ones in your chosen order, and skips any account that's nearly out of quota. Check with `uv run aao accounts --agent claude`.

## Testing that everything can be controlled

```bash
uv run pytest -q          # quick checks, no agents involved
uv run pytest -m e2e      # asks every configured agent a small question, every way it can be reached
```

The end-to-end run sends each agent a tiny arithmetic question: directly, in Docker, through herdr, over SSH and through herdr on each remote machine, once per account. It does the same for each chat website. Web logins are saved per site, so you sign in once with `uv run aao web login --all` and never again. Details: [spec/testing.md](spec/testing.md).

## How it reaches each kind of agent

| Kind of agent | Examples | How it is controlled |
| --- | --- | --- |
| Command-line coding agents | Claude Code, Codex, Cursor, Copilot, Kiro, Antigravity, ZCode, OpenCode, Kilo, Pi, Hermes, Muse Code | [herdr](https://herdr.dev) runs each agent in its own terminal pane and sends it prompts |
| Chat websites | Grok, Muse, Gemini, ChatGPT and remote Codex, Z.ai | [playwright-cli](https://github.com/microsoft/playwright-cli) drives the website like a person would |
| Claude on the web | claude.ai and remote Claude Code | The Claude in Chrome extension, in your own Chrome |
| Desktop apps | ChatGPT, Claude or Codex desktop apps | Computer use (screen control) from the Claude desktop app |

Website agents use your existing logins. You sign in once yourself, and the orchestrator never types your passwords.

## How the agents coordinate

**GitHub Project board: the shared to-do list.** Your main repository hosts a GitHub Project and includes each repository being worked on as a git submodule, so it behaves much like a single repository. Each task is a GitHub issue, and "blocked by" links record which tasks must finish first. Custom fields show where each task stands:

- **Status:** Planned → Research → Spec-reviewed → Building → AI-reviewed → Human-reviewed
- **Agent:** the last agent that worked on it (a `wip` label marks a task someone is working on right now)
- **Iteration:** the order tasks are taken in
- **Path:** which repository and folder the work is in

Releases go out once the agents have built and reviewed the work. Humans test after the release, and a human marks a task Human-reviewed once they have used it. Details: [spec/github_project.md](spec/github_project.md).

**Mailbox: direct messages between agents.** Agents leave each other messages as small files in a shared Dropbox or Box folder. Files are only ever added, never edited, so two agents can't overwrite each other. Details: [mailbox/AGENTS_MAILBOX.md](mailbox/AGENTS_MAILBOX.md).

## Manage project configurations

Each managed project has a JSON file in [projects/](projects/). It records the GitHub Project, parent repository, nested submodules, external references and optional collaborators and workstreams. The project ID is the file name without `.json`.

```bash
uv run aao projects list
uv run aao projects sync ai_engineer --dry-run  # preview the repository tree
uv run aao projects sync ai_engineer            # write projects/ai_engineer.json
uv run aao projects validate                    # validate every project config
uv run aao projects status ai_engineer          # live issue counts by Status
uv run aao projects setup ai_engineer --dry-run # preview GitHub Project setup
uv run aao projects setup ai_engineer           # create or complete the GitHub Project
```

`sync` reads `.gitmodules` recursively from each repository's default branch on GitHub and rebuilds the config's `repo` tree, preserving the other settings. Merge submodule URL changes in the owning repository before syncing; refresh the tree with this command instead of editing it by hand. References under `external/` stay separate from managed repositories.

The optional `collaborators` list names trusted GitHub accounts whose repositories are managed alongside the project owner's. For example, `git-hevangel` is the owner's shadow account and is listed in both current project configs. This setting records which accounts' repositories belong to the project; GitHub Project access is granted separately in **Settings → Manage access**, with Write access needed to edit fields.

`setup` creates or completes the GitHub Project's fields, links managed repositories, creates missing canonical labels and adds their issues. Sync and live GitHub operations use the authenticated GitHub CLI (`gh`); GitHub Project operations require the `project` scope. See [the project configuration spec](spec/github_project.md#project-configs) for the schema and setup rules.

## Install the CLI agents

[agents.json](agents.json) lists every supported agent and how to install and update it. On Linux, macOS or WSL (requires `jq`):

```bash
./install_agents.sh                 # install everything
./install_agents.sh --tags core     # only Claude Code, Codex and Cursor
./update_agents.sh                  # update the agents you have installed
./install_agents.sh --dry-run       # show what would run
```

Tags: `core`, `tier2`, `byok` (bring your own API key), `free-tokens`.

## Run everything in Docker (optional)

Docker is optional; everything also runs natively. The Docker image includes every CLI agent, plus the GitHub CLI, uv, playwright-cli and Chromium. It contains no logins: your agents' existing login folders on your computer (`~/.claude`, `~/.codex`, `~/.cursor` and so on) are shared into the container when it starts, so you stay signed in.

```bash
cp .env.example .env                              # optional settings
docker compose build
docker compose run --rm agents                    # shell, your workspace at /workspace
docker compose run --rm agents claude             # start one agent directly
docker compose build --no-cache                   # update all agents to their latest versions
```

To work on a different repository, set `WORKSPACE=../your-repo` in `.env`.

Some logins live in your operating system's password store rather than in a folder: Claude Code on macOS uses the Keychain, and `gh` on Windows may use Credential Manager. The container can't see those. Put a token in `.env` instead (for example `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`, or `GH_TOKEN`), or log in once inside the container.

The container doesn't include Claude in Chrome or computer use. They need your own Chrome and desktop.

## Reference projects

`external/` contains other open-source projects, included as git submodules for study only (`git submodule update --init`):

- [orca](https://github.com/stablyai/orca): a desktop app that runs coding agents in parallel
- [mcp-web-llm](https://github.com/HGD-coder/mcp-web-llm): controls the ChatGPT, Claude, Gemini, Grok and other chat websites
- [browser-harness](https://github.com/browser-use/browser-harness): lets agents control your real Chrome
- [MuseBridge](https://github.com/Some1sm/MuseBridge): makes Meta's Muse usable as a back end for Claude Code
- [quota](https://github.com/pinkpixel-dev/quota), [clanker-usage](https://github.com/wakamex/clanker-usage), [llm-runway](https://github.com/200ok-ch/llm-runway): read subscription quotas and project when they run out
- [ccusage](https://github.com/ryoppippi/ccusage), [Splunk Token Meter](https://github.com/splunk/token-meter), [tokenmeter](https://github.com/serkankorkut/tokenmeter): token and cost tracking from local agent logs

## For AI agents

Agents working in this repo start with [AGENTS.md](AGENTS.md).

## Contributing

All code here is written by AI agents; humans file issues, steer agents and review. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
