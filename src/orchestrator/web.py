"""Drive AI chat web apps with playwright-cli, one persistent browser profile per site.

    uv run aao web login grok          # opens a visible browser; you log in; the profile is saved
    uv run aao web login --all
    uv run aao web status              # which saved profiles are still logged in
    uv run aao web ask chatgpt --prompt "..." [--expect REGEX] [--chat <id|url|title>]
    (chat list, pins and groups: web_chats.py, `aao chats`)

Profiles live in AAO_WEB_PROFILE_DIR (default ~/.aao/browser-profiles/<target>), outside the
repository: they hold session cookies. Logins are always done by a human in the visible window;
this module never types credentials.
"""

import json
import os
import re
import tempfile
import time
from pathlib import Path

from orchestrator.config import Settings
from orchestrator.machines import _run

# composer: selector hints in priority order (spec/web_interfaces.md); chat: whether a prompt
# test is meaningful (remote Codex / Claude Code create cloud tasks, so they are only login-checked).
TARGETS = {
    # Studied 2026-11-11: the composer is a contenteditable labelled "Ask ChatGPT"; Enter sends and the
    # URL becomes /c/<id>. Each turn sits in [data-talvt-turn-state] ("complete" once done); the reply
    # is the last [data-markdown-text-style=assistant-message].
    "chatgpt": {"url": "https://chatgpt.com/",
                "composer": ["[contenteditable='true'][aria-label='Ask ChatGPT']", "#prompt-textarea",
                             "div[contenteditable='true']"],
                "user": "[data-user-message-bubble='true']",
                "assistant": "[data-markdown-text-style='assistant-message']",
                "busy": ["[data-talvt-turn-state]:not([data-talvt-turn-state='complete'])",
                         "button[aria-label^='Stop']", "[data-testid='stop-button']"],
                "mode": {"button": "button[aria-label^='Switch mode, current mode: ']",
                         "prefix": "Switch mode, current mode: ", "want": "ChatGPT"},
                "chat": True},
    # Studied 2026-11: Enter sends and the URL becomes /chat/<id>; the reply is the last
    # assistant-message, data-is-streaming="true" (plus a "Stop response" button) while it is written.
    "claude": {"url": "https://claude.ai/new",
               "composer": ["[role='textbox'][aria-label='Write your prompt to Claude']",
                            "div.ProseMirror[contenteditable='true']"],
               "send": "button[aria-label='Send message']",
               "user": "[data-testid='user-message']",
               "assistant": "[data-testid='assistant-message']",
               "busy": ["[data-testid='assistant-message'][data-is-streaming='true']",
                        "button[aria-label='Stop response']"],
               "strip": [".sr-only", "[data-testid='message-actions']"],
               "chat": True},
    # Studied 2026-11-12: composer rich-textarea; Enter sends; URL /app/<16 hex>. Reply: the last
    # model-response's message-content .markdown (aria-busy="true" while it is written). Chat | Task is
    # a segmented control; a Task shows its state in remy-goal-status-footer ("Complete").
    # Attachments: the "Upload and tools" menu creates hidden <input type=file multiple> (documents,
    # code, .zip); "More uploads" > "Import code" takes a GitHub repository or branch URL.
    "gemini": {"url": "https://gemini.google.com/app",
               "composer": ["rich-textarea [contenteditable='true'][role='textbox']", "rich-textarea div[role='textbox']",
                            "div[contenteditable='true'][role='textbox']"],
               "send": "button[aria-label='Send message']",
               "user": "user-query",
               "assistant": "model-response message-content .markdown",
               "busy": ["button[aria-label='Stop response']", "model-response message-content [aria-busy='true']"],
               "strip": ["sources-carousel-inline", "source-inline-chip"],  # file/source references
               "status": "remy-goal-status-footer",
               "segment": {"item": "gem-segmented-button", "selectedClass": "gem-segmented-button-selected"},
               "modes": ["chat", "task"],
               # human pace (ms ranges): Google answers bursts of scripted actions with a CAPTCHA
               "pace": {"pause": [700, 1600], "key": [45, 140], "hold": [60, 160], "pasteOver": 400},
               "attach": {"menu": "button[aria-label='Upload and tools']", "inputAccepts": ".py",
                          "chip": "gem-attachment", "chipBusy": "[role=progressbar], mat-progress-spinner, mat-progress-bar",
                          "moreUploads": "More uploads", "importCode": "Import code", "repoDialog": "mat-dialog-container",
                          "repoInput": "input[aria-label='GitHub repository or branch URL']", "repoImport": "Import", "repoParsed": "Repository:",
                          "repoNotConnected": "Connect to GitHub"},
               "chat": True},
    # Studied 2026-11-12: composer contenteditable "Message Grok"; Enter sends; URL /c/<id> (or
    # /project/<id>?chat=<id> inside a project). Reply: last [data-testid=assistant-message].
    "grok": {"url": "https://grok.com/",
             "composer": ["[contenteditable='true'][aria-label='Message Grok']", "textarea[placeholder*='Ask']",
                          "div[contenteditable='true']"],
             "send": "[data-testid='chat-submit']",
             "user": "[data-testid='user-message']",
             "assistant": "[data-testid='assistant-message']",
             "busy": ["button[aria-label^='Stop']"],
             "chat": True},
    # Google Flow (video/image studio, aao flow / web_flow.py): same Google account, so it shares the
    # gemini profile and browser session. Not a chat: only the login check (home has "New project").
    "flow": {"url": "https://flow.google.com/", "profile": "gemini",
             "composer": ["button:has-text('New project')", "button[aria-label='Add media menu']",
                          "button:has-text('Add ingredients')"],  # home, or a project page left open
             "settle": 12000,  # the home page loads slowly
             "chat": False},
    "muse": {"url": "https://muse.ai/", "composer": ["textarea[placeholder='Message']", "textarea",
                                                     "div[contenteditable='true']"], "chat": True},
    "zai": {"url": "https://chat.z.ai/", "composer": ["textarea", "div[contenteditable='true']"], "chat": True},
    # Codex is a mode of chatgpt.com (web_codex.py, `aao codex`); same account, so it shares the
    # chatgpt profile and browser session. Here only the login check.
    "codex-cloud": {"url": "https://chatgpt.com/", "profile": "chatgpt",
                    "composer": ["[contenteditable='true'][aria-label='Ask ChatGPT']", "[contenteditable='true']"],
                    "chat": False},
    # sessions are driven by web_code.py (`aao code`); here only the login check. Same site and
    # account as "claude", so it shares that profile and browser session.
    "claude-code-web": {"url": "https://claude.ai/code", "profile": "claude",
                        "composer": ["[data-testid='code-prompt-input']",
                                                                       "[role='textbox'][aria-label='Prompt']"], "chat": False},
}
LOGIN_URL = r"login|signin|sign-in|sign_in|/auth|accounts\.google\.com|oauth"

