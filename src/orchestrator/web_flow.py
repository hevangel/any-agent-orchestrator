"""Drive Google Flow (flow.google.com, AI studio for video and images) with playwright-cli. No LLM.

    uv run aao flow projects                              # every project: id, title, URL
    uv run aao flow new [--title "..."]                   # create an empty project (free)
    uv run aao flow read <project> [--type video|image|...]   # media in a project: index, type, name, thumbnail
    uv run aao flow download <project> <media> [--to DIR] [--quality original|gif|1080p|4k]
    uv run aao flow options [--project P]                 # the agent's settings and every choice they offer
    uv run aao flow upload <project> <file>...            # add reference images/videos to the project (free)
    uv run aao flow delete <project> --yes                # permanent: clips, ingredients and prompts go

<project> is a project id (uuid), its URL or its exact title. <media> is the 1-based index shown by
`read` (newest first) or the exact media name. Flow is signed in with the Google account of the
`gemini` login, so it shares that profile and browser session (`aao web login gemini`).

Credits: reading, creating, renaming, downloading at the original size and deleting cost nothing.
Generating media (the project's agent) and upscaled downloads (1080p, 4K) spend Google AI credits;
this module never generates, and upscales only when asked with --quality (see spec/web_interfaces.md).
"""

import json
from pathlib import Path

from orchestrator import web

TARGET = "flow"

# flow.google.com, studied 2026-11-13. Angular app; custom elements flow-grid-tile-container >
# flow-<type>-tile hold the media. The page of a project has no media ids in the DOM, so a media is
# addressed by its index (newest first) or its name.
RECIPE = {
    "home": "https://flow.google.com/",
    "projectUrl": "https://flow.google.com/project/{id}",
    "projectPath": r"^/project/([0-9a-f][0-9a-f_-]+)",
    "projectLink": "a[href^='/project/']",
    "tile": "flow-grid-tile-container",
    "tileTitle": "button",              # the button whose text is the media's name (videos)
    "tileKinds": {"flow-video-tile": "video", "flow-image-tile": "image"},
    "tileHotbarButtons": ["Favourite", "Reuse prompt", "More options"],
    "dismiss": [{"text": "uses cookies", "button": "No thanks"}],  # cookie banner covers the composer
    "addMenu": "Add media menu",        # top bar "+": Upload | New collection | Create character | New scene
    "settingsButton": "Settings",       # the last visible one is the agent's; the first is the page's
    "toggle": "button[role=radio]",     # aspect ratio and count groups: aria-checked
    "toggleGroup": "mat-button-toggle-group",
    "menuItem": "[role=menuitem]",
    "confirmDialog": "mat-dialog-container, [role=dialog]",
}

# Choices of the agent settings, read from the live page on 2026-11-13 (`aao flow options` reads them again).
OPTIONS = {
    "confirm_before_generating": {
        "Always": "the agent asks before generating media (default)",
        "Never": "the agent generates and spends credits at once",
    },
    "image": {
        "aspect_ratio": ["16:9", "4:3", "1:1", "3:4", "9:16"],
        "count": ["x1", "x2", "x3", "x4"],
        "model": ["Nano Banana Pro", "Nano Banana 2 Lite", "Nano Banana 2.1"],
    },
    "video": {
        "aspect_ratio": ["16:9", "9:16"],
        "count": ["x1", "x2", "x3", "x4"],
        "model": ["Omni 1.2 Flash", "Omni 1.1 Flash", "Veo 3.1 - Lite", "Veo 3.1 - Fast", "Veo 3.1 - Quality"],
    },
    "filters": {
        "type": ["Images", "Videos", "Collections", "Scenes", "Characters"],
        "aspect_ratio": ["Landscape", "Portrait", "Freeform"],
        "created": ["Generated", "Uploaded", "Favourites"],
        "duration_s": "3s .. 15s",
        "resolution": ["720p", "360p"],
        "sort": ["Newest", "Oldest"],
    },
}

# Download submenu of a media's "More options": label -> (cost note, free)
QUALITIES = {
    "gif": {"menu": r"^270p", "label": "Animated GIF (270p)", "free": True},
    "original": {"menu": r"^720p", "label": "Original size (720p)", "free": True},
    "1080p": {"menu": r"^1080p", "label": "Upscaled 1080p (5 credits)", "free": False},
    "4k": {"menu": r"^4K", "label": "Upscaled 4K (credits)", "free": False},
}

