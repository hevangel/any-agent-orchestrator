"""Gemini (gemini.google.com) chat list, studied 2026-11-12. Driven through web_chats (`aao chats gemini`).

Groups are **notebooks** (NotebookLM notebooks shown in Gemini).
- Sidebar "All" list: conversations-list[data-test-id=all-conversations] gem-nav-list-item
  [data-test-id=conversation], link /app/<16 hex>. A pinned chat stays in this list (on top) with a
  push_pin icon. A Task chat carries remy-goal-status-badge whose tooltip (gemtooltip) is its state
  ("Unread", ...); the sidebar Filter offers All / Ready / Needs attention / In progress.
- Row menu "More options for <title>": Share conversation, Pin/Unpin (pin-button / unpin-button),
  Rename, Add to notebook (dialog "Move chat" with one role=option per notebook), Delete. The menu
  opens on Enter, or else on a click event on the button.
- A chat moved into a notebook leaves "All" and is listed on the notebook page (/notebook/<uuid>)
  under "Past chats" as project-chat-row (title [data-test-id=chat-title], no link) whose menu has
  Move to notebook, Remove from notebook (remove-project-button), Rename, Delete.
- All notebooks: /notebooks/view, cards project-mgmt-row (emoji, name, "N sources") without links;
  opening a card goes to /notebook/<uuid>. Card menu: Pin, Rename, Delete.
- New notebook: /notebooks/create, input "Name of the notebook", Enter -> /notebook/<uuid>.
- Delete: "Delete notebook everywhere? This notebook, including all sources and chats, will be
  permanently deleted from both Gemini and NotebookLM." -> "Delete everywhere". delete-group
  therefore refuses a notebook with any source or chat.
- Search: "Search chats" opens a search box (input[aria-label='Search chats']); results are links
  /app/<id>#<message> (text: icon name, title, snippet). It matches message text and lags behind new
  chats, so search lists sidebar title matches first.
"""

CHAT_ID = r"[0-9a-f]{16}"

