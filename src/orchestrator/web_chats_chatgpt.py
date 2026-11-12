"""ChatGPT (chatgpt.com) chat list, studied 2026-11-11. Driven through web_chats (`aao chats chatgpt`).

ChatGPT's groups are projects. The sidebar (nav[aria-label="Chat history"]) has sections headed by
[data-app-action-sidebar-section-toggle] buttons: Pinned (pinned chats and pinned projects),
Projects (unpinned projects) and Recents. A chat row is [data-sidebar-cycle-item] (aria-label =
title) with "Pin chat"/"Unpin chat" and "Chat actions" buttons; its link is /c/<id>, or
/g/<project-id>[-slug]/c/<id> for a chat in a project. A project row carries its id, label and
collapsed state in data-app-action-sidebar-project-* attributes and has a "Project actions for
<name>" menu; expanded, it lists its five newest chats and "Show more". A project's home page
(/g/<project-id>/project) lists all its chats.

Things that differ from claude.ai:
- Menus are Radix menus: opened by focusing the button and pressing Enter (no hover, no click).
- A pinned chat leaves Recents (and its project's list) and shows only under Pinned.
- The chat page title menu has "Pin chat" and "Move to project" for a chat outside projects, but
  neither "Move to project" nor "Remove from project" for a chat inside one: those are only on the
  sidebar row, so the row is found first (expanding the project, then "Show more").
- Deleting a project permanently deletes every chat in it ("Delete from Chat and Work"), so
  delete-group refuses a project that still has chats.
"""

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

