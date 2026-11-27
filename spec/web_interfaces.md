# Controlling web and desktop AI interfaces

How the orchestrator drives AI products that have no CLI (or whose remote/cloud mode is web-only). CLI agents are covered in [AGENTS.md](../AGENTS.md#cli-agents-herdr). The implemented web drivers in `src/orchestrator/` use `playwright-cli`; the Claude in Chrome extension and native desktop control are not currently wired into AAO.

## Driver per channel

| Channel | Implemented driver | Scope |
| --- | --- | --- |
| Web apps and remote coding agents | `playwright-cli` with persistent profiles and named sessions | `aao web`, `aao chats`, `aao code`, `aao codex`, and `aao flow` |
| Existing Chrome / Browserless via CDP | `aao grok attach --config <path>` | Grok only; the config must provide `browser.cdpEndpoint` |
| Claude in Chrome extension | Not integrated | The reference in other project guidance is not an AAO driver |
| Native desktop apps | Not integrated | No computer-use driver is implemented in this repository |

## Targets

The implemented targets and their command surfaces are listed below. Web UIs change often; selectors in the code are site-specific recipes and may need updating when a site changes.

| Target | URL | Implemented support |
| --- | --- | --- |
| Grok | `https://grok.com/` | Chat turns and projects; Imagine image/video generation, saved @references, Draw, and Agent canvases; Grok-only CDP attach. Bot support is not implemented. |
| Muse | `https://muse.ai/` | Browser login/status, chat turn extraction (`aao web ask muse`, `aao chats muse list\|search\|read`), image/video/audio generation (up to 9 image references, `--file` video/audio inputs, `--aspect`, `--voice` direct TTS), Library listing/download (`aao web library\|download muse`), and usage quota (`aao web quota muse`). |
| Gemini | `https://gemini.google.com/app` | Chat and Task turns, local file and GitHub-repository attachments, chat list/notebooks, image/video generation, library listing/downloads (`aao web library gemini`, `aao web download gemini`), and the usage quota (`aao web quota gemini`: current and weekly limits from `gemini.google.com/usage` plus the Google AI credits balance, also on the Quotas tab). |
| Claude web | `https://claude.ai/new` | Chat turns and chat list/sections through Playwright. |
| Claude Code on the web | `https://claude.ai/code` | Sessions and beta projects through `aao code`; shares the Claude browser profile. |
| ChatGPT | `https://chatgpt.com/` | Chat turns, chat list/projects, image generation. |
| Codex on the web | `https://chatgpt.com/` in Codex mode | Threads and question handling through `aao codex`; shares the ChatGPT browser profile. |
| Google Flow | `https://flow.google.com/` | Project/media management and image/video generation; shares the Gemini browser profile. `aao web quota flow` reads the Google Flow credits left and their reset from the account menu (also on the Quotas tab). |
| Google Flow Music | `https://www.flowmusic.app/` | Song generation and MP3 downloads for AAO and ArtCraft. Sign in once with `aao web login flow-music`. |
| Z.ai | `https://chat.z.ai/` | Browser login/status only; a scripted chat turn is not implemented. |
| AutoClaw (Zwork Remote Control) | `https://autoclaw-remote-web.z.ai/` | Pair a Zwork computer, list sessions, read transcripts, and send prompts to an existing session. |

`aao web ask` has reliable, structured turn handling for Claude, ChatGPT, Gemini, and Grok. Muse and Z.ai are declared browser targets, but their recipes have no structured assistant-message or completion selectors; the generic whole-page fallback is not a reliable chat-turn driver. Use `aao code` and `aao codex` for remote coding sessions rather than `aao web ask`.

## Scripted recipes (`src/orchestrator/web.py`)

The four scripted chat targets have recipes in `TARGETS`; `PAGE_SCRIPT` runs them with `playwright-cli run-code`, with no LLM in the loop. Keys include `composer` (first visible selector), `send` (fallback button if Enter did not send), `user` (proves the turn was sent), `assistant` (the reply source), `busy` (still generating), and `strip` (elements removed before reading). Targets without an `assistant` selector fall back to whole-page text matching; that fallback does not reliably detect a completed reply and is not supported chat-turn behavior.

Turn: open the target's new-chat URL by default (or the resolved existing chat from `--chat`) → count assistant turns → click composer, insert text, Enter → wait up to 10 s for a new user turn or a URL change, else click `send` once → wait for a new assistant turn with no `busy` marker whose text is identical across two 1 s polls → return `reply`, `url`, `seconds`; `ok` is the optional `--expect` regex tested on the reply only.

| Target | Studied | Signals |
| --- | --- | --- |
| gemini | 2026-11-12 | Enter sends; URL `/app/<16 hex>`; user `user-query`; reply the last `model-response message-content .markdown` (`aria-busy="true"` and a `Stop response` button while writing; strip `sources-carousel-inline`, the file/source references). `--mode chat|task`: the `gem-segmented-button` row under the prompt box (selected one has `gem-segmented-button-selected`); a Task reports its state in `remy-goal-status-footer` ("Complete"), returned as `taskStatus`. `--file` (repeatable): open "Upload and tools", then `setInputFiles` on the hidden `input[type=file]` whose accept list has `.py` (documents, code and `.zip`; the menu creates the inputs; never the OS file dialog, which playwright-cli intercepts); wait for one `gem-attachment` chip per file. `--repo <GitHub URL>`: "More uploads" > "Import code", fill "GitHub repository or branch URL", wait for "Repository: owner/name", then press Import (a press right after that line is ignored, so it is pressed until the dialog closes); needs GitHub connected in the account. There is no scriptable folder upload ("Upload folder" uses the browser's folder picker): zip the folder and pass it with `--file`. |
| grok | 2026-11-12 | Enter sends (`[data-testid='chat-submit']` otherwise); URL `/c/<id>`, or `/project/<id>?chat=<id>` inside a project; user `[data-testid='user-message']` (starts with a "You" heading); reply the last `[data-testid='assistant-message']`; a `Stop` button while writing |
| chatgpt | 2026-11-11 | Enter sends; URL `/` → `/c/<id>` (`/g/<project>/c/<id>` in a project); user `[data-user-message-bubble='true']`; reply the last `[data-markdown-text-style='assistant-message']`; each turn sits in `[data-talvt-turn-state]`, `complete` once done (any other value, or a `Stop` button, means still writing). The old `data-message-author-role` markup is gone. |
| claude | 2026-11-12 | Enter sends; URL `/new` → `/chat/<id>`; user `[data-testid='user-message']`; reply `[data-testid='assistant-message']`, `data-is-streaming='true'` and a `button[aria-label='Stop response']` while writing; strip `.sr-only` (a hidden "Claude responded: …" heading) and `[data-testid='message-actions']` |

`aao web ask <t> --chat <id|url|title>` continues an existing chat instead of opening a new one (a title is resolved through the chat list below).

A new prompt always reloads the target's new-chat URL first: the shared browser may be left on another chat, a project page or another mode, and the prompt would land there (seen on Grok, where a reused project page put the new chat into the project). Targets with several modes on one page declare `mode` (chatgpt: switch to ChatGPT, since Codex mode shares the page).

## AutoClaw remote sessions (`src/orchestrator/web_autoclaw.py`)

```sh
uv run aao web login autoclaw
uv run aao web status autoclaw
uv run aao web sessions autoclaw
uv run aao web read autoclaw --chat "Session title"
uv run aao web ask autoclaw --chat "Session title" --prompt "Summarize our discussion" --timeout 300
uv run aao web close autoclaw
```

On the computer running Zwork, open **Settings → Remote Control**. Open its pairing link in the Chrome window launched by `aao web login autoclaw`, approve the pairing in Zwork, and connect to the computer in Chrome. The browser keeps the pairing in the dedicated `autoclaw` profile. AAO can find the connected tab when the pairing link opens a second one. Zwork must stay running for remote control to work.

Use the exact, unique session title from `sessions`. `read` returns the user and assistant messages currently loaded in the transcript. `ask` sends one text prompt to the chosen session and waits for its reply. If Zwork cannot load the transcript, AAO reopens the task once and reports any remaining error before submitting a prompt. It also reports a busy session, a disconnected computer, or a pending question or approval so the operator can handle it in Chrome. `status`, `sessions`, `read`, and `ask` support `--json`. `ask` also accepts an optional `--expect` regex.

The web UI selects sessions through the task list without changing the URL. The driver scopes replies to the new user message in `transcript-view` and uses the task status badge and queue markers to wait for completion.

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

ChatGPT (`aao chats chatgpt`, studied 2026-11-11; recipe and script in `web_chats_chatgpt.py`). Groups are **projects**: `projects`, `move <chat> <project> [--create]`, `unproject <chat>`, `new-project <name>`, `delete-project <name> --yes`, `list --project <name>` (all chats of one project, from its home page).

- Sidebar `nav[aria-label='Chat history']`; sections start at `[data-app-action-sidebar-section-toggle]` buttons: **Pinned** (pinned chats *and* pinned projects), **Projects** (unpinned projects), **Recents**. A section is the largest ancestor of its toggle holding no other toggle.
- Chat row `[data-sidebar-cycle-item]` (`aria-label` = title) with `Pin chat` / `Unpin chat` and `Chat actions` buttons; link `/c/<uuid>`, or `/g/g-p-<32 hex>[-slug]/c/<uuid>` for a chat in a project. A pinned chat shows only under Pinned. A chat in a project can also appear in Recents, so project membership is read from the chat's URL (opening `/c/<id>` redirects to the project path) or from the project's home page, never from the section it is listed under.
- Project row `[data-app-action-sidebar-project-row]` with `data-app-action-sidebar-project-id|label|collapsed`; it sits in a `[role=listitem]` together with its chats when expanded (the five newest, then `Show more`). Collapsed projects render no chats. Home page `/g/<id>/project` lists all chats (`main a[href*='/c/']`, loads more on scroll).
- **Menus are Radix menus: focus the button and press Enter.** No hover and no clicks on rows. Chat actions: `Rename`, `Pin`/`Unpin`, `Lock chat`, `Share`, `Archive`, `Delete`, `Move to project` (submenu via ArrowRight: `New project`, then one item per project), and `Remove from project` for a chat in a project. Project actions (`Project actions for <name>`): `Share project`, `Rename project`, `Project settings`, `Project home`, `Pin`/`Unpin project`, `Delete project`.
- The chat page title menu (`[data-app-shell-page-header] button[aria-haspopup=menu]`) says `Pin chat` and has `Move to project` only for a chat outside projects; a project chat's title menu has neither move item, so the script expands the project in the sidebar (and `Show more`) to reach the row, then folds it back.
- New project: `Add new project` (Projects heading) or `New project` in the move submenu → dialog `input[name='project-name']` → `Create project`; the page then opens the project.
- **Deleting a project permanently deletes all its chats** ("Delete this project from Chat and Work?" → `Delete from Chat and Work`). `delete-project` therefore refuses a project that still has chats and confirms only that exact dialog and button.
- Search: the visible `Search` button opens a dialog with `input[placeholder^='Search']`; results are `a[role=option]` links (title, then date/snippet).

Grok (`aao chats grok`, studied 2026-11-12; `web_chats_grok.py`). Chat and projects only. Groups are **projects**: same commands as ChatGPT.

- Sidebar rows are buttons with no link or id (`[data-reorder-item=recent|projects|bots]`, labels `[data-sidebar-item-label]`), so **reading uses the site's own endpoints** from the page with the signed-in session: `GET /rest/app-chat/conversations?pageSize=N&pageToken=T` (conversationId, title, `starred` = pinned, modifyTime; `nextPageToken`), `&workspaceId=<project>` for one project's chats (a chat's `workspaces` field stays empty), `&searchQuery=<q>` (matches message text, not titles, so `search` puts title matches first), and `GET /rest/workspaces` (projects). A 401/403 there means signed out. Actions never call these endpoints; they go through the UI and are verified by reading them again.
- Chat actions use the chat page's menu (`[data-testid=conversation-actions-trigger]` on `/c/<id>`), which reaches every chat: `Pin`/`Unpin`, `Rename`, `Add to Project` (or `Move to Project` and `Remove from Project` inside one), `Archive`; no delete. The project submenu is a search box (`input[aria-label='Search projects']`) with `role=option` items. `Remove from Project` asks "Remove this chat from <name>?".
- New project: `New Project` in the sidebar's Projects section → dialog `input[aria-label='Project name']` → `Create` (then opens `/project/<id>`). Project row menu `Actions for <name>`: Files, Instructions, Open project, Add files…, Rename, Share, Edit Icon, Edit instructions…, `Delete project`. **Deleting a Grok project keeps its chats** ("The project's chats stay as standalone chats"); the script confirms only a dialog with that sentence.
- Base UI menus open with focus + Enter. A hidden `role=menu` element is always in the DOM: only visible menus count. Dialogs are `position:fixed` (no `offsetParent`), and a hidden OneTrust "Privacy Preference Center" dialog is always present, so dialogs are matched by their own text.

Gemini (`aao chats gemini`, studied 2026-11-12; `web_chats_gemini.py`). Groups are **notebooks** (NotebookLM notebooks): `notebooks`, `list [--notebook N] [--status S]`, `search`, `read <chat>`, `pin`/`unpin`, `move <chat> <notebook> [--create]`, `unnotebook <chat>`, `new-notebook`, `delete-notebook --yes`.

- Chat list: `conversations-list[data-test-id=all-conversations] gem-nav-list-item[data-test-id=conversation]`, link `/app/<id>`, loads more on scroll. A pinned chat stays in the list, on top, with a `push_pin` icon. A Task's state is the tooltip (`gemtooltip`) of its `remy-goal-status-badge` ("Unread", ...); the list Filter offers All / Ready / Needs attention / In progress. `list --status "Needs attention"` lists the tasks waiting for the human, and `read <chat>` returns the last prompt, reply, task state and the buttons of the last answer, for the master agent to decide or show on the dashboard.
- Row menu `More options for <title>` (Enter, else a click event on the button): Share conversation, `Pin`/`Unpin` (`pin-button`/`unpin-button`), Rename, `Add to notebook` (dialog "Move chat", one `role=option` per notebook), Delete.
- A chat in a notebook leaves the list and appears on `/notebook/<uuid>` under "Past chats" (`project-chat-row`, title `[data-test-id=chat-title]`, no link); its menu: Move to notebook, `Remove from notebook` (`remove-project-button`), Rename, Delete.
- Notebooks: the sidebar shows the recent ones (links `/notebook/<uuid>`); `/notebooks/view` shows all as `project-mgmt-row` cards (name, "N sources", menu Pin / Rename / Delete) without links. New: `/notebooks/create`, input "Name of the notebook", Enter. **Delete removes the notebook's sources and chats from Gemini and NotebookLM** ("Delete notebook everywhere?" → `Delete everywhere`), so `delete-notebook` refuses a notebook with any source or chat.
- Search: `Search chats` opens `input[aria-label='Search chats']`; results are `/app/<id>#<message>` links (icon name, title, snippet). It matches message text and lags behind new chats, so sidebar title matches come first.
- **Rate limit.** Google answers a burst of page loads with its "unusual traffic" check (`google.com/sorry`, a CAPTCHA). Scripts never answer it: they stop with `blocked: true` and a human clears it in the browser. Page loads are spaced and kept few (`list` uses the sidebar's notebooks; only `notebooks` and `--notebook` open the notebooks page). Every action also runs at a human pace, set by `pace` in the recipe (gemini in `web.py` and `web_chats_gemini.py`): a 0.7-1.6 s pause before each click, the mouse moved onto the element and the button held 60-160 ms, text typed key by key 45-140 ms apart (longer than 400 characters it is pasted, as a person would), and 2.5-5 s before each page load. A full e2e run takes about 6 minutes.

The e2e test (`tests/e2e/test_web_chat_list.py`) makes its own chat and a throwaway `aao-e2e-<n>` group (a section on Claude, a project on ChatGPT), and deletes the group at the end.

## Browser sessions

Targets on the same site and account share one login: `TARGETS[t]["profile"]` (Claude Code shares `claude`, Codex shares `chatgpt`, and Flow shares `gemini`), so they share the named Playwright session `aao-web-<login>`. Profiles are stored outside the repository at `~/.aao/browser-profiles/<login>` by default; `AAO_WEB_PROFILE_DIR` changes the base directory. `aao web login` opens a headed browser and waits for a human to sign in; AAO does not type credentials. Commands reuse an open browser and leave it open; `aao web close [t|--all]` closes it, and `AAO_WEB_KEEP_OPEN=0` closes it after each command. `AAO_WEB_HEADED=0` requests a headless browser, and `AAO_WEB_BROWSER` selects the browser (set it to `chrome` to use installed Chrome). Do not run two commands against the same browser at once. Only Grok has a separate CDP attach command. playwright-cli `run-code` has no `URL` global outside `page.evaluate`: parse `page.url()` by hand.

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

## Codex on chatgpt.com (`src/orchestrator/web_codex.py`)

`aao codex threads | read <thread> | new --prompt .. [--env --work-in Cloud|Remote --computer --permission --model --effort] [--wait] | send <thread> --prompt .. [--wait] | wait <thread> | archive <thread> | attention [<thread>] | answer <thread> --choice <label> | --text <answer>`. Studied 2026-11-11; `tests/e2e/test_web_codex.py` runs the whole cycle on a throwaway thread.

- Codex is a **mode** of chatgpt.com, not a page: the "ChatGPT ⌄" button (`aria-label` "Switch mode, current mode: ChatGPT|Codex") opens a menu with ChatGPT and Codex. The URL stays `/`; the sidebar and composer change. Both `web_chats_chatgpt.py` and `web_codex.py` switch to their mode first. Menu items read "Codex
Build, debug, and ship": match the first `innerText` line (Playwright `hasText` sees the lines run together).
- Sidebar: Recents only. Thread rows `[data-app-action-sidebar-thread-row]` with `data-app-action-sidebar-thread-id`, `-title`, `-pinned`, `-active`; link `/local/<uuid>`. **Take the id from the link**: a thread started in the open page keeps a client id (`local:client-new-thread:<uuid>`) in the row until the next reload. Each row has an `Archive chat` button (no confirmation); where it runs: `[data-testid^='task-row-environment-']` (`cloud`). Thread menu: Rename, Mark as unread, Share, Copy, Archive (no pin, no projects).
- New thread composer: `Work in` (Cloud | Remote; Remote then shows `Select computer`, "No remote computers added" on this account), `Select environment` (searchable: Default environment, your environments, Create environment), `Change permissions` (Ask for approval | Approve for me | Full access), model and effort menu.
- **New thread**: only from `/` with the `Work in` control visible. "New chat" in the sidebar does not reliably leave the open thread; a prompt typed there goes into that thread (this happened once while studying). Settings are chosen from each menu by the item's first text line; effort is a `role=slider` in the model menu (the label beside the model name shows the level). Enter sends; the URL becomes `/local/<uuid>`.
- **Working**: the composer shows a `Stop` button (and "Thinking") while a turn runs and `Send` again when it ends; the turn then shows "Worked for …". `wait` polls for that, and stops early with `needs-attention` when a question opens.
- **Results**: "Edited N files +A -D" lists one button per file (path, +/-) with "Show N more files" (never press its `Undo`); it appears for patch edits, not for files written by shell commands. Expanding "Worked for …" shows the turn's actions ("Ran printf …", "Edited …"), read and folded back. Pull requests are read from `github.com/…/pull/…` links in the last turn (none seen yet: Codex pushes only when asked).
- Thread page: turns `[data-turn-key]`; user `[data-user-message-bubble='true']`; each turn's answer `[data-local-conversation-final-assistant='true']`; "Worked for …" and "Edited N files +A -D" per turn (read from the last turn only). Some threads show no messages on the web at all.
- **Questions** (Codex "request input"), above the composer in `[data-request-input-activity-root]`: title, `n of N` with `Previous question` / `Next question` arrows, `Dismiss` (`[data-request-input-dismiss]`), a body and action buttons in `[role=presentation]` (Skip, Next, Finish review). Multiple choice: the question text is a markdown `<p>`, options are `role=radio` buttons whose `aria-label` is the clean label (their text starts with a hidden number), an "Or write your own response" box, and `[data-question-actions]` Skip / Send; clicking an option submits a single-choice question at once. Other bodies seen: label/value rows with an edit button named after the label ("Install script: Not set"), and `[role=group][aria-label=<NAME>]` key/value groups for secrets (value input `type=password`) and environment variables, each with a Scope menu and, for secrets, allowed domains. Next / Finish review stay disabled until required values are filled. Paging with the arrows changes nothing. The environment badge "Needs attention N" counts the steps left; when the review is closed, `attention` opens it (view only) to read them. Next on a step does save it: after Next on step 1 the badge went from 3 to 2.
- **The driver reports, the master agent decides.** `read` and `attention` return every step (title, description, items, fields with `filled` and never a secret's value, options, actions with `enabled`, `missing`, `humanOnly`). `answer --choice` presses one named option or action (then Send if still needed), refuses a disabled one and names the fields it waits for; `answer --text` writes only into the question's own free-text box. It never types into a secret or value field: those are left to a human, and the master agent shows them on the dashboard as attention required.

## Google Flow (`src/orchestrator/web_flow.py`)

`flow.google.com` uses the Gemini profile and browser session (`aao web login gemini`). The `aao flow` commands manage projects and media. The CLI and ArtCraft use Flow's direct composer and download originals. By default, a generation creates a project and requests one output. Generation uses the signed-in account's quota or credits.

```sh
uv run aao flow status                                  # is the Google login still good
uv run aao flow projects                                # id, title (default title is the creation date)
uv run aao flow new [--title "..."]                     # empty project, free
uv run aao flow new-character <project> --name NAME (--face-prompt TEXT | --face-image FILE) [character options]
uv run aao flow edit-character <project> <character> [character options]
uv run aao flow read-character <project> <character>
uv run aao flow characters <project>
uv run aao flow read <project> [--type video|image|scene|character|collection]   # newest first: index, type, name, thumbnail
uv run aao flow download <project> <media> [--to DIR] [--quality original|gif|1080p|4k]
uv run aao flow options [--project P]
uv run aao flow upload <project> <file>...                # reference images/videos into the project library (free)
uv run aao flow add-to-scene <project> <video> "Scene name"
uv run aao flow save-frame <project> <video-or-scene> [--clip N] [--at PERCENT]
uv run aao flow extend <project> <video-or-scene> --prompt "What happens next"
uv run aao flow delete <project> --yes                  # PERMANENT: clips, ingredients and prompts are gone
```

`<project>` is an ID, a project URL or an exact title. `<media>` is the 1-based index from `read` or the exact name in the rendered media grid. Each project command reloads the page to start with a fresh grid and composer. Use `--json` before the subcommand for structured output.

**Credits.** Reading, creating projects, uploading, original-size downloads, and video GIF downloads are free. Generation, portrait edits with a prompt, Extend, and upscaling can spend credits. `download` uses the original by default. Use the scene's name or index to download its original. Character tiles have no download menu item. `read-character` returns the face and body image URLs along with the ID, name, info, and voice (see [Flow characters](#flow-characters)). Use the character's exact name, ID, or editor URL to read or edit it.

### Generation settings

`aao flow options` reads both Image and Video from the direct composer, including image quality, video resolution and model notices. JSON results include both under `modes`.

```sh
uv run aao web image flow --project P --model "Nano Banana 2.1" --aspect 16:9 --quality High --count 2 --prompt "A storefront at dusk"
uv run aao web video flow --project P --model "Omni 1.2 Flash" --resolution 720p --duration 8 --character "Sophie Goodman" --prompt "Sophie answers the door"
uv run aao web video flow --project P --start-frame entrance.png --end-frame office.png --prompt "Walk into the office"
```

Use `--count` from 1 to 4 and `--aspect` for either media type. `--quality` selects image quality or accepts a video resolution as an alias for `--resolution`. `--duration` requests seconds and fails before generation if Flow cannot provide that duration with the chosen settings. Repeat `--reference` to attach up to three local images, or `--character` to attach saved characters by name with their saved voices. Frames use project image names or indexes and require video generation in an existing project. Ingredients and frames use separate composer modes.

`add-to-scene` accepts an exact scene name or `"New scene"`. `save-frame` saves the chosen playhead position as an image in the project. `--clip` selects a clip in the scene and `--at` is the percentage through that clip. `extend` uses Flow's Scenebuilder Extend mode on the last clip and reports the longer duration when Flow finishes.

### Page notes

- Home: project cards `a[href^='/project/<id>']` with "Edit project title" and "Delete project" buttons; "New project" creates one and the URL becomes `/project/<id>`. Delete asks "Are you sure ... permanently deleted" with a `Delete project` button.
- Project page: media in `flow-grid-tile-container > flow-<type>-tile`; the title is an always-present `textbox "Editable text"` (an h1 only while not edited); a populated project shows the agent panel on the right, an empty one shows the box at the bottom. Clicking a tile opens a viewer without changing the URL.
- Upload opens Flow's file chooser once per file. The driver checks for a newly visible tile with the uploaded file's name and thumbnail, including when the virtualized grid keeps the same number of tiles.
- Characters: `new-character`, `edit-character` and `read-character` drive the project's character builder; see [Flow characters](#flow-characters). `characters` lists a project's saved characters.
- A cookie banner ("flow.google.com uses cookies") overlaps the bottom; the driver presses `No thanks`.
- `run-code` has no `URL` global (parse `page.url()` by hand) and the home page loads slowly: the `flow` target settles 12 s before the login check.
- Tests: `tests/e2e/test_web_flow.py` is read-only plus one free download; project create, upload and delete were checked by hand on throwaway projects.

### Flow characters

Use `--face-prompt` or `--face-image` for the portrait, and `--body-prompt` or `--body-image` for a separate full-body reference. The original `--prompt` and `--image` spellings still work for the face. `--info` sets the character's background or personality.

`--voice` selects a Flow voice by name. To create a custom voice, pass a base preset with `--voice` and describe its accent, tone or delivery with `--voice-description`. Use `--voice-name` to name the result, or let it default to the character name plus “Voice”. Generating a face, body or custom voice may use Flow credits. [Google Flow voice documentation](https://support.google.com/flow/answer/16353334#custom_voices)

```powershell
uv run aao flow new-character "My Project" --name Mara --face-image mara-face.png --body-prompt "Full-body view in an olive flight suit" --info "A calm test pilot" --voice Kore --voice-description "Low, measured delivery with a slight Vancouver accent"
uv run aao flow edit-character "My Project" Mara --body-image mara-flight-suit.png --info "Former rescue pilot"
uv run aao flow --json read-character "My Project" Mara
```

Edit and read accept the character's exact name, ID or editor URL. Edits apply the supplied fields. The read result includes the ID, name, editor URL, character info, voice and performance where exposed by the editor, and available face and body image URLs. If a multi-step change stops in the editor, the CLI prints the URL so you can reopen that character. Browser control follows the [flow-mcp UI reference](https://github.com/GTAI-1/flow-mcp/blob/main/CLAUDE.md) and the [gflow-cli body slot notes](https://github.com/ffroliva/gflow-cli/blob/develop/docs/CHARACTER.md).

## Grok chat and Imagine (`src/orchestrator/web_grok.py`)

`aao grok` uses the browser session shared by `aao web` and `aao chats grok`. Sign in once with `aao web login grok`. The new text command and `aao web ask grok --prompt` print the full captured reply and send a leading `Thought for …` header to stderr. A custom prompt succeeds without a matcher unless you supply `--expect`. `aao web ask grok` without a prompt still runs its generated smoke test.

```sh
uv run aao grok chat "Summarize our discussion" --chat "Project notes"
uv run aao grok image "A paper plane over mountains" --quality Quality --aspect 16:9 --count 4 -o downloads
uv run aao grok video "Animate the paper plane" --image reference.png --res 720p --duration 10s --aspect 16:9 -o downloads
uv run aao grok --json image "A red teapot" --aspect 1:1
uv run aao grok image "reading in a library" --ref Fox --ref Lena     # saved @references
uv run aao grok image "Restyle this" --upload photo.png              # one-off uploaded image
uv run aao grok reference list [--category Character|Prop|Location]
uv run aao grok reference new Hero --image front.png --image side.png [--voice hero.mp3] [--description ..] [--category Character]
uv run aao grok reference edit Hero [--title Heroine] [--description ..] [--image extra.png] [--voice v.mp3] [--replace-media]
uv run aao grok reference delete Hero --yes
uv run aao grok video "a slow pan" --image first.png [--middle-frame mid.png] [--last-frame last.png] [--voice Eve] [--audio|--no-audio] [--res 1080p --duration 15s]
uv run aao grok video "gentle sway" --image still.png --loop           # starts and ends on the image
uv run aao grok image "combine these" --pick generation:1 --pick upload:sketch.png --dry-run
uv run aao grok library generations|uploads|voices                    # what --pick and --voice can name
uv run aao grok template list
uv run aao grok template show photo-edit                               # sections, options, image slots
uv run aao grok template run photo-edit --image photo.jpg --choose "Edit=Cozy Warm" [--text "Details=.."] [--dry-run]
uv run aao grok draw "a sailboat on a lake" --sketch sketch.png [--strokes strokes.json] [--style Manga] [--aspect 2:3] [--brush 6]
uv run aao grok canvas                                               # New canvas: prints its URL
uv run aao grok canvas "Make a 4-panel storyboard" [--canvas <url|id>] [--file brief.pdf] [--note "text"] [--board-image ref.png]
uv run aao grok canvas --list
```

Imagine generation uses the signed-in account's quota. Images support `Speed` or `Quality` (the page labels it `Quality 2.0`; the driver accepts a trailing version) and counts `Auto`, `2`, `4`, `8`, and `12`. Videos support `480p`, `720p` or `1080p` and `6s`, `10s` or `15s`. `--dry-run` on `image`, `video` and `template run` sets everything up, prints what the composer or form holds, and stops before Submit, so nothing is spent. Both support `2:3`, `3:2`, `1:1`, `9:16`, and `16:9`. Video opens a visible browser when starting a session. It reuses an open session, so close a headless Grok session before generating video in a visible one. `--timeout` controls the time to wait for media after submission. Files default to `downloads/` and have unique names per run. JSON includes saved paths, available HTTPS media URLs, and the conversation URL. A partial download reports its saved files and returns a failure exit code.

The driver selects Imagine controls, clears the composer (it keeps drafts, including `@reference` chips, across reloads), records the existing media sources, submits once, and waits for new sources to settle. It saves inline and remote media using Playwright. The optional reference image is set through the Imagine file input before submitting a video.

**Video frames, Loop and audio.** An image attached in Video mode is the first frame; the composer then offers *Add intermediate frame* and *Add last frame*. Those buttons, like Loop's *Upload photo*, click a hidden file input that opens the OS file chooser, which blocks playwright-cli, so the driver replaces `input.click()` in the page to catch the input and sets its files directly. The *Loop* tile asks for the image the video starts and ends on and puts a `Loop` chip in the prompt (the prompt is appended after it). *Video audio* is an on/off switch (`--audio`/`--no-audio`; omitted leaves it as it is).

**The + toolbox.** The composer's + button (`Upload`) opens a toolbox with tabs Generations, Uploads, Audio and References, plus Draw and Upload media. A tab click during its opening animation is dropped, so the driver retries until the tab is selected.

- **Generations and Uploads** list your library newest first. Generations are all labelled `image.jpg`, so `--pick generation:N` takes them by position; uploads keep their file names (`--pick upload:NAME`, or `upload:N`). Selecting one attaches it like an uploaded image (the first frame in Video mode). These rows never count as stable for a pointer click, so the driver fires a DOM click.
- **Audio** lists 56 preset voices (Eve, Rex, Luna, …) with a sample each; `--voice NAME` attaches one to a video. `aao grok library voices` lists them.

- **References** are saved `@Name` characters, props and locations (Grok ships samples such as `@Fox`). `reference new` fills the New reference dialog: title, description, category, images (jpeg, png, gif, webp, bmp, tiff; at least one) and voice clips (mp3, wav, ogg, flac, aac, m4a, webm, opus). Grok derives the `@Name` from the title. Each voice clip opens a Trim step; the driver keeps the whole clip (Done). Grok screens voices and answers a clip it will not use (tested: a synthetic tone and Windows text-to-speech) with `audio-not-allowed` without showing anything on the page; the driver reads that answer and reports it. `reference edit` opens the same form (heading *Edit reference*, button *Update*): it renames, changes the description, adds images or clips, or with `--replace-media` removes the current ones first (each item's tile is itself a role=button named *Remove media*; the real control is the `<button>` inside it). Grok disables the category once a reference exists. Deleting asks Grok's confirmation dialog and needs `--yes` on the CLI. `--ref NAME` on `image` and `video` clicks *Use @NAME*, which inserts a mention chip, then appends the prompt. Grok files such a generation as an edit under the reference's own post, ignores the count and aspect ratio, and does not render the result on the page it opens; the driver finds the new post in the sidebar's recent media and downloads it from that post.
- **Draw** (`https://grok.com/imagine/draw`) is a brush canvas with Brush/Eraser, a size slider, seven preset colors plus a custom hex, Style (`Manga`, `Illustration`, `Painting`, `Realistic`), and Aspect ratio (`2:3`, `3:2`, `4:3`, `1:1`, `9:16`, `16:9`, `21:9`, `5:2`). The page redraws from its own stroke list, so a bitmap painted onto the canvas is discarded. `--sketch` therefore replays an image as horizontal brush strokes, one row per brush width; colors snap to the palette and light pixels are skipped. Keep sketches simple (flat colors, white background); a sketch over 6000 strokes is refused. `--strokes` takes `[{"points": [[x, y], ...], "color": "#rrggbb", "size": n}]` with `x` and `y` from 0 to 1; a color outside the palette is set through the hidden native color input without opening the OS picker. Generate opens `/imagine/post/<id>`, and the image under `/generated/<id>/` is saved.
- **New canvas** is the Agent mode (`/imagine/agent/<id>?conversation=<id>`): a chat with the Imagine agent beside an infinite board (Files and Canvas views; Select, Hand, Generate Image, Generate Video, Text and Upload image tools). Grok lists each canvas under Projects, named from its first message. `aao grok canvas` without a prompt creates one and prints its URL. With a prompt it attaches `--file`s (set on the input behind Attach files → Upload a file, because a native file chooser blocks playwright-cli), sends the message, waits for the reply's action toolbar, and saves new `/generated/` media from the turn. The reply goes to stdout and a `Thought for …` header to JSON `thinking`. `--canvas` takes the printed URL or the agent ID. `--note` places a text note with the board's Text tool and `--board-image` puts local images on the board with its Upload image tool, before any message. A canvas is a Grok project (`/project/<id>` is the same ID) whose conversation has kind `CONVERSATION_KIND_IMAGINE`; `canvas --list` reads the site's own `/rest/workspaces` and `/rest/app-chat/conversations?workspaceId=` endpoints and prints each canvas URL.
- **Templates** (`/imagine/template/<slug>`, 16 of them: Photo Collage, Icon Maker, Emoji Creator, Mascot Maker, Photo Edit, BG Removal & Change, Profile Picture, Product Color Change, UGC Photos, Professional Headshot, Reimagine, Smart Resize, E-Commerce Photos, Hero Product Reveal, Character Sprite, Editorial Product Poster) are forms of labelled sections: image slots (*Add reference*), option groups and text boxes. `template show` prints them; the text boxes have no labels of their own, so each control takes the first text in its nearest container that is not a control's own text. `template run` takes the slug, with or without `imagine-`, or the display name; `--image [SECTION=]FILE` (a bare file goes to the first image section), `--choose SECTION=OPTION` and `--text SECTION=TEXT` match section labels case-insensitively by prefix. Add reference opens an inline library picker that stays open after an upload, so its *Upload media* gets a DOM click; React re-renders the form after each step, so the driver re-reads the sections each time. Escape leaves the template, so the driver never presses it there. Results are collected like `@reference` generations: new `/generated/` media on the page or in the sidebar's recent media (tested with Photo Edit → Cozy Warm).

### Python, HTTP, and MCP

Import `chat`, `generate_image`, `generate_video`, `list_library`, `list_references`, `create_reference`, `edit_reference`, `delete_reference`, `list_templates`, `template_info`, `run_template`, `draw`, `canvas`, and `list_canvases` from `orchestrator.web_grok` for Python. Install the optional server dependencies with `uv sync --extra grok-api`.

- `uv run --extra grok-api aao grok serve --port 8000` binds to `127.0.0.1`. `GET /health` checks the service. POST JSON containing `prompt` and any options to `/chat/messages`, `/imagine/images`, or `/imagine/videos`. Imagine accepts `aspect_ratio`, `mentions` (saved reference names), `download_dir`, and `timeout_s` plus its image or video controls. References, Draw and canvas are CLI and Python only.
- `uv run --extra grok-api aao grok mcp` starts the stdio MCP server with `grok_chat`, `grok_generate_image`, and `grok_generate_video`. For an MCP client outside the repository, use `uv --directory <repo> run --extra grok-api aao grok mcp`.

Requests within one server share the browser and run serially. Use one CLI command or server at a time with the same browser session.

### Browser selection

`uv run aao grok install-browser` installs optional Playwright Chromium. Installed Chrome remains the default configuration. Close the Grok session before setting `AAO_WEB_BROWSER=chromium` to start a new one with Chromium.

To use an existing Chrome or Browserless instance, run `aao web close grok` and `aao grok attach --config <path>`. Pass a Playwright config whose `browser.cdpEndpoint` is the instance's HTTP or WebSocket endpoint. For example, `{ "browser": { "cdpEndpoint": "http://127.0.0.1:9222" } }` attaches to local Chrome listening on that port. The attached browser must have a valid Grok login. Use `aao web close grok` to close the AAO session before switching back to a local profile.

## Media generation from the web CLI

```sh
uv run aao web image flow --prompt "A paper plane on a white table"
uv run aao web image gemini --prompt "A paper plane on a white table"
uv run aao web image chatgpt --prompt "A paper plane on a white table"
uv run aao web image muse --prompt "A paper plane on a white table"
uv run aao web video flow --reference plane.png --reference lake.jpg --prompt "A paper plane gliding over a lake"
uv run aao web video gemini --reference plane.png --prompt "A paper plane gliding over a lake"
uv run aao web video muse --reference plane.png --prompt "A paper plane gliding over a lake"
uv run aao web video grok --reference plane.png --prompt "A paper plane gliding over a lake" --json
uv run aao web audio grok --prompt "A soft five-second two-note doorbell chime with a gentle echo" --json
uv run aao web audio muse --reference lake.jpg --prompt "A short instrumental with gently plucked guitar and a soft bell"
uv run aao web audio flow-music --prompt "An acoustic lullaby with piano and brushed drums" --out downloads
```

Flow Music uses a chat brief. `--lyrics`, `--style`, `--length` (target seconds), and `--instrumental` add instructions to that brief. `--song N` requests versions and downloads the Nth finished song shown in the generation thread.

Images support ChatGPT, Flow, Gemini, Grok, and Muse. Video supports Flow, Gemini, Grok, and Muse. Audio supports Flow Music, Grok, and Muse. Each `aao web image|video|audio` command requires `--prompt` and accepts `--out DIR`, `--timeout SECONDS`, and `--json`. Each run writes into a new subdirectory under `downloads/` by default. Use `aao grok image|video` for Grok-specific quality, count, and aspect controls and `aao flow` for Flow project and media management.

For `muse`, `aao web image|video|audio muse` accepts up to 9 `--reference IMAGE` files (PNG, JPEG, WebP up to 8 MiB each), `--aspect` (written into the prompt), and `--file` for additional local image, video (`.mp4`, `.mov`, `.webm`), or audio (`.mp3`, `.wav`, `.m4a`, `.ogg`) inputs up to 50 MiB each. `aao web audio muse --voice NAME` generates spoken TTS MP3 audio directly in Muse chat without `ffmpeg`; without `--voice`, it generates a short video with sound and exports the soundtrack as MP3. `aao web library muse` and `aao web download muse` list and download generated artifacts from `https://muse.ai/library/artifacts`, and `aao chats muse list|search|read` inspects Muse sidebar chats.

For video, `--reference IMAGE` (also `--image IMAGE`) accepts local PNG, JPEG, or WebP files up to 8 MiB each. Repeat the option for up to three Flow ingredients or nine Muse reference images (four via ArtCraft). Gemini accepts one reference image and Grok uses one image as the first frame. Omit the option for text-to-video. The CLI validates references before opening Chrome.

The CLI and ArtCraft use the same browser drivers. Gemini opens **Upload and tools → Upload files** and waits for the attachment chip before sending. Flow switches to **Ingredients** and checks that each uploaded image appears in the prompt. Grok waits for the first-frame preview, and Muse waits for its attachment controls before sending.

For audio, sign in once with `aao web login <target>`. Grok chat accepts a request for a specific downloadable WAV and can use its sandbox to render the audio. AAO saves the file from the inline audio player. Muse's direct audio requests reported unavailable in testing. AAO requests a short video with sound and exports its soundtrack as MP3 using FFmpeg on `PATH`. Flow Music creates a song in a new session and downloads it as MP3. The driver uses keyboard activation for **Send** so the cookie banner can stay open.

## ArtCraft generation via AAO

Apply [the ArtCraft patch](../integrations/artcraft/aao-generation.patch) to a writable ArtCraft checkout at `a6d52df45e`. From this repo, start the loopback job service with `uv run --extra grok-api aao media-serve` (127.0.0.1:8771). In ArtCraft, open the model dropdown under **Create Image** or **Create Video** and choose a provider labeled **via AAO**. The service uses the current CLI accounts and AAO browser profiles; it is local-only and accepts requests from the supported desktop origins, not arbitrary websites.

ArtCraft imports completed files into the signed-in account's **My Library** in the background. Imported files use the normal **Download** action and honor **Settings → Downloads → Auto Download**. The result controls let you retry a failed import or save the original AAO file locally while the library import is unavailable.

ArtCraft offers Codex CLI, Antigravity CLI, Cursor Agent CLI, ChatGPT Web, Gemini, Google Flow, Muse, and Grok Imagine under **Create Image** and **Image Editor**. CLI providers require their executable on `PATH` and use their configured YOLO permission flags. Under **Create Image**, use **Upload** or **Pick from library** on the reference card to add up to four PNG, JPEG, or WebP images (8 MiB each). Image jobs also accept an optional aspect ratio. The Image Editor sends its composited canvas as a reference. Gemini, Flow, and Grok use their native aspect controls where available. AAO adds an aspect instruction to the prompt for Muse, ChatGPT, CLI providers, and Grok ratios unavailable in its menu.

Under **Create Video**, use **Upload** or **Pick from library** on the reference card to add PNG, JPEG, or WebP images (8 MiB each). Grok Imagine takes one first frame, Gemini takes one reference image, Muse accepts up to four reference images, and Flow accepts up to three ingredients in a dedicated generation project. Provider quotas and limits apply.

Under **Create Audio**, choose **Google Flow Music (via AAO)** from the same model menu as Suno. The browser driver starts a new Flow Music session, submits the song description, and downloads the first completed song as an MP3. ArtCraft imports it into **My Library** and shows it in the audio feed. Flow Music uses its own Chrome profile and the signed-in account's generation quota.

**Grok Audio (via AAO)** asks the signed-in Grok chat for a standalone WAV attachment. **Muse Soundtrack (via AAO)** generates a short video with sound through the signed-in Muse account and uses FFmpeg on `PATH` to export the audio as MP3. Muse Soundtrack accepts up to four optional image references as inspiration for the soundtrack and visuals. Use **Upload** or **Pick from library** on its reference card, or repeat `--reference IMAGE` in the CLI. Each image must be PNG, JPEG, or WebP and at most 8 MiB. Flow Music and Grok Audio use text prompts. Describe tempo, instruments, vocals, or duration in the prompt. All three use the ArtCraft download and library import flow.

### Upstream declarative adapter compatibility

[Craft AI's declarative adapter](https://github.com/storytold/craft-ai-integrations/blob/a822ad5b4db2c0dcd7a43bb62d6d75251fd3f8b5/docs/design/declarative-adapter.md) can use the AAO media bridge. The host app needs a `ProviderFactory` that includes the declarative provider and a tool bound to a model with the matching capability. Stock ArtCraft Studio v0.41.1 does not load that provider. Its localhost setting selects the ArtCraft backend for the whole app.

1. Set `AAO_CRAFT_AI_TOKEN` in the environment where you run `uv run --extra grok-api aao media-serve`. Use a randomly generated URL-safe token of 32–128 characters. The bridge is disabled when the variable is unset. On PowerShell, generate a fresh token with `$env:AAO_CRAFT_AI_TOKEN = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`. Keep the same token for future starts of AAO and in the Craft connection.
2. In a Craft AI host that supports declarative providers, create a local connection with `provider: "declarative"` and `endpoint: "http://127.0.0.1:8771/craft-ai/v1"`. Save the bearer token through the host's secret store. Create a separate connection for each spec you use.
3. The host loads the spec from `specs/<connection-id>.json` beside `connections.json` under `CRAFT_AI_DIR` (default `%LOCALAPPDATA%/ArtCraft/ai` on Windows). Use the host-assigned connection ID as the filename and keep the spec's `base` identical to the connection's `endpoint`. Run the connection's read-only probe, then bind the matching generation tool to a model in the spec.

| Spec | Capability | AAO providers |
| --- | --- | --- |
| [AAO PNG](../integrations/artcraft/aao-png.json) | `image.generate` | ChatGPT, Google Flow, Gemini, Grok Imagine, Muse |
| [AAO MP4](../integrations/artcraft/aao-mp4.json) | `video.generate` | Google Flow, Gemini, Grok Imagine, Muse |
| [AAO MP3](../integrations/artcraft/aao-mp3.json) | `audio.generate` | Google Flow Music, Muse |
| [AAO WAV](../integrations/artcraft/aao-wav.json) | `audio.generate` | Grok |

The bridge shares AAO's existing media queue and drivers. Its `POST /jobs` accepts `provider`, `kind` (`image`, `video`, or `audio`), and `prompt`. The spec takes the UUID from `/id` and polls `GET /jobs/{job_id}`. The bridge responds with `pending`, `succeeded`, or `failed` in `/state`. Completed media is returned inline at `/result/data` because the adapter rejects localhost result URLs. Results above 23 MiB fail to keep encoded responses within the adapter's 32 MiB JSON limit. The bridge scopes jobs and bearer credentials separately from the patched Studio routes.

Version 1 accepts one prompt and extracts one result. The bridge returns the first usable image or video in the provider's response. FFmpeg on `PATH` converts JPEG and WebP to PNG and uses `libx264` or `libopenh264` to convert WebM to MP4, preserving an audio track when present. Reference attachments and dedicated aspect ratio settings are available through the patched Studio routes. The declarative host handles previews, downloads, and library imports. The specs omit cancellation because AAO does not stop in-progress browser work.

The upstream Rust adapter at revision `a822ad5b` verified all four specs against the AAO HTTP bridge. Audio fixtures matched byte for byte. The visual fixtures exercised JPEG to PNG and WebM to MP4, and `ffprobe` confirmed H.264 video and AAC audio. These tests used fixture files and did not submit live browser jobs.

### World Labs Atlas in ArtCraft Studio

Apply [the native Atlas patch](../integrations/artcraft/worldlabs-atlas.patch) to a writable ArtCraft checkout at `a6d52df45e` and rebuild the desktop app. The patch can be used alongside the AAO media patch. Atlas runs directly in Studio through Tauri and the [Atlas Developer API](https://docs.atlas.worldlabs.ai/quickstart). Create a project key in [Atlas Developers](https://atlas.worldlabs.ai/developers) with `assets.create`, `assets.read`, `tasks.create`, and `operations.read`. Run `worldlabs auth login` in your own terminal and enter the key at its hidden prompt. Studio reads the official CLI credential for the public Atlas API. An inherited `WLT_API_KEY` takes precedence. A Marble or Atlas browser login is separate from the project API key.

Open **Image to 3D World**, then select **World Labs Atlas**. Choose 2–32 overlapping JPEG, PNG, or WebP photos of the same scene, each smaller than 10 MiB. Add an optional scene description and select **Generate draft world**. Studio sends the photos to the project, estimates cameras and depth with `images2PosedRGBD`, and runs draft `posedImages2Splats`. Atlas charges the project for API usage. See the [draft reconstruction guide](https://docs.atlas.worldlabs.ai/posed-images-to-splats) and the project's rate card.

Studio saves operation IDs locally so reopening the Atlas panel can resume polling. If a network error interrupts polling, select **Resume Atlas job**. Once the result is ready, select **Save SPZ** and use Studio's 3D asset upload to bring the exported splat into a scene.

## Turn protocol (`aao web ask`)

This protocol applies to the scripted chat targets Claude, ChatGPT, Gemini, and Grok. It does not describe the separate `aao code`, `aao codex`, Flow, Imagine, or ArtCraft workflows.

1. Open or reuse the target's named Playwright session and persistent profile; targets that share a site/account also share a session.
2. `aao web login` opens a headed browser and waits for the human to complete sign-in. AAO never types passwords or 2FA codes. Profiles are kept outside the repository (see [Browser sessions](#browser-sessions)).
3. `aao web ask` starts at the target's new-chat page, or resolves `--chat` and opens that existing conversation. It uses fixed selectors and Enter, with a send-button fallback where configured. Only Gemini supports `--file` and `--repo` attachments.
4. The scripted targets confirm that the user turn was sent, then wait for a new assistant turn with no busy marker and unchanged text across two polls. `--expect` tests the reply text; it is optional.
5. Claude Code and Codex work is started and polled through their own commands. Use `--wait` when the caller should wait for a turn, or `read`/`wait` to inspect an existing session or thread.

## Reference projects (`external/`, read-only)

- **[Grok Web SDK](../external/grok-web-sdk/README.md)** — reference for Imagine selectors, media settings, result detection, and chat reasoning headers.
- **mcp-web-llm** — Playwright adapters (`models/<site>.py`, base class `models/base.py`) for ChatGPT, Claude, Gemini, DeepSeek, Grok, Qwen, over CDP to a dedicated Chrome (`--remote-debugging-port=9222 --user-data-dir=...`).
- **MuseBridge** — Muse web UI exposed as an Anthropic Messages API (`127.0.0.1:8765/v1/messages`) for Claude Code; its headed `login` command auto-discovers composer/send/message selectors into a config file. Muse access is region-restricted.
- **browser-harness** — CDP control of the user's running Chrome with per-domain skills (`agent-workspace/domain-skills/`) and interaction skills (uploads, iframes, shadow DOM, dialogs).
- **orca** — Electron app that runs coding agents in parallel worktrees with a coordinator/worker model; reference for orchestration UX, not a runtime dependency.
