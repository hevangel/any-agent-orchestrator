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
| Grok | `https://grok.com/` | `[contenteditable='true'][aria-label='Message Grok']` | **Studied and scripted (chat and projects; Grok Bot not covered), see below** | `web.py` `TARGETS["grok"]`, `web_chats_grok.py` |
| Muse (Meta) | `https://muse.ai` (chat URL discovered at login) | `textarea[placeholder='Message']` | Send button with `aria-label*=Send`; generating while a visible `Stop` button or `animate-pulse` in `.hatch-chat-groupable-bubble` | `external/MuseBridge/muse_bridge.py` (`detect_selectors`, `_is_generating`, `_wait_for_reply`) |
| Gemini | `https://gemini.google.com/app` | `rich-textarea [contenteditable][role=textbox]` | **Studied and scripted (chat, task, attachments, notebooks), see below**; signing in by hand in the playwright-cli Chrome worked | `web.py` `TARGETS["gemini"]`, `web_chats_gemini.py` |
| Claude web | `https://claude.ai/new` | `[role='textbox'][aria-label='Write your prompt to Claude']` (ProseMirror) | **Studied and scripted (playwright-cli), see below** | `web.py` `TARGETS["claude"]`; Claude in Chrome remains an alternative |
| Remote Claude Code | `https://claude.ai/code` | `[data-testid='code-prompt-input']` | **Studied and scripted, see "Claude Code on the web"** | `web_code.py`; shares the `claude` login |
| ChatGPT | `https://chatgpt.com/` | `[contenteditable='true'][aria-label='Ask ChatGPT']` (`#prompt-textarea` is gone) | **Studied and scripted (playwright-cli), see below** | `web.py` `TARGETS["chatgpt"]`, `web_chats_chatgpt.py` |
| Remote Codex | `https://chatgpt.com/` in **Codex mode** (threads `/local/<id>`) | contenteditable "Do anything" | **Studied (reading and questions), see "Codex on chatgpt.com"** | `web_codex.py`; shares the `chatgpt` login |
| Google Flow | `https://flow.google.com/` | project page: agent box "What do you want to create?" | **Studied and scripted (projects, media, downloads, agent options; generation not driven), see "Google Flow"** | `web_flow.py`; shares the `gemini` login |
| Z.ai coder | `https://chat.z.ai/` | discover with `snapshot` | discover with `snapshot` | none yet: use MuseBridge's auto-discovery approach |

## Scripted recipes (`src/orchestrator/web.py`)

Each studied target has a recipe in `TARGETS`; `PAGE_SCRIPT` runs it with playwright-cli `run-code`, with no LLM in the loop. Keys: `composer` (selectors, first visible wins), `send` (button clicked only if Enter did not send), `user` (user turns, proves the send), `assistant` (assistant turns; the last one is the reply), `busy` (any visible means still writing), `strip` (removed from the reply before reading its text). A target without `assistant` still uses the old whole-page text check.

Turn: open the target URL (always a new chat) → count assistant turns → click composer, insert text, Enter → wait up to 10 s for a new user turn or a URL change, else click `send` once → wait for a new assistant turn with no `busy` marker whose text is identical across two 1 s polls → return `reply`, `url`, `seconds`; `ok` is the `expect` regex tested on the reply only.

