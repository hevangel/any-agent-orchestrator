# Controlling web and desktop AI interfaces

How the orchestrator drives AI products that have no CLI (or whose remote/cloud mode is web-only). CLI agents are covered in [AGENTS.md](../AGENTS.md#cli-agents-herdr).

## Driver per channel

| Channel | Default driver | Exception |
| --- | --- | --- |
| Web app | `playwright-cli`, one named session per target (`playwright-cli -s=<target> ...`) | **Claude web / remote Claude Code** use the native **Claude in Chrome** extension (`mcp__claude-in-chrome__*`) in the user's real Chrome |
| Web app that blocks automated Chromium (Google sign-in, Cloudflare challenges) | Attach to the user's real Chrome over CDP: `playwright-cli attach`, or `external/browser-harness` | — |
| Native desktop app | computer-use (`mcp__computer-use__*` in the Claude desktop app) | — |

## Targets

Selectors below come from the reference adapters at their pinned submodule commits. Web UIs change often: prefer `playwright-cli snapshot` element refs at run time and treat these selectors only as hints.

| Target | URL | Composer | Send / done signal | Reference |
| --- | --- | --- | --- | --- |
| Grok | `https://grok.com/` | `textarea[placeholder*='Ask']`, else `div[contenteditable='true']` | Enter; reply bubbles stop changing | `external/mcp-web-llm/models/grok.py` |
| Muse (Meta) | `https://muse.ai` (chat URL discovered at login) | `textarea[placeholder='Message']` | Send button with `aria-label*=Send`; generating while a visible `Stop` button or `animate-pulse` in `.hatch-chat-groupable-bubble` | `external/MuseBridge/muse_bridge.py` (`detect_selectors`, `_is_generating`, `_wait_for_reply`) |
| Gemini | `https://gemini.google.com/` | `rich-textarea div[role='textbox']` | Send button; Google sign-in rejects automation-flagged Chromium, so attach over CDP | `external/mcp-web-llm/models/gemini.py`, `external/browser-harness/agent-workspace/domain-skills/gemini/` |
| Claude web | `https://claude.ai/new` | `[role='textbox'][aria-label='Write your prompt to Claude']` (ProseMirror) | **Studied and scripted (playwright-cli), see below** | `web.py` `TARGETS["claude"]`; Claude in Chrome remains an alternative |
| Remote Claude Code | `https://claude.ai/code` | `[data-testid='code-prompt-input']` | **Studied and scripted, see "Claude Code on the web"** | `web_code.py`; shares the `claude` login |
| ChatGPT | `https://chatgpt.com/` | `#prompt-textarea` | `[data-testid="send-button"]`; streaming ends when the stop button is gone | `external/mcp-web-llm/models/chatgpt.py` |
| Remote Codex | `https://chatgpt.com/codex` | task prompt box | task list status; results arrive as diffs / PRs | none yet: discover with `snapshot` |
| Z.ai coder | `https://chat.z.ai/` | discover with `snapshot` | discover with `snapshot` | none yet: use MuseBridge's auto-discovery approach |

## Scripted recipes (`src/orchestrator/web.py`)

Each studied target has a recipe in `TARGETS`; `PAGE_SCRIPT` runs it with playwright-cli `run-code`, with no LLM in the loop. Keys: `composer` (selectors, first visible wins), `send` (button clicked only if Enter did not send), `user` (user turns, proves the send), `assistant` (assistant turns; the last one is the reply), `busy` (any visible means still writing), `strip` (removed from the reply before reading its text). A target without `assistant` still uses the old whole-page text check.

Turn: open the target URL (always a new chat) → count assistant turns → click composer, insert text, Enter → wait up to 10 s for a new user turn or a URL change, else click `send` once → wait for a new assistant turn with no `busy` marker whose text is identical across two 1 s polls → return `reply`, `url`, `seconds`; `ok` is the `expect` regex tested on the reply only.

| Target | Studied | Signals |
| --- | --- | --- |
| claude | 2026-11-12 | Enter sends; URL `/new` → `/chat/<id>`; user `[data-testid='user-message']`; reply `[data-testid='assistant-message']`, `data-is-streaming='true'` and a `button[aria-label='Stop response']` while writing; strip `.sr-only` (a hidden "Claude responded: …" heading) and `[data-testid='message-actions']` |

`aao web ask <t> --chat <id|url|title>` continues an existing chat instead of opening a new one (a title is resolved through the chat list below).

## Chat list (`src/orchestrator/web_chats.py`)

`aao chats <t> list | sections | search <q> [--kind Code] | pin | unpin <chat> | move <chat> <section> [--create] | unsection <chat> | new-section <name> [--chat <chat>] | delete-section <name> --yes` for `<t>` = `claude` (chats) or `claude-code-web` (Code sessions). Claude calls groups **sections**; the older names `groups`, `ungroup`, `new-group`, `delete-group` still work. `<chat>` is an id, a URL or an exact title (sidebar first, then search). Each action is a fixed menu sequence from `RECIPES` and is verified by reading the sidebar again; `--json` prints the full result.

claude.ai (studied 2026-11-12):

- The sidebar keeps one pane per mode, `aside [data-kind=cowork]` (chats) and `aside [data-kind=code]` (Code sessions); a visited mode's pane stays in the DOM with `display:none`, so every selector is scoped to one pane and the page must be in that mode.
- Each pane is a flat list of `[data-row-key]` rows: `label:pinned`, `label:custom-cg-<uuid>` (a section), `label:recents` start sections; the `chat:<uuid>` / `code:session_<id>` rows after a label belong to it. Skip `label:viewall` and `label:hint:*` (the placeholder in an empty section). The Code pane's Pinned heading has **no** label row: rows before the first label are pinned. Rows in a collapsed section stay in the DOM but are hidden.
- An item is in exactly one section: pinning takes it out of its section, unpinning returns it to Recents.
- Row menu: `button[aria-label^='More options for ']` → `Pin`/`Unpin`, `Move to section` (submenu: one `menuitemradio` per section, `Remove from <section>`, `New section`), `Archive`, `Delete` (Code sessions add `Open PR`, `Open in`, `Copy link`, `Continue in project`). Item text carries icon-font glyphs (U+E000–F8FF), a shortcut letter or a count: compare the first non-empty line with glyphs removed.
- **Open menus with `dispatchEvent('click')` on the button element, never a positional click.** The menu button is hidden until hover; a positional click on it lands on the row's link, and claude.ai hands session/thread links to the desktop app (Chrome then asks "Open Claude?"). The event toggles and does not move focus: close open menus first, then focus the menu so Escape and arrow keys reach it (`closeMenus` focuses each menu before Escape).
- New section: chats edit the name inline (`input[aria-label='Rename section']`); the Code tab opens a "New section" dialog with `Create section`. An empty section made on the Code tab was gone after the next page load.
- Section menu: chats `More options for <name>` → `New section`, `Rename section`, `Delete section`. Code tab: `Group and sort` (no delete); `Delete section` is in the **right-click menu of the section title** (`button[data-group-toggle]`), next to `Archive all (n)` and `Delete all sessions (n)`, so only the exact item is ever picked, and a confirmation is accepted only through a button named exactly `Delete` / `Delete section`. Code section rows also hold a `New session in …, added to <name>` button: never pressed.
- Known prompts answered with a fixed button: "Enable notifications" → `Not now`; "Links like this one open in the Claude desktop app" → `Keep in this browser`.
- The chat page header menu is **not** a substitute: it has no `Move to section` and says `Unpin` for any chat in a section. It is used only to pin a chat that is not in the sidebar at all (then it is in no section, so `Pin` is accurate). A chat in a collapsed section is acted on by expanding that section and collapsing it again.
- Search: the `Search` button opens a command palette, `textarea#command-palette-input`; results are `[role=option][data-item-type]` (`conversation`, `thread`, ...; `action` items are palette commands and are dropped) with id `command-palette-item-<uuid>`; filter tabs All / Artifacts / Sessions / Code / Projects / Scheduled. It matches message text as well as titles.

The e2e test (`tests/e2e/test_web_chat_list.py`) makes its own chat and a throwaway `aao-e2e-<n>` group, and deletes the group at the end.

## Browser sessions

Targets on the same site and account share one login: `TARGETS[t]["profile"]` (claude-code-web uses `claude`), so one profile and one playwright-cli session `aao-web-<login>` (Chrome cannot open a profile twice). Commands reuse an open browser (`playwright-cli list`) and leave it open; `aao web close [t|--all]` closes it, `AAO_WEB_KEEP_OPEN=0` closes after every command. Do not run two commands against the same browser at once. playwright-cli `run-code` has no `URL` global outside `page.evaluate`: parse `page.url()` by hand.

## Claude Code on the web (`src/orchestrator/web_code.py`)

`aao code sessions | read <session> | send <session> --prompt … [--wait] | new --prompt … [--repo owner/name] [--env NAME] [--permission Auto|"Accept edits"|Plan] [--wait] | wait <session>`; projects: `aao code projects | project <name|id> [--last N] | project-send <project> --prompt … [--wait]`. Pin and sections: `aao chats claude-code-web …`. `<session>` is `session_<id>`, its URL or its exact sidebar title. Studied 2026-11-12.

- **Where a session runs** comes only from its sidebar row: the row's detail text ("Cloud · Merged · 2 pull requests", "sidonia · Connected", "Remote Control · Offline") plus `[data-env-glyph]` (`cloud` / `bridge`). `runs_on` is `cloud` or `machine`; `machine` is the name when the row gives one; Remote Control rows do not name the machine (the session rail does not either). Status: `[role=status]` ("Something went wrong"); PR: `[data-pr-glyph]` (merged, …).
- Session page: turns `[data-testid='user-message']` / `[data-testid='assistant-message']`; working while `[data-testid='code-working-line'][data-turn-working='true']`; title `button[aria-label$=', rename session']`; composer `[data-testid='code-prompt-input']`. The right rail loads after the transcript: wait for `[data-rail-grip]`; a section is the largest ancestor of its grip (`aria-label="Reorder <Section>"`) holding no other grip. Environment name `[data-cds=OverflowTooltip]`, then the connection text; branch = the menu button's `aria-label`; PRs = `a[href*="/pull/"]`, state from `nav[aria-label='Repository and pull request controls'] [role=img]`.
- Reply text: lay the cloned message out off-screen (not `visibility:hidden`) before reading `innerText`; a detached node loses line breaks.
- Turn: refuse while working → click composer, insert, Enter → sent when a user turn appears, the URL changes or work starts → wait: working seen or a new assistant message, then working false and the reply unchanged over two polls.
- New session form: `Environment:` button → `menuitemradio` by name (full, Local, Auto, Default, named environments); `Add repository` combobox → `input[aria-label='Search repositories…']` → option `owner/repo`; `Permissions:` → Auto / Accept edits / Plan. The URL becomes `/code/session_<id>` after Enter.
- Usage: the composer's `button[aria-label^='Usage:']` ("31% of 5-hour limit, Resets …") is the live meter. A "Weekly limit reached" banner can be left from an earlier turn: it is reported, never trusted to block a send.

### Projects (beta)

A project is one ongoing conversation with a coordinator Claude that splits work into **threads**, each a session (usually cloud, optionally on your machine via "Work locally"); threads share the project's instructions, memory, files and repositories, default to Opus at high effort, and are **not** listed in the sidebar's session list ([docs](https://code.claude.com/docs/en/claude-projects)). Pages: list `https://claude.ai/code/projects/browse` (`a[href^='/code/project/chan_']`), project `https://claude.ai/code/project/chan_<id>`.

- Conversation rows `[data-testid='transcript-row']`: Claude's `[data-testid='assistant-message']`; yours have a `Show message actions for You said…` button (no user-message test id). Only recent rows are rendered.
- Threads: cards `[data-testid='channel-created-thread'] button[aria-label]` ("Title — View thread — 4 replies — New messages — Resolved") and inline links `button[data-line-thread=<state>]` (text "Title — Resolved"). The list is what the loaded conversation mentions.
- Overview counts: `[data-home-section]` headings "Waiting on you · 0", "Resolved · 4". The Resolved group did not expand from a script.
- **Never click thread links or cards**: they hand off to the desktop app. Reading uses text only; `project-send` types only into the project's own message box (untested end to end).
- Not scripted: creating a project ("New project" dialog: Name, "What do you want to work on?", Context, `Create project`), project settings, messaging a thread directly.

Tests: `tests/e2e/test_web_code.py` is read only (sessions, one session, projects, one project). `aao code new --prompt "hi" --wait` was verified by hand (cloud session, Default environment, reply read back).

To study a new target: log in (`aao web login <t>`), open its profile headed, `snapshot` for the composer, then send one prompt from a `run-code` script that samples buttons, `data-testid`s and streaming attributes every second; turn what you see into a recipe and verify with `uv run pytest -m e2e -k "<t>"` and a long reply (`aao web ask <t> --prompt "...ENDMARK" --expect "ENDMARK\s*$"`). Every test prompt creates a chat in that account's history.

## Turn protocol (every web target)

1. Open or reuse the target's session; never share one browser session between targets.
2. Check login (URL contains `login`/`auth`/`sign`, or no composer). If logged out, stop and ask the human to log in in a headed session; never type credentials. Persist with `playwright-cli state-save` (stored under `.playwright-cli/`, gitignored, or the `playwright-profiles` Docker volume).
3. `snapshot` → fill the composer → send. Upload files through the page's file input (`upload`) only after clicking the attach control (see `ModelAdapter.upload_files` in mcp-web-llm).
4. Wait until generation ends: no visible Stop button **and** the last assistant message is unchanged across two polls.
5. Extract only the last assistant message and return it to the orchestrator; long-running remote sessions (remote Claude Code, Codex tasks) are polled, not awaited.

## Reference projects (`external/`, read-only)

- **mcp-web-llm** — Playwright adapters (`models/<site>.py`, base class `models/base.py`) for ChatGPT, Claude, Gemini, DeepSeek, Grok, Qwen, over CDP to a dedicated Chrome (`--remote-debugging-port=9222 --user-data-dir=...`).
- **MuseBridge** — Muse web UI exposed as an Anthropic Messages API (`127.0.0.1:8765/v1/messages`) for Claude Code; its headed `login` command auto-discovers composer/send/message selectors into a config file. Muse access is region-restricted.
- **browser-harness** — CDP control of the user's running Chrome with per-domain skills (`agent-workspace/domain-skills/`) and interaction skills (uploads, iframes, shadow DOM, dialogs).
- **orca** — Electron app that runs coding agents in parallel worktrees with a coordinator/worker model; reference for orchestration UX, not a runtime dependency.
