"""Drive Google Flow (flow.google.com, AI studio for video and images) with playwright-cli. No LLM.

    uv run aao flow projects                              # every project: id, title, URL
    uv run aao flow new [--title "..."]                   # create an empty project (free)
    uv run aao flow read <project> [--type video|image|...]   # media in a project: index, type, name, thumbnail
    uv run aao flow download <project> <media> [--to DIR] [--quality original|gif|1080p|4k]
    uv run aao flow options [--project P]                 # the agent's settings and every choice they offer
    uv run aao flow upload <project> <file>...            # add reference images/videos to the project (free)
    uv run aao flow new-character <project> --name NAME (--face-prompt TEXT | --face-image FILE)
    uv run aao flow edit-character <project> <character> [character options]
    uv run aao flow characters|read-character <project> [<character>]
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
  const uuid = '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}';
  const editorPath = new RegExp('/character/(' + uuid + ')/?$', 'i');
  const visible = locator => locator.filter({ visible: true });
  const slot = name => button(name).or(page.getByRole('tab', { name }));
  const characterName = () => page.getByRole('textbox', { name: 'Character name', exact: true });
  const characterInfo = () => page.getByRole('textbox', { name: /^Character (personality|info)(\s*\(optional\))?$/i });
  const picture = () => visible(page.getByRole('img', { name: 'Generated character image', exact: true })).first();
  const imageUrl = () => picture().evaluateAll(images => images[0]?.currentSrc || null);
  const bodySlot = () => visible(slot(/^(Create body|Body)$/i).or(
    page.locator('flow-slot-chip-button:has(mat-icon:text-is("accessibility_new")) button'))).first();
  const faceSlot = () => visible(slot(/^(Portrait|Face)$/i)).first();
  const waitForEditor = (timeout = cfg.timeoutMs) => page.waitForURL(url => editorPath.test(url.pathname), { timeout });
  const openCharacter = async () => {
    const ref = cfg.character;
    const project = await resolveProject(cfg.project);
    const url = r.projectUrl.replace('{id}', project);
    const link = ref.split(/[?#]/)[0].match(new RegExp('/project/([^/]+)/character/(' + uuid + ')/?$', 'i'));
    const id = ref.match(new RegExp('^' + uuid + '$', 'i'))?.[0] || link?.[2];
    if (link && link[1] !== project) throw new Error('Character URL belongs to a different Flow project');
    if (id) {
      await goto(url + '/character/' + id, 'input[aria-label="Character name"], input[placeholder="Character name"], .ProseMirror');
    } else {
      await openProject(project);
      const nav = page.getByRole('navigation', { name: 'Project navigation' });
      await nav.locator('button, a').filter({ hasText: /\bCharacters\s*$/ }).click();
      const cards = page.locator('flow-character-tile');
      await cards.first().waitFor({ timeout: 4000 }).catch(() => {});
      const names = await cards.evaluateAll(items => items.map(card => {
        const copy = card.cloneNode(true);
        for (const icon of copy.querySelectorAll('mat-icon, i.google-symbols')) icon.remove();
        return (copy.textContent || '').replace(/\s+/g, ' ').trim();
      }));
      const matches = names.map((name, index) => ({ name, index })).filter(item => item.name === ref);
      if (matches.length !== 1) throw new Error(matches.length
        ? 'Several Flow characters are named "' + ref + '"; use the ID or editor URL'
        : 'No Flow character named "' + ref + '" in this project');
      await cards.nth(matches[0].index).click();
    }
    await waitForEditor();
    await characterName().waitFor({ timeout: 15000 });
  };
  const waitForPicture = async (previous, deadline = Date.now() + cfg.timeoutMs) => {
    while (Date.now() < deadline) {
      const ready = await picture().evaluateAll(images => {
        const img = images[0];
        return img?.complete && img.naturalWidth > 0 ? img.currentSrc : null;
      });
      if (ready && ready !== previous) return;
      await wait(400);
    }
    throw new Error('Timed out waiting for the new character image in Flow');
  };
  const submitCharacterPrompt = async prompt => {
    await visible(page.locator('.ProseMirror[contenteditable="true"]')).last().fill(prompt, { timeout: 15000 });
    await button(/^(Start generation|Generate( image)?)$/i).click({ timeout: 15000 });
  };
  const performanceField = () => visible(page.getByRole('textbox', { name: /^(Voice performance|Customize performance)$/i })
    .or(page.getByPlaceholder(/^(Voice performance|Customize performance)$/i))).first();
  const voiceCard = () => visible(page.locator('flow-voice-ingredient-chip, flow-voice-card, [aria-label="Selected voice"]')).first();
  const voiceEdit = () => visible(button(/^(Edit voice|Change voice|Edit)$/i)
    .or(page.locator('button:has(mat-icon:text-is("voice_selection"))'))).first();
  const setCharacterVoice = async () => {
    const picker = visible(button('Select a voice')).or(voiceEdit()).first();
    await picker.click({ timeout: 15000 });
    const voices = page.getByRole('listbox', { name: 'Asset list', exact: true });
    const options = voices.getByRole('option');
    await options.first().waitFor({ timeout: 10000 });
    const escaped = cfg.voice.replace(/[.*+?^$(){}|[\]\\]/g, '\\$&');
    const voice = voices.getByRole('option', { name: new RegExp('^' + escaped + '(?:\\s|$)', 'i') });
    if (await voice.count() !== 1) {
      throw Object.assign(new Error('Flow could not match one voice named "' + cfg.voice + '"'),
        { choices: (await options.allInnerTexts()).map(label => label.trim()) });
    }
    await voice.click();
    const add = button(/^Add to character$/i);
    if (!cfg.voiceDescription) {
      const selectedName = (await voice.innerText()).split('\n').map(s => s.trim())
        .find(s => s.toLowerCase() === cfg.voice.toLowerCase()) || cfg.voice;
      await add.click();
      await add.waitFor({ state: 'hidden', timeout: 15000 });
      return { voice: selectedName };
    }
    const customName = cfg.voiceName ?? (await characterName().inputValue()) + ' Voice';
    if (!await performanceField().isVisible()) {
      await button(/^(Customize performance|Create new voice)$/i).click({ timeout: 10000 });
    }
    await performanceField().fill(cfg.voiceDescription, { timeout: 15000 });
    const voiceName = visible(page.getByRole('dialog').getByRole('textbox', { name: /^(Name|Voice name)$/i })
      .or(page.getByPlaceholder(/^(Name|Voice name)$/i))).first();
    await voiceName.fill(customName, { timeout: 15000 });
    const save = button(/^Save new voice$/i);
    if (!await save.isEnabled({ timeout: 10000 })) {
      await button(/^(Preview|Sync)$/i).click({ timeout: 10000 });
    }
    await save.click({ timeout: cfg.timeoutMs });
    if (await add.waitFor({ timeout: 5000 }).then(() => true, () => false)) {
      const exactName = customName.replace(/[.*+?^$(){}|[\]\\]/g, '\\$&');
      const option = voices.getByRole('option', { name: new RegExp('^' + exactName + '(?:\\s|$)') });
      await option.click({ timeout: 10000 });
      await add.click({ timeout: 15000 });
      await add.waitFor({ state: 'hidden', timeout: 15000 });
    } else if ((await readVoice()).voice !== customName) {
      throw new Error('Flow saved the custom voice but did not confirm it was attached to the character');
    }
    return { voice: customName, voice_description: cfg.voiceDescription };
  };
  const readVoice = async () => {
    if (await button('Select a voice').isVisible()) return { voice: null, voice_description: null };
    const card = voiceCard();
    const edit = voiceEdit();
    const region = await card.isVisible() ? card : edit.locator('..');
    const lines = await region.isVisible() ? (await region.innerText()).split('\n').map(s => s.trim()) : [];
    const voice = lines.find(label => label && !/^(voice|edit|change voice|edit voice|play|play_arrow|voice_selection)$/i.test(label));
    let description = await performanceField().isVisible() ? await performanceField().inputValue() : null;
    if (cfg.op === 'character-read' && !description && await edit.isVisible()) {
      await edit.click({ timeout: 5000 });
      const dialog = visible(page.getByRole('dialog')).last();
      try {
        if (await performanceField().isVisible()) description = await performanceField().inputValue();
      } finally {
        const close = dialog.getByRole('button', { name: /^(Close|Cancel)$/i }).first();
        if (await close.isVisible()) await close.click();
        else await page.keyboard.press('Escape');
      }
    }
    return { voice: voice || null, voice_description: description || null };
  };
  const readCharacter = async () => {
    const url = page.url().split(/[?#]/)[0];
    const name = await characterName().inputValue({ timeout: 15000 });
    const infoBox = characterInfo();
    const info = await infoBox.count() ? await infoBox.inputValue() : null;
    const voice = await readVoice();
    const face = faceSlot();
    if (await face.isVisible()) await face.click();
    const faceUrl = await imageUrl();
    const body = visible(slot(/^Body$/i)).first();
    let bodyUrl = null;
    if (await body.isVisible()) {
      await body.click();
      bodyUrl = await imageUrl();
      if (await face.isVisible()) await face.click();
    }
    return { id: url.match(editorPath)[1], name, url, info, ...voice, face_url: faceUrl, body_url: bodyUrl };
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
      case 'characters': {
        await showCharacters(await resolveProject(cfg.project));
        return out({ ok: true, characters: (await tiles()).filter(tile => tile.type === 'character') });
      }
      case 'character-read': {
        await openCharacter();
        return out({ ok: true, character: await readCharacter() });
      }
      case 'character-open': {
        if (cfg.character) await openCharacter();
        else await openCharacterBuilder(cfg.project);
        return out({ ok: true, url: page.url() });
      }
      case 'character-image': {
        if (cfg.slot === 'body') {
          await waitForEditor();
          await waitForPicture(null, Date.now() + 15000);
          const face = await imageUrl();
          const body = bodySlot();
          const seed = visible(page.locator('flow-ingredient-chip:has(img), flow-generated-media-card:has(img):has(mat-icon:text-is("cancel"))'));
          const seedsBefore = await seed.count();
          await body.click({ timeout: 15000 });
          const switched = async () => await body.getAttribute('aria-selected') === 'true'
            || await body.getAttribute('aria-pressed') === 'true'
            || await imageUrl() !== face || await seed.count() > seedsBefore;
          const deadline = Date.now() + 10000;
          while (!await switched() && Date.now() < deadline) await wait(200);
          if (!await switched()) {
            return fail('Flow did not switch to the body editor', { url: page.url() });
          }
        } else if (cfg.existing && await faceSlot().isVisible()) {
          await faceSlot().click();
        }
        const previous = await imageUrl();
        if (cfg.upload) {
          await visible(button(/^(Upload( (image|media|portrait|body))?|Replace image)$/i)).first().click({ timeout: 15000 });
        } else {
          const deadline = Date.now() + cfg.timeoutMs;
          await submitCharacterPrompt(cfg.prompt);
          await waitForEditor(Math.max(1, deadline - Date.now()));
          await waitForPicture(previous, deadline);
        }
        return out({ ok: true, previous, url: page.url() });
      }
      case 'character-image-wait': {
        const deadline = Date.now() + cfg.timeoutMs;
        await waitForEditor();
        await waitForPicture(cfg.previous, deadline);
        return out({ ok: true, url: page.url() });
      }
      case 'character-save': {
        await waitForEditor();
        if (cfg.name !== null) {
          const name = characterName();
          await name.fill(cfg.name, { timeout: 15000 });
          await name.press('Enter');
        }
        if (cfg.info !== null) await characterInfo().fill(cfg.info, { timeout: 15000 });
        const voice = cfg.voice ? await setCharacterVoice() : {};
        const character = { ...await readCharacter(), ...voice };
        await button(/^Done( editing)?$/i).click({ timeout: 15000 });
        await page.waitForURL(u => !editorPath.test(u.pathname), { timeout: 15000 });
        return out({ ok: true, character });
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
          const finished = document.querySelector('flow-scene-timeline .clip.is-last button[aria-label="Redo extend"]');
          return Boolean(timecode && timecode > before && finished);
        }, before, { timeout: cfg.timeoutMs });
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


def create_character(project, name, *, prompt=None, image=None, **options):
    """Create a character. The original prompt and image arguments refer to its face."""
    return _change_character(project, None, name=name, face_prompt=prompt, face_image=image, **options)


def edit_character(project, character, **options):
    """Update the supplied fields of an existing character."""
    return _change_character(project, character, **options)


def read_character(project, character, *, settings=None, timeout_s=180):
    """Read the saved character's details and available reference image URLs."""
    if timeout_s <= 0:
        return {"ok": False, "reason": "the Flow timeout must be positive"}
    return session("character-read", project=project, character=character, settings=settings, timeout_s=timeout_s)


