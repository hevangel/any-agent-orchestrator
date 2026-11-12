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
| Claude web | `https://claude.ai/` | ProseMirror `[contenteditable='true'][role='textbox']` | `div[data-is-streaming='false']` on the last reply | Claude in Chrome; fallback selectors in `external/mcp-web-llm/models/claude.py` |
| Remote Claude Code | `https://claude.ai/code` | session prompt box | session status in the page | Claude in Chrome |
| ChatGPT | `https://chatgpt.com/` | `#prompt-textarea` | `[data-testid="send-button"]`; streaming ends when the stop button is gone | `external/mcp-web-llm/models/chatgpt.py` |
| Remote Codex | `https://chatgpt.com/codex` | task prompt box | task list status; results arrive as diffs / PRs | none yet: discover with `snapshot` |
| Z.ai coder | `https://chat.z.ai/` | discover with `snapshot` | discover with `snapshot` | none yet: use MuseBridge's auto-discovery approach |

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