| Target | Studied | Signals |
| --- | --- | --- |
| gemini | 2026-11-12 | Enter sends; URL `/app/<16 hex>`; user `user-query`; reply the last `model-response message-content .markdown` (`aria-busy="true"` and a `Stop response` button while writing; strip `sources-carousel-inline`, the file/source references). `--mode chat|task`: the `gem-segmented-button` row under the prompt box (selected one has `gem-segmented-button-selected`); a Task reports its state in `remy-goal-status-footer` ("Complete"), returned as `taskStatus`. `--file` (repeatable): open "Upload and tools", then `setInputFiles` on the hidden `input[type=file]` whose accept list has `.py` (documents, code and `.zip`; the menu creates the inputs; never the OS file dialog, which playwright-cli intercepts); wait for one `gem-attachment` chip per file. `--repo <GitHub URL>`: "More uploads" > "Import code", fill "GitHub repository or branch URL", wait for "Repository: owner/name", then press Import (a press right after that line is ignored, so it is pressed until the dialog closes); needs GitHub connected in the account. There is no scriptable folder upload ("Upload folder" uses the browser's folder picker): zip the folder and pass it with `--file`. |
| grok | 2026-11-12 | Enter sends (`[data-testid='chat-submit']` otherwise); URL `/c/<id>`, or `/project/<id>?chat=<id>` inside a project; user `[data-testid='user-message']` (starts with a "You" heading); reply the last `[data-testid='assistant-message']`; a `Stop` button while writing |
| chatgpt | 2026-11-11 | Enter sends; URL `/` → `/c/<id>` (`/g/<project>/c/<id>` in a project); user `[data-user-message-bubble='true']`; reply the last `[data-markdown-text-style='assistant-message']`; each turn sits in `[data-talvt-turn-state]`, `complete` once done (any other value, or a `Stop` button, means still writing). The old `data-message-author-role` markup is gone. |
| claude | 2026-11-12 | Enter sends; URL `/new` → `/chat/<id>`; user `[data-testid='user-message']`; reply `[data-testid='assistant-message']`, `data-is-streaming='true'` and a `button[aria-label='Stop response']` while writing; strip `.sr-only` (a hidden "Claude responded: …" heading) and `[data-testid='message-actions']` |

`aao web ask <t> --chat <id|url|title>` continues an existing chat instead of opening a new one (a title is resolved through the chat list below).

A new prompt always reloads the target's new-chat URL first: the shared browser may be left on another chat, a project page or another mode, and the prompt would land there (seen on Grok, where a reused project page put the new chat into the project). Targets with several modes on one page declare `mode` (chatgpt: switch to ChatGPT, since Codex mode shares the page).

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

`flow.google.com` (AI studio for video and images), studied 2026-11-13. Same Google account as Gemini, so the `flow` target uses the `gemini` profile and browser session (`aao web login gemini`; the site was `labs.google/fx/tools/flow` and now redirects). It is not a chat: a project is a grid of media plus an **agent** panel ("What do you want to create?") that asks before it generates. This driver covers everything that costs nothing; generation is deliberately left out until a credit policy is chosen.

```sh
uv run aao flow status                                  # is the Google login still good
uv run aao flow projects                                # id, title (default title is the creation date)
uv run aao flow new [--title "..."]                     # empty project, free
uv run aao flow read <project> [--type video|image]     # media, newest first: index, type, name, thumbnail
uv run aao flow download <project> <media> [--to DIR] [--quality original|gif|1080p|4k]
uv run aao flow options [--project P]                   # live agent settings and every choice; never saves
uv run aao flow upload <project> <file>...                # reference images/videos into the project library (free)
uv run aao flow delete <project> --yes                  # PERMANENT: clips, ingredients and prompts are gone
```

`<project>` is an id, a project URL or an exact title (one old project has the id `..._backfill`, so ids are not always uuids). `<media>` is the 1-based index from `read` (the tiles carry no id in the DOM) or the exact media name. Every command accepts `--json` before the sub-command (`aao flow --json read ...`).

**Credits.** Free: read, create, rename, upload, original-size download, GIF download, delete. Spends credits: generating (agent prompt) and the upscaled downloads. `download` is `--quality original` unless told otherwise; `1080p` is "Upscaled, 5 credits" and `4k` is "Upscaled" (also credits). A media's download menu is `More options > Download > 270p Animated GIF | 720p Original size | 1080p Upscaled | 4K Upscaled`; images have no GIF.

### Options the agent can choose from

Read from the live page; `aao flow options` reads them again, so trust it over this list when they differ.

| Setting | Choices |
| --- | --- |
| Confirm before generating | `Always` (default: the agent asks before it generates) / `Never` (generates and spends credits at once) |
| Image aspect ratio | `16:9` (default), `4:3`, `1:1`, `3:4`, `9:16` |
| Image count per prompt | `x1`, `x2` (default), `x3`, `x4` |
| Image model | `Nano Banana Pro`, `Nano Banana 2 Lite`, `Nano Banana 2.1` (default) |
| Video aspect ratio | `16:9` (default), `9:16` |
| Video count per prompt | `x1` (default), `x2`, `x3`, `x4` |
| Video model | `Omni 1.2 Flash` (default), `Omni 1.1 Flash` (leaving 5 Dec), `Veo 3.1 - Lite`, `Veo 3.1 - Fast` (leaving 5 Dec), `Veo 3.1 - Quality` (leaving 5 Dec) |

These are the agent's per-project defaults in the panel's **Settings**, applied with **Save**; `options` only opens the panel and closes it without saving. The media grid's filter menu offers: type (Images, Videos, Collections, Scenes, Characters), aspect ratio (Landscape, Portrait, Freeform), created (Generated, Uploaded, Favourites), duration (3s..15s), resolution (720p, 360p) and sort (Newest, Oldest). A media's **More options** menu: Favourite, Reuse prompt, Add to scene, Add to prompt, Download, Copy, Rename, Share, Publish to YouTube, Set project cover, Flag output, Move to bin. Sharing, publishing and the bin are not driven.

### Page notes

- Home: project cards `a[href^='/project/<id>']` with "Edit project title" and "Delete project" buttons; "New project" creates one and the URL becomes `/project/<id>`. Delete asks "Are you sure ... permanently deleted" with a `Delete project` button.
- Project page: media in `flow-grid-tile-container > flow-<type>-tile`; the title is an always-present `textbox "Editable text"` (an h1 only while not edited); a populated project shows the agent panel on the right, an empty one shows the box at the bottom. Clicking a tile opens a viewer without changing the URL.
- Upload: top bar `Add media menu` (+) > `Upload` opens the OS file chooser. playwright-cli reports it as a modal state (every other command fails with "does not handle the modal state" until it is filled) and `playwright-cli upload <file>` fills it, one file per call, so `aao flow upload` runs open / upload / wait-for-the-new-tile once per file. An uploaded image becomes an `image` tile named after the file, listed newest first; the agent can use it as a reference ("ingredient"). The same menu offers New collection, Create character and New scene (not driven).
- A cookie banner ("flow.google.com uses cookies") overlaps the bottom; the driver presses `No thanks`.
- `run-code` has no `URL` global (parse `page.url()` by hand) and the home page loads slowly: the `flow` target settles 12 s before the login check.
- Tests: `tests/e2e/test_web_flow.py` is read-only plus one free download; project create, upload and delete were checked by hand on throwaway projects.

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
