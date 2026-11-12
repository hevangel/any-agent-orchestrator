# Machines: where agents run

Three classes, collected by `src/orchestrator/machines.py` and shown on the dashboard's Machines tab.

| Class | What it is | Resources | Agents | Dispatch |
| --- | --- | --- | --- | --- |
| `local` | this computer | probe in-process (psutil) | `herdr agent list` | `herdr pane split` / `herdr agent start` |
| `ssh` | a machine saved in herdr | stdlib probe piped over SSH | `herdr --machine <label> agent list` | `herdr --machine <label> ...` |
| `cloud` | an agent hosted by an AI lab | none (not ours to measure) | the provider's task list | the provider's CLI or API |

## SSH machines (herdr)

herdr is the source of truth for remote machines: `herdr machine list --json` gives each machine's `label` and SSH `target`. Add one with `herdr machine add`, check with `herdr machine status`.

- Every herdr command takes `--machine <label>`; the remote herdr server runs there, and herdr does not need to be on the remote's non-interactive PATH.
- Resource and usage collection use plain `ssh -o BatchMode=yes <target> python3 -` with the probe/scanner source on stdin, so the remote needs only Python 3 and key-based SSH, nothing installed.
- The usage scanner keeps its incremental cache on the remote (`~/.cache/aao/usage_scan.json`).
- Repositories, credentials and agent CLIs must exist on the remote; paths differ per OS (`B:\repo` vs `/home/u/repo`), so dispatch uses the remote's own paths.

**GPUs** (`probe.gpus()`, field `gpus`, empty when there is none): NVIDIA through `nvidia-smi` on any OS (load, VRAM, temperature, power, driver); on Linux every `/sys/class/drm/cardN` with its `lspci` name, plus load and VRAM where the driver exposes them (amdgpu); on Windows other adapters from `Win32_VideoController` (name, driver; VRAM only below its 4 GiB reporting cap); on macOS `system_profiler` names. Integrated GPUs usually report a name only. The AMD sysfs and macOS paths are untested.

**Choosing a machine** for new work: prefer a machine that already has the repo and the agent's login, then the most available RAM and lowest load (each agent process uses roughly 0.5–2 GB), then the fewest busy herdr agents.

## Permission mode and startup screens

All herdr control is a deterministic procedure in `src/orchestrator/drivers.py`: fixed herdr command sequences, never a model deciding what to type.

- `AAO_PERMISSION_MODE=auto` (default) launches each CLI in its own automatic-approval mode; `yolo` skips all permission checks. The flags per CLI are in `agents.json` `permission` (Claude `--permission-mode auto` / `--dangerously-skip-permissions`, Codex `--approve-for-me` / `--dangerously-bypass-approvals-and-sandbox`, Cursor `--auto-review --trust` / `--yolo --trust`, Copilot `--allow-all-tools --trust-folder` / `--yolo`, Kiro, Antigravity, OpenCode, Kilo, Hermes). They apply to one-shot runs and to interactive starts.
- CLIs with a trust flag (Cursor, Copilot, Kiro) trust the folder through it. Claude and Codex have none, so an interactive start runs `start_interactive`: read the visible screen, match it against `startup_forms` by literal text, send that form's keys (`herdr pane send-keys`), poll `herdr agent get` until idle, repeat up to 4 times. Known forms: Claude folder trust (down, enter), Codex update notice (esc: updates belong to `update_agents.sh`). `AAO_TRUST_FOLDERS=0` stops answering trust screens. An unknown screen fails with its text; add it to `startup_forms` with its literal marker and keys.
- If herdr reports a prompt as stalled (typed but no working state seen), the driver presses Enter once only when the prompt is still visible in the input box, then waits for idle.

## Accounts: several logins per CLI

`.env` lists every subscription and API key (`AAO_ACCOUNTS`, then `AAO_ACCOUNT_<id>_*`: agent, plan, tier, home, hosts, priority, monthly cost). Each CLI's way of switching accounts is recorded in `agents.json` under `account`:

| Mechanism | CLIs | How |
| --- | --- | --- |
| credential directory | claude (`CLAUDE_CONFIG_DIR`), codex (`CODEX_HOME`), opencode / kilo (`XDG_DATA_HOME`, unverified), copilot (`COPILOT_HOME`, unverified) | `HOME=~/.claude-work`; log that account in once with the variable set |
| API key from a named variable | cursor (`CURSOR_API_KEY`), zcode (`ZAI_API_KEY`, unverified) | `SECRET_ENV=CURSOR_KEY_2`, never the key itself |
| arguments | hermes (`-p <profile>`) | `HOME=<profile name>` |
| none known | kiro, antigravity, pi, muse | only the default login |

`accounts.choose(agent, host)` (CLI: `uv run aao accounts --agent claude --host local`) picks the next account: any account with a quota window under 10% remaining goes last; then free before paid before API-billed, then `PRIORITY`, then the most quota left. Quotas are read per account by running clanker-usage with that account's credential directory (claude, codex). Every driver mode launches through `accounts.launch_spec`, so the e2e tests exercise each account the way dispatch will. Token-usage rows are not yet split by account.

## Cloud agents (AI labs)

Hosted agents run in the provider's VM against a GitHub repo and deliver a diff or PR. They have no CPU/RAM we can see; what matters is task state and the plan quota they consume (Quotas tab).

| Provider | List / status | Dispatch | Notes |
| --- | --- | --- | --- |
| Codex cloud | `codex cloud list --json --limit 20`, `codex cloud status <id>` | `codex cloud exec --env <ENV_ID> "<task>"`; `codex cloud diff/apply <id>` | needs a Codex environment configured on chatgpt.com/codex |
| Copilot coding agent | `gh agent-task list --json ...` | `gh agent-task create -R OWNER/REPO "<task>"` (`--follow` streams logs) | result is a PR; preview CLI |
| Claude Code cloud | no listing CLI; claude.ai/code via Claude in Chrome | `claude --cloud "<task>"`, attach with `claude --cloud <id>` or `--teleport` | `--environment ccpool_...` runs on a self-hosted environment |
| Cursor cloud agents | `GET https://api.cursor.com/v0/agents` (Basic auth, `CURSOR_API_KEY`) | `POST /v0/agents` | API shape taken from Cursor's public docs, not yet tested here; `agent worker` lets Cursor's cloud agents run on your own machine instead |

Rules:

- Treat a cloud task as a unit of work on the GitHub Project: put the task URL in the issue's claim comment and set **Agent** to the provider (e.g. Codex) while it runs.
- A cloud agent works from the pushed branch, not your working tree: push first.
- Cloud agents count against the same subscription windows as the local CLI; check the Quotas tab before fanning out.
- Add a provider by writing a collector returning `_task(...)` rows and registering it in `CLOUD_PROVIDERS`.
