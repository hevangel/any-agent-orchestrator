"""Drive Google Flow (flow.google.com, AI studio for video and images) with playwright-cli. No LLM.

    uv run aao flow projects                              # every project: id, title, URL
    uv run aao flow new [--title "..."]                   # create an empty project (free)
    uv run aao flow read <project> [--type video|image|...]   # media in a project: index, type, name, thumbnail
    uv run aao flow download <project> <media> [--to DIR] [--quality original|gif|1080p|4k]
    uv run aao flow options [--project P]                 # the agent's settings and every choice they offer
    uv run aao flow upload <project> <file>...            # add reference images/videos to the project (free)
    uv run aao flow new-character <project> --name NAME (--prompt TEXT | --image FILE)
    uv run aao flow delete <project> --yes                # permanent: clips, ingredients and prompts go

<project> is a project id (uuid), its URL or its exact title. <media> is the 1-based index shown by
`read` (newest first) or the exact media name. Flow is signed in with the Google account of the
`gemini` login, so it shares that profile and browser session (`aao web login gemini`).

Credits: reading, creating, renaming, downloading at the original size and deleting cost nothing.
Use ``aao web image flow`` or ``aao web video flow`` for direct generation. Generation and
upscaled downloads can spend Google AI credits. Upscales require an explicit --quality.
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
    "original": {"menu": r"\bOriginal size\b", "label": "Original size", "free": True, "direct": True},
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
    const updates = page.getByRole('dialog', { name: "What's new", exact: true });
    if (!await updates.isVisible()) return;
    const start = updates.getByRole('button', { name: 'Get started', exact: true });
    const next = updates.frameLocator('iframe').getByRole('button', { name: 'Next', exact: true });
    for (let i = 0; i < 8 && !(await start.isEnabled()); i++) {
      await next.click({ timeout: 3000 });
    }
    await start.click({ timeout: 3000 });
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
  const openCharacterBuilder = async project => {
    const id = await resolveProject(project);
    await goto(r.projectUrl.replace('{id}', id) + '/character',
      'button[aria-label="Start generation"], button:has-text("Add from project")');
  };
  const finishCharacter = async () => {
    const editorPath = /\/character\/([0-9a-f-]{36})(?:[/?#]|$)/i;
    const deadline = Date.now() + cfg.timeoutMs;
    await page.waitForURL(editorPath, { timeout: cfg.timeoutMs });
    const url = page.url();
    if (cfg.prompt) {
      await page.getByRole('img', { name: 'Generated character image', exact: true })
        .first().waitFor({ timeout: Math.max(1, deadline - Date.now()) });
    }
    const name = page.getByRole('textbox', { name: 'Character name', exact: true });
    await name.fill(cfg.name, { timeout: 15000 });
    await name.press('Enter');
    if (cfg.info) {
      await page.getByRole('textbox', { name: /^(Character personality|Character info)$/i }).fill(cfg.info);
    }
    if (cfg.voice) {
      await page.getByRole('button', { name: 'Select a voice', exact: true }).click();
      const voices = page.getByRole('listbox', { name: 'Asset list', exact: true });
      const options = voices.getByRole('option');
      await options.first().waitFor({ timeout: 10000 });
      const escaped = cfg.voice.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const voice = voices.getByRole('option', { name: new RegExp('^' + escaped + '(?:\\s|$)', 'i') });
      if (await voice.count() !== 1) {
        const choices = (await options.allInnerTexts()).map(label => label.trim());
        throw Object.assign(new Error('Flow could not match one preset voice named "' + cfg.voice + '"'), { choices });
      }
      await voice.click();
      await page.getByRole('button', { name: 'Add to character', exact: true }).click();
      await voices.waitFor({ state: 'hidden', timeout: 10000 });
    }
    await page.getByRole('button', { name: /^Done( editing)?$/i }).click();
    await page.waitForURL(u => !editorPath.test(u.pathname), { timeout: 15000 });
    return out({ ok: true, character: { id: url.match(editorPath)[1], name: cfg.name, url } });
  };
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
  const createProject = async title => {
    await homeReady();
    await page.getByRole('button', { name: /New project/ }).click();
    await page.waitForURL(/\/project\//, { timeout: 20000 });
    await page.locator('button[aria-label="Add media menu"], button:has-text("Add ingredients")').first().waitFor({ timeout: 30000 });
    await dismiss();
    if (title) {
      const textbox = page.getByRole('textbox', { name: 'Editable text' });
      await textbox.fill(title);
      await textbox.press('Enter');
    }
  };
  const pickTile = async ref => {
    const all = await tiles();
    let hit = /^\d+$/.test(String(ref)) ? all.filter(t => t.index === Number(ref)) : all.filter(t => t.name === ref);
    if (hit.length !== 1) throw Object.assign(new Error(hit.length ? 'several media are named "' + ref + '"; use the index' : 'no media "' + ref + '"'), { choices: all.map(t => t.index + '  ' + t.type + '  ' + t.name) });
    return page.locator(r.tile).nth(hit[0].index - 1);
  };
  const downloadTile = async (tile, quality, timeout) => {
    await tile.hover();
    await tile.getByRole('button', { name: 'More options', exact: true }).click();
    const download = page.getByRole('menuitem', { name: 'Download', exact: true });
    const choice = page.getByRole('menuitem', { name: new RegExp(quality.menu, 'i') }).first();
    const saved = page.waitForEvent('download', { timeout });
    const select = async () => {
      await download.click();
      const submenu = await Promise.race([
        saved.then(() => false),
        choice.waitFor({ timeout: Math.min(8000, timeout) }).then(() => true, () => {
          throw new Error('No ' + quality.label + ' download for this media');
        }),
      ]);
      if (submenu) await choice.click();
      else if (!quality.direct) throw new Error('No ' + quality.label + ' download for this media');
    };
    const [file] = await Promise.all([saved, select()]);
    const path = cfg.to.replace(/\/$/, '') + '/' + file.suggestedFilename();
    await file.saveAs(path);
    return { path, file: file.suggestedFilename(), quality: quality.label };
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
        await createProject(cfg.title);
        return out({ ok: true, ...(await projectInfo()) });
      }
      case 'character-generate': {
        await openCharacterBuilder(cfg.project);
        await page.locator('.ProseMirror:visible').fill(cfg.prompt, { timeout: 15000 });
        await page.getByRole('button', { name: /^(Start generation|Generate)$/i }).click();
        return await finishCharacter();
      }
      case 'character-upload-open': {
        await openCharacterBuilder(cfg.project);
        await page.getByRole('button', { name: 'Upload', exact: true }).click();
        return out({ ok: true });
      }
      case 'character-finish': return await finishCharacter();
      case 'generate-prepare': {
        await createProject(cfg.title);
        const composer = page.getByRole('textbox', { name: 'What do you want to create?', exact: true });
        await composer.waitFor({ timeout: 30000 });
        const agent = page.getByRole('button', { name: 'Flow Agent', exact: true });
        if (await agent.getAttribute('aria-pressed') === 'true') await agent.click();
        if (await agent.getAttribute('aria-pressed') !== 'false') return fail('Flow is not in direct generation mode');
        const settings = page.getByRole('button', { name: 'Settings trigger', exact: true });
        await settings.click();
        const mode = { name: cfg.kind === 'image' ? 'Image' : 'Video', exact: true };
        const one = { name: 'x1', exact: true };
        await page.getByRole('radio', mode).click();
        await page.getByRole('radio', { ...mode, checked: true }).waitFor({ timeout: 10000 });
        if (cfg.kind === 'video' && cfg.references) {
          const ingredients = { name: 'Ingredients', exact: true };
          await page.getByRole('radio', ingredients).click();
          await page.getByRole('radio', { ...ingredients, checked: true }).waitFor({ timeout: 10000 });
        }
        await page.getByRole('radio', one).click();
        await page.getByRole('radio', { ...one, checked: true }).waitFor({ timeout: 10000 });
        if (cfg.aspectRatio) {
          const ratio = { name: cfg.aspectRatio, exact: true };
          await page.getByRole('radio', ratio).click();
          await page.getByRole('radio', { ...ratio, checked: true }).waitFor({ timeout: 10000 });
        }
        await settings.click();
        await composer.fill(cfg.prompt);
        return out({ ok: true, url: page.url() });
      }
      case 'generate-reference-open': {
        const images = page.locator(r.tile).filter({ has: page.locator('flow-image-tile img') });
        const before = await images.count();
        await page.getByRole('button', { name: r.addMenu, exact: true }).click();
        await page.getByRole('menuitem', { name: 'Upload', exact: true }).click();
        return out({ ok: true, before });
      }
      case 'generate-reference-add': {
        const images = page.locator(r.tile).filter({ has: page.locator('flow-image-tile img') });
        await images.nth(cfg.before).waitFor({ timeout: 30000 });
        const ingredient = images.first();
        const attached = page.getByRole('button', { name: 'Ingredient', exact: true });
        const previous = await attached.count();
        await ingredient.hover();
        await ingredient.getByRole('button', { name: 'More options', exact: true }).click();
        await page.getByRole('menuitem', { name: 'Add to prompt', exact: true }).click();
        if (cfg.kind === 'video') await attached.nth(previous).waitFor({ timeout: 10000 });
        return out({ ok: true });
      }
      case 'generate': {
        const outputTiles = page.locator(r.tile).filter({ has: page.locator('flow-' + cfg.kind + '-tile img') });
        const before = await outputTiles.count();
        await page.getByRole('button', { name: 'Start generation', exact: true }).click();
        await outputTiles.nth(before).waitFor({ timeout: cfg.timeoutMs });
        return out({ ok: true, ...(await downloadTile(outputTiles.first(), cfg.quality, 30000)), url: page.url() });
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
        if (cfg.qualityKey === 'gif' && await tile.locator('flow-image-tile').count()) {
          return fail('Flow offers GIF downloads for videos only');
        }
        return out({ ok: true, ...(await downloadTile(tile, cfg.quality, cfg.timeoutMs)) });
      }
      case 'options': {
        await openProject(await resolveProject(cfg.project));
        const direct = page.getByRole('button', { name: 'Settings trigger', exact: true });
        if (await direct.isVisible()) {
          await direct.click();
          try {
            const family = page.getByRole('button', { name: 'Select model family', exact: true });
            await family.waitFor({ timeout: 5000 });
            const groups = await toggles();
            const current = (await family.innerText()).trim().split('\n')[0];
            await family.click();
            const items = page.getByRole('menuitem').or(page.getByRole('option')).filter({ visible: true });
            await items.first().waitFor({ timeout: 3000 }).catch(() => {});
            const choices = (await items.allTextContents()).map(label => label.trim()).filter(Boolean);
            return out({ ok: true, mode: 'direct', toggles: groups, dropdowns: [{ current, choices }] });
          } finally {
            await page.keyboard.press('Escape');
            if (await page.getByRole('button', { name: 'Select model family', exact: true }).isVisible()) {
              await direct.click();
            }
          }
        }
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
    return fail(String(e && e.message || e).split('\n')[0], { url: page.url(), ...(e?.choices ? { choices: e.choices } : {}) });
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
        web.release(TARGET, settings=settings)


def download(project, media, to="temp/flow", quality="original", settings=None, timeout_s=180):
    """Save one media to a folder. Upscaled qualities spend credits, so they must be named explicitly."""
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {', '.join(QUALITIES)}")
    folder = Path(to).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return session("download", settings=settings, timeout_s=timeout_s, project=project, media=str(media),
                   to=folder.as_posix(), quality=QUALITIES[quality], qualityKey=quality)


def create_character(project, name, *, prompt=None, image=None, info=None, voice=None, settings=None, timeout_s=180):
    """Create a reusable Flow character from one generated portrait or local image."""
    name, prompt = name.strip(), (prompt or "").strip()
    if not name or bool(prompt) == bool(image) or timeout_s <= 0:
        return {"ok": False, "reason": "provide a name, exactly one non-empty prompt or image, and a positive timeout"}
    fields = {"name": name, "info": info, "voice": voice}
    if prompt:
        return session("character-generate", settings=settings, timeout_s=timeout_s, project=project,
                       prompt=prompt, **fields)

    path = Path(image).expanduser().resolve()
    if not path.is_file():
        return {"ok": False, "reason": f"no such image: {image}"}
    ok, _, error = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": error}
    try:
        opened = run("character-upload-open", timeout_s=timeout_s, project=project)
        if not opened.get("ok"):
            return {"target": TARGET, **opened}
        ok, output, error = web._pw(TARGET, "upload", path.as_posix(), timeout=timeout_s)
        if not ok:
            return {"target": TARGET, "ok": False, "reason": "character image upload failed",
                    "error": (error or output or "")[-300:]}
        return {"target": TARGET, **run("character-finish", timeout_s=timeout_s, **fields)}
    finally:
        web.release(TARGET, settings=settings)


def generate(kind, prompt, directory, timeout_s=720, *, aspect_ratio=None, references=()):
    """Start one direct generation in a dedicated project and save its original output."""
    if kind not in {"image", "video"}:
        raise ValueError("Flow media kind must be image or video")
    directory = Path(directory).resolve()
    ok, _, error = web.open_target(TARGET)
    if not ok:
        return {"target": TARGET, "ok": False, "error": error}
    try:
        result = run("generate-prepare", kind=kind, prompt=prompt, title=f"AAO {kind} {directory.name[-12:]}",
                     aspectRatio=aspect_ratio, references=bool(references))
        if not result.get("ok"):
            return result
        for path in references:
            result = run("generate-reference-open", timeout_s=60)
            if not result.get("ok"):
                return result
            uploaded, _, error = web._pw(TARGET, "upload", Path(path).resolve().as_posix(), timeout=120)
            if not uploaded:
                return {"ok": False, "reason": error or "Could not upload the Flow reference image"}
            result = run("generate-reference-add", timeout_s=60, before=result["before"], kind=kind)
            if not result.get("ok"):
                return result
        return {"target": TARGET, **run("generate", timeout_s=timeout_s, kind=kind, to=directory.as_posix(),
                                        quality=QUALITIES["original"])}
    finally:
        web.release(TARGET)


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
