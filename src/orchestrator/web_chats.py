"""Manage a chat web app's chat list with playwright-cli: list, search, pin, groups. No LLM.

    uv run aao chats claude list [--group NAME]     # sidebar sections: Pinned, your groups, Recents
    uv run aao chats claude groups                  # group names and chat counts
    uv run aao chats claude search "query" [--kind Code]
    uv run aao chats claude pin|unpin <chat>
    uv run aao chats claude move <chat> <group> [--create]
    uv run aao chats claude ungroup <chat>
    uv run aao chats claude new-group <name> [--chat <chat>]
    uv run aao chats claude delete-group <name> --yes

    uv run aao chats gemini list [--status S]|notebooks|search|read|pin|move <chat> <notebook>|unnotebook|new-notebook|delete-notebook
    uv run aao chats grok list|projects|search|pin|move <chat> <project>|unproject|new-project|delete-project
    uv run aao chats chatgpt list|projects|move <chat> <project>|unproject|new-project|delete-project
                                                    # ChatGPT: groups are projects (web_chats_chatgpt.py)

<chat> is a chat id, a chat URL or an exact title (looked up in the sidebar, then in search).
Every action is a fixed sequence of selector checks (RECIPES, studied by hand; see
spec/web_interfaces.md) and is verified by reading the sidebar again afterwards.
"""

import json
import re

from orchestrator import web, web_chats_chatgpt, web_chats_gemini, web_chats_grok

# claude.ai, studied 2026-11-12. The sidebar is a flat list of rows keyed by data-row-key:
# "label:<id>" starts a section (pinned, recents, custom-cg-<uuid> for a group) and the
# "chat:<uuid>" rows after it belong to that section. Claude calls groups "sections".
_CLAUDE = {
        "keyAttr": "data-row-key",
        "sectionPrefix": "label:",
        "ignore": ["label:viewall", "label:hint:"],  # "View all" link, "Nothing here yet" in an empty group
        "builtin": {"pinned": "pinned", "recents": "recents"},
        "sectionName": "[data-group-name]",
        "rowLink": "a[href]",
        "rowLabel": "[data-row-label]",
        "rowMeta": "[data-find-omitted]",  # status / detail spans inside a row's label (Code sessions)
        "rowStatus": "[role=status]",
        "rowPr": "[data-pr-glyph]", "rowPrAttr": "data-pr-glyph",  # merged / open / ... (Code sessions)
        "menuButton": "button[aria-label^='More options for ']",
        # a section's menu: "More options for <name>" (chats) or "Group and sort" (Code tab). Code-tab
        # section rows also hold a "New session in …, added to <name>" button: never press that one.
        "sectionMenuButton": "button[aria-label^='More options for '], button[aria-label='Group and sort']",
        "sectionTitle": "button[data-group-toggle]",
        "pageMenuButton": "main button[aria-label^='More options for ']",
        "origin": "https://claude.ai",
        # site prompts answered with a fixed button (never a permission grant)
        "dismiss": [{"text": "Enable notifications", "button": "Not now"},
                    {"text": "open in the Claude desktop app", "button": "Keep in this browser"}],
        "pin": "Pin", "unpin": "Unpin",
        "moveMenu": "Move to section",
        "removeFrom": "Remove from ",
        "newSection": "New section",
        "deleteSection": "Delete section",
        "renameInput": "input[aria-label='Rename section']",
        "createSection": "Create section",  # Code tab: "New section" is a dialog with this button
        # section menus that offer "New section" (creates an empty group), tried in order
        "newSectionFrom": [["pinned", "button[aria-label^='More options for ']"],
                           ["recents", "button[aria-label='Filter and group recents'], button[aria-label='Group and sort']"]],
        "searchButton": "Search",
        "searchInput": "#command-palette-input",
        "searchResult": "[role=option][data-item-type]",
        "searchIdPrefix": "command-palette-item-",
        "searchSkipTypes": ["action"],  # palette commands such as "Show archived", not content
        "searchFilters": ["All", "Artifacts", "Sessions", "Code", "Projects", "Scheduled"],
}

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SESSION_ID = r"session_[A-Za-z0-9]+"

