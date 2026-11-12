# Testing

| Suite | Command | What it touches |
| --- | --- | --- |
| Unit | `uv run pytest -q` | nothing outside the repo; synthetic pages and logs |
| End-to-end | `uv run pytest -m e2e` | real agents, machines and web apps; spends a few hundred tokens per case |
| One e2e case | `uv run pytest -m e2e -k "ssh-brunhild-codex"` | |
| Override the matrix | `AAO_E2E_MODES=herdr-agent AAO_E2E_CLIS=claude uv run pytest -m e2e` | environment beats `.env` |

`pyproject.toml` deselects `e2e` by default, so a plain `pytest` never spends tokens.

## Results and evidence

Every pytest run is recorded by `tests/conftest.py` (set `AAO_TEST_RESULTS=0` to skip):

- `tests/result/runs/<run-id>/summary.json`: schema `aao-test-run-v1` with run metadata (platform, commit, pytest args), totals, and per test: outcome, duration, parameters, failure text, `info` (mode, host, CLI, account, command, seconds, expected answer) and evidence paths.
- `tests/result/runs/<run-id>/evidence/<test>/`: `transcript.txt` for each CLI case (prompt, command, terminal output) and `page.png` + `result.json` for each web case.
- `tests/result/index.js`: the last 50 summaries as `window.AAO_TEST_RUNS`.

Open `tests/index.html` in a browser (straight from disk works) to pick a run and see the agent × mode grid, the web-app row and every test with its evidence. Tests add evidence through the `evidence` fixture: `evidence.text(name, content)`, `evidence.screenshot(name)` (returns a path for the tool to write to) and `evidence.info(**fields)`. `tests/result/` is gitignored because transcripts and screenshots can show account details.

## CLI control matrix (`tests/e2e/test_cli_control.py`)

Cases are generated from `.env` (template `.env.example`): every CLI in `AAO_E2E_CLIS` × every mode in `AAO_E2E_MODES` × every configured account for that CLI on that machine (`AAO_E2E_ACCOUNTS=default` limits it to the default login). Each case asks for the sum of two random numbers as `AAO<sum>` and accepts the sum with or without the prefix (regex `(?:AAO|\b)<sum>\b`; some models drop it). The sum never appears in the prompt, so terminal-reading modes cannot pass by seeing the echoed command line. `AAO_E2E_MODEL_<cli>` (`[provider:]model`) picks the model for that CLI's cases through its `agents.json` `model_args` (Hermes: `--provider openrouter -m <id>`), e.g. a free OpenRouter model so the tests spend no credits. Give the provider when the CLI's own config uses another one; free models are often rate-limited (HTTP 429), so pick one that answers. A CLI listed in `AAO_E2E_CLIS` but not installed on a machine (per `AAO_HOST_<label>_CLIS`, or PATH locally) gets no case there.

| Mode | Path under test | Needs |
| --- | --- | --- |
| `native` | `subprocess` with the agent's `headless` command from `agents.json` | the CLI on PATH |
| `docker` | `docker compose run --rm -T -v <tmp>:/task -w /task agents <cmd>` | `docker compose build` done; uses the compose credential mounts, default accounts only. Agents whose login lives in the Windows credential store (Cursor, Kiro, Copilot, Antigravity) need a token in `.env` or one login inside the container |
| `herdr` | throwaway herdr workspace, headless command run in its pane, `pane wait-output --regex` | herdr running |
| `herdr-agent` | interactive TUI through `herdr agent start / prompt --wait / read` (the orchestration path), started in a fresh folder | startup screens it knows (`startup_forms` in agents.json: folder trust, update notice, consent) are answered with fixed keys; any other screen fails with its text |
| `ssh` | `ssh <target> bash -lc '<env> <cmd>'` | key-based SSH; the CLI on the remote's login-shell PATH |
| `herdr-remote` | `herdr` mode with `--machine <label>` | the host saved in herdr (`AAO_HOST_<label>_HERDR`) |

## Web apps (`tests/e2e/test_web_interfaces.py`)

Every site in `AAO_E2E_WEB` through playwright-cli with a persistent profile per site (`AAO_WEB_PROFILE_DIR`, default `~/.aao/browser-profiles/<site>`, outside the repo). Log in once with `uv run aao web login <site>` (or `--all`): a visible browser opens, you sign in, and the command returns as soon as the chat box appears without a "Sign in" button. Chat sites get the same sum check; `codex-cloud` and `claude-code-web` are only login-checked, because a prompt there starts a cloud task. `uv run aao web status --all` shows which saved logins still work. A guest session (sign-in button visible) counts as logged out.