SCRIPT = r"""async page => {
  const cfg = __CFG__, r = cfg.recipe;
  const wait = ms => page.waitForTimeout(ms);
  const out = obj => JSON.stringify({ loggedIn: true, ...obj });
  const fail = (reason, extra) => JSON.stringify({ loggedIn: true, ok: false, reason, ...(extra || {}) });
  const dismiss = async () => {
    for (const d of r.dismiss) {
      if (!await page.getByText(d.text).first().isVisible().catch(() => false)) continue;
      await page.getByRole('button', { name: d.button, exact: true }).first().click({ timeout: 3000 }).catch(() => {}); await wait(400);
    }
  };
  const goto = async (url, ready) => {
    if (page.url().split(/[?#]/)[0] !== url) await page.goto(url, { waitUntil: 'domcontentloaded' });
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) throw Object.assign(new Error('login page'), { login: true });
    await page.locator(ready).first().waitFor({ state: 'visible', timeout: 30000 });
    await wait(1500); await dismiss();
  };
  const projectId = () => { const m = page.url().replace(/^https?:\/\/[^/]+/, '').split(/[?#]/)[0].match(new RegExp(r.projectPath)); return m ? m[1] : null; };  // run-code has no URL global: parse by hand
  // the card of one project on the home page: the smallest ancestor of its link that holds no other project link
  const card = id => page.locator('div').filter({ has: page.locator(r.projectLink + '[href="/project/' + id + '"]') })
    .filter({ hasNot: page.locator(r.projectLink + ':not([href="/project/' + id + '"])') }).last();
  const homeReady = () => goto(r.home, 'button:has-text("New project")');
  const projects = async () => {
    await homeReady();
    await page.locator(r.projectLink).first().waitFor({ timeout: 8000 }).catch(() => {});  // a new account has none
    return page.evaluate(sel => {
      const links = [...document.querySelectorAll(sel)];
      return links.map(a => {
        let n = a;
        while (n.parentElement && n.parentElement.querySelectorAll(sel).length < 2 && n.parentElement !== document.body) n = n.parentElement;
        const lines = (n.innerText || '').split('\n').map(s => s.trim()).filter(s => s && !['edit', 'delete'].includes(s));
        const id = a.getAttribute('href').replace('/project/', '');
        return { id, title: lines[0] || '', url: 'https://flow.google.com/project/' + id };
      }).filter((p, i, all) => all.findIndex(q => q.id === p.id) === i);
    }, r.projectLink);
  };
  const resolveProject = async ref => {
    if (!ref) { const first = (await projects())[0]; if (!first) throw new Error('no projects in this account'); return first.id; }
    const m = String(ref).match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
    if (m && !/_backfill/.test(ref)) return m[0];
    const all = await projects();  // old projects have odd ids ("..._backfill"): match the id or the title
    const hit = all.filter(p => p.title === ref || ref.endsWith('/project/' + p.id) || p.id === ref);
    if (hit.length === 1) return hit[0].id;
    throw Object.assign(new Error(hit.length ? 'several projects are titled "' + ref + '"; use the id' : 'no project titled "' + ref + '"'), { choices: all.map(p => p.title + '  ' + p.id) });
  };
  const openProject = async id => goto(r.projectUrl.replace('{id}', id), 'button[aria-label="Add media menu"], button:has-text("Add ingredients")');
  const tiles = () => page.evaluate(({ sel, kinds }) => [...document.querySelectorAll(sel)].map((t, i) => {
    const kindEl = [...t.querySelectorAll('*')].find(e => kinds[e.tagName.toLowerCase()]);
    const img = t.querySelector('img');
    const name = [...t.querySelectorAll('button')].map(b => (b.getAttribute('aria-label') || b.innerText || '').trim())
      .filter(s => s && !['Favourite', 'Reuse prompt', 'More options', 'Expand'].includes(s))[0] || (img && img.alt) || '';
    return { index: i + 1, type: kindEl ? kinds[kindEl.tagName.toLowerCase()] : null, name, thumbnail: img ? img.src : null, selected: false };
  }), { sel: r.tile, kinds: r.tileKinds });
  const projectInfo = async () => {
    // the title is an always-present textbox, an h1 only while it is not being edited
    const title = await page.getByRole('textbox', { name: 'Editable text' }).inputValue({ timeout: 3000 }).catch(() => null)
      || await page.locator('h1').first().innerText({ timeout: 3000 }).catch(() => null);
    await wait(1500);
    return { id: projectId(), url: page.url(), title, media: await tiles() };
  };
  const pickTile = async ref => {
    const all = await tiles();
    let hit = /^\d+$/.test(String(ref)) ? all.filter(t => t.index === Number(ref)) : all.filter(t => t.name === ref);
    if (hit.length !== 1) throw Object.assign(new Error(hit.length ? 'several media are named "' + ref + '"; use the index' : 'no media "' + ref + '"'), { choices: all.map(t => t.index + '  ' + t.type + '  ' + t.name) });
    return page.locator(r.tile).nth(hit[0].index - 1);
  };
  const toggles = () => page.evaluate(({ sel, group }) => [...document.querySelectorAll(group)].map(g => (
    [...g.querySelectorAll(sel)].map(b => ({ label: b.innerText.trim().split('\n').pop(), selected: b.getAttribute('aria-checked') === 'true' })))), { sel: r.toggle, group: r.toggleGroup });
  try {
    switch (cfg.op) {
      case 'status': {
        await homeReady();
        return out({ ok: true, url: page.url() });
      }
      case 'projects': return out({ ok: true, projects: await projects() });
      case 'new': {
        await homeReady();
        await page.getByRole('button', { name: /New project/ }).click();
        for (let i = 0; i < 40 && !projectId(); i++) await wait(500);
        if (!projectId()) return fail('no project URL after "New project"', { url: page.url() });
        await page.locator('button[aria-label="Add media menu"], button:has-text("Add ingredients")').first().waitFor({ timeout: 30000 });
        await wait(1500); await dismiss();
        if (cfg.title) {
          const tb = page.getByRole('textbox', { name: 'Editable text' });
          await tb.fill(cfg.title); await page.keyboard.press('Enter'); await wait(1500);
        }
        return out({ ok: true, ...(await projectInfo()) });
      }
      case 'read': {
        await openProject(await resolveProject(cfg.project));
        await page.locator(r.tile).first().waitFor({ timeout: 6000 }).catch(() => {});  // an empty project has no tiles
        const info = await projectInfo();
        if (cfg.mediaType) info.media = info.media.filter(m => m.type === cfg.mediaType);
        return out({ ok: true, ...info });
      }
      case 'download': {
        await openProject(await resolveProject(cfg.project));
        await page.locator(r.tile).first().waitFor({ timeout: 15000 });
        const tile = await pickTile(cfg.media);
        await tile.hover(); await wait(500);
        await tile.getByRole('button', { name: 'More options' }).click();
        await page.getByRole('menuitem', { name: /Download/ }).click(); await wait(900);
        const choice = page.getByRole('menuitem', { name: new RegExp(cfg.quality.menu) });
        if (!await choice.count()) { await page.keyboard.press('Escape'); return fail('no "' + cfg.quality.label + '" download for this media (images have no GIF)'); }
        const saved = page.waitForEvent('download', { timeout: cfg.timeoutMs });
        await choice.first().click();
        const d = await saved;
        const path = cfg.to.replace(/\/$/, '') + '/' + d.suggestedFilename();
        await d.saveAs(path);
        return out({ ok: true, path, file: d.suggestedFilename(), quality: cfg.quality.label });
      }
      case 'options': {
        await openProject(await resolveProject(cfg.project));
        await page.locator(r.tile).first().waitFor({ timeout: 8000 }).catch(() => {});
        const s = page.getByRole('button', { name: r.settingsButton, exact: true }).filter({ visible: true });
        await s.last().click(); await wait(1200);
        const radios = await page.evaluate(() => [...document.querySelectorAll('mat-radio-button, [role=radio]:not(.mat-button-toggle-button)')]
          .map(e => ({ label: (e.innerText || '').trim().split('\n')[0], detail: (e.innerText || '').trim().split('\n').slice(1).join(' '), selected: (e.querySelector('input') || e).checked === true || e.classList.contains('mdc-radio--selected') || e.getAttribute('aria-checked') === 'true' })));
        const groups = await toggles();
        const find = () => page.evaluate(() => [...document.querySelectorAll('span')]
          .filter(e => e.children.length === 0 && !e.closest('[role=menu]') && /(Nano Banana|Omni|Veo)/.test(e.textContent) && e.getBoundingClientRect().width > 0)
          .map(e => { const b = e.getBoundingClientRect(); return { text: e.textContent.trim(), x: b.x + b.width / 2, y: b.y + b.height / 2 }; }));
        const dropdowns = [];
        for (const t of await find()) {                   // each trigger: current model, then the menu's choices
          await page.mouse.click(t.x, t.y); await wait(900);
          const items = await page.locator(r.menuItem).evaluateAll(es => es.map(e => e.innerText.trim().replace(/\n+/g, ' / ')));
          dropdowns.push({ current: t.text, choices: items.filter(x => /(Nano Banana|Omni|Veo)/.test(x)) });
          // re-pick the current model: it closes the menu and changes nothing
          await page.getByRole('menuitem', { name: t.text.replace(/^\S+\s(?=Nano)/, '') }).first().click().catch(() => {}); await wait(600);
        }
        const close = page.getByRole('button', { name: 'Close', exact: true }).filter({ visible: true });
        await close.last().click().catch(() => {}); // never Save: nothing is changed
        return out({ ok: true, confirm: radios, toggles: groups, dropdowns });
      }
      case 'upload-open': {   // leaves the OS file chooser pending: playwright-cli takes it as a modal, `playwright-cli upload` fills it
        await openProject(await resolveProject(cfg.project));
        await page.locator(r.tile).first().waitFor({ timeout: 6000 }).catch(() => {});
        const before = (await tiles()).length;
        await page.getByRole('button', { name: r.addMenu }).first().click(); await wait(600);
        await page.getByRole('menuitem', { name: /Upload/ }).first().click({ noWaitAfter: true, timeout: 5000 });
        return out({ ok: true, before });
      }
      case 'upload-wait': {
        const want = cfg.before + cfg.count;
        let n = 0;
        for (let i = 0; i < cfg.timeoutMs / 1000; i++) { n = (await tiles()).length; if (n >= want) break; await wait(1000); }
        await wait(1500);
        const all = await tiles();
        if (all.length < want) return fail('the upload did not finish: ' + (all.length - cfg.before) + ' of ' + cfg.count + ' media appeared', { media: all });
        return out({ ok: true, uploaded: cfg.count, media: all });
      }
      case 'delete': {
        const id = await resolveProject(cfg.project);
        await homeReady();
        const c = card(id);
        if (!await c.count()) return fail('project not on the home page: ' + id);
        await c.hover();
        await c.getByRole('button', { name: 'Delete project' }).click(); await wait(900);
        const dlg = page.locator(r.confirmDialog).filter({ visible: true }).last();
        await dlg.getByRole('button', { name: 'Delete project' }).click(); await wait(2500);
        const gone = await page.locator(r.projectLink + '[href="/project/' + id + '"]').count() === 0;
        return gone ? out({ ok: true, deleted: id }) : fail('project is still listed after deleting', { id });
      }
      default: return fail('unknown op ' + cfg.op);
    }
  } catch (e) {
    if (e && e.login) return JSON.stringify({ loggedIn: false, reason: 'login page' });
    return fail(String(e && e.message || e).split('\n')[0], e && e.choices ? { choices: e.choices } : {});
  }
}"""


