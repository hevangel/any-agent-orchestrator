"""Codex on chatgpt.com (`aao codex`), studied 2026-11-11. No LLM: fixed selector checks.

    uv run aao codex threads                         # sidebar threads and where they run
    uv run aao codex read <thread>                   # last prompt, reply, work time, file changes, questions
    uv run aao codex attention [<thread>] [--all]    # questions waiting for an answer, every step
    uv run aao codex answer <thread> --choice Next   # press an option or button of the open question
    uv run aao codex new --prompt ".." [--env E] [--work-in Cloud|Remote --computer C] [--permission P]
                         [--model M] [--effort E] [--wait]
    uv run aao codex send <thread> --prompt ".." [--wait]
    uv run aao codex wait <thread>                   # until the turn ends or a question opens
    uv run aao codex archive <thread>

Codex is a mode of chatgpt.com, switched in the "ChatGPT ⌄" menu at the top left ("Switch mode,
current mode: …"); the URL stays /, threads are /local/<id>. Same login and browser as `chatgpt`.

Questions ("request input") sit above the composer in [data-request-input-activity-root]: a title,
"n of N" with Previous/Next question arrows, a Dismiss button, the body (rows of label + value,
key/value groups, options) and action buttons (Skip, Next, Finish review, ...). This module only
reads them and presses what the caller names: deciding is up to the master agent (or the human,
shown as "attention required" on the dashboard). It never types into a field: secret and value
fields are reported as missing and left to a human.
"""

import json
import re

from orchestrator import web

TARGET = "codex-cloud"
THREAD_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