# The sidebar keeps one pane per mode: [data-kind=cowork] (chats) and [data-kind=code] (Code
# sessions); the other mode's pane stays in the DOM with display:none once visited, so every
# selector is scoped to one pane and the page must be in that mode (`path`).
RECIPES = {
    "claude": {**_CLAUDE, "row": "aside [data-kind=cowork] [data-row-key]", "itemType": "chat",
               "idPattern": UUID, "chatUrl": "https://claude.ai/chat/{id}",
               "home": "/new", "path": r"^/(?!code)", "searchResolve": True},
    # claude.ai/code, studied 2026-11-12: rows "code:session_<id>", same menus as chats (plus Open PR,
    # Continue in project); a row's link holds a [role=status] span ("Something went wrong") and a
    # location span ("Cloud", "sidonia · Connected", "Remote Control · Offline").
    "claude-code-web": {**_CLAUDE, "row": "aside [data-kind=code] [data-row-key]", "itemType": "code",
                        "idPattern": SESSION_ID, "chatUrl": "https://claude.ai/code/{id}",
                        "home": "/code", "path": r"^/code", "searchResolve": False},
    # chatgpt.com: groups are projects; its own script (web_chats_chatgpt.py)
    "chatgpt": web_chats_chatgpt.RECIPE,
    # grok.com: groups are projects; reads through the site's own endpoints (web_chats_grok.py)
    "grok": web_chats_grok.RECIPE,
    # gemini.google.com: groups are notebooks (web_chats_gemini.py)
    "gemini": web_chats_gemini.RECIPE,
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const done = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });

  // (run-code's sandbox has no URL global: parse page.url() by hand; URL is fine inside page.evaluate)
  const here = () => page.url().startsWith(r.origin + '/') && new RegExp(r.path).test(page.url().slice(r.origin.length).split(/[?#]/)[0]);
  if (!here()) await page.goto(r.origin + r.home, { waitUntil: 'domcontentloaded' });
  if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return JSON.stringify({ loggedIn: false, reason: 'login page' });
  try { await page.locator(r.row).first().waitFor({ state: 'visible', timeout: 20000 }); }
  catch { return JSON.stringify({ loggedIn: false, reason: 'no sidebar (logged out, sidebar hidden or page changed)' }); }
  await wait(600);
  // known site prompts (e.g. "Enable notifications") block every click: answer them with their fixed button
  for (const d of r.dismiss || []) {  // dialogs and toasts: known text on screen -> its fixed button
    if (!await page.getByText(d.text).first().isVisible().catch(() => false)) continue;
    await page.getByRole('button', { name: d.button, exact: true }).first().click({ timeout: 3000 }).catch(() => {}); await wait(500);
  }

  // ---- reading
  const sidebar = () => page.evaluate(r => {
    // The Code pane's Pinned heading has no label row: rows before the first label are pinned.
    const pinned = { id: 'pinned', name: 'Pinned', kind: 'pinned', collapsed: false, items: [] };
    const sections = []; let cur = pinned;
    for (const el of document.querySelectorAll(r.row)) {
      const key = el.getAttribute(r.keyAttr) || '';
      if (r.ignore.some(p => key.startsWith(p))) continue;
      if (key.startsWith(r.sectionPrefix)) {
        const id = key.slice(r.sectionPrefix.length);
        const name = (el.querySelector(r.sectionName) || el).textContent.replace(/[\uE000-\uF8FF]/g, '').trim();
        const toggle = el.querySelector('[aria-expanded]');
        if (id === 'pinned') { cur = pinned; sections.push(pinned); continue; }  // chats pane: a real label row
        if (!sections.includes(pinned)) sections.push(pinned);
        cur = { id, name, kind: r.builtin[id] || 'group',
                collapsed: toggle ? toggle.getAttribute('aria-expanded') === 'false' : false, items: [] };
        sections.push(cur);
        continue;
      }
      const a = el.querySelector(r.rowLink);
      if (!cur || !a) continue;
      const colon = key.indexOf(':');
      const href = new URL(a.getAttribute('href'), location.origin);
      const glyphless = s => (s || '').replace(/[\uE000-\uF8FF]/g, '').replace(/\s+/g, ' ').trim();
      // title: the row label without its status/detail spans (PR count badges sit outside the label)
      const label = el.querySelector(r.rowLabel) || a;
      const copy = label.cloneNode(true);
      copy.querySelectorAll(r.rowMeta).forEach(e => e.remove());
      const status = glyphless((el.querySelector(r.rowStatus) || {}).textContent);
      // detail, e.g. "Cloud \u00B7 Merged \u00B7 2 pull requests" or "sidonia \u00B7 Connected"
      const detail = [...label.querySelectorAll(r.rowMeta)].map(e => glyphless(e.textContent)).filter(t => t && t !== status).join(' \u00B7 ');
      const pr = el.querySelector(r.rowPr);
      const envGlyph = el.querySelector('[data-env-glyph]');  // cloud | bridge (Remote Control machine)
      cur.items.push({ id: key.slice(colon + 1), type: key.slice(0, colon), title: glyphless(copy.textContent),
                       url: href.origin + href.pathname,
                       ...(status ? { status } : {}), ...(detail ? { detail } : {}),
                       ...(pr ? { pr: pr.getAttribute(r.rowPrAttr) } : {}),
                       ...(envGlyph ? { env: envGlyph.getAttribute('data-env-glyph') } : {}) });
    }
    if (!sections.includes(pinned) && pinned.items.length) sections.unshift(pinned);
    return sections;
  }, r);
  const findSection = (sections, name) => sections.filter(s => s.kind === 'group' && s.name === name);
  const sectionOf = (sections, id) => (sections.find(s => s.items.some(c => c.id === id)) || {});
  const isPinned = (sections, id) => sections.some(s => s.kind === 'pinned' && s.items.some(c => c.id === id));

  const search = async (query, kind) => {
    const box = page.locator(r.searchInput);
    if (!await box.isVisible().catch(() => false)) {
      await page.locator('aside').getByRole('button', { name: r.searchButton, exact: true }).first().click();
      await box.waitFor({ timeout: 5000 });
    }
    if (kind) {
      const f = page.getByRole('dialog').getByText(kind, { exact: true }).first();
      if (!await f.isVisible().catch(() => false)) return { error: 'unknown search filter ' + kind + ' (have ' + r.searchFilters.join(', ') + ')' };
      await f.click(); await wait(300);
      await box.focus();
    }
    await box.fill(query);
    // results settle: same list on two polls 600 ms apart (at most ~10 s)
    let prev = null, results = [];
    for (let i = 0; i < 16; i++) {
      await wait(600);
      results = await page.locator(r.searchResult).evaluateAll((es, r) => es.map(e => {
        const id = (e.id || '').startsWith(r.searchIdPrefix) ? e.id.slice(r.searchIdPrefix.length) : e.id;
        const type = e.getAttribute('data-item-type');
        // icons are private-use glyphs of an icon font; drop them before reading the text
        const lines = (e.innerText || '').replace(/[\uE000-\uF8FF]/g, '').split('\n').map(s => s.trim()).filter(Boolean);
        return { id, type, title: lines[0] || '', detail: lines.slice(1).join(' ').slice(0, 200),
                 url: type === 'conversation' ? r.chatUrl.replace('{id}', id) : null };
      }).filter(x => !r.searchSkipTypes.includes(x.type)), r);
      const sig = JSON.stringify(results);
      if (i >= 2 && sig === prev) break;
      prev = sig;
    }
    await page.keyboard.press('Escape'); await wait(300);
    return { results };
  };

  // <chat> -> {id, title}: an id, a URL, or an exact (case-insensitive) title in the sidebar, then search
  const resolve = async ref => {
    const m = String(ref).match(new RegExp(r.idPattern, 'i'));
    const sections = await sidebar();
    const all = sections.flatMap(s => s.items).filter(c => c.type === r.itemType);
    if (m) {
      const id = r.itemType === 'chat' ? m[0].toLowerCase() : m[0];  // session ids are case-sensitive
      const hit = all.find(c => c.id === id);
      return { id, title: hit ? hit.title : null, url: r.chatUrl.replace('{id}', id) };
    }
    const want = String(ref).trim().toLowerCase();
    let hits = [...new Map(all.filter(c => c.title.toLowerCase() === want).map(c => [c.id, c])).values()];
    if (!hits.length && r.searchResolve) {
      const s = await search(String(ref).trim());
      hits = (s.results || []).filter(c => c.type === 'conversation' && c.title.toLowerCase() === want);
    }
    if (hits.length === 1) return { id: hits[0].id, title: hits[0].title, url: r.chatUrl.replace('{id}', hits[0].id) };
    return { error: hits.length ? 'several chats are titled "' + ref + '"; use the id' : 'no chat titled "' + ref + '"',
             matches: hits.map(h => ({ id: h.id, title: h.title })) };
  };

  // ---- menus (Base UI: menus open on click; the row buttons only show on hover)
  // Escape only reaches a menu that has focus (a menu opened by a click event does not take it)
  const closeMenus = async () => {
    for (let i = 0; i < 4 && await page.locator('[role=menu]').count(); i++) {
      await page.locator('[role=menu]').last().focus().catch(() => {}); await page.keyboard.press('Escape'); await wait(200);
    }
  };
  const pick = async (role, label, { prefix = false, last = false } = {}) => {
    const loc = page.locator(`[role=menu] [role=${role}]`);
    // item text: first non-empty line without icon glyphs ("\nUnpin\nP" -> "Unpin"; the P is the shortcut)
    const texts = await loc.evaluateAll(es => es.map(e => ((e.innerText || '').replace(/[\uE000-\uF8FF]/g, '')
      .split('\n').map(s => s.trim()).filter(Boolean)[0]) || ''));
    const match = t => prefix ? t.startsWith(label) : t === label;
    const i = last ? texts.findLastIndex(match) : texts.findIndex(match);
    return i < 0 ? null : loc.nth(i);
  };
  // Open a row's "More options" menu with a click event on the button element itself: no hover and
  // no positional click (on a hidden button that lands on the row's link, which hands off to the
  // desktop app). The event toggles, so any open menu is closed first.
  const openRowMenu = async (row, button = r.menuButton) => {
    await closeMenus();
    await row.scrollIntoViewIfNeeded();
    await row.locator(button).first().dispatchEvent('click', {}, { timeout: 5000 });
    await page.locator('[role=menu]').first().waitFor({ timeout: 5000 });
    await page.locator('[role=menu]').last().focus().catch(() => {});  // so Escape and arrow keys reach it
  };
  // The sidebar row is the only reliable menu: the chat page header menu has no "Move to section" and
  // shows "Unpin" for any chat in a section (groups too). A row inside a collapsed section is
  // expanded for the action and collapsed again afterwards (see `recollapse`).
  const recollapse = [];
  const rowOf = async id => {
    const row = page.locator(`${r.row}[${r.keyAttr}="${r.itemType}:${id}"]`).first();
    if (!await row.count()) return null;  // not in the sidebar: older than the Recents list, in no section
    if (!await row.isVisible().catch(() => false)) {
      const sec = sectionOf(await sidebar(), id);
      const toggle = page.locator(`${r.row}[${r.keyAttr}="${r.sectionPrefix}${sec.id}"] [aria-expanded="false"]`).first();
      if (await toggle.count()) { await toggle.click(); recollapse.push(sec.id); await wait(500); }
    }
    return row;
  };
  const openChatMenu = async id => {
    const row = await rowOf(id);
    if (row) return openRowMenu(row);
    await page.goto(r.chatUrl.replace('{id}', id), { waitUntil: 'domcontentloaded' });
    const b = page.locator(r.pageMenuButton).first();
    await b.waitFor({ timeout: 20000 }); await wait(800);
    await b.click();
    await page.locator('[role=menu]').first().waitFor({ timeout: 5000 });
  };
  // a chat outside the sidebar is in no section, so the header's "Pin" is accurate; pinning puts it
  // in the sidebar, where "Move to section" is available
  const pinFromHeader = async id => {
    await openChatMenu(id);
    const p = await pick('menuitem', r.pin);
    if (!p) { await closeMenus(); throw new Error('chat is not in the sidebar and its page menu has no "' + r.pin + '"'); }
    await p.click();
    const v = await until(s => isPinned(s, id));
    if (!v.verified) throw new Error('pinned the chat but it did not appear in the sidebar');
  };
  const openMoveMenu = async id => {
    if (!await rowOf(id)) await pinFromHeader(id);
    await openChatMenu(id);
    const mv = await pick('menuitem', r.moveMenu);
    if (!mv) { await closeMenus(); throw new Error('no "' + r.moveMenu + '" item'); }
    await mv.focus(); await page.keyboard.press('ArrowRight'); await wait(600);
  };
  // chats: an inline "Rename section" input; Code tab: a "New section" dialog with "Create section"
  const nameNewSection = async name => {
    const inline = page.locator(r.renameInput);
    const dialog = page.locator('[role=dialog]').filter({ hasText: r.newSection }).last();
    for (let i = 0; i < 20; i++) {
      if (await inline.isVisible().catch(() => false)) {
        await inline.fill(name); await page.keyboard.press('Enter'); await wait(500); return;
      }
      if (await dialog.isVisible().catch(() => false)) {
        await dialog.getByRole('textbox').first().fill(name);
        await dialog.getByRole('button', { name: r.createSection, exact: true }).click(); await wait(500); return;
      }
      await wait(250);
    }
    throw new Error('no section name box appeared');
  };
  // poll the sidebar until `check(sections)` holds (the server round-trip takes a moment)
  const until = async check => {
    let sections = [];
    for (let i = 0; i < 16; i++) { await wait(500); sections = await sidebar(); if (check(sections)) return { verified: true, sections }; }
    return { verified: false, sections };
  };
  const chatArg = async () => {
    const c = await resolve(cfg.chat);
    if (c.error) throw Object.assign(new Error(c.error), { extra: { matches: c.matches } });
    return c;
  };

  const restore = async () => {  // put collapsed sections back the way the user had them
    for (const id of recollapse) {
      const t = page.locator(`${r.row}[${r.keyAttr}="${r.sectionPrefix}${id}"] [aria-expanded="true"]`).first();
      if (await t.count()) { await t.click().catch(() => {}); await wait(300); }
    }
  };
  try { return await act(); } finally { await restore().catch(() => {}); }

  async function act() {
  await closeMenus();  // a menu left open by an earlier command blocks every hover behind its overlay
  try {
    switch (cfg.op) {
      case 'list': {
        const sections = await sidebar();
        return done({ ok: true, sections });
      }
      case 'search': {
        const s = await search(cfg.query, cfg.kind);
        return s.error ? fail(s.error) : done({ ok: true, query: cfg.query, results: s.results });
      }
      case 'resolve': {
        const c = await resolve(cfg.chat);
        return c.error ? fail(c.error, { matches: c.matches }) : done({ ok: true, ...c });
      }
      case 'pin': case 'unpin': {
        const c = await chatArg();
        const want = cfg.op === 'pin';
        // every chat in a section is in the sidebar, so no row means "in no section": not pinned
        if (!want && !await rowOf(c.id)) return done({ ok: true, chat: c, changed: false, reason: 'not pinned' });
        await openChatMenu(c.id);
        const has = await pick('menuitem', want ? r.pin : r.unpin);
        if (!has) { await closeMenus(); return done({ ok: true, chat: c, changed: false, reason: want ? 'already pinned' : 'not pinned' }); }
        await has.click();
        const v = await until(s => isPinned(s, c.id) === want);
        return done({ ok: v.verified, chat: c, changed: true, ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      case 'move': {
        const c = await chatArg();
        const before = await sidebar();
        const groups = before.filter(s => s.kind === 'group').map(s => s.name);
        if (findSection(before, cfg.group).length > 1) return fail('several groups are named "' + cfg.group + '"');
        await openMoveMenu(c.id);
        const radio = await pick('menuitemradio', cfg.group);
        if (radio) {
          if (await radio.getAttribute('aria-checked') === 'true') { await closeMenus(); return done({ ok: true, chat: c, group: cfg.group, changed: false, reason: 'already in this group' }); }
          await radio.click();
        } else if (cfg.create) {
          await (await pick('menuitem', r.newSection, { last: true })).click();
          await nameNewSection(cfg.group);
        } else {
          await closeMenus();
          return fail('no group named "' + cfg.group + '" (use --create)', { groups });
        }
        const v = await until(s => sectionOf(s, c.id).name === cfg.group);
        return done({ ok: v.verified, chat: c, group: cfg.group, changed: true, created: !radio,
                      ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      case 'ungroup': {
        const c = await chatArg();
        if (sectionOf(await sidebar(), c.id).kind !== 'group') return done({ ok: true, chat: c, changed: false, reason: 'not in a group' });
        await openMoveMenu(c.id);
        const rm = await pick('menuitemradio', r.removeFrom, { prefix: true }) || await pick('menuitem', r.removeFrom, { prefix: true });
        if (!rm) { await closeMenus(); return done({ ok: true, chat: c, changed: false, reason: 'not in a group' }); }
        // item text "Remove from aao-test\n4": the name is the first line, without the trailing count
        const from = (await rm.innerText()).replace(/[-]/g, '').trim().split('\n')[0].slice(r.removeFrom.length).trim();
        await rm.click();
        const v = await until(s => sectionOf(s, c.id).kind !== 'group');
        return done({ ok: v.verified, chat: c, group: from, changed: true, ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      case 'new-group': {
        const before = await sidebar();
        if (findSection(before, cfg.group).length) return done({ ok: true, group: cfg.group, changed: false, reason: 'group already exists' });
        let item = null;
        for (const [label, button] of r.newSectionFrom) {
          const host = page.locator(`${r.row}[${r.keyAttr}="${r.sectionPrefix}${label}"]`).first();
          if (!await host.isVisible().catch(() => false)) continue;
          await closeMenus(); await host.scrollIntoViewIfNeeded();
          await host.locator(button).first().dispatchEvent('click');
          await page.locator('[role=menu]').first().waitFor({ timeout: 5000 });
          if ((item = await pick('menuitem', r.newSection))) break;
          await closeMenus();
        }
        if (!item) return fail('no section menu offers "' + r.newSection + '"');
        await item.click();
        await nameNewSection(cfg.group);
        const v = await until(s => findSection(s, cfg.group).length === 1);
        return done({ ok: v.verified, group: cfg.group, changed: true, ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      case 'delete-group': {
        const before = await sidebar();
        const found = findSection(before, cfg.group);
        if (found.length !== 1) return fail(found.length ? 'several groups are named "' + cfg.group + '"' : 'no group named "' + cfg.group + '"');
        const row = page.locator(`${r.row}[${r.keyAttr}="${r.sectionPrefix}${found[0].id}"]`).first();
        // chats: the section's "More options" menu; Code tab: the right-click menu of the section title
        // (a button, not a link). Only the exact "Delete section" item is ever picked: the same menu
        // has "Archive all (n)" and "Delete all sessions (n)".
        await openRowMenu(row, r.sectionMenuButton);
        let item = await pick('menuitem', r.deleteSection);
        if (!item) {
          await closeMenus();
          await row.locator(r.sectionTitle).first().click({ button: 'right' });
          await page.locator('[role=menu]').first().waitFor({ timeout: 5000 }).catch(() => {});
          item = await pick('menuitem', r.deleteSection);
        }
        if (!item) { await closeMenus(); return fail('no "' + r.deleteSection + '" item'); }
        await item.click(); await wait(800);
        // a confirmation dialog, if the site shows one: only a button named exactly Delete / Delete section
        const dlg = page.locator('[role=alertdialog], [role=dialog]').last();
        if (await dlg.isVisible().catch(() => false)) {
          const confirm = dlg.getByRole('button', { name: /^(Delete|Delete section)$/ }).first();
          if (await confirm.isVisible().catch(() => false)) await confirm.click();
          else { await page.keyboard.press('Escape'); return fail('unexpected confirmation: ' + (await dlg.innerText()).slice(0, 200)); }
        }
        const v = await until(s => !s.some(x => x.id === found[0].id));
        return done({ ok: v.verified, group: cfg.group, moved: found[0].items, changed: true,
                      ...(v.verified ? {} : { reason: 'sidebar did not update' }) });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    await closeMenus().catch(() => {});
    return fail(String(e && e.message || e).split('\n')[0], e && e.extra);
  }
  }
}"""


def _recipe(target):
    if target not in RECIPES:
        raise ValueError(f"chat-list control is not studied for {target!r} (have: {', '.join(RECIPES)})")
    return RECIPES[target]


def run(target, op, timeout_s=120, **args):
    """Run one chat-list operation in the target's already open session (see web.open_target)."""
    recipe = _recipe(target)
    payload = {"recipe": recipe, "op": op, "loginUrl": web.LOGIN_URL, **args}
    script = {"chatgpt": web_chats_chatgpt.SCRIPT, "grok": web_chats_grok.SCRIPT,
              "gemini": web_chats_gemini.SCRIPT}.get(recipe.get("engine"), SCRIPT)
    return web.run_script(target, script.replace("__CFG__", json.dumps(payload)), timeout_s)


def resolve(target, chat):
    """<chat> (id, URL or exact title) -> {"id", "title", "url"} or {"error"}; the session must be open."""
    recipe = _recipe(target)
    match = re.search(recipe["idPattern"], str(chat), re.I)
    if match:  # no page needed for an id or URL
        chat_id = match.group(0).lower() if recipe["itemType"] == "chat" else match.group(0)
        return {"id": chat_id, "url": recipe["chatUrl"].format(id=chat_id)}
    out = run(target, "resolve", chat=chat)
    return out if out.get("ok") else {"error": out.get("reason") or out.get("error"), "matches": out.get("matches")}


def session(target, op, settings=None, **args):
    """Open the target headed/headless per settings, run one operation, close the browser."""
    _recipe(target)
    ok, _, err = web.open_target(target, settings=settings)
    if not ok:
        return {"target": target, "ok": False, "error": err}
    try:
        return {"target": target, **run(target, op, **args)}
    finally:
        web.release(target)
