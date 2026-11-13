"""Grok (grok.com) chat list, studied 2026-11-12. Driven through web_chats (`aao chats grok`).

Chat and projects only (Grok Bot is not covered). Groups are **projects**.

Reading uses the site's own JSON endpoints, called from the page with the signed-in session (the
sidebar rows are buttons without links or ids):
- GET /rest/app-chat/conversations?pageSize=N[&pageToken=T]: conversationId, title, starred (pinned),
  createTime, modifyTime; nextPageToken. `&workspaceId=<project>` lists one project's chats (the
  `workspaces` field of a chat stays empty, so membership comes from this filter).
  `&searchQuery=<q>` searches message text (not titles), so search adds title matches first.
- GET /rest/workspaces?pageSize=N: projects (workspaceId, name).
Actions go through the UI, never through those endpoints, and are verified by reading them again:
- The chat page's actions menu ([data-testid=conversation-actions-trigger]) has Pin/Unpin, Rename,
  Add to Project (outside a project) or Move to Project + Remove from Project (inside one), Archive.
  The project submenu is a search box ("Search projects") with role=option items. Remove from Project
  asks "Remove this chat from <name>?" (button "Remove from Project").
- New Project (sidebar Projects section) opens a dialog: input "Project name", button "Create".
- A project's sidebar row menu "Actions for <name>" has "Delete project", confirmed in "Are you sure?
  The project's chats stay as standalone chats." (button "Delete project"). Chats are kept.
- Menus are Base UI: focus the button and press Enter; submenus open with ArrowRight. A hidden
  role=menu element is always in the DOM, so only visible menus count. Dialogs are
  position:fixed (no offsetParent). A hidden OneTrust "Privacy Preference Center" dialog is always in
  the DOM, so dialogs are matched by their own text.
"""

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

