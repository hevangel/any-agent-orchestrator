"""Drive Claude Code on the web (claude.ai/code) with playwright-cli: read, prompt and start sessions. No LLM.

    uv run aao code sessions                         # sidebar: sessions with status and where they run
    uv run aao code read <session>                   # status, environment, repo, branch, PRs, last reply
    uv run aao code send <session> --prompt "..." [--wait]
    uv run aao code new --prompt "..." [--repo owner/name] [--env NAME] [--permission Auto|"Accept edits"|Plan] [--wait]
    uv run aao code wait <session> [--timeout 1800]

<session> is a session id (session_...), its URL or its exact title in the sidebar. Pin and group
sessions with `aao chats claude-code-web ...` (same sidebar menus as chats). A prompt starts real work
on the account (cloud container or a connected machine), so tests use a throwaway prompt.
"""

import json

from orchestrator import web, web_chats

TARGET = "claude-code-web"

# claude.ai/code, studied 2026-11-12.
RECIPE = {
    "origin": "https://claude.ai",
    "home": "https://claude.ai/code",
    "sessionUrl": "https://claude.ai/code/{id}",
    "sessionPath": r"^/code/(session_[A-Za-z0-9]+)",
    "composer": "[data-testid='code-prompt-input']",
    "user": "[data-testid='user-message']",
    "assistant": "[data-testid='assistant-message']",
    "strip": [".sr-only", "[data-testid='message-actions']"],
    # data-turn-working="true" while Claude works on a turn
    "working": "[data-testid='code-working-line']",
    "workingAttr": "data-turn-working",
    "row": "aside [data-kind=code] [data-row-key]",
    "railGrip": "[data-rail-grip]",          # one per rail section, aria-label "Reorder <Section>"
    "railName": "[data-cds=OverflowTooltip]",
    "prNav": "nav[aria-label='Repository and pull request controls']",
    "title": "main button[aria-label$=', rename session']",
    "limitBanner": r"^(Weekly|Session|Daily|5-hour|Usage) limit reached$",
    # Projects (beta), studied 2026-11-12 on a project made before the limit; see spec/web_interfaces.md
    "projectsUrl": "https://claude.ai/code/projects/browse",
    "projectUrl": "https://claude.ai/code/project/{id}",
    "projectLink": "main a[href^='/code/project/chan_']",
    "projectId": r"chan_[A-Za-z0-9]+",
    "row_": "[data-testid='transcript-row']",
    "youSaid": "button[aria-label^='Show message actions for You said']",
    "threadCard": "[data-testid='channel-created-thread'] button[aria-label]",
    # inline thread links in messages: button[data-line-thread=<state>] whose text is "Title — Resolved".
    # Never click these or the cards: thread links hand off to the Claude desktop app ("Open Claude?").
    "threadChip": "button[data-line-thread]",
    "homeSection": "[data-home-section]",
    "envButton": "^Environment:",
    "repoCombo": "Add repository",
    "repoSearch": "input[aria-label='Search repositories…']",
    "permissionButton": "^Permissions:",
    "dismiss": [{"text": "Enable notifications", "button": "Not now"},
                # toast after a link that would open the desktop app: stay in the browser
                {"text": "open in the Claude desktop app", "button": "Keep in this browser"}],
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const glyphless = s => (s || '').replace(/[-]/g, '').replace(/[ \t]+/g, ' ').trim();
  const out = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });
  const dismiss = async () => {
    // dialogs and toasts: when the known text is on screen, click its fixed button (exact name)
    for (const d of r.dismiss) {
      if (!await page.getByText(d.text).first().isVisible().catch(() => false)) continue;
      await page.getByRole('button', { name: d.button, exact: true }).first().click({ timeout: 3000 }).catch(() => {}); await wait(400);
    }
  };
  const goto = async url => {
    if (page.url().split('?')[0] !== url) await page.goto(url, { waitUntil: 'domcontentloaded' });
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) throw Object.assign(new Error('login page'), { login: true });
    await page.locator(r.composer).first().waitFor({ state: 'attached', timeout: 30000 });
    await wait(1200); await dismiss();
  };
  // run-code's sandbox has no URL global: take the path by hand
  const sessionId = () => { const m = page.url().slice(r.origin.length).split(/[?#]/)[0].match(new RegExp(r.sessionPath)); return m ? m[1] : null; };
  const working = async () => (await page.locator(r.working).first().getAttribute(r.workingAttr).catch(() => null)) === 'true';
  const count = sel => page.locator(sel).count();
  // text of the last match without `strip` parts; the copy is laid out off-screen because a detached
  // node's innerText has no line breaks
  const lastText = sel => page.evaluate(({ sel, strip }) => {
    const all = document.querySelectorAll(sel); if (!all.length) return '';
    const copy = all[all.length - 1].cloneNode(true);
    for (const s of strip) copy.querySelectorAll(s).forEach(e => e.remove());
    const holder = document.createElement('div');
    // off-screen, not visibility:hidden (innerText skips hidden text)
    holder.style.cssText = 'position:fixed;left:-100000px;top:0;width:900px;pointer-events:none';
    holder.append(copy); document.body.append(holder);
    const text = copy.innerText; holder.remove();
    return (text || '').replace(/[-]/g, '').replace(/\n{3,}/g, '\n\n').trim();
  }, { sel, strip: r.strip });

  // a usage-limit banner above the composer ("Weekly limit reached ... Resets Sat, Nov 14, 8:00 PM")
  // the composer's usage meter ("31% of 5-hour limit, Resets Thu 1:10 AM") is the live number
  const usageMeter = async () => ((await page.locator('main button[aria-label^="Usage:"]').first().getAttribute('aria-label', { timeout: 2000 }).catch(() => null)) || '').slice(7) || null;
  const usageLimit = () => page.evaluate(re => {
    const hit = [...document.querySelectorAll('main *')].find(e => e.childElementCount === 0 && new RegExp(re, 'i').test(e.textContent.trim()));
    if (!hit) return null;
    let p = hit; for (let i = 0; i < 3 && p.parentElement; i++) p = p.parentElement;
    const resets = (p.innerText.match(/Resets [^\n·]+/) || [''])[0].trim();
    return hit.textContent.trim() + (resets ? ' · ' + resets : '');
  }, r.limitBanner);

  // session page -> facts from the header, the transcript and the right rail
  const info = async () => {
    const rail = await page.evaluate(r => {
      const leafTexts = el => [...el.querySelectorAll('*')].filter(e => e.childElementCount === 0 && !e.closest('[aria-hidden=true],[hidden]'))
        .map(e => e.textContent.replace(/[-]/g, '').trim()).filter(Boolean);
      const section = name => {
        // the section is the largest ancestor of its grip that holds no other section's grip
        const g = [...document.querySelectorAll(r.railGrip)].find(x => x.getAttribute('aria-label') === 'Reorder ' + name);
        let p = g; while (p && p.parentElement && p.parentElement.querySelectorAll(r.railGrip).length === 1) p = p.parentElement;
        return p;
      };
      const env = section('Environment');
      const res = {};
      if (env) {
        const nameEl = env.querySelector(r.railName);
        res.environment = nameEl ? nameEl.textContent.trim() : null;
        const texts = leafTexts(env);
        const i = res.environment ? texts.indexOf(res.environment) : -1;
        res.connection = i >= 0 && texts[i + 1] && !/\//.test(texts[i + 1]) ? texts[i + 1] : null;
        const branchBtn = env.querySelector('button[aria-haspopup=menu][aria-label]:not([aria-label^="More options"])');
        res.branch = branchBtn ? branchBtn.getAttribute('aria-label') : null;
        // owner/name texts that are not the branch (a branch like claude/x looks the same)
        res.repos = [...new Set([...env.querySelectorAll('span')].filter(s => s.childElementCount === 0 && !s.closest('[aria-hidden=true],[hidden]'))
          .map(s => s.textContent.trim()).filter(t => /^[\w.-]+\/[\w.-]+$/.test(t) && t !== res.branch && !(res.branch || '').startsWith(t)))];
      }
      const nav = document.querySelector(r.prNav);
      const links = [...new Set([...document.querySelectorAll('main a[href*="/pull/"]')].map(a => a.href))];
      res.pullRequests = links.map(url => ({ url, state: nav ? (nav.querySelector('[role=img][aria-label]') || {}).ariaLabel || null : null }));
      return res;
    }, r);
    const titleBtn = page.locator(r.title).first();
    const title = await titleBtn.count() ? glyphless(await titleBtn.innerText()) : null;
    // where it runs: only the sidebar row says ("Cloud", "sidonia · Connected", "Remote Control · Offline")
    const row = await page.evaluate(({ r, id }) => {
      const el = document.querySelector(`${r.row}[data-row-key="code:${id}"]`);
      if (!el) return {};
      const t = s => (s || '').replace(/[-]/g, '').replace(/\s+/g, ' ').trim();
      const status = t((el.querySelector('[role=status]') || {}).textContent);
      const detail = [...el.querySelectorAll('[data-find-omitted]')].map(e => t(e.textContent)).filter(x => x && x !== status).join(' · ');
      const env = el.querySelector('[data-env-glyph]');
      return { rowStatus: status || null, detail: detail || null, env: env ? env.getAttribute('data-env-glyph') : null };
    }, { r, id: sessionId() });
    const limit = await usageLimit(), meter = await usageMeter();
    return { ...(meter ? { usage: meter } : {}), id: sessionId(), url: page.url().split('?')[0], title, working: await working(), ...row, ...rail,
             ...(limit ? { usageLimit: limit } : {}),
             turns: await count(r.user), lastPrompt: await lastText(r.user), reply: await lastText(r.assistant) };
  };

  // wait for the current turn: a new assistant message (or working seen), working false, reply stable
  const waitTurn = async (repliesBefore, timeoutMs) => {
    const deadline = Date.now() + timeoutMs;
    let seenWork = false, prev = null;
    while (Date.now() < deadline) {
      await wait(2000);
      if (await working()) { seenWork = true; prev = null; continue; }
      if (!seenWork && await count(r.assistant) <= repliesBefore) continue;
      const reply = await lastText(r.assistant);
      if (reply && reply === prev) return { finished: true };
      prev = reply;
    }
    return { finished: false };
  };

  const sendPrompt = async (text, userSel = r.user) => {
    const usersBefore = await count(userSel), repliesBefore = await count(r.assistant), url0 = page.url();
    const box = page.locator(r.composer).first();
    await box.click();
    await page.keyboard.insertText(text);
    await page.keyboard.press('Enter');
    for (let i = 0; i < 40; i++) {  // sent: a new user turn, a new session URL, or work started
      await wait(500);
      if (await count(userSel) > usersBefore || page.url() !== url0 || await working()) return { sent: true, repliesBefore };
    }
    return { sent: false, repliesBefore };
  };

  const pickRadio = async (label) => {
    const loc = page.locator('[role=menu] [role=menuitemradio]');
    const texts = await loc.evaluateAll(es => es.map(e => (e.innerText || '').replace(/[-]/g, '').split(/\n| ,/)[0].trim()));
    const i = texts.findIndex(t => t.toLowerCase() === label.toLowerCase());
    return { item: i < 0 ? null : loc.nth(i), choices: texts };
  };
  // Escape only reaches a menu that has focus (a menu opened by a click event does not take it)
  const closeMenus = async () => {
    for (let i = 0; i < 4 && await page.locator('[role=menu]').count(); i++) {
      await page.locator('[role=menu]').last().focus().catch(() => {}); await page.keyboard.press('Escape'); await wait(200);
    }
  };

  // ---- Projects: one conversation with a coordinator Claude that starts threads (cloud sessions)
  const projectsList = async () => {
    await page.goto(r.projectsUrl, { waitUntil: 'domcontentloaded' });
    await page.locator(r.projectLink).first().waitFor({ timeout: 20000 }).catch(() => {});
    await wait(800);
    return page.locator(r.projectLink).evaluateAll((as, re) => as.map(a => {
      const lines = a.innerText.replace(/[-]/g, '').split('\n').map(s => s.trim()).filter(Boolean);
      const id = (a.getAttribute('href').match(new RegExp(re)) || [''])[0];
      return { id, name: lines[0] || '', detail: lines.slice(1).join(' · '), url: 'https://claude.ai/code/project/' + id };
    }), r.projectId);
  };
  const projectUrl = async ref => {
    const m = String(ref).match(new RegExp(r.projectId));
    if (m) return r.projectUrl.replace('{id}', m[0]);
    const hits = (await projectsList()).filter(p => p.name.toLowerCase() === String(ref).trim().toLowerCase());
    if (hits.length !== 1) throw new Error(hits.length ? 'several projects are named "' + ref + '"' : 'no project named "' + ref + '"');
    return hits[0].url;
  };
  const projectInfo = async () => {
    const data = await page.evaluate(r => {
      const t = s => (s || '').replace(/[-]/g, '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
      const text = el => {  // laid out off-screen so innerText keeps line breaks
        const copy = el.cloneNode(true);
        copy.querySelectorAll('.sr-only,[data-testid="message-actions"],[data-testid="row-hover-time"]').forEach(e => e.remove());
        copy.querySelectorAll(r.threadChip).forEach(b => b.replaceWith('[thread: ' + t(b.textContent).split(/\s+—\s+/)[0] + ']'));
        const h = document.createElement('div'); h.style.cssText = 'position:fixed;left:-100000px;top:0;width:900px';
        h.append(copy); document.body.append(h); const s = copy.innerText; h.remove(); return t(s);
      };
      // "Title — View thread — 4 replies — New messages — Resolved": title first, then flags; a status is the last flag
      const threads = new Map();
      // cards (aria-label "Title — View thread — … — Resolved") and inline links (text "Title — Resolved")
      for (const b of document.querySelectorAll(r.threadCard + ',' + r.threadChip)) {
        const label = b.matches(r.threadChip) ? t(b.textContent) : b.getAttribute('aria-label');
        const parts = label.split(/\s+—\s+/).map(s => s.trim()).filter(Boolean);
        const flags = parts.slice(1).filter(p => !/^(Open|View) thread$/.test(p));
        const status = flags.filter(f => !/replies?$|^New messages$/.test(f)).pop() || 'Active';
        threads.set(parts[0], { title: parts[0], status, flags });  // a later card for the same thread wins
      }
      const groups = [...document.querySelectorAll(r.homeSection)].map(s => {
        const m = t((s.querySelector('h4') || {}).textContent).match(/^(.*?)\s*·\s*(\d+)$/);
        return m ? { name: m[1], count: +m[2], key: s.getAttribute('data-home-section') } : null;
      }).filter(Boolean);
      const messages = [...document.querySelectorAll(r.row_)].map(row => {
        const a = row.querySelector('[data-testid="assistant-message"]');
        if (a) return { from: 'claude', text: text(a) };
        if (row.querySelector(r.youSaid)) return { from: 'you', text: text(row).replace(/^You said:\s*/, '')
          .replace(/\s*Sent to a thread\s*$/, ' (sent to a thread)') };
        return null;
      }).filter(Boolean);
      const name = document.querySelector('main button[aria-label$=", rename project"]');
      const model = document.querySelector('main button[aria-label^="Model:"]');
      return { name: name ? t(name.textContent) : null, model: model ? model.getAttribute('aria-label').slice(7) : null,
               threads: [...threads.values()], groups, messages };
    }, r);
    const limit = await usageLimit(), meter = await usageMeter();
    return { ...(meter ? { usage: meter } : {}), url: page.url().split('?')[0], ...data, messages: data.messages.slice(-cfg.last), ...(limit ? { usageLimit: limit } : {}) };
  };
  const gotoProject = async url => {
    if (page.url().split('?')[0] !== url) await page.goto(url, { waitUntil: 'domcontentloaded' });
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) throw Object.assign(new Error('login page'), { login: true });
    await page.locator(r.composer).first().waitFor({ state: 'attached', timeout: 30000 });
    await page.locator('[data-testid="assistant-message"]').first().waitFor({ timeout: 20000 }).catch(() => {});
    await wait(1500); await dismiss();
  };

  try {
    switch (cfg.op) {
      case 'projects':
        return out({ ok: true, projects: await projectsList() });
      case 'project-read': {
        await gotoProject(await projectUrl(cfg.project));
        return out({ ok: true, ...(await projectInfo()) });
      }
      case 'project-send': {
        // untested end to end (weekly limit reached while studying): message box + Enter, then the
        // coordinator's reply is the new assistant message once it stops changing
        await gotoProject(await projectUrl(cfg.project));
        const before = await count('[data-testid="assistant-message"]');
        // a project's own messages carry no user-message test id: a row with "You said" actions is one
        const s = await sendPrompt(cfg.prompt, `${r.row_}:has(${r.youSaid})`);
        if (!s.sent) return fail('message was not sent', await projectInfo());
        if (!cfg.wait) return out({ ok: true, sent: true, ...(await projectInfo()) });
        const deadline = Date.now() + cfg.timeoutMs;
        let prev = null;
        while (Date.now() < deadline) {
          await wait(3000);
          if (await count('[data-testid="assistant-message"]') <= before) continue;
          const reply = await lastText('[data-testid="assistant-message"]');
          if (reply && reply === prev) return out({ ok: true, sent: true, ...(await projectInfo()) });
          prev = reply;
        }
        return out({ ok: false, sent: true, reason: 'no settled reply yet', ...(await projectInfo()) });
      }
      case 'read': {
        await goto(cfg.url);
        await page.locator(r.assistant + ',' + r.user).first().waitFor({ timeout: 30000 }).catch(() => {});
        await page.locator(r.railGrip).first().waitFor({ timeout: 10000 }).catch(() => {});  // rail loads later
        await wait(1500);
        return out({ ok: true, ...(await info()) });
      }
      case 'wait': {
        await goto(cfg.url);
        const w = await waitTurn(await count(r.assistant) - 1, cfg.timeoutMs);
        return out({ ok: w.finished, ...(w.finished ? {} : { reason: 'still working' }), ...(await info()) });
      }
      case 'send': {
        await goto(cfg.url);
        if (await working()) return fail('the session is still working on a turn; wait for it first', await info());
        // the "limit reached" banner can be left over from an earlier turn: report it, do not trust it
        const s = await sendPrompt(cfg.prompt);
        if (!s.sent) return fail('prompt was not sent', await info());
        if (!cfg.wait) return out({ ok: true, sent: true, ...(await info()) });
        const w = await waitTurn(s.repliesBefore, cfg.timeoutMs);
        return out({ ok: w.finished, sent: true, ...(w.finished ? {} : { reason: 'timed out; the session keeps working' }), ...(await info()) });
      }
      case 'new': {
        await goto(r.home);
        const chosen = {};
        if (cfg.env) {
          await page.getByRole('button', { name: new RegExp(r.envButton) }).first().click(); await wait(800);
          const { item, choices } = await pickRadio(cfg.env);
          if (!item) { await closeMenus(); return fail('no environment named "' + cfg.env + '"', { choices }); }
          await item.click(); await wait(600); chosen.environment = cfg.env;
        }
        if (cfg.repo) {
          await page.getByRole('combobox', { name: r.repoCombo }).first().click(); await wait(800);
          await page.locator(r.repoSearch).fill(cfg.repo); await wait(1500);
          const opts = page.locator('[role=listbox] [role=option]');
          const texts = await opts.evaluateAll(es => es.map(e => (e.innerText || '').replace(/[-]/g, '').split(/\n| ,/)[0].trim()));
          const i = texts.findIndex(t => t.toLowerCase() === cfg.repo.toLowerCase());
          if (i < 0) { await closeMenus(); return fail('no repository "' + cfg.repo + '"', { choices: texts.slice(0, 20) }); }
          await opts.nth(i).click(); await wait(800); chosen.repo = cfg.repo;
        }
        if (cfg.permission) {
          await page.getByRole('button', { name: new RegExp(r.permissionButton) }).first().click(); await wait(800);
          const { item, choices } = await pickRadio(cfg.permission);
          if (!item) { await closeMenus(); return fail('no permission mode "' + cfg.permission + '"', { choices }); }
          await item.click(); await wait(400); chosen.permission = cfg.permission;
        }
        const s = await sendPrompt(cfg.prompt);
        if (!s.sent) return fail('prompt was not sent (is a repository or environment required?)', { chosen });
        for (let i = 0; i < 40 && !sessionId(); i++) await wait(500);  // URL becomes /code/session_<id>
        if (!sessionId()) return fail('no session URL after sending', { chosen, url: page.url() });
        if (!cfg.wait) return out({ ok: true, sent: true, chosen, ...(await info()) });
        const w = await waitTurn(0, cfg.timeoutMs);
        return out({ ok: w.finished, sent: true, chosen, ...(w.finished ? {} : { reason: 'timed out; the session keeps working' }), ...(await info()) });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    if (e && e.login) return JSON.stringify({ loggedIn: false, reason: 'login page' });
    return fail(String(e && e.message || e).split('\n')[0]);
  }
}"""


CONNECTION_WORDS = {"Connected", "Offline", "Disconnected", "Connecting", "Not connected"}
PR_WORDS = ("Merged", "Open", "Closed", "Draft", "pull request")


def where(item):
    """Where a session runs, from its sidebar row: detail "Cloud · Merged", "sidonia · Connected",
    "Remote Control · Offline" (+ env glyph cloud|bridge) -> {"runs_on", "machine", "connection"}.
    runs_on is "cloud" or "machine"; machine is None when the site does not name it (Remote Control)."""
    parts = [p.strip() for p in (item.get("detail") or "").split("·") if p.strip()]
    parts = [p for p in parts if not any(w in p for w in PR_WORDS)]
    connection = next((p for p in parts if p in CONNECTION_WORDS), None)
    rest = [p for p in parts if p not in CONNECTION_WORDS]
    if (rest and rest[0] == "Cloud") or (item.get("env") == "cloud" and not connection):
        return {"runs_on": "cloud", "machine": None, "connection": None}
    if rest or connection or item.get("env") == "bridge":
        machine = rest[0] if rest and rest[0] != "Remote Control" else None
        return {"runs_on": "machine", "machine": machine, "connection": connection}
    return {"runs_on": None, "machine": None, "connection": None}


def sessions(settings=None):
    """Sidebar sections with each session's status, PR state and where it runs."""
    result = web_chats.session(TARGET, "list", settings=settings)
    for section in result.get("sections") or []:
        for item in section["items"]:
            if item.get("type") == "code":
                item.update(where(item))
    return result


def where_label(item):
    if item.get("runs_on") == "cloud":
        return "cloud"
    if item.get("runs_on") == "machine":
        name = item.get("machine") or "unnamed machine"  # Remote Control rows do not name the machine
        return name + (f" ({item['connection'].lower()})" if item.get("connection") else "")
    return "?"


def run(op, timeout_s=600, **args):
    """One operation in the already open claude-code-web session (web.open_target)."""
    payload = {"recipe": RECIPE, "op": op, "loginUrl": web.LOGIN_URL, "timeoutMs": timeout_s * 1000, "last": 6, **args}
    return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(payload)), timeout_s + 120)


def session(op, session_ref=None, settings=None, timeout_s=600, **args):
    """Open the browser, resolve <session> through the sidebar if needed, run one operation, close."""
    ok, _, err = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": err}
    try:
        if session_ref:
            found = web_chats.resolve(TARGET, session_ref)
            if not found.get("url"):
                return {"target": TARGET, "ok": False, "reason": found.get("error"), "matches": found.get("matches")}
            args["url"] = found["url"]
        result = run(op, timeout_s=timeout_s, **args)
        if result.get("id"):
            result.update(where(result))
        return {"target": TARGET, **result}
    finally:
        web.release(TARGET)
