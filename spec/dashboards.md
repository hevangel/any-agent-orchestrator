# Dashboards

`uv run aao serve` serves `dashboard/index.html` at <http://127.0.0.1:8765/> and runs the collectors in the background (machines every 60 s, quotas every 5 min, usage every 10 min). Snapshots go to `state/` (gitignored); committed data lives in `data/`. `uv run aao collect [machines|quotas|usage]` runs one collector from the shell.

| Tab | Source | Module |
| --- | --- | --- |
| Machines | herdr + SSH probe + cloud task lists | `machines.py`, `probe.py`, [spec/machines.md](machines.md) |
| Quotas | clanker-usage → quota-cli → CLI status screens → Codex logs | `quotas.py`, `quota_screens.py` |
| Tokens & cost | local session logs on every machine, priced from the price table | `usage_scan.py`, `usage.py`, `pricing.py` |
| Free tokens | `data/free_tokens.json` | `free_tokens.py` |
| API prices | `data/api_prices.json` | `pricing.py` |

## Quotas (5-hour, weekly, monthly windows)

- **clanker-usage** (`uv tool install clanker-usage`) is primary: one stable `usage-v1` JSON schema for Claude, Codex, Cursor, Antigravity, Gemini CLI, Muse, OpenCode Go, Z.ai, DeepSeek, Devin.
- **quota-cli** (`cargo install quota-cli`) fills in Copilot, Grok, Kiro, Kimi. It reports *remaining* %, converted to used %.
- **Codex session logs** carry `rate_limits` (primary = 5 h, secondary = weekly), read without any network call.
- **CLI status screens** fill missing providers/accounts before the Codex-log fallback. `quota_screens.read_quota("claude")` opens Claude Code and runs its built-in `/usage`; `read_quota("codex")` runs `/status`. Each read uses a private herdr workspace, submits no natural-language prompt, waits for stable quota windows, and closes only that workspace. Startup screens follow the existing literal `startup_forms`; login screens are never answered. Other CLIs can be added with a verified built-in command in `agents.json` `quota_screen` and a parser in `quota_screens.py`. Local collection uses every installed CLI with that metadata; `AAO_QUOTA_SCREEN_AGENTS=claude,codex` limits it, and `none` disables it. Configured local account homes use `accounts.launch_spec`; the Python reader also accepts a saved herdr `machine` and its remote `cwd` for explicit remote reads. The dashboard collector currently reads local quota screens only.
- Screen percentages labelled **left/remaining** are converted to used percentage. Context-window percentages and token totals are ignored. Reset times with a timezone or an explicit relative duration become timestamps; ambiguous local clock/date labels stay as `reset_text` and are displayed verbatim, without a run-out prediction. Screen-format, login, and cleanup failures appear in Collector messages; they never become guessed quota values.
- **Kiro** uses its built-in `/usage` in `kiro-cli chat`, reporting monthly credits used out of the plan allowance and the displayed reset date. **Antigravity** uses [`/usage`](https://antigravity.google/docs/cli/commands/usage/), reporting weekly and five-hour remaining quotas for Gemini and Claude/GPT, plus the separate Argon weekly quota when present. The reader scrolls the panel using only the keys in `agents.json` `quota_screen.read_pages`, merges windows by name and model group, and never sends a model prompt. Both readers are included in the default screen fallback list.
- **Cursor** uses the built-in `/usage` command in the installed CLI. Its Included percentage is the billing-cycle quota; Auto and API are breakdowns of that same allowance and are not counted as separate quotas. A bounded On-Demand dollar allowance becomes a separate scoped window; disabled, unavailable, or unlimited on-demand usage is not converted into a quota percentage. The native screen is preferred (`quota_screen.prefer` in `agents.json`), with existing external data retained if the command is unavailable in the current CLI mode or the screen cannot be parsed. This command was verified against the installed CLI and its actual screen, even though the public command list omitted it.
- **Cursor fallback** uses `clanker-usage` with its existing signed-in CLI account. On Windows, the collector retries Cursor alone with `XDG_CONFIG_HOME=%APPDATA%` when the default lookup fails, matching the CLI's native `%APPDATA%/Cursor/auth.json` location. An explicitly configured `XDG_CONFIG_HOME` is respected. External quota subprocesses omit `SSLKEYLOGFILE`, matching the repository HTTP client's workaround for Windows monitoring pipes. Install the optional collector with `uv tool install clanker-usage`; no credentials are changed or refreshed by the orchestrator.
- Every collection appends to `state/quota_history.jsonl`; the run-out projection is a linear fit over snapshots of the *same* window period (same `resets_at`) and warns when 100 % would be reached before the reset.
- quota's herdr plugin (`external/quota/herdr-plugin`) can show the same numbers in the herdr sidebar per pane.

## Token usage and API-equivalent cost

Every row is (agent, session, model, day) with tokens split the way API pricing is: non-cached input, cache write 5 m, cache write 1 h, cache read, output (reasoning counted inside output).

| Agent | Log | Gotchas |
| --- | --- | --- |
| Claude Code | `~/.claude/projects/*/*.jsonl` | one line per content block repeats the same usage: dedupe by (message id, request id); `cache_creation` gives the 5 m/1 h split; `usage.speed == "fast"` marks fast mode |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` | `token_count` totals are cumulative and re-emitted on rate-limit updates: count only increases; `input_tokens` *includes* `cached_input_tokens` |
| OpenCode / Kilo | `~/.local/share/{opencode,kilo}/*.db` (SQLite, read-only) | `reasoning` is separate from `output` and billed as output |

**Pricing mechanics** (learned from ccusage and Splunk Token Meter):

1. `data/api_prices.json`: committed per-model prices with `effective_from`, maintained by a **script, not an LLM**: `uv run aao prices update` (`src/orchestrator/price_update.py`) fetches the official pricing pages as Markdown and parses them: Anthropic, OpenAI, Google Gemini (including its announced future price dates), xAI, Z.ai. It runs daily in `.github/workflows/update-prices.yml` and opens a PR on change. A price change is a **new entry**, never an edit, so history keeps the price in force on that day; unchanged prices only move `verified_at`. If a page's layout changes, the parser reports "found no prices" and fails the job rather than writing guesses. `cache_write_5m` / `cache_write_1h` hold the provider's short/long-TTL cache-write price (OpenAI: 30 min / 12 h); providers without a write fee bill writes as input. `fast_multiplier` prices fast mode; `aliases` cover same-priced ids. DeepSeek (HTML only, peak/off-peak), Kimi and Qwen have no machine-readable official page and use the LiteLLM fallback.
2. LiteLLM's public price list (`uv run aao prices sync` → `state/litellm_prices.json`) for anything not in the table. It lists models under resellers too, so lookup prefers the bare key, then the row's own provider prefix, then a lab's first-party API. LiteLLM has no price history: it is the current price.
3. Routes marked `-free` / `:free` cost 0. Anything else is **unpriced** and listed on the dashboard; add it to the table.

Costs are computed at read time, so fixing a price re-prices history without rescanning. Cost is what the tokens *would* cost at API list prices; subscription usage is not billed this way, which is the point: it shows what each plan is worth.

## Free tokens

`data/free_tokens.json` (schema `free-tokens-v1`) lists free tiers, credits, promos and free model routes the agents in `agents.json` can use. Each source: `id`, `provider`, `offer`, `kind` (free-tier, daily, weekly, monthly, one-time, trial, promo, referral, credits), `amount` (as the provider states it), `agent_ids`, `access` (cli/web/api/ide/desktop), `url` (official, https), `expires`, `status` (active/expired/unverified/ended), `verified_at`, optional `how_to_claim` and `notes`. The dashboard flags the list as stale after 36 h.

Free offers need judgment (reading announcements, spotting expiry), so a daily LLM agent maintains this file: [prompts/daily_data_update.md](../prompts/daily_data_update.md).