def run(op, timeout_s=180, **args):
    """One operation in the already open flow session (web.open_target)."""
    payload = {"recipe": RECIPE, "op": op, "loginUrl": web.LOGIN_URL, "timeoutMs": timeout_s * 1000, **args}
    return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(payload)), timeout_s + 120)


def session(op, settings=None, timeout_s=180, **args):
    """Open the browser (the gemini login's), run one operation, release it."""
    ok, _, err = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": err}
    try:
        return {"target": TARGET, **run(op, timeout_s=timeout_s, **args)}
    finally:
        web.release(TARGET)


def download(project, media, to="temp/flow", quality="original", settings=None):
    """Save one media to a folder. Upscaled qualities spend credits, so they must be named explicitly."""
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {', '.join(QUALITIES)}")
    folder = Path(to).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return session("download", settings=settings, project=project, media=str(media),
                   to=folder.as_posix(), quality=QUALITIES[quality])


def upload(project, files, settings=None, timeout_s=180):
    """Upload local images (or videos) into a project's library, where the agent can use them as
    ingredients / references. Free; the new media show in `read` (newest first).
    Flow's Upload item opens an OS file chooser, which playwright-cli reports as a modal state and
    fills with its own `upload` command, so this is three steps: open the chooser, hand it the
    files, wait for the new tiles."""
    paths = []
    for f in files:
        path = Path(f).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {f}")
        paths.append(path.as_posix())
    ok, _, err = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": err}
    try:
        result = {"ok": True}
        for path in paths:  # the chooser takes one file per `playwright-cli upload`
            opened = run("upload-open", timeout_s=60, project=project)
            if not opened.get("ok"):
                return {"target": TARGET, **opened}
            ok, out, err = web._pw(TARGET, "upload", path, timeout=120)
            if not ok:
                return {"target": TARGET, "ok": False, "loggedIn": True, "reason": "file chooser was not filled",
                        "error": (err or out)[-300:]}
            result = run("upload-wait", timeout_s=timeout_s, before=opened["before"], count=1)
            if not result.get("ok"):
                return {"target": TARGET, **result}
        result["uploaded"] = len(paths)
        return {"target": TARGET, **result}
    finally:
        web.release(TARGET)