PAGE_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  // passive (login wait): only look at the page; never navigate, click or type while a human logs in
  // A prompt always starts from a fresh load of its page: the reused browser may be on another chat,
  // a project page or another mode of the site, and the message would land there.
  const sameUrl = page.url().split('#')[0] === cfg.url;
  if (!cfg.passive && (cfg.prompt ? !sameUrl || !cfg.reuse : !page.url().startsWith(cfg.url)))
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(cfg.settleMs);
  // sites with several modes on one page (chatgpt.com: ChatGPT | Codex): switch to the target's mode
  if (!cfg.passive && cfg.mode) {
    const btn = page.locator(cfg.mode.button).filter({ visible: true }).first();
    const now = async () => ((await btn.getAttribute('aria-label').catch(() => '')) || '').slice(cfg.mode.prefix.length);
    for (let tries = 0; tries < 3 && await btn.count() && await now() !== cfg.mode.want; tries++) {
      await btn.focus(); await page.keyboard.press('Enter');
      const items = page.locator('[role=menu] [role=menuitem]');
      if (!await items.first().waitFor({ timeout: 4000 }).then(() => true, () => false)) continue;
      const i = (await items.evaluateAll(es => es.map(e => (e.innerText || '').trim().split('\n')[0]))).indexOf(cfg.mode.want);
      if (i >= 0) await items.nth(i).click();
      await page.waitForTimeout(1500);
    }
    if (await btn.count() && await now() !== cfg.mode.want)
      return JSON.stringify({ loggedIn: true, ok: false, url: page.url(), reason: 'could not switch to ' + cfg.mode.want + ' mode' });
  }
  const url = page.url();
  if (new RegExp(cfg.loginUrl, 'i').test(url)) return JSON.stringify({ loggedIn: false, url, reason: 'login page' });
  let composer = null;
  for (const sel of cfg.composer) {
    const loc = page.locator(sel).first();
    if (await loc.isVisible().catch(() => false)) { composer = loc; break; }
  }
  if (/google\.com\/sorry/.test(page.url()))  // Google's "unusual traffic" CAPTCHA: only a human may clear it
    return JSON.stringify({ loggedIn: true, ok: false, blocked: true, url, reason: 'Google is showing its "unusual traffic" check; a human must clear it in the browser' });
  if (!composer) return JSON.stringify({ loggedIn: false, url, reason: 'no chat box (logged out or page changed)' });
  // Several sites let guests chat; a visible "Sign in" / "Log in" control means we are a guest.
  const signIn = /^\s*(sign in|log in|login|sign up|get started)\s*$/i;
  const guest = await page.getByRole('button', { name: signIn }).or(page.getByRole('link', { name: signIn }))
    .first().isVisible().catch(() => false);
  if (guest) return JSON.stringify({ loggedIn: false, guest: true, url, reason: 'guest session (sign-in button visible)' });
  if (!cfg.prompt) return JSON.stringify({ loggedIn: true, url });

  // ---- deterministic turn: no model decides anything here, every step is a fixed selector check
  const started = Date.now();
  const count = sel => sel ? page.locator(sel).count() : Promise.resolve(0);
  const anyVisible = async sels => {
    for (const s of sels || []) if (await page.locator(s).first().isVisible().catch(() => false)) return true;
    return false;
  };
  const lastReply = () => page.evaluate(({ sel, strip }) => {
    const all = document.querySelectorAll(sel);
    if (!all.length) return '';
    const copy = all[all.length - 1].cloneNode(true);
    for (const s of strip || []) copy.querySelectorAll(s).forEach(e => e.remove());
    return (copy.innerText || copy.textContent || '').trim();
  }, { sel: cfg.assistant, strip: cfg.strip });

  const repliesBefore = await count(cfg.assistant);
  const usersBefore = await count(cfg.user);
  const fail = (stage, reason, extra) => JSON.stringify({ loggedIn: true, ok: false, url: page.url(), stage, reason, ...(extra || {}) });
  // human pace for sites that rate-limit scripted bursts (cfg.pace): pauses, held clicks, typed keys
  const rand = ([lo, hi]) => lo + Math.random() * (hi - lo);
  const pace = cfg.pace;
  const pause = () => pace ? page.waitForTimeout(rand(pace.pause)) : Promise.resolve();
  const click = async loc => {
    await pause();
    if (pace) { await loc.hover().catch(() => {}); await page.waitForTimeout(rand([150, 400])); }
    await loc.click(pace ? { delay: rand(pace.hold) } : {});
  };
  const typeText = async text => {
    if (!pace || text.length > pace.pasteOver) { await page.keyboard.insertText(text); return; }
    for (const ch of text) await page.keyboard.type(ch, { delay: rand(pace.key) });
  };
  const escape = async () => { for (let i = 0; i < 3; i++) { await page.keyboard.press('Escape'); await page.waitForTimeout(200); } };

  // 0a. composer mode (gemini: Chat | Task), a segmented control under the prompt box
  if (cfg.segment && cfg.composeMode) {
    const seg = page.locator(cfg.segment.item).filter({ hasText: new RegExp('^\\s*' + cfg.composeMode + '\\s*$', 'i') }).first();
    if (!await seg.count()) return fail('mode', 'no "' + cfg.composeMode + '" mode on this page');
    if (!await seg.evaluate((e, c) => e.classList.contains(c), cfg.segment.selectedClass)) {
      await click(seg.locator('button').first()); await page.waitForTimeout(800);
    }
    if (!await seg.evaluate((e, c) => e.classList.contains(c), cfg.segment.selectedClass)) return fail('mode', 'could not select ' + cfg.composeMode);
  }
  // 0b. attachments: local files through the upload menu's hidden file input (no OS file dialog),
  // GitHub repositories through "Import code"; each must show as a finished chip before sending
  const a = cfg.attach || {};
  const chips = () => a.chip ? page.locator(a.chip).count() : Promise.resolve(0);
  const chipsReady = async want => {
    for (let i = 0; i < 120; i++) {
      const n = await chips();
      const busyChip = a.chipBusy ? await page.locator(a.chipBusy.split(',').map(s => a.chip + ' ' + s.trim()).join(', ')).count() : 0;
      if (n >= want && !busyChip) return true;
      await page.waitForTimeout(500);
    }
    return false;
  };
  if ((cfg.files || []).length || cfg.repo) {
    if (!cfg.attach) return fail('attach', 'attachments are not studied for this site');
  }
  if ((cfg.files || []).length) {
    const before = await chips();
    await click(page.locator(a.menu).filter({ visible: true }).first());
    await page.waitForTimeout(800);
    const inputs = page.locator('input[type=file]');
    const idx = (await inputs.evaluateAll((is, has) => is.map(i => (i.accept || '').includes(has)), a.inputAccepts)).indexOf(true);
    if (idx < 0) { await escape(); return fail('attach', 'no file input for documents and code'); }
    await inputs.nth(idx).setInputFiles(cfg.files);
    await escape();
    if (!await chipsReady(before + cfg.files.length)) return fail('attach', 'the files did not finish uploading', { attached: await chips() - before });
  }
  if (cfg.repo) {
    const before = await chips();
    const pane = () => page.locator('.cdk-overlay-pane').filter({ visible: true });
    await click(page.locator(a.menu).filter({ visible: true }).first()); await page.waitForTimeout(800);
    await click(pane().locator('button').filter({ hasText: a.moreUploads }).first()); await page.waitForTimeout(800);
    await click(pane().locator('button, [role^=menuitem]').filter({ hasText: new RegExp('^\\s*' + a.importCode + '\\s*$') }).first());
    const dlg = page.locator(a.repoDialog).filter({ visible: true }).filter({ has: page.locator(a.repoInput) }).last();
    if (!await dlg.waitFor({ timeout: 8000 }).then(() => true, () => false)) { await escape(); return fail('attach', 'no "' + a.importCode + '" dialog'); }
    if (a.repoNotConnected && await dlg.getByText(a.repoNotConnected).count()) { await escape(); return fail('attach', 'GitHub is not connected in this account (connect it once by hand)'); }
    await click(dlg.locator(a.repoInput)); await typeText(cfg.repo);
    // the dialog confirms the parsed URL ("Repository: owner/name") before Import takes it
    if (a.repoParsed && !await dlg.getByText(a.repoParsed).first().waitFor({ timeout: 15000 }).then(() => true, () => false)) {
      const msg = await dlg.innerText().catch(() => '');
      await escape(); return fail('attach', 'the repository URL was not accepted', { dialog: msg.slice(0, 300) });
    }
    // a press right after the repository line shows is ignored: settle, then press until the dialog closes
    await page.waitForTimeout(1500);
    for (let i = 0; i < 3 && await dlg.isVisible().catch(() => false); i++) {
      await click(dlg.getByRole('button', { name: a.repoImport, exact: true })).catch(() => {});
      for (let k = 0; k < 8 && await dlg.isVisible().catch(() => false); k++) await page.waitForTimeout(500);
    }
    if (!await chipsReady(before + 1)) {
      const msg = await page.locator(a.repoDialog).filter({ visible: true }).innerText().catch(() => '');
      await escape(); return fail('attach', 'the repository was not attached', { dialog: msg.slice(0, 300) });
    }
  }
  await click(composer);
  await typeText(cfg.prompt);
  await pause();
  await page.keyboard.press('Enter');

  // 1. the message must leave the composer: a new user turn or a URL change; else click Send once
  const sent = async () => (cfg.user && await count(cfg.user) > usersBefore) || page.url() !== url;
  let ok = false;
  for (let i = 0; i < 20 && !(ok = await sent()); i++) await page.waitForTimeout(500);
  if (!ok && cfg.send) {
    await page.locator(cfg.send).first().click().catch(() => {});
    for (let i = 0; i < 20 && !(ok = await sent()); i++) await page.waitForTimeout(500);
  }
  if (!ok) return JSON.stringify({ loggedIn: true, ok: false, url: page.url(), stage: 'send', reason: 'message was not sent' });

  // 2. wait for the reply: a new assistant turn, no busy marker, and text unchanged over two polls
  const deadline = started + cfg.timeoutMs;
  let reply = '', previous = null, stage = 'reply';
  while (Date.now() < deadline) {
    await page.waitForTimeout(1000);
    if (!cfg.assistant) {  // site not studied yet: fall back to the whole page text
      reply = await page.locator('body').innerText();
      if (new RegExp(cfg.expect).test(reply)) return JSON.stringify({ loggedIn: true, ok: true, url: page.url(), mode: 'body' });
      continue;
    }
    if (await count(cfg.assistant) <= repliesBefore) { stage = 'waiting for reply'; continue; }
    if (await anyVisible(cfg.busy)) { stage = 'streaming'; previous = null; continue; }
    reply = await lastReply();
    if (reply && reply === previous) {
      const matched = cfg.expect ? new RegExp(cfg.expect).test(reply) : true;
      // a task's status line (gemini Task: "Complete", or a state that needs the human)
      const status = cfg.status ? await page.locator(cfg.status).last().innerText().then(s => s.trim(), () => null) : null;
      return JSON.stringify({ loggedIn: true, ok: matched, url: page.url(), reply, seconds: (Date.now() - started) / 1000,
                              ...(status ? { taskStatus: status } : {}),
                              ...(matched ? {} : { reason: 'reply does not match ' + cfg.expect }) });
    }
    previous = reply; stage = 'settling';
  }
  return JSON.stringify({ loggedIn: true, ok: false, url: page.url(), stage, reason: 'timed out',
                          reply: cfg.assistant ? reply : undefined, tail: cfg.assistant ? undefined : reply.slice(-400) });
}"""


def _settings():
    return Settings()


def login_name(target):
    """Targets on the same site and account share one login: one profile and one browser session
    (Chrome cannot open a profile twice). E.g. claude-code-web uses the claude login."""
    return TARGETS[target].get("profile", target)


def profile_dir(target, settings=None):
    s = settings or _settings()
    base = os.path.expanduser(s.get("AAO_WEB_PROFILE_DIR", "~/.aao/browser-profiles"))
    path = Path(base) / login_name(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session(target):
    return f"-s=aao-web-{login_name(target)}"


def _pw(target, *args, timeout=120):
    return _run(["playwright-cli", _session(target), *args], timeout=timeout)


def open_sessions():
    """Names of the playwright-cli browser sessions that are open (`playwright-cli list`)."""
    ok, out, _ = _run(["playwright-cli", "list"], timeout=30)
    names, current = set(), None
    for line in (out or "").splitlines():
        m = re.match(r"^- (\S+):\s*$", line)
        if m:
            current = m.group(1)
        elif current and re.match(r"^\s+- status: open\s*$", line):
            names.add(current)
    return names


def is_open(target):
    return _session(target)[3:] in open_sessions()


def open_target(target, headed=None, settings=None):
    """Open the target's browser, or reuse it when it is already open (the page is left where it is;
    every script navigates to what it needs)."""
    s = settings or _settings()
    cfg = TARGETS[target]
    if is_open(target):
        return True, "reused", ""
    headed = s.get("AAO_WEB_HEADED", "1") != "0" if headed is None else headed
    args = ["open", "--persistent", "--profile", str(profile_dir(target, s))]
    browser = s.get("AAO_WEB_BROWSER")  # e.g. chrome: Google sign-in rejects automation-flagged Chromium
    if browser:
        args += ["--browser", browser]
    if headed:
        args.append("--headed")
    return _pw(target, *args, cfg["url"], timeout=120)


def close_target(target):
    return _pw(target, "close", timeout=60)


def release(target, settings=None):
    """End of one command: keep the browser open for the next one (default), or close it when
    AAO_WEB_KEEP_OPEN=0. `aao web close` closes it explicitly."""
    s = settings or _settings()
    if s.get("AAO_WEB_KEEP_OPEN", "1") == "0":
        close_target(target)


def _run_page(target, prompt=None, expect=None, timeout_s=120, settle_ms=4000, passive=False, url=None,
              files=None, repo=None, mode=None):
    """One page check or prompt turn. `url` opens that page (e.g. an existing chat) instead of a new chat.
    `files` (local paths) and `repo` (GitHub URL) are attached first; `mode` picks the composer mode."""
    cfg = TARGETS[target]
    if passive and prompt:
        raise ValueError("a passive page check cannot send a prompt")
    if (files or repo) and not cfg.get("attach"):
        raise ValueError(f"attachments are not studied for {target}")
    if not mode and prompt and cfg.get("modes"):
        mode = cfg["modes"][0]  # the page keeps the last mode used (gemini: a Task), so set the default
    if mode and mode.lower() not in cfg.get("modes", []):
        raise ValueError(f"{target} has no {mode!r} mode (have: {', '.join(cfg.get('modes', [])) or 'none'})")
    settle_ms = max(settle_ms, cfg.get("settle", 0)) if not passive else settle_ms
    paths = []
    for f in files or []:
        path = Path(f).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {f}")
        paths.append(path.as_posix())
    payload = {"url": url or cfg["url"], "composer": cfg["composer"], "loginUrl": LOGIN_URL, "prompt": prompt,
               "expect": expect, "timeoutMs": timeout_s * 1000, "settleMs": settle_ms, "passive": passive,
               "reuse": bool(url),  # an existing chat already open may be used as it is
               "files": paths, "repo": repo, "composeMode": mode.capitalize() if mode else None,
               **{k: cfg.get(k) for k in ("send", "user", "assistant", "busy", "strip", "mode", "status", "segment", "attach", "pace")}}
    return run_script(target, PAGE_SCRIPT.replace("__CFG__", json.dumps(payload)), timeout_s + 60)


def run_script(target, script, timeout_s=180):
    """Run a page function (`async page => JSON string`) in the target's open session; return its dict."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        ok, out, err = _pw(target, "run-code", "--filename", path, "--raw", timeout=timeout_s)
    finally:
        os.unlink(path)
    if not ok:
        return {"loggedIn": None, "error": (err or out)[-600:]}
    # `--raw` prints the function's return value as JSON, i.e. our JSON string quoted once more.
    try:
        data = json.loads(out.strip())
        while isinstance(data, str):
            data = json.loads(data)
        return data if isinstance(data, dict) else {"loggedIn": None, "error": out[-300:]}
    except ValueError:
        match = re.search(r"\{.*\}", out, re.S)
        try:
            return json.loads(match.group(0)) if match else {"loggedIn": None, "error": out[-300:]}
        except ValueError:
            return {"loggedIn": None, "error": out[-300:]}