RECIPE = {
    "engine": "gemini", "groupWord": "notebook",
    "origin": "https://gemini.google.com", "itemType": "chat", "idPattern": CHAT_ID,
    "chatUrl": "https://gemini.google.com/app/{id}", "home": "/app",
    "searchResolve": True,
    "row": "conversations-list[data-test-id=all-conversations] gem-nav-list-item[data-test-id=conversation]",
    "rowMenu": "button[aria-label^='More options for']",
    "badge": "remy-goal-status-badge [gemtooltip]",
    "pin": "pin-button", "unpin": "unpin-button", "addTo": "Add to notebook", "moveDialog": "Move chat",
    "notebooksUrl": "/notebooks/view", "notebookCard": "project-mgmt-row",
    "notebookMenu": "button[aria-label='Open notebook actions menu']",
    "notebookChat": "project-chat-row", "notebookChatTitle": "[data-test-id=chat-title]",
    "removeFrom": "remove-project-button", "deleteButton": "delete-button",
    "createUrl": "/notebooks/create", "notebookName": "input[aria-label='Name of the notebook']",
    "deleteConfirmText": "Delete notebook everywhere?", "deleteConfirm": "Delete everywhere",
    "searchButton": "button[aria-label='Search chats']", "searchInput": "input[aria-label='Search chats']",
    # reading a chat
    "user": "user-query", "reply": "model-response message-content .markdown",
    "strip": ["sources-carousel-inline", "source-inline-chip"],
    "busy": ["button[aria-label='Stop response']", "model-response message-content [aria-busy='true']"],
    "taskStatus": "remy-goal-status-footer",
    # human pace (ms ranges): Google rate-limits bursts of scripted actions with a CAPTCHA
    "pace": {"pause": [700, 1600], "load": [2500, 5000], "key": [45, 140], "hold": [60, 160], "pasteOver": 400},
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const done = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });
  const chatUrl = id => r.chatUrl.replace('{id}', id);
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Every page load counts toward Google's rate limit; too many in a row lead to its "unusual traffic"
  // check (google.com/sorry), a CAPTCHA only a human may answer. So loads are few and spaced.
  const blocked = () => /google\.com\/sorry/.test(page.url());
  const BLOCKED = 'Google is showing its "unusual traffic" check (a CAPTCHA) in the Gemini browser; a human must clear it there';
  const rand = ([a, b]) => a + Math.random() * (b - a);
  const pause = () => wait(rand(r.pace.pause));
  // a click as a person makes it: pause, move onto the element, hold the button briefly
  const click = async loc => {
    await pause();
    await loc.scrollIntoViewIfNeeded().catch(() => {});
    await loc.hover().catch(() => {});
    await wait(rand([150, 400]));
    await loc.click({ delay: rand(r.pace.hold) });
  };
  // typing key by key with an uneven rhythm; a long text is pasted, as a person would
  const typeInto = async (loc, text) => {
    await click(loc);
    await loc.fill('');
    if (text.length > r.pace.pasteOver) { await page.keyboard.insertText(text); return; }
    for (const ch of text) await page.keyboard.type(ch, { delay: rand(r.pace.key) });
  };
  const goto = async path => {
    await wait(rand(r.pace.load));
    await page.goto(r.origin + path, { waitUntil: 'domcontentloaded' });
    if (blocked()) throw new Error(BLOCKED);
    await wait(2500);
  };

  // already on the check page: report it without loading anything (each load extends the block)
  if (blocked()) return JSON.stringify({ loggedIn: true, ok: false, blocked: true, reason: BLOCKED });
  try { if (!page.url().startsWith(r.origin + '/')) await goto(r.home); }
  catch (e) { return JSON.stringify({ loggedIn: true, ok: false, blocked: blocked(), reason: String(e.message || e).split('\n')[0] }); }
  if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return JSON.stringify({ loggedIn: false, reason: 'login page' });
  const sidebarReady = async () => {
    try { await page.locator(r.row).first().waitFor({ timeout: 20000 }); return true; } catch { return false; }
  };
  if (!await sidebarReady()) {
    const guest = await page.getByRole('link', { name: /^sign in$/i }).or(page.getByRole('button', { name: /^sign in$/i })).first().isVisible().catch(() => false);
    return JSON.stringify({ loggedIn: false, reason: guest ? 'signed out (Sign in visible)' : 'no chat list (signed out or page changed)' });
  }

  // ---- reading
  const chats = () => page.evaluate(r => [...document.querySelectorAll(r.row)].map(row => {
    const a = row.querySelector('a[href^="/app/"]');
    if (!a) return null;
    const id = a.getAttribute('href').split('/').pop().split('#')[0];
    const badge = row.querySelector(r.badge);
    const status = badge ? badge.getAttribute('gemtooltip') : null;
    return { id, type: 'chat', title: (a.getAttribute('aria-label') || a.innerText || '').trim(),
             url: r.chatUrl.replace('{id}', id),
             pinned: [...row.querySelectorAll('mat-icon')].some(i => i.getAttribute('fonticon') === 'push_pin'),
             ...(status ? { status } : {}) };
  }).filter(Boolean), r);
  // the list loads more on scroll: scroll its last row into view until `found` or nothing new
  const loadMore = async (found, rounds = 8) => {
    for (let i = 0; i < rounds; i++) {
      const list = await chats();
      if (found && found(list)) return list;
      const before = list.length;
      await page.locator(r.row).last().scrollIntoViewIfNeeded().catch(() => {});
      await wait(1200);
      if ((await chats()).length === before) break;
    }
    return chats();
  };
  const sidebarNotebooks = () => page.$$eval('a[href^="/notebook/"]', as => as.filter(a => a.getClientRects().length)
    .map(a => ({ name: a.innerText.trim(), id: a.getAttribute('href').split('/').pop(), sources: null })));
  const notebooks = async () => {
    await goto(r.notebooksUrl);
    await page.locator(r.notebookCard).first().waitFor({ timeout: 15000 }).catch(() => {});
    await wait(800);
    const ids = await page.$$eval('a[href^="/notebook/"]', as => Object.fromEntries(as.map(a => [a.innerText.trim(), a.getAttribute('href').split('/').pop()])));
    return page.$$eval(r.notebookCard, (cs, ids) => cs.map(c => {
      const lines = c.innerText.split('\n').map(s => s.trim()).filter(Boolean);
      const name = lines.find(l => !/^\p{Extended_Pictographic}/u.test(l) && !/^\d+ sources?$/.test(l)) || '';
      const src = lines.find(l => /^\d+ sources?$/.test(l));
      return { name, sources: src ? +src.split(' ')[0] : 0, ...(ids[name] ? { id: ids[name] } : {}) };
    }), ids);
  };
  const named = async name => (await notebooks()).filter(n => n.name === name);
  // a card by its name line (textContent runs the card's lines together, so hasText cannot anchor it)
  const cardOf = async name => {
    await page.locator(r.notebookCard).first().waitFor({ timeout: 15000 }).catch(() => {});
    const i = await page.$$eval(r.notebookCard, (cs, n) => cs.findIndex(c => c.innerText.split('\n').map(x => x.trim()).includes(n)), name);
    return i < 0 ? null : page.locator(r.notebookCard).nth(i);
  };
  // open a notebook from its card (cards have no links)
  const openNotebook = async name => {
    await goto(r.notebooksUrl);
    const card = await cardOf(name);
    if (!card) throw new Error('no notebook named "' + name + '"');
    await click(card.locator('.project-card, div').first());
    for (let i = 0; i < 20 && !/\/notebook\//.test(page.url()); i++) await wait(300);
    await wait(2500);
    return page.url().split('/notebook/')[1].split(/[?#]/)[0];
  };
  const notebookChats = () => page.$$eval(r.notebookChat + ' ' + r.notebookChatTitle, es => es.map(e => ({ type: 'chat', title: e.textContent.trim() })));

  const resolve = async ref => {
    const m = String(ref || '').match(new RegExp('(?:/app/)?(' + r.idPattern + ')', 'i'));
    if (m) {
      const id = m[1].toLowerCase(), hit = (await chats()).find(c => c.id === id);
      return { id, title: hit ? hit.title : null, url: chatUrl(id) };
    }
    const want = String(ref || '').trim().toLowerCase();
    const list = await loadMore(l => l.some(c => c.title.toLowerCase() === want));
    const hits = list.filter(c => c.title.toLowerCase() === want);
    if (hits.length === 1) return { id: hits[0].id, title: hits[0].title, url: hits[0].url };
    return { error: hits.length ? 'several chats are titled "' + ref + '"; use the id' : 'no chat titled "' + ref + '" in the chat list (chats inside a notebook: use the id)',
             matches: hits.map(h => ({ id: h.id, title: h.title })) };
  };
  const chatArg = async () => {
    const c = await resolve(cfg.chat);
    if (c.error) throw Object.assign(new Error(c.error), { extra: { matches: c.matches } });
    return c;
  };
  // a chat's title from its own page (chats inside notebooks are not in the sidebar)
  const titleOf = async id => {
    await goto('/app/' + id);
    for (let i = 0; i < 20; i++) { const t = (await page.title()).replace(/\s*-\s*Google Gemini$/, '').trim(); if (t && t !== 'Google Gemini') return t; await wait(500); }
    return null;
  };

  // ---- menus (Material: Enter on the button, else a click event on it; never a click on the row link)
  const items = () => page.locator('[role=menu] [role^=menuitem]').filter({ visible: true });
  const escape = async () => { for (let i = 0; i < 3 && await page.locator('[role=menu], mat-dialog-container').filter({ visible: true }).count(); i++) { await page.keyboard.press('Escape'); await wait(250); } };
  const openMenu = async button => {
    await escape();
    await button.scrollIntoViewIfNeeded().catch(() => {});
    await pause();
    await button.focus(); await page.keyboard.press('Enter'); await wait(700);
    if (!await items().count()) { await button.dispatchEvent('click'); await wait(700); }
    if (!await items().count()) throw new Error('the menu did not open');
  };
  const rowOf = async id => {
    const row = page.locator(r.row).filter({ has: page.locator('a[href="/app/' + id + '"]') }).first();
    if (await row.count()) return row;
    await loadMore(l => l.some(c => c.id === id));
    return await row.count() ? row : null;
  };
  const until = async (check, tries = 12) => { for (let i = 0; i < tries; i++) { await wait(700); const v = await check(); if (v) return v; } return null; };

  await escape();
  try {
    switch (cfg.op) {
      case 'list': {
        const all = await chats();
        // the sidebar shows the recent notebooks; all of them (with source counts) only when asked
        const nbs = cfg.allGroups || cfg.group ? await notebooks() : await sidebarNotebooks();
        const groups = [];
        for (const n of nbs) {
          let items_ = [], collapsed = true;
          if (cfg.group && n.name === cfg.group) { await openNotebook(n.name); items_ = await notebookChats(); collapsed = false; }
          groups.push({ id: n.id || null, name: n.name, kind: 'group', sources: n.sources, collapsed, items: items_ });
        }
        if (page.url().indexOf(r.home) < 0) await goto(r.home);
        let sections = [{ id: 'pinned', name: 'Pinned', kind: 'pinned', collapsed: false, items: all.filter(c => c.pinned) },
                        ...groups,
                        { id: 'recents', name: 'All', kind: 'recents', collapsed: false, items: all.filter(c => !c.pinned) }];
        if (cfg.group) sections = sections.filter(s => s.name === cfg.group);
        if (cfg.status) sections = sections.filter(s => s.kind !== 'group')
          .map(s => ({ ...s, items: s.items.filter(c => (c.status || '').toLowerCase() === cfg.status.toLowerCase()) }));
        return done({ ok: true, sections });
      }
      case 'search': {
        const q = String(cfg.query || '');
        const byTitle = (await loadMore()).filter(c => c.title.toLowerCase().includes(q.toLowerCase()));
        await click(page.locator(r.searchButton).filter({ visible: true }).first());
        const box = page.locator(r.searchInput).filter({ visible: true }).first();
        await box.waitFor({ timeout: 8000 });
        await typeInto(box, q);
        let prev = null, found = [];
        for (let i = 0; i < 14; i++) {
          await wait(700);
          found = await page.$$eval('a[href^="/app/"]', (as, r) => as.filter(a => a.getClientRects().length && !a.closest('gem-nav-list-item')).map(a => {
            const id = a.getAttribute('href').split('/').pop().split('#')[0];
            const lines = a.innerText.split('\n').map(s => s.trim()).filter(Boolean);
            const text = /^[a-z_]+$/.test(lines[0] || '') ? lines.slice(1) : lines;  // drop the icon name
            return { id, type: 'chat', title: text[0] || '', detail: text.slice(1).join(' ').slice(0, 200), url: r.chatUrl.replace('{id}', id) };
          }), r);
          const sig = JSON.stringify(found);
          if (i >= 2 && sig === prev) break;
          prev = sig;
        }
        await goto(r.home);
        const results = [...new Map([...byTitle, ...found].map(c => [c.id, c])).values()];
        return done({ ok: true, query: q, results });
      }
      case 'resolve': {
        const c = await resolve(cfg.chat);
        return c.error ? fail(c.error, { matches: c.matches }) : done({ ok: true, ...c });
      }
      case 'read': {  // one chat: last prompt, reply, task state; a waiting task is reported, not answered
        const c = await chatArg();
        await goto('/app/' + c.id);
        await page.locator(r.user).first().waitFor({ timeout: 20000 }).catch(() => {});
        await wait(1500);
        const info = await page.evaluate(r => {
          const text = e => (e && e.innerText || '').trim();
          const users = [...document.querySelectorAll(r.user)];
          const replies = [...document.querySelectorAll(r.reply)];
          const last = replies[replies.length - 1];
          let reply = '';
          if (last) { const copy = last.cloneNode(true); r.strip.forEach(s => copy.querySelectorAll(s).forEach(e => e.remove())); reply = text(copy) || copy.textContent.trim(); }
          const status = document.querySelectorAll(r.taskStatus);
          const lastModel = [...document.querySelectorAll('model-response')].pop();
          const actions = lastModel ? [...lastModel.querySelectorAll('button')].filter(b => b.getClientRects().length)
            .map(b => b.getAttribute('aria-label') || text(b)).filter(t => t && !/copy|good response|bad response|share|more options|redo|listen|show thinking|sources/i.test(t)) : [];
          const lastUser = users[users.length - 1];
          const prompt = lastUser ? ([...lastUser.querySelectorAll('.query-text-line')].map(text).join('\n') || text(lastUser)) : null;
          const files = lastUser ? [...lastUser.querySelectorAll('gem-attachment, uploader-file-preview')].map(text).filter(Boolean) : [];
          return { title: document.title.replace(/\s*-\s*Google Gemini$/, ''), turns: users.length,
                   lastPrompt: prompt, attachments: files, reply,
                   taskStatus: status.length ? text(status[status.length - 1]) : null, actions: [...new Set(actions)].slice(0, 12) };
        }, r);
        let working = false;
        for (const s of r.busy) if (await page.locator(s).first().isVisible().catch(() => false)) working = true;
        const row = (await chats()).find(x => x.id === c.id);
        return done({ ok: true, id: c.id, url: chatUrl(c.id), ...info, working, ...(row && row.status ? { status: row.status } : {}) });
      }
      case 'pin': case 'unpin': {
        const c = await chatArg();
        const want = cfg.op === 'pin';
        if (!page.url().endsWith(r.home)) await goto(r.home);
        const row = await rowOf(c.id);
        if (!row) return fail('the chat is not in the chat list (chats inside a notebook cannot be pinned there)');
        const pinned = async () => (await chats()).some(x => x.id === c.id && x.pinned);
        if (await pinned() === want) return done({ ok: true, chat: c, changed: false, reason: want ? 'already pinned' : 'not pinned' });
        await openMenu(row.locator(r.rowMenu).first());
        const it = items().and(page.locator('[data-test-id="' + (want ? r.pin : r.unpin) + '"]')).first();
        if (!await it.count()) { await escape(); return fail('no "' + (want ? 'Pin' : 'Unpin') + '" item'); }
        await click(it);
        const v = await until(async () => await pinned() === want);
        return done({ ok: !!v, chat: c, changed: true, ...(v ? {} : { reason: 'pin state did not change' }) });
      }
      case 'move': {
        const c = await chatArg();
        const found = await named(cfg.group);
        if (found.length > 1) return fail('several notebooks are named "' + cfg.group + '"');
        let created = false;
        if (!found.length) {
          if (!cfg.create) return fail('no notebook named "' + cfg.group + '" (use --create)', { groups: (await notebooks()).map(n => n.name) });
          await goto(r.createUrl);
          await typeInto(page.locator(r.notebookName), cfg.group); await pause(); await page.locator(r.notebookName).press('Enter');
          if (!await until(async () => /\/notebook\//.test(page.url()))) return fail('the new notebook did not open');
          created = true;
        }
        await goto(r.home);
        const row = await rowOf(c.id);
        if (!row) return fail('the chat is not in the chat list (already in a notebook? use unproject first)');
        await openMenu(row.locator(r.rowMenu).first());
        const add = items().filter({ hasText: new RegExp('^\\s*' + r.addTo + '\\s*$') }).first();
        if (!await add.count()) { await escape(); return fail('no "' + r.addTo + '" item'); }
        await click(add);
        const dlg = page.locator('mat-dialog-container').filter({ visible: true }).filter({ hasText: r.moveDialog }).last();
        await dlg.waitFor({ timeout: 8000 });
        const opt = dlg.locator('[role=option]').filter({ hasText: new RegExp('^\\s*' + esc(cfg.group) + '\\s*$') }).first();
        if (!await opt.count()) { await escape(); return fail('"' + cfg.group + '" is not offered in the Move chat dialog'); }
        await click(opt);
        const gone = await until(async () => !(await chats()).some(x => x.id === c.id));
        await openNotebook(cfg.group);
        const there = (await notebookChats()).some(x => x.title === c.title);
        await goto(r.home);
        return done({ ok: !!gone && (there || !c.title), chat: c, group: cfg.group, changed: true, created,
                      ...(gone ? {} : { reason: 'chat is still in the chat list' }) });
      }
      case 'ungroup': {
        const c = await chatArg();
        if ((await chats()).some(x => x.id === c.id)) return done({ ok: true, chat: c, changed: false, reason: 'not in a notebook' });
        const title = c.title || await titleOf(c.id);
        if (!title) return fail('could not read the chat title');
        // find the notebook that lists it (notebook rows have titles, not ids)
        let home = null;
        for (const n of await notebooks()) {
          await openNotebook(n.name);
          if ((await notebookChats()).some(x => x.title === title)) { home = n.name; break; }
        }
        if (!home) return fail('no notebook lists "' + title + '"');
        const row = page.locator(r.notebookChat).filter({ has: page.locator(r.notebookChatTitle, { hasText: new RegExp('^\\s*' + esc(title) + '\\s*$') }) }).first();
        await openMenu(row.locator('button[aria-label="More options"]').first());
        const rm = items().and(page.locator('[data-test-id="' + r.removeFrom + '"]')).first();
        if (!await rm.count()) { await escape(); return fail('no "Remove from notebook" item'); }
        await click(rm);
        await goto(r.home);
        const back = await until(async () => (await loadMore(l => l.some(x => x.id === c.id))).some(x => x.id === c.id), 6);
        return done({ ok: !!back, chat: { ...c, title }, group: home, changed: true, ...(back ? {} : { reason: 'chat did not come back to the chat list' }) });
      }
      case 'new-group': {
        if ((await named(cfg.group)).length) return done({ ok: true, group: cfg.group, changed: false, reason: 'notebook already exists' });
        await goto(r.createUrl);
        await typeInto(page.locator(r.notebookName), cfg.group); await pause(); await page.locator(r.notebookName).press('Enter');
        const ok = await until(async () => /\/notebook\//.test(page.url()));
        const id = ok ? page.url().split('/notebook/')[1].split(/[?#]/)[0] : null;
        return done({ ok: !!ok, group: cfg.group, notebook: id, changed: true, ...(ok ? {} : { reason: 'the notebook did not open' }) });
      }
      case 'delete-group': {
        const found = await named(cfg.group);
        if (found.length !== 1) return fail(found.length ? 'several notebooks are named "' + cfg.group + '"' : 'no notebook named "' + cfg.group + '"');
        // Gemini deletes a notebook's sources and chats everywhere: only an empty notebook is deleted
        if (found[0].sources) return fail('notebook "' + cfg.group + '" has ' + found[0].sources + ' source(s); deleting it deletes them in Gemini and NotebookLM. Nothing deleted.');
        await openNotebook(cfg.group);
        const inside = await notebookChats();
        if (inside.length) return fail('notebook "' + cfg.group + '" has ' + inside.length + ' chat(s); deleting it deletes them. Move them out first (aao chats gemini unnotebook <chat>).', { chats: inside });
        await goto(r.notebooksUrl);
        const card = await cardOf(cfg.group);
        if (!card) return fail('no notebook card named "' + cfg.group + '"');
        await openMenu(card.locator(r.notebookMenu).first());
        const del = items().and(page.locator('[data-test-id="' + r.deleteButton + '"]')).first();
        if (!await del.count()) { await escape(); return fail('no "Delete" item'); }
        await click(del);
        const dlg = page.locator('mat-dialog-container').filter({ visible: true }).filter({ hasText: r.deleteConfirmText }).last();
        if (!await dlg.waitFor({ timeout: 6000 }).then(() => true, () => false)) { await escape(); return fail('unexpected delete confirmation; nothing deleted'); }
        await click(dlg.getByRole('button', { name: r.deleteConfirm, exact: true }));
        const v = await until(async () => !(await page.$$eval(r.notebookCard, (cs, n) => cs.some(c => c.innerText.split('\n').map(s => s.trim()).includes(n)), cfg.group)));
        return done({ ok: !!v, group: cfg.group, changed: true, ...(v ? {} : { reason: 'notebook is still listed' }) });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    if (blocked()) return JSON.stringify({ loggedIn: true, ok: false, blocked: true, reason: BLOCKED });
    await escape().catch(() => {});
    return fail(String(e && e.message || e).split('\n')[0], e && e.extra);
  }
}"""