RECIPE = {
    "engine": "grok", "groupWord": "project",
    "origin": "https://grok.com", "itemType": "chat", "idPattern": UUID,
    "chatUrl": "https://grok.com/c/{id}", "projectUrl": "https://grok.com/project/{id}",
    "searchResolve": True, "pageSize": 60, "maxPages": 5,
    "conversations": "/rest/app-chat/conversations", "workspaces": "/rest/workspaces",
    "sidebar": "[data-reorder-item=recent], [data-reorder-item=projects]",
    "actionsButton": "[data-testid=conversation-actions-trigger]",
    "pin": "Pin", "unpin": "Unpin", "addTo": ["Add to Project", "Move to Project"], "removeFrom": "Remove from Project",
    "removeConfirmText": "Remove this chat from", "projectSearch": "input[aria-label='Search projects']",
    "newProjectButton": "[data-reorder-item=projects] button", "newProjectLabel": "New Project",
    "projectNameInput": "input[aria-label='Project name']", "create": "Create",
    "projectActions": "[data-reorder-item=projects] button[aria-label='Actions for {name}']",
    "deleteProject": "Delete project", "deleteConfirmText": "chats stay as standalone chats",
    "notDialog": "Privacy Preference",
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const done = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });
  const chatUrl = id => r.chatUrl.replace('{id}', id);

  if (!page.url().startsWith(r.origin + '/')) await page.goto(r.origin + '/', { waitUntil: 'domcontentloaded' });
  if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return JSON.stringify({ loggedIn: false, reason: 'login page' });
  // the site's own endpoints, with the page's session
  const api = async (path, params) => {
    const q = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
    return page.evaluate(async u => {
      const res = await fetch(u, { credentials: 'include' });
      return { status: res.status, body: res.ok ? await res.json() : null };
    }, path + (q ? '?' + q : ''));
  };
  const probe = await api(r.workspaces, { pageSize: 1 });
  if (probe.status === 401 || probe.status === 403) return JSON.stringify({ loggedIn: false, reason: 'not signed in (' + probe.status + ')' });
  if (probe.status !== 200) return fail('workspaces endpoint answered ' + probe.status);

  const conversations = async params => {
    const all = [];
    let token = null;
    for (let i = 0; i < r.maxPages; i++) {
      const res = await api(r.conversations, { pageSize: r.pageSize, pageToken: token, ...(params || {}) });
      if (res.status !== 200) throw new Error('conversations endpoint answered ' + res.status);
      all.push(...(res.body.conversations || []));
      token = res.body.nextPageToken;
      if (!token) break;
    }
    return all.map(c => ({ id: c.conversationId, type: 'chat', title: c.title || '', url: chatUrl(c.conversationId),
                           pinned: !!c.starred, updated: c.modifyTime || null }));
  };
  const projects = async () => ((await api(r.workspaces, { pageSize: 100 })).body.workspaces || [])
    .map(w => ({ id: w.workspaceId, name: w.name, url: r.projectUrl.replace('{id}', w.workspaceId) }));
  // sections: Pinned, one per project, Recents (chats in no project and not pinned)
  const sidebar = async () => {
    const chats = await conversations();
    const projs = await projects();
    const inProject = new Set();
    const groups = [];
    for (const p of projs) {
      const items = await conversations({ workspaceId: p.id });
      items.forEach(c => inProject.add(c.id));
      groups.push({ id: p.id, name: p.name, kind: 'group', url: p.url, collapsed: false, items });
    }
    return [{ id: 'pinned', name: 'Pinned', kind: 'pinned', collapsed: false, items: chats.filter(c => c.pinned) },
            ...groups,
            { id: 'recents', name: 'Recents', kind: 'recents', collapsed: false, items: chats.filter(c => !c.pinned && !inProject.has(c.id)) }];
  };
  const projectOf = async id => {
    for (const p of await projects()) if ((await conversations({ workspaceId: p.id })).some(c => c.id === id)) return p;
    return null;
  };
  const projectsNamed = async name => (await projects()).filter(p => p.name === name);

  const resolve = async ref => {
    const m = String(ref || '').match(new RegExp(r.idPattern, 'i'));
    if (m) return { id: m[0].toLowerCase(), url: chatUrl(m[0].toLowerCase()) };
    const want = String(ref || '').trim().toLowerCase();
    let hits = (await conversations()).filter(c => c.title.toLowerCase() === want);
    if (!hits.length) hits = (await conversations({ searchQuery: String(ref).trim() })).filter(c => c.title.toLowerCase() === want);
    if (hits.length === 1) return { id: hits[0].id, title: hits[0].title, url: hits[0].url };
    return { error: hits.length ? 'several chats are titled "' + ref + '"; use the id' : 'no chat titled "' + ref + '"',
             matches: hits.map(h => ({ id: h.id, title: h.title })) };
  };
  const chatArg = async () => {
    const c = await resolve(cfg.chat);
    if (c.error) throw Object.assign(new Error(c.error), { extra: { matches: c.matches } });
    return c;
  };

  // ---- UI (keyboard only). A hidden role=menu element stays in the DOM: only visible menus count.
  const menus = () => page.locator('[role=menu]').filter({ visible: true });
  const press = async loc => { await loc.scrollIntoViewIfNeeded().catch(() => {}); await loc.focus(); await page.keyboard.press('Enter'); };
  const closeMenus = async () => {
    for (let i = 0; i < 5 && await menus().count(); i++) {
      await menus().last().focus().catch(() => {}); await page.keyboard.press('Escape'); await wait(250);
    }
  };
  const pick = async labels => {
    const items = menus().last().locator('[role=menuitem]');
    const texts = await items.evaluateAll(es => es.map(e => (e.innerText || '').trim().split('\n')[0]));
    const i = texts.findIndex(t => [].concat(labels).includes(t));
    return i < 0 ? null : items.nth(i);
  };
  const dialog = text => page.locator('[role=dialog]').filter({ hasNotText: r.notDialog }).filter({ hasText: text }).last();
  // the chat's own page and its actions menu (reaches every chat, also ones not in the sidebar)
  const openChatMenu = async id => {
    await closeMenus();
    if (!page.url().includes(id)) await page.goto(chatUrl(id), { waitUntil: 'domcontentloaded' });
    const b = page.locator(r.actionsButton).first();
    await b.waitFor({ timeout: 20000 }); await wait(800);
    await press(b);
    await menus().first().waitFor({ timeout: 5000 });
  };
  const until = async (check, tries = 12) => {
    for (let i = 0; i < tries; i++) { await wait(700); const v = await check(); if (v) return v; }
    return null;
  };
  const createProject = async name => {
    await closeMenus();
    if (!await page.locator(r.sidebar).first().isVisible().catch(() => false)) await page.goto(r.origin + '/', { waitUntil: 'domcontentloaded' });
    const b = page.locator(r.newProjectButton).filter({ hasText: new RegExp('^' + r.newProjectLabel + '$') }).first();
    await b.waitFor({ timeout: 15000 });
    await press(b);
    const d = page.locator('[role=dialog]').filter({ has: page.locator(r.projectNameInput) }).last();
    await d.locator(r.projectNameInput).fill(name);
    await d.getByRole('button', { name: r.create, exact: true }).click();
    return until(async () => (await projectsNamed(name))[0]);
  };

  await closeMenus();
  try {
    switch (cfg.op) {
      case 'list': {
        let sections = await sidebar();
        if (cfg.group) sections = sections.filter(s => s.name === cfg.group);
        return done({ ok: true, sections });
      }
      case 'search': {
        // the server search matches message text, not titles: title matches first, then its results
        const q = String(cfg.query || '').toLowerCase();
        const byTitle = (await conversations()).filter(c => c.title.toLowerCase().includes(q));
        const byText = await conversations({ searchQuery: cfg.query });
        const results = [...new Map([...byTitle, ...byText].map(c => [c.id, c])).values()];
        return done({ ok: true, query: cfg.query, results });
      }
      case 'resolve': {
        const c = await resolve(cfg.chat);
        return c.error ? fail(c.error, { matches: c.matches }) : done({ ok: true, ...c });
      }
      case 'pin': case 'unpin': {
        const c = await chatArg();
        const want = cfg.op === 'pin';
        const isPinned = async () => (await conversations()).some(x => x.id === c.id && x.pinned);
        if (await isPinned() === want) return done({ ok: true, chat: c, changed: false, reason: want ? 'already pinned' : 'not pinned' });
        await openChatMenu(c.id);
        const item = await pick(want ? r.pin : r.unpin);
        if (!item) { await closeMenus(); return fail('no "' + (want ? r.pin : r.unpin) + '" item'); }
        await press(item);
        const v = await until(async () => await isPinned() === want);
        return done({ ok: !!v, chat: c, changed: true, ...(v ? {} : { reason: 'pin state did not change' }) });
      }
      case 'move': {
        const c = await chatArg();
        const found = await projectsNamed(cfg.group);
        if (found.length > 1) return fail('several projects are named "' + cfg.group + '"');
        let target = found[0];
        const cur = await projectOf(c.id);
        if (target && cur && cur.id === target.id) return done({ ok: true, chat: c, group: cfg.group, changed: false, reason: 'already in this project' });
        if (!target) {
          if (!cfg.create) return fail('no project named "' + cfg.group + '" (use --create)', { groups: (await projects()).map(p => p.name) });
          target = await createProject(cfg.group);
          if (!target) return fail('the new project did not appear');
        }
        await openChatMenu(c.id);
        const add = await pick(r.addTo);
        if (!add) { await closeMenus(); return fail('no "' + r.addTo.join('" / "') + '" item'); }
        await add.focus(); await page.keyboard.press('ArrowRight');
        const box = menus().last().locator(r.projectSearch);
        await box.waitFor({ timeout: 5000 });
        await box.fill(cfg.group); await wait(700);
        const opt = menus().last().locator('[role=option]').filter({ hasText: new RegExp('^' + cfg.group.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$') }).first();
        if (!await opt.count()) { await closeMenus(); return fail('"' + cfg.group + '" is not offered in the project list'); }
        await opt.click();
        await closeMenus();
        const v = await until(async () => (await conversations({ workspaceId: target.id })).some(x => x.id === c.id));
        return done({ ok: !!v, chat: c, group: cfg.group, project: target.id, changed: true, created: !found.length,
                      ...(v ? {} : { reason: 'chat is not in the project' }) });
      }
      case 'ungroup': {
        const c = await chatArg();
        const cur = await projectOf(c.id);
        if (!cur) return done({ ok: true, chat: c, changed: false, reason: 'not in a project' });
        await openChatMenu(c.id);
        const rm = await pick(r.removeFrom);
        if (!rm) { await closeMenus(); return fail('no "' + r.removeFrom + '" item'); }
        await press(rm);
        const d = dialog(r.removeConfirmText);
        await d.waitFor({ timeout: 5000 });
        await d.getByRole('button', { name: r.removeFrom, exact: true }).click();
        const v = await until(async () => !(await conversations({ workspaceId: cur.id })).some(x => x.id === c.id));
        return done({ ok: !!v, chat: c, group: cur.name, changed: true, ...(v ? {} : { reason: 'chat is still in the project' }) });
      }
      case 'new-group': {
        if ((await projectsNamed(cfg.group)).length) return done({ ok: true, group: cfg.group, changed: false, reason: 'project already exists' });
        const p = await createProject(cfg.group);
        return done({ ok: !!p, group: cfg.group, project: p ? p.id : null, changed: true, ...(p ? {} : { reason: 'the new project did not appear' }) });
      }
      case 'delete-group': {
        const found = await projectsNamed(cfg.group);
        if (found.length !== 1) return fail(found.length ? 'several projects are named "' + cfg.group + '"' : 'no project named "' + cfg.group + '"');
        const kept = await conversations({ workspaceId: found[0].id });
        if (!await page.locator(r.sidebar).first().isVisible().catch(() => false)) await page.goto(r.origin + '/', { waitUntil: 'domcontentloaded' });
        const b = page.locator(r.projectActions.replace('{name}', cfg.group.replace(/'/g, "\\'"))).first();
        if (!await b.count()) return fail('project "' + cfg.group + '" is not in the sidebar');
        await press(b);
        await menus().first().waitFor({ timeout: 5000 });
        const del = await pick(r.deleteProject);
        if (!del) { await closeMenus(); return fail('no "' + r.deleteProject + '" item'); }
        await press(del);
        // only the dialog that says the chats are kept is confirmed
        const d = dialog(r.deleteConfirmText);
        if (!await d.waitFor({ timeout: 5000 }).then(() => true, () => false)) {
          await page.keyboard.press('Escape');
          return fail('the delete confirmation did not say the chats are kept; nothing deleted');
        }
        await d.getByRole('button', { name: r.deleteProject, exact: true }).click();
        const v = await until(async () => !(await projects()).some(p => p.id === found[0].id));
        return done({ ok: !!v, group: cfg.group, changed: true, moved: kept, ...(v ? {} : { reason: 'project is still listed' }) });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    await closeMenus().catch(() => {});
    return fail(String(e && e.message || e).split('\n')[0], e && e.extra);
  }
}"""