RECIPE = {
    "origin": "https://chatgpt.com", "threadUrl": "https://chatgpt.com/local/{id}", "idPattern": THREAD_ID,
    "modeButton": "button[aria-label^='Switch mode, current mode: ']", "modePrefix": "Switch mode, current mode: ",
    "mode": "Codex",
    "toggle": "[data-app-action-sidebar-section-toggle]",
    "threadRow": "[data-app-action-sidebar-thread-row]",
    "envTestid": "task-row-environment-",
    # thread page
    "turn": "[data-turn-key]",
    "user": "[data-user-message-bubble='true']",
    "final": "[data-local-conversation-final-assistant='true']",
    "assistant": "[data-markdown-text-style='assistant-message']",
    "busy": ["button[aria-label^='Stop']", "[data-testid='stop-button']"],
    "headerTitle": "[data-app-shell-page-header] button[aria-haspopup=menu]",
    "needsAttention": "Needs attention",
    # new-thread composer (Work in: Cloud | Remote; Remote adds "Select computer")
    "workIn": "button[aria-label='Work in']", "computer": "button[aria-label='Select computer']",
    "env": "button[aria-label='Select environment']", "permission": "button[aria-label='Change permissions']",
    "modelButton": "button[aria-haspopup=menu]", "modelButtonText": "Select effort",
    "efforts": ["None", "Minimal", "Light", "Medium", "High", "Extra High", "Max", "Ultra", "Persistent"],
    "archiveButton": "button[aria-label='Archive chat']",
    "logVerbs": ["Ran", "Edited", "Created", "Deleted", "Read", "Searched", "Explored", "Listed", "Viewed", "Applied", "Called"],
    # questions
    "request": "[data-request-input-activity-root]",
    "questionActions": "[data-question-actions]",
    "freeText": "[contenteditable=true][aria-label='Or write your own response']",
    "submit": ["Send", "Submit", "Continue"],
    "dismiss": "[data-request-input-dismiss]",
    "prevQuestion": "button[aria-label='Previous question']", "nextQuestion": "button[aria-label='Next question']",
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const done = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });
  const press = async loc => { await loc.scrollIntoViewIfNeeded().catch(() => {}); await loc.focus(); await page.keyboard.press('Enter'); };
  const closeMenus = async () => {
    for (let i = 0; i < 4 && await page.locator('[role=menu]').count(); i++) {
      await page.locator('[role=menu]').last().focus().catch(() => {}); await page.keyboard.press('Escape'); await wait(200);
    }
  };

  // ---- Codex mode (the URL alone does not say which mode the sidebar shows)
  if (!page.url().startsWith(r.origin + '/')) await page.goto(r.origin + '/', { waitUntil: 'domcontentloaded' });
  if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return JSON.stringify({ loggedIn: false, reason: 'login page' });
  const modeBtn = page.locator(r.modeButton).filter({ visible: true }).first();
  try { await modeBtn.waitFor({ timeout: 20000 }); }
  catch { return JSON.stringify({ loggedIn: false, reason: 'no mode menu (logged out or page changed)' }); }
  const mode = async () => ((await modeBtn.getAttribute('aria-label')) || '').slice(r.modePrefix.length);
  // the item's first line is the mode name; textContent runs its lines together, so match innerText
  for (let tries = 0; tries < 3 && await mode() !== r.mode; tries++) {
    await closeMenus();
    await press(modeBtn);
    const items = page.locator('[role=menu] [role=menuitem]');
    if (!await items.first().waitFor({ timeout: 4000 }).then(() => true, () => false)) continue;
    const i = (await items.evaluateAll(es => es.map(e => (e.innerText || '').trim().split('\n')[0]))).indexOf(r.mode);
    if (i < 0) continue;
    await items.nth(i).click({ timeout: 5000 });
    for (let k = 0; k < 20 && await mode() !== r.mode; k++) await wait(300);
    await wait(1000);
  }
  if (await mode() !== r.mode) { await closeMenus(); return fail('could not switch to ' + r.mode + ' mode'); }

  // ---- threads
  const threads = () => page.evaluate(r => [...document.querySelectorAll(r.threadRow)].map(row => {
    // the link holds the thread id; the row's data id is a client id ("client-new-thread:…") for a
    // thread started in this page until the next reload
    const raw = (row.getAttribute('data-app-action-sidebar-thread-id') || '');
    const link = row.querySelector('a[href*="/local/"]');
    const m = link && link.getAttribute('href').match(new RegExp('/local/(' + r.idPattern + ')', 'i'));
    const id = m ? m[1].toLowerCase() : raw.slice(raw.indexOf(':') + 1);
    const env = row.querySelector('[data-testid^="' + r.envTestid + '"]');
    return { id, host: raw.slice(0, raw.indexOf(':')), title: row.getAttribute('data-app-action-sidebar-thread-title') || '',
             url: r.threadUrl.replace('{id}', id), runsOn: env ? env.getAttribute('data-testid').slice(r.envTestid.length) : null,
             pinned: row.getAttribute('data-app-action-sidebar-thread-pinned') === 'true',
             active: row.getAttribute('data-app-action-sidebar-thread-active') === 'true' };
  }), r);
  const waitThreads = async () => { await page.locator(r.toggle).first().waitFor({ timeout: 15000 }).catch(() => {}); await wait(800); };
  const resolve = async ref => {
    const m = String(ref || '').match(new RegExp(r.idPattern, 'i'));
    if (m) return { id: m[0].toLowerCase(), url: r.threadUrl.replace('{id}', m[0].toLowerCase()) };
    await waitThreads();
    const want = String(ref || '').trim().toLowerCase();
    const hits = (await threads()).filter(t => t.title.toLowerCase() === want);
    if (hits.length === 1) return hits[0];
    return { error: hits.length ? 'several threads are titled "' + ref + '"; use the id' : 'no thread titled "' + ref + '"',
             matches: hits.map(h => ({ id: h.id, title: h.title })) };
  };
  const openThread = async t => {
    if (!page.url().startsWith(t.url)) await page.goto(t.url, { waitUntil: 'domcontentloaded' });
    await page.locator(r.turn).first().waitFor({ timeout: 25000 }).catch(() => {});
    await wait(1500);
  };

  // ---- the open question (request input), read only
  const questionStep = () => page.evaluate(r => {
    const act = document.querySelector(r.request);
    const dismiss = act && act.querySelector(r.dismiss) || document.querySelector(r.dismiss);
    if (!dismiss) return null;
    let root = dismiss;
    while (root.parentElement && !root.querySelector('[role=presentation] button')) root = root.parentElement;
    const text = e => (e && (e.innerText || e.textContent) || '').trim();
    const leaves = [...root.querySelectorAll('*')].filter(e => !e.children.length && text(e));
    const counter = leaves.find(e => /^\d+ of \d+$/.test(text(e)));
    const [step, steps] = counter ? text(counter).split(' of ').map(Number) : [1, 1];
    const title = text(leaves[0]);
    const body = root.querySelector('fieldset, section') || root;
    const description = [...body.querySelectorAll('p')].map(text).filter(Boolean).join('\n') || null;
    // label/value rows with an edit button named after the label ("Install script  Not set  ✎")
    const items = [...body.querySelectorAll('button[aria-label]')].map(b => {
      let row = b; while (row.parentElement && row.parentElement !== body && !row.querySelector('span + span')) row = row.parentElement;
      const spans = [...row.querySelectorAll('span')].filter(s => !s.children.length && text(s));
      return spans.length >= 2 && text(spans[0]) === b.getAttribute('aria-label')
        ? { label: text(spans[0]), value: text(spans[1]), set: text(spans[1]) !== 'Not set' } : null;
    }).filter(Boolean);
    // key/value groups (secrets, environment variables): values are never returned for secrets
    const fields = [...body.querySelectorAll('[role=group][aria-label]')].map(g => {
      const inputs = [...g.querySelectorAll('input, textarea')];
      const valueInput = inputs.find(i => /^Value for /.test(i.getAttribute('aria-label') || '')) || inputs[inputs.length - 1];
      const secret = !!valueInput && valueInput.type === 'password';
      const filled = !!valueInput && valueInput.value !== '';
      const scope = g.querySelector('button[aria-label^="Scope for"]');
      const domains = g.querySelector('button[aria-label^="Allowed domains for"]');
      return { name: g.getAttribute('aria-label'), kind: secret ? 'secret' : 'variable', filled,
               ...(!secret && filled ? { value: valueInput.value } : {}),
               ...(scope ? { scope: text(scope) } : {}), ...(domains ? { domains: text(domains) } : {}) };
    });
    // choices: radios / checkboxes / options, else unlabeled buttons in the body
    // (a radio's text starts with its hidden number, "1 red": its aria-label is the clean label)
    const roleOpts = [...body.querySelectorAll('[role=radio], [role=checkbox], [role=option], [role=menuitemradio], input[type=radio], input[type=checkbox]')]
      .map(o => ({ label: o.getAttribute('aria-label') || text(o.closest('label')) || text(o) || o.value,
                   selected: o.getAttribute('aria-checked') === 'true' || o.getAttribute('aria-selected') === 'true' || !!o.checked }));
    const actionBox = root.querySelector(r.questionActions);
    const btnOpts = roleOpts.length ? [] : [...body.querySelectorAll('button:not([aria-label])')]
      .filter(b => !b.closest('[role=group], [role=presentation], ' + r.questionActions) && text(b))
      .map(b => ({ label: text(b), selected: b.getAttribute('aria-pressed') === 'true' }));
    const actions = [...(actionBox || root).querySelectorAll(actionBox ? 'button' : '[role=presentation] button')]
      .filter(b => text(b)).map(b => ({ label: text(b), enabled: !b.disabled }));
    const free = root.querySelector(r.freeText);
    const question = text(root.querySelector('[data-markdown-text-style] p'));
    const nav = sel => { const b = root.querySelector(sel); return !!b && !b.disabled; };
    const needs = [...fields.filter(f => !f.filled).map(f => f.name), ...items.filter(i => !i.set).map(i => i.label)];
    return { step, steps, title, question: question || null, description: description === question ? null : description,
             items, fields, options: [...roleOpts, ...btnOpts], actions,
             freeText: free ? { label: free.getAttribute('aria-label'), filled: text(free) !== '' } : null,
             canPrev: nav(r.prevQuestion), canNext: nav(r.nextQuestion), dismissable: true,
             missing: needs, humanOnly: fields.some(f => f.kind === 'secret' && !f.filled) };
  }, r);
  const stepTo = async n => {  // move with the question arrows (reading only; nothing is answered)
    for (let i = 0; i < 12; i++) {
      const q = await questionStep(); if (!q || q.step === n) return q;
      const b = page.locator(q.step < n ? r.nextQuestion : r.prevQuestion).first();
      if (await b.isDisabled().catch(() => true)) return q;
      await press(b); await wait(800);
    }
    return questionStep();
  };
  const questions = async () => {  // every step of the open question, then back to where it was
    let first = await questionStep();
    if (!first) {  // a closed environment review shows only its "Needs attention N" badge: open it to read
      const badge = page.locator('main button').filter({ hasText: new RegExp('^' + r.needsAttention) }).filter({ visible: true }).first();
      if (await badge.count()) { await press(badge); await wait(1500); first = await questionStep(); }
    }
    if (!first) return null;
    const all = [first];
    for (let n = first.step + 1; n <= first.steps; n++) { const q = await stepTo(n); if (q && q.step === n) all.push(q); }
    for (let n = first.step - 1; n >= 1; n--) { const q = await stepTo(n); if (q && q.step === n) all.unshift(q); }
    await stepTo(first.step);
    return { current: first.step, steps: all };
  };

  // ---- thread content
  const readThread = () => page.evaluate(r => {
    const text = e => (e && e.innerText || '').trim();
    const users = [...document.querySelectorAll(r.user)];
    const finals = [...document.querySelectorAll(r.final)];
    const last = finals[finals.length - 1];
    const reply = last ? (text(last.querySelector(r.assistant)) || text(last).replace(/^ChatGPT said:\s*/, '')) : '';
    // work time and file changes of the last turn only (earlier turns keep their own)
    const turns = document.querySelectorAll(r.turn);
    const lastTurn = turns[turns.length - 1] || document.querySelector('main');
    const leaves = lastTurn ? [...lastTurn.querySelectorAll('*')].filter(e => !e.children.length) : [];
    const lastOf = re => { const m = leaves.filter(e => re.test(text(e))); return m.length ? text(m[m.length - 1]) : null; };
    const edit = (() => {
      const m = leaves.filter(e => /^Edited \d+ files?$/.test(text(e)));
      if (!m.length) return null;
      let box = m[m.length - 1];
      for (let i = 0; i < 6 && box.parentElement && !box.querySelector('button[aria-expanded] span + span'); i++) box = box.parentElement;
      const t = text(box), add = t.match(/\n\+(\d+)\n/), del = t.match(/\n-(\d+)\n/);
      // one button per file: path, then "+a" "-d" (the hidden copy splits folder and name)
      const files = [...box.querySelectorAll('button[aria-expanded]')].map(b => {
        const spans = [...b.children].filter(s => s.tagName === 'SPAN');
        const stat = (b.querySelector('[data-thread-find-skip]') || {}).innerText || '';
        const a = stat.match(/\+(\d+)/), d = stat.match(/-(\d+)/);
        return spans.length && a ? { path: text(spans[0]), added: +a[1], removed: d ? +d[1] : 0 } : null;
      }).filter(Boolean);
      const more = t.match(/Show (\d+) more files?/);
      return { summary: text(m[m.length - 1]), added: add ? +add[1] : null, removed: del ? +del[1] : null,
               files, ...(more ? { notListed: +more[1] } : {}) };
    })();
    const prs = lastTurn ? [...new Set([...lastTurn.querySelectorAll('a[href*="github.com/"][href*="/pull/"]')].map(a => a.href))] : [];
    const badge = [...document.querySelectorAll('main button')].map(text).find(t => t.startsWith(r.needsAttention));
    const title = text(document.querySelector(r.headerTitle + ':not([hidden])')) || document.title;
    return { title, turns: document.querySelectorAll(r.turn).length,
             lastPrompt: users.length ? text(users[users.length - 1]) : null, reply,
             worked: lastOf(/^Worked for /), edits: edit, pullRequests: prs,
             finals: finals.length, users: users.length,
             environmentAttention: badge ? (+(badge.match(/\d+/) || [0])[0] || true) : 0 };
  }, r);
  // the last turn's actions from its "Worked for …" log (expanded to read, folded back)
  const workLog = async () => {
    const turn = page.locator(r.turn).last();
    const b = turn.locator('button', { hasText: /^Worked for/ }).first();
    if (!await b.count()) return [];
    const open = await b.getAttribute('aria-expanded') === 'true';
    if (!open) { await press(b); await wait(1000); }
    const lines = (await turn.innerText().catch(() => '')).split('\n').map(s => s.trim())
      .filter(s => new RegExp('^(' + r.logVerbs.join('|') + ') ').test(s));
    if (!open) { await press(b).catch(() => {}); await wait(300); }
    return lines.slice(0, 60);
  };
  const report = async extra => ({ ...extra, actions: await workLog(), working: await busy(), attention: await questions() });
  // a turn is running while the composer shows "Stop" (the "Send" button is back when it ends)
  const waitTurn = async (before, timeoutMs) => {
    const t0 = Date.now();
    let started = await busy(), idle = 0;
    while (Date.now() - t0 < timeoutMs) {
      await wait(2000);
      const b = await busy();
      if (b) { started = true; idle = 0; continue; }
      const th = await readThread();
      if (!started && th.finals <= before && Date.now() - t0 < 30000) continue;  // not picked up yet
      if (await questionStep()) return { status: 'needs-attention', ...th };
      if (++idle >= 2) return { status: 'done', ...th };
    }
    return { status: 'timeout', ...(await readThread()) };
  };
  // a menu button's item by its first text line (items carry a second, descriptive line)
  const choose = async (button, label, what) => {
    await closeMenus();
    if (!await button.count()) throw new Error('no ' + what + ' control');
    await press(button.first());
    const items = page.locator('[role=menu]').last().locator('[role^=menuitem]');
    await items.first().waitFor({ timeout: 5000 });
    const texts = await items.evaluateAll(es => es.map(e => (e.innerText || '').trim().split('\n')[0]));
    const i = texts.indexOf(label);
    if (i < 0) { await closeMenus(); throw Object.assign(new Error('no ' + what + ' "' + label + '"'), { extra: { choices: texts.filter(Boolean) } }); }
    await items.nth(i).click(); await wait(700);
    await closeMenus();
  };
  const pathOf = u => u.slice(r.origin.length).split(/[?#]/)[0];
  // effort: a slider in the model menu; the label next to the model name shows the current level
  const setEffort = async (modelBtn, want) => {
    if (!r.efforts.includes(want)) throw Object.assign(new Error('unknown effort "' + want + '"'), { extra: { choices: r.efforts } });
    await closeMenus();
    await press(modelBtn.first());
    const slider = page.locator('[role=menu] [role=slider]').first();
    await slider.waitFor({ timeout: 5000 });
    const level = () => page.locator('[role=menu] [data-power-disabled]').first().innerText().then(s => s.trim(), () => '');
    await slider.focus();
    for (let i = 0; i < 10; i++) {
      const cur = await level();
      if (cur === want) break;
      await page.keyboard.press(r.efforts.indexOf(cur) < r.efforts.indexOf(want) ? 'ArrowRight' : 'ArrowLeft');
      await wait(300);
    }
    const got = await level();
    await closeMenus();
    if (got !== want) throw Object.assign(new Error('effort "' + want + '" is not available for this model'), { extra: { effort: got } });
  };
  const composerSettings = () => page.evaluate(r => {
    const t = sel => { const b = [...document.querySelectorAll(sel)].find(x => x.offsetParent); return b ? b.innerText.trim().split('\n')[0] : null; };
    const m = [...document.querySelectorAll(r.modelButton)].find(b => b.offsetParent && b.textContent.includes(r.modelButtonText));
    const lines = m ? m.innerText.trim().split('\n') : [];
    return { workIn: t(r.workIn), computer: t(r.computer), env: t(r.env), permission: t(r.permission),
             model: lines[0] || null, effort: lines[1] || null };
  }, r);
  const composer = () => page.locator('[contenteditable=true]').filter({ visible: true }).last();
  const typePrompt = async text => {
    const ed = composer();
    await ed.waitFor({ timeout: 15000 });
    await ed.click();
    await page.keyboard.insertText(text);
    await wait(300);
    await page.keyboard.press('Enter');
  };
  const busy = async () => { for (const s of r.busy) if (await page.locator(s).first().isVisible().catch(() => false)) return true; return false; };
  const threadArg = async () => {
    const t = await resolve(cfg.thread);
    if (t.error) throw Object.assign(new Error(t.error), { extra: { matches: t.matches } });
    await openThread(t);
    return t;
  };

  await closeMenus();
  try {
    switch (cfg.op) {
      case 'threads': {
        await waitThreads();
        return done({ ok: true, mode: await mode(), threads: await threads() });
      }
      case 'read': {
        const t = await threadArg();
        return done(await report({ ok: true, id: t.id, url: page.url(), ...(await readThread()) }));
      }
      case 'attention': {
        // one thread, or every sidebar thread (--all): open each and read its question, if any
        let list;
        if (cfg.thread) list = [await threadArg()];
        else { await waitThreads(); list = await threads(); }
        const out = [];
        for (const t of list) {
          await openThread(t);
          const q = await questions();
          const th = await readThread();
          if (q || th.environmentAttention) out.push({ id: t.id, title: t.title || th.title, url: t.url, runsOn: t.runsOn || null,
                                                        environmentAttention: th.environmentAttention, attention: q });
        }
        return done({ ok: true, checked: list.length, threads: out });
      }
      case 'answer': {
        // press one option or action of the open question; never types into a field
        const t = await threadArg();
        let q = await questionStep();
        if (!q) return fail('no open question in this thread');
        if (cfg.step) q = await stepTo(cfg.step);
        const submitBtn = () => page.locator(r.questionActions + ' button').filter({ hasText: new RegExp('^(' + r.submit.join('|') + ')$') }).first();
        if (cfg.text) {  // free-text answer: only the "Or write your own response" box, never a field
          const box = page.locator(r.freeText).filter({ visible: true }).first();
          if (!q.freeText || !await box.count()) return fail('this question takes no free-text answer', { question: q });
          await box.click(); await page.keyboard.insertText(cfg.text); await wait(500);
          if (!await submitBtn().isEnabled().catch(() => false)) return fail('the send button stayed disabled', { question: await questionStep() });
          await submitBtn().click(); await wait(1500);
          return done({ ok: true, id: t.id, answered: cfg.text, step: q.step, title: q.question || q.title, next: await questionStep() });
        }
        const want = String(cfg.choice || '').trim();
        const opt = q.options.find(o => o.label === want);
        const act = q.actions.find(a => a.label === want);
        if (!opt && !act && want !== 'Dismiss')
          return fail('"' + want + '" is not an option or action of step ' + q.step, { question: q });
        if (act && !act.enabled)
          return fail('"' + want + '" is disabled until these are filled: ' + (q.missing.join(', ') || 'unknown fields')
                      + (q.humanOnly ? ' (secrets: a human must enter them)' : ''), { question: q });
        const root = page.locator(r.request).filter({ has: page.locator(r.dismiss) }).first();
        const scope = await root.count() ? root : page.locator('main');
        if (opt) {  // pick the option, then press Send (disabled until an option is picked) unless --no-send
          const radio = scope.getByRole('radio', { name: want, exact: true }).or(scope.getByRole('checkbox', { name: want, exact: true })).first();
          await (await radio.count() ? radio : scope.getByText(want, { exact: true }).first()).click({ timeout: 5000 });
          await wait(600);
          // a single-choice question submits on the click itself; otherwise Send is enabled now
          let next = await questionStep();
          if (next && !cfg.noSend && await submitBtn().count() && await submitBtn().isEnabled().catch(() => false)) {
            await submitBtn().click(); await wait(1500); next = await questionStep();
          }
          const sent = !next || next.step !== q.step || next.title !== q.title;
          return done({ ok: true, id: t.id, pressed: want, sent, step: q.step, title: q.question || q.title, next });
        }
        const btn = want === 'Dismiss' ? scope.locator(r.dismiss).first()
          : opt ? scope.getByText(want, { exact: true }).first()
          : scope.locator('[role=presentation] button').filter({ hasText: new RegExp('^' + want.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$') }).first();
        await btn.click({ timeout: 5000 });
        await wait(1500);
        const after = await questionStep();
        return done({ ok: true, id: t.id, pressed: want, step: q.step, title: q.title, next: after });
      }
      case 'new': {
        // only ever types on the new-thread page: URL "/" with the "Work in" control (else a prompt
        // could land in whatever thread is open)
        await page.goto(r.origin + '/', { waitUntil: 'domcontentloaded' });
        const workIn = page.locator(r.workIn).filter({ visible: true });
        if (!await workIn.first().waitFor({ timeout: 20000 }).then(() => true, () => false) || !/^\/?$/.test(pathOf(page.url())))
          return fail('not on the Codex new-thread page (no "Work in" control at /)');
        if (cfg.workIn) await choose(workIn, cfg.workIn, 'work-in option');
        if (cfg.computer) await choose(page.locator(r.computer).filter({ visible: true }), cfg.computer, 'computer');
        if (cfg.env) await choose(page.locator(r.env).filter({ visible: true }), cfg.env, 'environment');
        if (cfg.permission) await choose(page.locator(r.permission).filter({ visible: true }), cfg.permission, 'permission');
        const modelBtn = page.locator(r.modelButton).filter({ visible: true }).filter({ hasText: r.modelButtonText });
        if (cfg.model) await choose(modelBtn, cfg.model, 'model');
        if (cfg.effort) await setEffort(modelBtn, cfg.effort);
        const settings = await composerSettings();
        if (pathOf(page.url()).replace(/\/$/, '') !== '' || !await workIn.first().isVisible())
          return fail('left the new-thread page while choosing settings', { settings });
        await typePrompt(cfg.prompt);
        let id = null;
        for (let i = 0; i < 40 && !id; i++) { await wait(500); const m = pathOf(page.url()).match(new RegExp('^/local/(' + r.idPattern + ')')); id = m && m[1]; }
        if (!id) return fail('the thread did not start (URL stayed ' + pathOf(page.url()) + ')', { settings });
        const turn = cfg.wait ? await waitTurn(0, cfg.timeoutMs) : { status: 'started', ...(await readThread()) };
        return done(await report({ ok: turn.status !== 'timeout', id, url: r.threadUrl.replace('{id}', id), settings, ...turn }));
      }
      case 'send': {
        const t = await threadArg();
        if (await busy()) return fail('the thread is still working; wait for it first');
        const before = await readThread();
        await typePrompt(cfg.prompt);
        let sent = false;
        for (let i = 0; i < 40 && !(sent = (await readThread()).users > before.users || await busy()); i++) await wait(500);
        if (!sent) return fail('the message was not sent');
        const turn = cfg.wait ? await waitTurn(before.finals, cfg.timeoutMs) : { status: 'sent', ...(await readThread()) };
        return done(await report({ ok: turn.status !== 'timeout', id: t.id, url: page.url(), ...turn }));
      }
      case 'wait': {
        const t = await threadArg();
        const turn = await waitTurn(-1, cfg.timeoutMs);
        return done(await report({ ok: turn.status !== 'timeout', id: t.id, url: page.url(), ...turn }));
      }
      case 'archive': {
        const t = await resolve(cfg.thread);
        if (t.error) return fail(t.error, { matches: t.matches });
        await waitThreads();
        const row = page.locator(`${r.threadRow}:has(a[href$="/local/${t.id}"])`).first();
        if (!await row.count()) return fail('thread is not in the sidebar (already archived?)');
        await press(row.locator(r.archiveButton).first());
        await wait(1200);
        const dlg = page.locator('[role=dialog], [role=alertdialog]').last();
        if (await dlg.isVisible().catch(() => false)) {
          const ok = dlg.getByRole('button', { name: 'Archive', exact: true }).first();
          if (!await ok.isVisible().catch(() => false)) { await page.keyboard.press('Escape'); return fail('unexpected dialog: ' + (await dlg.innerText()).slice(0, 200)); }
          await ok.click(); await wait(1000);
        }
        let gone = false;
        for (let i = 0; i < 16 && !(gone = !(await threads()).some(x => x.id === t.id)); i++) await wait(500);
        return done({ ok: gone, id: t.id, title: t.title || null, ...(gone ? {} : { reason: 'thread is still in the sidebar' }) });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    await closeMenus().catch(() => {});
    return fail(String(e && e.message || e).split('\n')[0], e && e.extra);
  }
}"""


def run(op, timeout_s=300, **args):
    """One operation in the already open chatgpt.com browser (shared with the chatgpt target)."""
    payload = {"recipe": RECIPE, "op": op, "loginUrl": web.LOGIN_URL, "timeoutMs": timeout_s * 1000, **args}
    return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(payload)), timeout_s + 120)


def session(op, settings=None, timeout_s=300, **args):
    ok, _, err = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": err}
    try:
        return {"target": TARGET, **run(op, timeout_s=timeout_s, **args)}
    finally:
        web.release(TARGET)


def thread_id(ref):
    m = re.search(THREAD_ID, str(ref or ""), re.I)
    return m.group(0).lower() if m else None