def _change_character(project, character, *, name=None, face_prompt=None, face_image=None,
                      body_prompt=None, body_image=None, info=None, voice=None,
                      voice_description=None, voice_name=None, settings=None, timeout_s=180):
    name = name.strip() if name is not None else None
    face_prompt = (face_prompt or "").strip()
    body_prompt = (body_prompt or "").strip()
    voice = (voice or "").strip()
    voice_description = (voice_description or "").strip()
    voice_name = (voice_name or "").strip() or None
    if timeout_s <= 0:
        return {"ok": False, "reason": "the Flow timeout must be positive"}
    if name == "" or (character is None and not name):
        return {"ok": False, "reason": "provide a non-empty character name"}
    if (face_prompt and face_image) or (body_prompt and body_image):
        return {"ok": False, "reason": "provide one prompt or image per character slot"}
    if character is None and not (face_prompt or face_image):
        return {"ok": False, "reason": "provide a face prompt or face image"}
    if voice_description and not voice:
        return {"ok": False, "reason": "provide a base preset with --voice when customizing its performance"}
    if voice_name and not voice_description:
        return {"ok": False, "reason": "--voice-name requires --voice-description"}
    if character is not None and name is None and info is None and not any(
        (face_prompt, face_image, body_prompt, body_image, voice)
    ):
        return {"ok": False, "reason": "provide at least one character field to edit"}

    sources = []
    for slot, prompt, file in (("face", face_prompt, face_image), ("body", body_prompt, body_image)):
        path = Path(file).expanduser().resolve() if file else None
        if path and not path.is_file():
            return {"ok": False, "reason": f"no such {slot} image: {file}"}
        if prompt or path:
            sources.append((slot, prompt, path))

    ok, _, error = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": error}
    try:
        opened = run("character-open", timeout_s=timeout_s, project=project, character=character)
        if not opened.get("ok"):
            return {"target": TARGET, **opened}
        url = opened.get("url")
        for slot, prompt, path in sources:
            changed = run("character-image", timeout_s=timeout_s, slot=slot, existing=character is not None,
                          prompt=prompt, upload=path is not None)
            if not changed.get("ok"):
                return {"target": TARGET, "url": url, **changed}
            url = changed.get("url")
            if path:
                ok, output, error = web._pw(TARGET, "upload", path.as_posix(), timeout=timeout_s)
                if not ok:
                    return {"target": TARGET, "ok": False, "reason": f"{slot} image upload failed",
                            "error": (error or output or "")[-300:], "url": url}
                finished = run("character-image-wait", timeout_s=timeout_s, previous=changed["previous"])
                if not finished.get("ok"):
                    return {"target": TARGET, "url": url, **finished}
                url = finished.get("url")
        saved = run("character-save", timeout_s=timeout_s, name=name, info=info, voice=voice,
                    voiceDescription=voice_description, voiceName=voice_name)
        return {"target": TARGET, **saved} if saved.get("ok") else {"target": TARGET, "url": url, **saved}
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
