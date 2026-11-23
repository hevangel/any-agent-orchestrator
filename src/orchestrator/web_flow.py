"""Drive Google Flow (flow.google.com, AI studio for video and images) with playwright-cli. No LLM.

    uv run aao flow projects                              # every project: id, title, URL
    uv run aao flow new [--title "..."]                   # create an empty project (free)
    uv run aao flow read <project> [--type video|image|...]   # media in a project: index, type, name, thumbnail
    uv run aao flow download <project> <media> [--to DIR] [--quality original|gif|1080p|4k]
    uv run aao flow options [--project P]                 # the agent's settings and every choice they offer
    uv run aao flow upload <project> <file>...            # add reference images/videos to the project (free)
    uv run aao flow new-character <project> --name NAME (--prompt TEXT | --image FILE)
    uv run aao flow characters|read-character|edit-character ...
    uv run aao flow add-to-scene|save-frame|extend ...
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

# flow.google.com, Angular app; custom elements flow-grid-tile-container >
# flow-<type>-tile hold the media. The page of a project has no media ids in the DOM, so a media is
# addressed by its index (newest first) or its name.
RECIPE = {
    "home": "https://flow.google.com/",
    "projectUrl": "https://flow.google.com/project/{id}",
    "projectPath": r"^/project/([0-9a-f][0-9a-f_-]+)",
    "projectLink": "a[href^='/project/']",
    "tile": "flow-grid-tile-container",
    "tileKinds": {"flow-video-tile": "video", "flow-image-tile": "image",
                  "flow-character-tile": "character", "flow-scene-tile": "scene",
                  "flow-collection-tile": "collection"},
    "dismiss": [{"text": "uses cookies", "button": "No thanks"}],  # cookie banner covers the composer
    "addMenu": "Add media menu",        # top bar "+": Upload | New collection | Create character | New scene
    "toggle": "button[role=radio]",     # aspect ratio and count groups: aria-checked
    "toggleGroup": "mat-button-toggle-group",
    "confirmDialog": "mat-dialog-container, [role=dialog]",
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
    await page.goto(url, { waitUntil: 'domcontentloaded' });
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
  const editorPath = /\/character\/([0-9a-f-]{36})(?:[/?#]|$)/i;
  const characterName = () => page.getByRole('textbox', { name: 'Character name', exact: true });
  const characterInfo = () => page.getByRole('textbox', { name: /^Character (personality|info)( \(optional\))?$/i });
  const portrait = () => page.getByRole('img', { name: 'Generated character image', exact: true }).first();
  const voiceButton = () => page.locator('button').filter({ has: page.locator('mat-icon:text-is("voice_selection")') }).first();
  const finishCharacter = async () => {
    const deadline = Date.now() + cfg.timeoutMs;
    await page.waitForURL(editorPath, { timeout: cfg.timeoutMs });
    const url = page.url();
    if (cfg.prompt) {
      await page.getByRole('img', { name: 'Generated character image', exact: true })
        .first().waitFor({ timeout: Math.max(1, deadline - Date.now()) });
    }
    const name = characterName();
    if (cfg.name != null) {
      const edit = button('Edit name');
      if (await edit.isVisible()) await edit.click();
      await name.fill(cfg.name, { timeout: 15000 });
      await name.press('Enter');
    }
    if (cfg.info != null) await characterInfo().fill(cfg.info);
    if (cfg.voice) {
      await button('Select a voice').or(voiceButton()).first().click({ timeout: 10000 });
      const voices = page.getByRole('listbox', { name: 'Asset list', exact: true });
      const options = voices.getByRole('option');
      const search = page.getByRole('textbox', { name: 'Search assets', exact: true });
      await search.fill(cfg.voice);
      const voice = voices.getByRole('option', { name: new RegExp('^' + escaped(cfg.voice) + '(?:\\s|$)', 'i') });
      await voice.first().waitFor({ timeout: 4000 }).catch(() => {});
      if (await voice.count() !== 1) {
        await search.clear();
        await options.first().waitFor({ timeout: 4000 }).catch(() => {});
        const choices = (await options.allInnerTexts()).map(label => label.trim().split('\n')[1] || label.trim());
        throw Object.assign(new Error('Flow could not match one preset voice named "' + cfg.voice + '"'), { choices });
      }
      if (await voice.getAttribute('aria-selected') !== 'true') await voice.click();
      await page.getByRole('button', { name: 'Add to character', exact: true }).click();
      await voices.waitFor({ state: 'hidden', timeout: 10000 });
    }
    const savedName = await name.inputValue();
    await page.getByRole('button', { name: /^Done( editing)?$/i }).click();
    await page.waitForURL(u => !editorPath.test(u.pathname), { timeout: 15000 });
    return out({ ok: true, character: { id: url.match(editorPath)[1], name: savedName, url } });
  };
  const tiles = () => page.evaluate(({ sel, kinds }) => [...document.querySelectorAll(sel)].map((t, i) => {
    const kindEl = [...t.querySelectorAll('*')].find(e => kinds[e.tagName.toLowerCase()]);
    const img = t.querySelector('img');
    const name = [...t.querySelectorAll('button')].map(b => (b.getAttribute('aria-label') || b.innerText || '').trim())
      .filter(s => s && !['Favourite', 'Reuse prompt', 'More options', 'Expand'].includes(s))[0]
      || t.querySelector('.character-tile-name, flow-scene-tile .title')?.textContent.trim() || (img && img.alt) || '';
    return { index: i + 1, type: kindEl ? kinds[kindEl.tagName.toLowerCase()] : null, name, thumbnail: img?.currentSrc || img?.src || null, selected: false };
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
  const closeMenu = () => page.keyboard.press('Escape').catch(() => {});
  const tileMenu = async tile => {
    await tile.hover();
    await tile.getByRole('button', { name: 'More options', exact: true }).click();
  };
  const downloadTile = async (tile, quality, timeout, prefix = '') => {
    try {
      return await saveTile(tile, quality, timeout, prefix);
    } finally {
      await closeMenu();
      if (await page.getByRole('menu').filter({ visible: true }).first().isVisible()) await closeMenu();
    }
  };
  const saveTile = async (tile, quality, timeout, prefix) => {
    await tileMenu(tile);
    const download = page.getByRole('menuitem', { name: 'Download', exact: true });
    await download.waitFor({ timeout: 4000 }).catch(() => { throw new Error('Flow does not offer a download for this media'); });
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
    return saveFile(file, quality.label, prefix);
  };
  const saveFile = async (file, quality, prefix = '') => {
    const path = cfg.to.replace(/\/$/, '') + '/' + prefix + file.suggestedFilename();
    await file.saveAs(path);
    return { path, file: file.suggestedFilename(), quality };
  };
  const toggles = () => page.evaluate(({ sel, group }) => [...document.querySelectorAll(group)].map(g => (
    [...g.querySelectorAll(sel)].map(b => {
      const copy = b.cloneNode(true);
      for (const icon of copy.querySelectorAll('mat-icon')) icon.remove();
      return { label: copy.textContent.trim(), selected: b.getAttribute('aria-checked') === 'true' };
    }))), { sel: r.toggle, group: r.toggleGroup });
  const button = name => page.getByRole('button', { name, exact: typeof name === 'string' });
  const settings = button('Settings trigger');
  const modelFamily = button('Select model family');
  const escaped = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const radio = label => page.getByRole('radio', { name: new RegExp('^' + escaped(label) + '(?:$|\\s)', 'i') });
  const selectRadio = async label => {
    await radio(label).click({ timeout: 5000 }).catch(() => { throw new Error('Flow does not offer this setting: ' + label); });
    await radio(label).and(page.locator('[aria-checked=true]')).waitFor({ timeout: 5000 });
  };
  const directSettings = async action => {
    const agent = button(/^Flow agent$/i);
    const wasAgent = await agent.getAttribute('aria-pressed', { timeout: 5000 }) === 'true';
    if (wasAgent) await agent.click();
    try {
      await settings.click({ timeout: 10000 });
      return await action();
    } finally {
      await closeMenu();
      if (await modelFamily.isVisible()) await settings.click({ timeout: 3000 }).catch(() => {});
      if (cfg.op === 'options' && wasAgent) await agent.click({ timeout: 3000 });
    }
  };
  const modelChoices = () => page.getByRole('menuitem').or(page.getByRole('option')).filter({ visible: true });
  const showCharacters = async project => {
    await openProject(project);
    await page.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Characters', exact: true }).click();
    await page.locator('flow-character-tile').first().waitFor({ timeout: 4000 }).catch(() => {});
  };
  const characterTile = name => page.locator(r.tile).filter({
    has: page.locator('.character-tile-name').and(page.getByText(name, { exact: true })),
  });
  const openCharacter = async (project, ref) => {
    const base = r.projectUrl.replace('{id}', project);
    const id = ref.match(/^(?:https:\/\/flow\.google\.com\/project\/[^/]+\/character\/)?([0-9a-f-]{36})\/?(?:[?#].*)?$/i)?.[1];
    if (id) {
      if (ref.startsWith('https://') && !ref.startsWith(base + '/character/')) throw new Error('Character URL belongs to a different Flow project');
      await goto(base + '/character/' + id, 'input[aria-label="Character name"], button:has-text("Edit name")');
    } else {
      await showCharacters(project);
      const tile = characterTile(ref);
      if (await tile.count() !== 1) throw new Error('Expected one Flow character named "' + ref + '"');
      await tile.locator('flow-character-tile').click();
    }
    await page.waitForURL(editorPath, { timeout: 15000 });
    await characterName().waitFor({ timeout: 15000 });
  };
  const readCharacter = async () => ({
    id: page.url().match(editorPath)[1],
    url: page.url(),
    name: await characterName().inputValue(),
    info: await characterInfo().inputValue(),
    image: await portrait().getAttribute('src', { timeout: 5000 }).catch(() => null),
    voice: await voiceButton().evaluateAll(buttons => {
      const name = buttons[0]?.innerText.trim().split('\n').pop();
      return name && name !== 'Select a voice' ? name : null;
    }),
  });
  const attachFrame = async (slot, ref) => {
    const tile = (await tiles()).find(media => media.index === Number(ref));
    const name = /^\d+$/.test(ref) ? tile?.name : ref;
    if (!name || (tile && tile.type !== 'image' && /^\d+$/.test(ref))) throw new Error('No Flow image for the ' + slot + ' frame: ' + ref);
    await button(slot).click();
    const assets = page.getByRole('listbox', { name: 'Asset list', exact: true });
    const option = assets.getByRole('option', { name, exact: true });
    try {
      await assets.waitFor({ timeout: 5000 });
      if (!await option.count()) await page.getByRole('textbox', { name: 'Search assets', exact: true }).fill(name);
      await option.waitFor({ timeout: 5000 });
      if (await option.getAttribute('aria-selected') !== 'true') await option.click({ timeout: 5000 });
      const add = button('Add to prompt');
      if (await button(slot).isVisible() && await add.isVisible()) await add.click({ timeout: 5000 });
      await button(slot).waitFor({ state: 'hidden', timeout: 5000 });
      await assets.waitFor({ state: 'hidden', timeout: 5000 });
    } catch (error) {
      throw new Error('Could not attach Flow ' + slot.toLowerCase() + ' frame "' + name + '": ' + String(error.message).split('\n')[0]);
    }
  };
  const openTimeline = async (project, media) => {
    await openProject(project);
    await page.locator(r.tile).first().waitFor({ timeout: 15000 });
    const tile = await pickTile(media);
    const video = tile.locator('flow-video-tile .container');
    const scene = tile.locator('flow-scene-tile .tile-container');
    if (await video.count()) await video.click({ position: { x: 30, y: 30 } });
    else if (await scene.count()) await scene.click({ position: { x: 30, y: 30 } });
    else throw new Error('Choose a Flow video or scene');
    await button('Done editing scene').waitFor({ timeout: 15000 });
  };
  const newTiles = async (before, match, count, timeout) => {
    const deadline = Date.now() + timeout;
    let found;
    do {
      const remaining = [...before];
      found = (await tiles()).filter(tile => {
        if (!tile.thumbnail || !match(tile)) return false;
        const index = remaining.findIndex(old => old.name === tile.name && old.thumbnail === tile.thumbnail);
        if (index === -1) return true;
        remaining.splice(index, 1);
        return false;
      });
      if (found.length >= count) return found.slice(0, count);
      await wait(400);
    } while (Date.now() < deadline);
    throw new Error('Timed out waiting for Flow media: ' + found.length + ' of ' + count + ' appeared');
  };
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
      case 'characters': {
        await showCharacters(await resolveProject(cfg.project));
        return out({ ok: true, characters: (await tiles()).filter(tile => tile.type === 'character') });
      }
      case 'read-character':
      case 'edit-character': {
        await openCharacter(await resolveProject(cfg.project), cfg.character);
        if (cfg.op === 'read-character') return out({ ok: true, character: await readCharacter() });
        if (cfg.imagePrompt) {
          const previous = await portrait().getAttribute('src');
          await page.getByRole('textbox', { name: 'What do you want to change?', exact: true }).fill(cfg.imagePrompt);
          await button('Start generation').click();
          await page.waitForFunction(previous => {
            const image = document.querySelector('img[alt="Generated character image"]');
            return image?.complete && image.naturalWidth && image.getAttribute('src') !== previous;
          }, previous, { timeout: cfg.timeoutMs });
        }
        return await finishCharacter();
      }
      case 'generate-prepare': {
        if (cfg.project) await openProject(await resolveProject(cfg.project));
        else await createProject(cfg.title);
        const composer = page.getByRole('textbox', { name: 'What do you want to create?', exact: true });
        await directSettings(async () => {
          await selectRadio(cfg.kind === 'image' ? 'Image' : 'Video');
          if (cfg.startFrame || cfg.endFrame) await selectRadio('Frames');
          else if (cfg.kind === 'video' && (cfg.references || cfg.characters.length)) await selectRadio('Ingredients');
          if (cfg.model) {
            await modelFamily.click();
            const model = modelChoices().filter({
              hasText: new RegExp('^(?:🍌\\s*)?' + escaped(cfg.model) + '(?:\\s*Leaving\\b.*)?$', 'i'),
            });
            await model.click({ timeout: 5000 }).catch(() => { throw new Error('Flow does not offer model: ' + cfg.model); });
          }
          await selectRadio('x' + cfg.count);
          if (cfg.aspectRatio) await selectRadio(cfg.aspectRatio);
          if (cfg.qualitySetting) await selectRadio(cfg.qualitySetting);
          if (cfg.duration && await radio(cfg.duration + 's').count()) await selectRadio(cfg.duration + 's');
        });
        if (cfg.duration && !new RegExp('(?:^|\\s)' + cfg.duration + 's\\b').test(await settings.innerText())) {
          throw new Error('Flow does not offer the requested duration for these settings: ' + cfg.duration + 's');
        }
        await composer.fill(cfg.prompt);
        if (cfg.startFrame) await attachFrame('Start', cfg.startFrame);
        if (cfg.endFrame) await attachFrame('End', cfg.endFrame);
        if (cfg.characters.length) {
          await page.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Characters', exact: true }).click();
          const attached = button('Ingredient');
          for (const name of cfg.characters) {
            const tile = characterTile(name);
            await tile.waitFor({ timeout: 5000 });
            const previous = await attached.count();
            await tileMenu(tile);
            await page.getByRole('menuitem', { name: 'Add to prompt', exact: true }).click({ timeout: 5000 });
            await attached.nth(previous).waitFor({ timeout: 10000 });
          }
          await page.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'All media', exact: true }).click();
        }
        return out({ ok: true, url: page.url() });
      }
      case 'generate-reference-open':
      case 'upload-open': {
        if (cfg.op === 'upload-open') await openProject(await resolveProject(cfg.project));
        await page.locator(r.tile).first().waitFor({ timeout: 6000 }).catch(() => {});
        const before = await tiles();
        await button(r.addMenu).click();
        await page.getByRole('menuitem', { name: 'Upload', exact: true }).click({ noWaitAfter: true, timeout: 5000 });
        return out({ ok: true, before });
      }
      case 'upload-wait':
      case 'generate-reference-add': {
        const expected = cfg.filename.toLowerCase();
        const stem = expected.replace(/\.[^.]+$/, '');
        const [uploaded] = await newTiles(cfg.before, tile => {
          const name = tile.name.toLowerCase();
          return ['image', 'video'].includes(tile.type) && (name === expected || name === stem);
        }, 1, cfg.timeoutMs);
        if (cfg.op === 'upload-wait') return out({ ok: true, uploaded: 1, media: await tiles() });
        const attached = button('Ingredient');
        const previous = await attached.count();
        try {
          await tileMenu(page.locator(r.tile).nth(uploaded.index - 1));
          await page.getByRole('menuitem', { name: 'Add to prompt', exact: true }).click({ timeout: 5000 });
          if (cfg.kind === 'video') await attached.nth(previous).waitFor({ timeout: 10000 });
        } finally {
          await closeMenu();
        }
        return out({ ok: true });
      }
      case 'generate': {
        const before = await tiles();
        await button('Start generation').click();
        const generated = await newTiles(before, tile => tile.type === cfg.kind, cfg.count, cfg.timeoutMs);
        const saved = [];
        try {
          for (const [index, media] of generated.entries()) {
            const tile = page.locator(r.tile).nth(media.index - 1);
            saved.push(await downloadTile(tile, cfg.quality, 30000, cfg.count === 1 ? '' : (index + 1) + '-'));
          }
        } catch (error) {
          return fail(String(error.message).split('\n')[0], { files: saved.map(file => file.path), url: page.url() });
        }
        return out({ ok: true, ...saved[0], files: saved.map(file => file.path), url: page.url() });
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
        if (await tile.locator('flow-character-tile').count()) {
          return fail('Flow does not offer tile downloads for characters. Open the character editor for its portrait.');
        }
        if (await tile.locator('flow-scene-tile').count() && cfg.qualityKey !== 'original') {
          return fail('Flow offers only original-size downloads for scenes');
        }
        if (cfg.qualityKey === 'gif' && await tile.locator('flow-image-tile').count()) {
          return fail('Flow offers GIF downloads for videos only');
        }
        return out({ ok: true, ...(await downloadTile(tile, cfg.quality, cfg.timeoutMs)) });
      }
      case 'add-to-scene': {
        await openProject(await resolveProject(cfg.project));
        await page.locator(r.tile).first().waitFor({ timeout: 15000 });
        const tile = await pickTile(cfg.media);
        if (!await tile.locator('flow-video-tile').count()) return fail('Choose a Flow video to add to the scene');
        try {
          await tileMenu(tile);
          await page.getByRole('menuitem', { name: 'Add to scene', exact: true }).hover({ timeout: 5000 });
          await page.getByRole('menuitem', { name: cfg.scene, exact: true }).click({ timeout: 5000 });
          await page.getByText('Added to scene', { exact: true }).waitFor({ timeout: 10000 });
          return out({ ok: true, scene: cfg.scene, url: page.url() });
        } finally {
          await closeMenu();
          if (await page.getByRole('menu').filter({ visible: true }).first().isVisible()) await closeMenu();
        }
      }
      case 'save-frame':
      case 'extend': {
        await openTimeline(await resolveProject(cfg.project), cfg.media);
        const clips = page.locator('flow-scene-timeline .timeline-contents .clip');
        if (cfg.op === 'save-frame') {
          const clip = clips.nth(cfg.clip - 1);
          await clip.scrollIntoViewIfNeeded({ timeout: 5000 });
          const bounds = await clip.boundingBox();
          const track = await page.locator('flow-scene-timeline .timeline-background').boundingBox();
          const offset = Math.max(1, Math.min(bounds.width - 1, bounds.width * cfg.at / 100));
          await page.mouse.click(bounds.x + offset, track.y + 4);
          const save = button('Save frame');
          await save.click({ timeout: 5000 });
          await page.getByText('Frame saved', { exact: true }).waitFor({ timeout: 10000 });
          return out({ ok: true, url: page.url(), clip: cfg.clip, at: cfg.at });
        }
        const duration = page.locator('.duration-timecode-value');
        const before = (await duration.innerText({ timeout: 10000 })).trim();
        await page.getByRole('button', { name: 'Add clip', exact: true }).click({ timeout: 5000 });
        await page.getByRole('menuitem', { name: 'Extend', exact: true }).click({ timeout: 5000 });
        await page.getByRole('textbox', { name: 'What happens next?', exact: true }).fill(cfg.prompt);
        await button('Start generation').click({ timeout: 5000 });
        // Flow extends the last clip in place. The clip count can stay the same.
        await page.waitForFunction(before => {
          const timecode = document.querySelector('.duration-timecode-value')?.textContent.trim();
          return Boolean(timecode && timecode > before);
        }, before, { timeout: cfg.timeoutMs });
        await clips.last().getByRole('button', { name: 'Redo extend', exact: true }).waitFor({ timeout: 10000 });
        const timecode = (await duration.innerText()).trim();
        const url = page.url();
        await button('Done editing scene').click({ timeout: 10000 });
        return out({ ok: true, url, duration: timecode });
      }
      case 'options': {
        await openProject(await resolveProject(cfg.project));
        return await directSettings(async () => {
          const original = await radio('Image').getAttribute('aria-checked') === 'true' ? 'Image' : 'Video';
          const modes = {};
          try {
            for (const mode of ['Image', 'Video']) {
              await selectRadio(mode);
              const groups = await toggles();
              const current = (await modelFamily.innerText()).trim().split('\n')[0];
              await modelFamily.click();
              try {
                const items = modelChoices();
                await items.first().waitFor({ timeout: 5000 });
                const choices = (await items.allInnerTexts()).map(label => label.trim().replace(/\s+/g, ' '));
                modes[mode.toLowerCase()] = { toggles: groups, dropdowns: [{ current, choices }] };
              } finally {
                await closeMenu();
              }
            }
            return out({ ok: true, mode: 'direct', ...modes[original.toLowerCase()], modes });
          } finally {
            if (await modelFamily.isVisible()) await selectRadio(original);
          }
        });
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
    await closeMenu();
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


def generate(kind, prompt, directory, timeout_s=720, *, aspect_ratio=None, references=(),
             project=None, model=None, resolution=None, quality=None, duration=None, count=1,
             characters=(), start_frame=None, end_frame=None):
    """Start direct generation and save the original output files."""
    if kind not in {"image", "video"}:
        raise ValueError("Flow media kind must be image or video")
    if not 1 <= count <= 4:
        raise ValueError("Flow output count must be between 1 and 4")
    if kind == "image" and (resolution or duration is not None):
        raise ValueError("Flow resolution and duration apply to video")
    if kind == "video" and quality and resolution and quality.lower() != resolution.lower():
        raise ValueError("Flow video --quality and --resolution must agree")
    if duration is not None and duration <= 0:
        raise ValueError("Flow duration must be positive")
    if characters and not project:
        raise ValueError("Flow characters require --project")
    if start_frame or end_frame:
        if kind != "video" or not project or references or characters:
            raise ValueError("Flow frames require video and --project, with no ingredients")
    directory = Path(directory).resolve()
    ok, _, error = web.open_target(TARGET)
    if not ok:
        return {"target": TARGET, "ok": False, "error": error}
    try:
        result = run("generate-prepare", kind=kind, prompt=prompt, title=f"AAO {kind} {directory.name[-12:]}",
                     project=project, aspectRatio=aspect_ratio, references=bool(references), model=model,
                     qualitySetting=resolution or quality, duration=duration, count=count,
                     characters=characters, startFrame=start_frame, endFrame=end_frame)
        if not result.get("ok"):
            return result
        for path in references:
            result = run("generate-reference-open", timeout_s=60)
            if not result.get("ok"):
                return result
            uploaded, _, error = web._pw(TARGET, "upload", Path(path).resolve().as_posix(), timeout=120)
            if not uploaded:
                return {"ok": False, "reason": error or "Could not upload the Flow reference image"}
            result = run("generate-reference-add", timeout_s=60, before=result["before"], kind=kind,
                         filename=Path(path).name)
            if not result.get("ok"):
                return result
        return {"target": TARGET, **run("generate", timeout_s=timeout_s, kind=kind, count=count, to=directory.as_posix(),
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
        for completed, path in enumerate(paths):  # the chooser takes one file per `playwright-cli upload`
            opened = run("upload-open", timeout_s=60, project=project)
            if not opened.get("ok"):
                return {"target": TARGET, **opened, "uploaded": completed}
            ok, out, err = web._pw(TARGET, "upload", path, timeout=120)
            if not ok:
                return {"target": TARGET, "ok": False, "loggedIn": True, "reason": "file chooser was not filled",
                        "error": (err or out)[-300:], "uploaded": completed}
            result = run("upload-wait", timeout_s=timeout_s, before=opened["before"], filename=Path(path).name)
            if not result.get("ok"):
                return {"target": TARGET, **result, "uploaded": completed}
        result["uploaded"] = len(paths)
        return {"target": TARGET, **result}
    finally:
        web.release(TARGET, settings=settings)