def screenshot(target, path):
    """Save the current page as PNG evidence (best effort)."""
    _pw(target, "screenshot", f"--filename={path}", timeout=60)


def status(target, settings=None, screenshot_path=None):
    ok, _, err = open_target(target, settings=settings)
    if not ok:
        return {"target": target, "loggedIn": None, "error": err}
    try:
        return {"target": target, **_run_page(target)}
    finally:
        if screenshot_path:
            screenshot(target, screenshot_path)
        release(target)


def login(target, wait_minutes=15, settings=None):
    """Open a visible browser on the target and wait until a human has logged in."""
    ok, _, err = open_target(target, headed=True, settings=settings)
    if not ok:
        return {"target": target, "loggedIn": False, "error": err}
    print(f"[{target}] log in in the browser window (profile {profile_dir(target, settings)}); waiting up to {wait_minutes} min ...")
    deadline = time.time() + wait_minutes * 60
    try:
        while time.time() < deadline:
            # passive: a page reload or redirect here would wipe what the human is typing
            state = _run_page(target, settle_ms=500, passive=True)
            if state.get("loggedIn"):
                print(f"[{target}] logged in; profile saved")
                return {"target": target, **state}
            time.sleep(5)
        return {"target": target, "loggedIn": False, "error": "timed out waiting for login"}
    finally:
        release(target)


def ask(target, prompt, expect, timeout_s=120, settings=None, screenshot_path=None, chat=None,
        files=None, repo=None, mode=None):
    """Send one prompt; `chat` (id, URL or exact title) continues that chat instead of starting a new one.
    `files` / `repo` attach local files / a GitHub repository first (gemini); `mode` is chat or task (gemini)."""
    ok, _, err = open_target(target, settings=settings)
    if not ok:
        return {"target": target, "ok": False, "error": err}
    try:
        url = None
        if chat:
            from orchestrator import web_chats
            found = web_chats.resolve(target, chat)
            if not found.get("url"):
                return {"target": target, "ok": False, **found}
            url = found["url"]
        return {"target": target, **_run_page(target, prompt, expect, timeout_s, url=url, files=files, repo=repo, mode=mode)}
    finally:
        if screenshot_path:
            screenshot(target, screenshot_path)
        release(target)