RECIPE = {
    "engine": "chatgpt", "groupWord": "project",
    "origin": "https://chatgpt.com", "notPath": r"^/(codex|auth)", "itemType": "chat",
    "idPattern": UUID, "projectPattern": r"g-p-[0-9a-f]{32}",
    "chatUrl": "https://chatgpt.com/c/{id}", "projectUrl": "https://chatgpt.com/g/{id}/project",
    "searchResolve": True,
    "modeButton": "button[aria-label^='Switch mode, current mode: ']", "modePrefix": "Switch mode, current mode: ",
    "mode": "ChatGPT",
    # sidebar
    "toggle": "[data-app-action-sidebar-section-toggle]",
    "builtin": {"Pinned": "pinned", "Projects": "projects", "Recents": "recents"},
    "showSidebar": "button[aria-label='Show sidebar'], button[aria-label='Open sidebar']",
    "chatRow": "[data-sidebar-cycle-item]",
    "projectRow": "[data-app-action-sidebar-project-row]",
    "projectIdAttr": "data-app-action-sidebar-project-id",
    "projectLabelAttr": "data-app-action-sidebar-project-label",
    "projectCollapsedAttr": "data-app-action-sidebar-project-collapsed",
    "showMore": "Show more",
    # chat row and chat page
    "pinButton": "Pin chat", "unpinButton": "Unpin chat",
    "chatMenuButton": "button[aria-label='Chat actions']",
    "headerMenuButton": "[data-app-shell-page-header] button[aria-haspopup=menu]",
    "pin": ["Pin", "Pin chat"], "unpin": ["Unpin", "Unpin chat"],
    "moveMenu": "Move to project", "removeFrom": "Remove from project", "newProject": "New project",
    # projects
    "projectMenuButton": "button[aria-label^='Project actions for']",
    "addProjectButton": "button[aria-label='Add new project']",
    "projectNameInput": "input[name='project-name']", "createProject": "Create project",
    "deleteProject": "Delete project", "deleteConfirmText": "Delete this project",
    "deleteConfirm": "Delete from Chat and Work",
    # search
    "searchButton": "button[aria-label='Search']",
    "searchInput": "[role=dialog] input[placeholder^='Search']",
    "searchResult": "[role=dialog] a[role=option][href]",
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const done = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });
  // (run-code's sandbox has no URL global: parse page.url() by hand)
  const pathOf = u => u.slice(r.origin.length).split(/[?#]/)[0];
  const chatIdOf = href => { const m = (href || '').match(new RegExp('/c/(' + r.idPattern + ')', 'i')); return m ? m[1].toLowerCase() : null; };
  const projectOf = href => { const m = (href || '').match(new RegExp('/g/(' + r.projectPattern + ')')); return m ? m[1] : null; };
  const chatUrl = id => r.chatUrl.replace('{id}', id);

  if (!page.url().startsWith(r.origin + '/') || new RegExp(r.notPath).test(pathOf(page.url())))
    await page.goto(r.origin + '/', { waitUntil: 'domcontentloaded' });
  if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return JSON.stringify({ loggedIn: false, reason: 'login page' });
  // ChatGPT and Codex share the page and the sidebar; the mode menu says which one is shown
  const modeBtn = page.locator(r.modeButton).filter({ visible: true }).first();
  await modeBtn.waitFor({ timeout: 20000 }).catch(() => {});
  const mode = async () => ((await modeBtn.getAttribute('aria-label').catch(() => '')) || '').slice(r.modePrefix.length);
  // the menu item's first text line is the mode name ("ChatGPT\nCreate, learn, and explore"); its
  // textContent runs the lines together, so items are matched on innerText
  for (let tries = 0; await modeBtn.count() && tries < 3 && await mode() !== r.mode; tries++) {
    for (let i = 0; i < 3 && await page.locator('[role=menu]').count(); i++) {
      await page.locator('[role=menu]').last().focus().catch(() => {}); await page.keyboard.press('Escape'); await wait(200);
    }
    await modeBtn.focus(); await page.keyboard.press('Enter');
    const items = page.locator('[role=menu] [role=menuitem]');
    if (!await items.first().waitFor({ timeout: 4000 }).then(() => true, () => false)) continue;
    const i = (await items.evaluateAll(es => es.map(e => (e.innerText || '').trim().split('\n')[0]))).indexOf(r.mode);
    if (i < 0) continue;
    await items.nth(i).click({ timeout: 5000 });
    for (let k = 0; k < 20 && await mode() !== r.mode; k++) await wait(300);
    await wait(1000);
  }
  const sidebarReady = () => page.locator(r.toggle).first().isVisible().catch(() => false);
  let ready = false;
  for (let i = 0; i < 40 && !(ready = await sidebarReady()); i++) {
    if (i === 8) {  // sidebar folded away (narrow window): open it
      const s = page.locator(r.showSidebar).filter({ visible: true }).first();
      if (await s.count()) { await s.focus(); await page.keyboard.press('Enter'); }
    }
    await wait(500);
  }
  if (!ready) {
    const guest = await page.getByRole('button', { name: /^log in$/i }).first().isVisible().catch(() => false);
    return JSON.stringify({ loggedIn: false, reason: guest ? 'logged out (Log in button visible)' : 'no sidebar (logged out or page changed)' });
  }
  await wait(500);

  // ---- keyboard only: Radix menus open on Enter; a click on a row would open the chat
  const press = async loc => { await loc.scrollIntoViewIfNeeded().catch(() => {}); await loc.focus(); await page.keyboard.press('Enter'); };
  const closeMenus = async () => {
    for (let i = 0; i < 4 && await page.locator('[role=menu]').count(); i++) {
      await page.locator('[role=menu]').last().focus().catch(() => {}); await page.keyboard.press('Escape'); await wait(200);
    }
  };
  const openMenu = async button => {
    await closeMenus();
    await press(button);
    await page.locator('[role=menu]').first().waitFor({ timeout: 5000 });
  };
  // an item of the innermost open menu whose first text line is one of `labels`
  const menuTexts = () => page.locator('[role=menu]').last().locator('[role=menuitem]')
    .evaluateAll(es => es.map(e => (e.innerText || '').trim().split('\n')[0]));
  const missing = async (label, where) => {
    const extra = { menu: where, items: await menuTexts().catch(() => []) };
    await closeMenus();
    return fail('no "' + label + '" item', extra);
  };
  const pick = async labels => {
    const loc = page.locator('[role=menu]').last().locator('[role=menuitem]');
    const texts = await loc.evaluateAll(es => es.map(e => ((e.innerText || '').split('\n').map(s => s.trim()).filter(Boolean)[0]) || ''));
    const i = texts.findIndex(t => [].concat(labels).includes(t));
    return i < 0 ? null : loc.nth(i);
  };

  // ---- reading
  const sidebar = () => page.evaluate(r => {
    const chatId = href => { const m = (href || '').match(new RegExp('/c/(' + r.idPattern + ')', 'i')); return m ? m[1].toLowerCase() : null; };
    const projectOf = href => { const m = (href || '').match(new RegExp('/g/(' + r.projectPattern + ')')); return m ? m[1] : null; };
    const heads = [], projects = [];
    for (const t of document.querySelectorAll(r.toggle)) {
      // the section: the largest ancestor that holds this toggle and no other
      let root = t;
      while (root.parentElement && root.parentElement.querySelectorAll(r.toggle).length === 1) root = root.parentElement;
      const name = t.innerText.trim();
      const sec = { id: name.toLowerCase(), name, kind: r.builtin[name] || 'other',
                    collapsed: t.getAttribute('aria-expanded') === 'false', items: [] };
      heads.push(sec);
      const byId = {};
      for (const row of root.querySelectorAll(r.projectRow)) {
        const item = row.closest('[role=listitem]');
        const more = !!item && [...item.querySelectorAll('button, [role=button]')].some(b => b.innerText.trim() === r.showMore);
        const p = { id: row.getAttribute(r.projectIdAttr), name: row.getAttribute(r.projectLabelAttr), kind: 'group',
                    pinned: sec.kind === 'pinned', collapsed: row.getAttribute(r.projectCollapsedAttr) === 'true',
                    more, items: [] };
        byId[p.id] = p; projects.push(p);
      }
      for (const row of root.querySelectorAll(r.chatRow)) {
        const a = row.querySelector('a[href]');
        const id = a && chatId(a.getAttribute('href'));
        if (!id) continue;
        const project = projectOf(a.getAttribute('href'));
        const chat = { id, type: 'chat', title: (row.getAttribute('aria-label') || a.innerText || '').trim(),
                       url: r.chatUrl.replace('{id}', id), ...(project ? { project } : {}) };
        // a chat listed under a project sits in the project's list item; a pinned chat has its own
        let owner = null;
        for (let e = row.parentElement; e && e !== root; e = e.parentElement) {
          if (!e.matches('[role=listitem]')) continue;
          const pr = e.querySelector(r.projectRow);
          if (pr) { owner = byId[pr.getAttribute(r.projectIdAttr)]; break; }
        }
        (owner || sec).items.push(chat);
      }
    }
    // Pinned (chats), every project (pinned ones first, as on screen), Recents
    return [...heads.filter(s => s.kind === 'pinned'), ...projects,
            ...heads.filter(s => s.kind !== 'pinned' && s.kind !== 'projects')];
  }, r);
  const projectsNamed = (sections, name) => sections.filter(s => s.kind === 'group' && s.name === name);
  const isPinned = (sections, id) => sections.some(s => s.kind === 'pinned' && s.items.some(c => c.id === id));

  // every chat of a project, from its home page (scrolled until no more load)
  const projectChats = async pid => {
    await page.goto(r.projectUrl.replace('{id}', pid), { waitUntil: 'domcontentloaded' });
    await page.locator('main h1').first().waitFor({ timeout: 20000 }).catch(() => {});
    await wait(2500);
    const read = () => page.locator('main a[href*="/c/"]').evaluateAll((as, r) => as.map(a => {
      const m = (a.getAttribute('href') || '').match(new RegExp('/c/(' + r.idPattern + ')', 'i'));
      const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
      return m && { id: m[1].toLowerCase(), type: 'chat', title: lines[0] || '', detail: lines.slice(1).join(' ').slice(0, 200),
                    url: r.chatUrl.replace('{id}', m[1].toLowerCase()) };
    }).filter(Boolean), r);
    let chats = await read(), same = 0;
    for (let i = 0; i < 30 && chats.length && same < 2; i++) {
      await page.locator('main a[href*="/c/"]').last().scrollIntoViewIfNeeded().catch(() => {});
      await wait(800);
      const next = await read();
      same = next.length === chats.length ? same + 1 : 0;
      chats = next;
    }
    return [...new Map(chats.map(c => [c.id, c])).values()];
  };

  // the project a chat is in: its page URL turns /c/<id> into /g/<project>/c/<id>
  const projectOfChat = async id => {
    await page.goto(chatUrl(id), { waitUntil: 'domcontentloaded' });
    for (let i = 0; i < 12; i++) {
      await wait(500);
      const p = projectOf(page.url());
      if (p || (i >= 5 && await page.locator(r.headerMenuButton).filter({ visible: true }).count())) {
        await page.locator(r.toggle).first().waitFor({ timeout: 15000 }).catch(() => {});
        await wait(1000);
        return p;
      }
    }
    return null;
  };

  const search = async query => {
    await closeMenus();
    const box = page.locator(r.searchInput).filter({ visible: true }).first();
    if (!await box.count()) {
      await press(page.locator(r.searchButton).filter({ visible: true }).first());
      await box.waitFor({ timeout: 5000 });
    }
    await box.fill(query);
    // results settle: same list on two polls 600 ms apart (at most ~10 s)
    let prev = null, results = [];
    for (let i = 0; i < 16; i++) {
      await wait(600);
      results = await page.locator(r.searchResult).evaluateAll((as, r) => as.map(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(new RegExp('/c/(' + r.idPattern + ')', 'i'));
        const p = href.match(new RegExp('/g/(' + r.projectPattern + ')'));
        const lines = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        return m && { id: m[1].toLowerCase(), type: 'chat', title: lines[0] || '', detail: lines.slice(1).join(' ').slice(0, 200),
                      url: r.chatUrl.replace('{id}', m[1].toLowerCase()), ...(p ? { project: p[1] } : {}) };
      }).filter(Boolean), r);
      const sig = JSON.stringify(results);
      if (i >= 2 && sig === prev) break;
      prev = sig;
    }
    await page.locator(r.searchInput).first().focus().catch(() => {});
    await page.keyboard.press('Escape'); await wait(300);
    return { results };
  };

  // <chat> -> {id, title, url}: an id, a URL, or an exact (case-insensitive) title in the sidebar, then search
  const resolve = async ref => {
    const m = String(ref).match(new RegExp(r.idPattern, 'i'));
    const all = (await sidebar()).flatMap(s => s.items);
    if (m) {
      const id = m[0].toLowerCase(), hit = all.find(c => c.id === id);
      return { id, title: hit ? hit.title : null, url: chatUrl(id) };
    }
    const want = String(ref).trim().toLowerCase();
    let hits = [...new Map(all.filter(c => c.title.toLowerCase() === want).map(c => [c.id, c])).values()];
    if (!hits.length) hits = ((await search(String(ref).trim())).results || []).filter(c => c.title.toLowerCase() === want);
    if (hits.length === 1) return { id: hits[0].id, title: hits[0].title, url: chatUrl(hits[0].id) };
    return { error: hits.length ? 'several chats are titled "' + ref + '"; use the id' : 'no chat titled "' + ref + '"',
             matches: hits.map(h => ({ id: h.id, title: h.title })) };
  };
  const chatArg = async () => {
    const c = await resolve(cfg.chat);
    if (c.error) throw Object.assign(new Error(c.error), { extra: { matches: c.matches } });
    return c;
  };

  // ---- the chat's sidebar row: Recents/Pinned, or inside its project (expanded, then "Show more")
  const rowSel = id => `${r.chatRow}:has(a[href*="/c/${id}"])`;
  const recollapse = [];
  const projectRowSel = pid => `${r.projectRow}[${r.projectIdAttr}="${pid}"]`;
  const findRow = async (id, pid) => {
    const row = page.locator(rowSel(id)).first();
    if (await row.count()) return row;
    if (pid === undefined) pid = await projectOfChat(id);
    if (!pid) return null;
    const prow = page.locator(projectRowSel(pid)).first();
    if (!await prow.count()) return null;
    if (await prow.getAttribute(r.projectCollapsedAttr) === 'true') { await press(prow); recollapse.push(pid); await wait(800); }
    for (let i = 0; i < 10; i++) {
      if (await row.count()) return row;
      const more = prow.locator('xpath=ancestor::*[@role="listitem"][1]').getByRole('button', { name: r.showMore, exact: true }).first();
      if (!await more.count()) break;
      await press(more); await wait(1000);
    }
    return await row.count() ? row : null;
  };
  // the row's "Chat actions" menu, else the chat page's title menu (chats outside the sidebar)
  const openChatMenu = async (id, pid) => {
    const row = await findRow(id, pid);
    if (row) { await openMenu(row.locator(r.chatMenuButton).first()); return 'row'; }
    if (chatIdOf(page.url()) !== id) await page.goto(chatUrl(id), { waitUntil: 'domcontentloaded' });
    const b = page.locator(r.headerMenuButton).filter({ visible: true }).first();
    await b.waitFor({ timeout: 20000 }); await wait(500);
    await openMenu(b);
    return 'page';
  };
  const until = async check => {
    let sections = [];
    for (let i = 0; i < 16; i++) { await wait(500); sections = await sidebar(); if (check(sections)) return { verified: true, sections }; }
    return { verified: false, sections };
  };
  const untilProject = async (id, check) => {  // reloads the chat page, so a few tries only
    for (let i = 0; i < 4; i++) { const p = await projectOfChat(id); if (check(p)) return { verified: true, project: p }; await wait(1000); }
    return { verified: false };
  };
  const nameDialog = async name => {
    const input = page.locator(r.projectNameInput).filter({ visible: true }).first();
    await input.waitFor({ timeout: 8000 });
    await input.fill(name);
    await page.locator('[role=dialog]').last().getByRole('button', { name: r.createProject, exact: true }).click();
    await wait(1500);
  };

  const restore = async () => {  // fold back the projects this command expanded
    for (const pid of recollapse) {
      const prow = page.locator(projectRowSel(pid)).first();
      if (await prow.count() && await prow.getAttribute(r.projectCollapsedAttr) === 'false') { await press(prow).catch(() => {}); await wait(300); }
    }
  };
  try { return await act(); } finally { await restore().catch(() => {}); }

  async function act() {
  await closeMenus();
  try {
    switch (cfg.op) {
      case 'list': {
        let sections = await sidebar();
        if (cfg.group) {  // one project, complete, from its home page
          const found = projectsNamed(sections, cfg.group);
          if (found.length > 1) return fail('several projects are named "' + cfg.group + '"');
          if (found.length === 1) {
            const items = await projectChats(found[0].id);
            sections = sections.map(s => s === found[0] ? { ...s, items, more: false, complete: true } : s);
          }
        }
        return done({ ok: true, sections });
      }
      case 'search': {
        const s = await search(cfg.query);
        return done({ ok: true, query: cfg.query, results: s.results });
      }
      case 'resolve': {
        const c = await resolve(cfg.chat);
        return c.error ? fail(c.error, { matches: c.matches }) : done({ ok: true, ...c });
      }
      case 'pin': case 'unpin': {
        const c = await chatArg();
        const want = cfg.op === 'pin';
        if (isPinned(await sidebar(), c.id) === want) return done({ ok: true, chat: c, changed: false, reason: want ? 'already pinned' : 'not pinned' });
        // a pinned chat is always under Pinned, so "not there" already means "not pinned"
        const row = page.locator(rowSel(c.id)).first();
        const direct = row.locator(`button[aria-label="${want ? r.pinButton : r.unpinButton}"]`).first();
        if (await row.count() && await direct.count()) await press(direct);
        else {
          await openChatMenu(c.id);
          const item = await pick(want ? r.pin : r.unpin);
          if (!item) { await closeMenus(); return fail('no "' + (want ? r.pin : r.unpin)[0] + '" item'); }
          await item.click();
        }
        const v = await until(s => isPinned(s, c.id) === want);
        return done({ ok: v.verified, chat: c, changed: true, ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      case 'move': {
        const c = await chatArg();
        const before = await sidebar();
        const groups = before.filter(s => s.kind === 'group').map(s => s.name);
        const found = projectsNamed(before, cfg.group);
        if (found.length > 1) return fail('several projects are named "' + cfg.group + '"');
        if (!found.length && !cfg.create) return fail('no project named "' + cfg.group + '" (use --create)', { groups });
        const cur = await projectOfChat(c.id);
        if (found.length && cur === found[0].id) return done({ ok: true, chat: c, group: cfg.group, changed: false, reason: 'already in this project' });
        const where = await openChatMenu(c.id, cur);
        const mv = await pick(r.moveMenu);
        if (!mv) return missing(r.moveMenu, where);
        await mv.focus(); await page.keyboard.press('ArrowRight'); await wait(800);
        if (found.length) {
          const item = await pick(cfg.group);
          if (!item) { await closeMenus(); return fail('"' + cfg.group + '" is not in the Move to project menu'); }
          await item.click(); await wait(1000);
        } else {
          const item = await pick(r.newProject);
          if (!item) { await closeMenus(); return fail('no "' + r.newProject + '" item'); }
          await item.click();
          await nameDialog(cfg.group);
        }
        const v = await untilProject(c.id, p => !!p && p !== cur);
        const named = await until(s => s.some(x => x.kind === 'group' && x.id === v.project));
        const into = named.sections.find(s => s.kind === 'group' && s.id === v.project);
        const ok = v.verified && (found.length ? v.project === found[0].id : !!into && into.name === cfg.group);
        return done({ ok, chat: c, group: cfg.group, project: v.project || null, changed: true, created: !found.length,
                      ...(ok ? {} : { reason: v.verified ? 'chat is in another project' : 'chat did not move' }) });
      }
      case 'ungroup': {
        const c = await chatArg();
        const cur = await projectOfChat(c.id);
        if (!cur) return done({ ok: true, chat: c, changed: false, reason: 'not in a project' });
        const from = ((await sidebar()).find(s => s.kind === 'group' && s.id === cur) || {}).name || cur;
        const where = await openChatMenu(c.id, cur);
        const rm = await pick(r.removeFrom);
        if (!rm) return missing(r.removeFrom, where);
        await rm.click(); await wait(1000);
        const v = await untilProject(c.id, p => !p);
        return done({ ok: v.verified, chat: c, group: from, changed: true, ...(v.verified ? {} : { reason: 'chat is still in the project' }) });
      }
      case 'new-group': {
        if (projectsNamed(await sidebar(), cfg.group).length) return done({ ok: true, group: cfg.group, changed: false, reason: 'project already exists' });
        await press(page.locator(r.addProjectButton).first());
        await nameDialog(cfg.group);
        const v = await until(s => projectsNamed(s, cfg.group).length === 1);
        const p = projectsNamed(v.sections, cfg.group)[0];
        return done({ ok: v.verified, group: cfg.group, project: p ? p.id : null, changed: true, ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      case 'delete-group': {
        const found = projectsNamed(await sidebar(), cfg.group);
        if (found.length !== 1) return fail(found.length ? 'several projects are named "' + cfg.group + '"' : 'no project named "' + cfg.group + '"');
        // ChatGPT deletes a project's chats with it, permanently: only an empty project is deleted
        const chats = await projectChats(found[0].id);
        if (chats.length) return fail('project "' + cfg.group + '" has ' + chats.length + ' chat(s), and deleting a ChatGPT project '
          + 'permanently deletes its chats; move them out first (aao chats chatgpt unproject <chat>)', { chats });
        await openMenu(page.locator(projectRowSel(found[0].id)).first().locator(r.projectMenuButton).first());
        const del = await pick(r.deleteProject);
        if (!del) { await closeMenus(); return fail('no "' + r.deleteProject + '" item'); }
        await del.click(); await wait(800);
        const dlg = page.locator('[role=dialog], [role=alertdialog]').last();
        const confirm = dlg.getByRole('button', { name: r.deleteConfirm, exact: true }).first();
        if (!(await dlg.innerText().catch(() => '')).includes(r.deleteConfirmText) || !await confirm.isVisible().catch(() => false)) {
          await page.keyboard.press('Escape');
          return fail('unexpected confirmation: ' + (await dlg.innerText().catch(() => '')).slice(0, 200));
        }
        await confirm.click();
        const v = await until(s => !s.some(x => x.kind === 'group' && x.id === found[0].id));
        return done({ ok: v.verified, group: cfg.group, changed: true, ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    await closeMenus().catch(() => {});
    return fail(String(e && e.message || e).split('\n')[0], e && e.extra);
  }
  }
}"""
