"""Grok chat and Imagine through AAO's shared Playwright CLI session."""

import base64
import json
import math
import mimetypes
import re
from pathlib import Path
from threading import Lock
from typing import Literal, get_args
from uuid import uuid4

from orchestrator import web

Aspect = Literal["2:3", "3:2", "1:1", "9:16", "16:9"]
Quality = Literal["Speed", "Quality"]
Count = Literal["Auto", "2", "4", "8", "12"]
Resolution = Literal["480p", "720p", "1080p"]
Duration = Literal["6s", "10s", "15s"]
Category = Literal["Character", "Prop", "Location"]
DrawStyle = Literal["Manga", "Illustration", "Painting", "Realistic"]
DrawAspect = Literal["2:3", "3:2", "4:3", "1:1", "9:16", "16:9", "21:9", "5:2"]
Library = Literal["generations", "uploads", "voices"]
DEFAULT_OUT = "downloads"
IMAGINE_URL = "https://grok.com/imagine"
_browser = Lock()

# Shared page helpers, inserted after `const cfg = ...;` in every Imagine script.
# The SDK reference provides the Imagine selectors and the result-set settling behavior.
HELPERS = r"""
  const output = { files: [], media_urls: [] };
  const finish = (ok, extra = {}) => ({ ok, conversation_url: page.url(), ...output, ...extra });
  const visible = selector => page.locator(selector).filter({ visible: true });
  const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Grok appends model versions to some labels ("Quality 2.0").
  const named = text => new RegExp('^\\s*' + escape(text) + '(\\s+v?[\\d.]+)?\\s*$');
  const choice = label => page.getByRole('radio', { name: named(label) })
    .or(page.getByRole('button', { name: named(label) })).filter({ visible: true }).first();
  const signIn = /^\s*(sign in|log in|sign up)\s*$/i;
  const signedOut = async () => new RegExp(cfg.loginUrl, 'i').test(page.url()) ||
    await page.getByRole('button', { name: signIn }).or(page.getByRole('link', { name: signIn })).first().isVisible();
  const loginFailure = () => finish(false, { loggedIn: false, reason: 'Grok sign-in required' });
  const dismissIntro = async () => {
    const dialog = visible('[role="dialog"]').filter({ hasText: /introducing|what.s new/i }).first();
    if (await dialog.isVisible()) {
      await dialog.getByRole('button', { name: /^(continue|got it|close|dismiss|skip|maybe later)$/i })
        .first().click({ timeout: 5000 });
    }
  };
  // Generated media keyed by URL path, so a signed URL with a new token is not a new result.
  const mediaSources = (kind, only, withNav = false) => page.evaluate(([kind, only, withNav]) => {
    const sources = new Map();
    const selector = kind === 'image' ? 'img' : kind === 'video' ? 'video' : 'img, video';
    for (const element of document.querySelectorAll(selector)) {
      const src = element.currentSrc || element.src;
      if (!src || (!withNav && element.closest('[aria-label="App navigation"]'))) continue;
      if (only && !src.includes(only)) continue;
      const alt = (element.alt || '').trim();
      const url = new URL(src, location.href);
      if (element.tagName === 'IMG' && (['pfp', 'Company Logo', 'Powered by Onetrust'].includes(alt) ||
        element.closest('footer') || /^Thumbnail \d+$/i.test(alt) ||
        (alt !== 'Generated image' && src.startsWith('data:image/') && element.naturalWidth < 384) ||
        !(alt === 'Generated image' || src.startsWith('data:image/') || url.hostname === 'assets.grok.com'))) continue;
      const grokAsset = url.protocol === 'https:' && /(^|\.)(grok\.com|x\.ai)$/.test(url.hostname) &&
        !url.username && !url.password;
      const type = element.tagName === 'IMG' ? 'image' : 'video';
      if (grokAsset || src.startsWith('data:' + type + '/') || (url.protocol === 'blob:' && url.origin === location.origin)) {
        const key = url.protocol === 'https:' ? url.origin + url.pathname : src;
        if (!sources.has(key)) sources.set(key, src);
      }
    }
    return [...sources.entries()];
  }, [kind, only || null, withNav]);
  const mediaKeys = async (kind, only, withNav) => new Set((await mediaSources(kind, only, withNav)).map(([key]) => key));
  // Wait for new media to appear and stop changing; give up after emptyMs when nothing appears.
  const awaitMedia = async (before, kind, only, deadline, emptyMs = Infinity, withNav = false) => {
    const start = Date.now();
    let current = [];
    let changed = Date.now();
    while (Date.now() < deadline) {
      const fresh = (await mediaSources(kind, only, withNav)).filter(([key]) => !before.has(key))
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
      if (fresh.length !== current.length || fresh.some(([key], i) => key !== current[i][0])) {
        current = fresh;
        changed = Date.now();
      } else if (current.length && Date.now() - changed >= 6000) {
        break;
      } else if (!current.length && Date.now() - start >= emptyMs) {
        break;
      }
      await page.waitForTimeout(500);
    }
    return current.map(([, src]) => src);
  };

  const download = async (source, index) => {
    let data = source;
    let mime = source.match(/^data:([^;,]+)/i)?.[1];
    if (source.startsWith('https:')) {
      const response = await page.request.get(source, { timeout: 45000, maxRedirects: 0 });
      try {
        if (!response.ok()) throw Error('media server returned HTTP ' + response.status());
        mime = response.headers()['content-type'];
        data = 'data:' + mime + ';base64,' + (await response.body()).toString('base64');
      } finally {
        await response.dispose();
      }
    } else if (source.startsWith('blob:')) {
      mime = await page.evaluate(async url => (await fetch(url)).headers.get('content-type'), source);
    }
    mime = mime?.split(';')[0].trim().toLowerCase();
    const extensions = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
                         'image/gif': 'gif', 'image/avif': 'avif', 'video/mp4': 'mp4', 'video/webm': 'webm' };
    if (mime === 'application/octet-stream') {
      const ext = source.split(/[?#]/, 1)[0].match(/\.(\w+)$/)?.[1]?.toLowerCase();
      mime = Object.keys(extensions).find(type => extensions[type] === ext);
    }
    if (!mime || !extensions[mime] || (cfg.kind !== 'media' && !mime.startsWith(cfg.kind + '/'))) {
      throw Error('unsupported media type');
    }
    const name = cfg.filename + '-' + index + '.' + extensions[mime];
    const [file] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      page.evaluate(({ data, name }) => {
        const link = document.createElement('a');
        link.href = data;
        link.download = name;
        document.body.append(link);
        link.click();
        link.remove();
      }, { data, name }),
    ]);
    const path = cfg.out + '/' + name;
    await file.saveAs(path);
    return path;
  };
  const saveAll = async (sources, extra = {}) => {
    output.media_urls = sources.filter(src => src.startsWith('https:'));
    const errors = [];
    for (const [index, source] of sources.entries()) {
      try {
        output.files.push(await download(source, index + 1));
      } catch (error) {
        errors.push((error.message || String(error)).split('\n')[0]);
      }
    }
    return errors.length ? finish(false, { reason: 'some media could not be downloaded', errors, ...extra })
      : finish(true, extra);
  };

  const composer = visible('[role="textbox"][contenteditable="true"]').first();
  // The composer keeps drafts (including @reference chips) across reloads.
  const clearComposer = async () => {
    await composer.click({ timeout: 10000 });
    await page.keyboard.press('ControlOrMeta+a');
    await page.keyboard.press('Backspace');
  };
  // The + button opens the toolbox: Generations, Uploads, Audio, References, Draw and Upload media.
  const toolbox = () => page.getByRole('button', { name: 'Upload', exact: true }).first();
  const openTab = async (name, ready) => {
    const tab = page.getByRole('tab', { name });
    // A tab click during the toolbox's opening animation is dropped, so retry until the tab shows.
    for (let attempt = 0; attempt < 5; attempt++) {
      if (await toolbox().getAttribute('aria-expanded') !== 'true') await toolbox().click({ timeout: 10000 });
      await tab.waitFor({ timeout: 10000 });
      await page.waitForTimeout(500);
      await tab.click({ timeout: 10000 });
      try {
        if (await tab.getAttribute('aria-selected') !== 'true') throw Error('tab not selected');
        await ready.first().waitFor({ state: 'attached', timeout: 3000 });
        await page.waitForTimeout(1000);
        return;
      } catch {}
    }
    await ready.first().waitFor({ state: 'attached', timeout: 10000 });
  };
  const openReferences = () => openTab('References', page.getByRole('button', { name: 'New', exact: true }));
  const closeToolbox = async () => {
    if (await toolbox().getAttribute('aria-expanded').catch(() => null) === 'true') {
      await toolbox().click({ timeout: 5000 }).catch(() => {});
    }
  };
  // Buttons such as Add last frame or Upload photo open a native file chooser, which blocks
  // playwright-cli. Catch the input they click instead, and set its files directly.
  const pickFiles = async (trigger, files, dispatch = false) => {
    await page.evaluate(() => {
      window.__aaoPicked = null;
      if (window.__aaoNoPicker) return;
      window.__aaoNoPicker = true;
      const click = HTMLInputElement.prototype.click;
      HTMLInputElement.prototype.click = function () {
        if (this.type !== 'file') return click.call(this);
        window.__aaoPicked = this;
      };
      HTMLInputElement.prototype.showPicker = function () {
        if (this.type === 'file') window.__aaoPicked = this;
      };
    });
    await (dispatch ? trigger.dispatchEvent('click') : trigger.click({ timeout: 10000 }));
    const end = Date.now() + 5000;
    while (!await page.evaluate(() => !!window.__aaoPicked)) {
      if (Date.now() > end) throw Error('the button did not open a file input');
      await page.waitForTimeout(100);
    }
    await page.evaluate(() => {
      document.querySelectorAll('[data-aao-picked]').forEach(e => e.removeAttribute('data-aao-picked'));
      window.__aaoPicked.setAttribute('data-aao-picked', '1');
    });
    await page.locator('input[data-aao-picked]').setInputFiles(files, { timeout: 10000 });
  };
  const waitCount = async (locator, count, ms = 60000) => {
    const end = Date.now() + ms;
    while (await locator.count() < count) {
      if (Date.now() > end) throw Error('timed out waiting for the attachment');
      await page.waitForTimeout(300);
    }
  };
  // Composer attachments: frames and images, library picks, and voices.
  const attachments = () => page.locator('form').first().getByRole('button', { name: /^Remove (image|video|audio)$/ });
  const useButton = name => page.getByRole('button', { name: 'Use @' + name, exact: true });
  const referenceCards = () => page.evaluate(() =>
    [...document.querySelectorAll('button[aria-label^="Use @"]')].map(button => ({
      name: button.getAttribute('aria-label').slice(5),
      sample: /\bSample\b/.test(button.parentElement.innerText),
      image: button.parentElement.querySelector('img')?.src || null,
    })));
"""


def _script(body):
    return "async page => {\n  const cfg = __CFG__;\n" + HELPERS + body + "\n}"


IMAGINE_SCRIPT = _script(r"""
  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return loginFailure();
    const dialog = visible('[role="dialog"]').filter({ hasText: /introducing|what.s new/i });
    await choice(cfg.mode).or(dialog.first()).first().waitFor({ timeout: 20000 });
    await dismissIntro();
    if (await signedOut()) return loginFailure();
    await choice(cfg.mode).click({ timeout: 10000 });
    for (const label of cfg.radios) {
      const settingName = label.endsWith('p') ? 'Video resolution' : 'Video duration';
      const setting = page.getByRole('button', { name: settingName, exact: true });
      if (cfg.kind === 'video' && await setting.isVisible()) {
        if ((await setting.innerText()).trim() !== label) {
          await setting.click({ timeout: 10000 });
          await page.getByRole('menuitemradio', { name: label, exact: true }).click({ timeout: 10000 });
        }
      } else {
        await choice(label).click({ timeout: 10000 });
      }
    }
    if (cfg.kind === 'video' && typeof cfg.audio === 'boolean') {
      const audio = page.getByRole('button', { name: 'Video audio', exact: true });
      if ((await audio.getAttribute('aria-pressed') === 'true') !== cfg.audio) await audio.click({ timeout: 10000 });
    }
    for (const [label, value] of cfg.menus) {
      await visible('[aria-label="' + label + '"]').first().click({ timeout: 10000 });
      const pattern = new RegExp('(^|[^0-9])' + value + '($|[^0-9])', 'i');
      const option = visible('[role="menuitem"], [role="menuitemradio"], [role="option"], [role="radio"]')
        .filter({ hasText: pattern }).first();
      await option.click({ timeout: 10000 });
    }
    // Clear first: the Loop chip lives in the editor, and drafts survive reloads.
    await clearComposer();
    for (const remove of await attachments().all()) await remove.click({ timeout: 5000 }).catch(() => {});
    const fileInput = page.locator('form').first().locator('input[type="file"]').first();
    if (cfg.loop) {
      // The Loop tile asks for the image the video starts and ends on and puts a Loop chip in the prompt.
      await page.getByRole('button', { name: 'Loop', exact: true }).click({ timeout: 10000 });
      const loop = page.getByRole('dialog').filter({ hasText: 'Pick the image your video starts' });
      await loop.waitFor({ timeout: 10000 });
      await pickFiles(loop.getByRole('button', { name: 'Upload photo' }), cfg.image);
      await waitCount(attachments(), 1);
    } else if (cfg.image) {
      await fileInput.setInputFiles(cfg.image, { timeout: 10000 });
      await waitCount(attachments(), 1);
    }
    for (const [button, file] of [['Add intermediate frame', cfg.middleFrame], ['Add last frame', cfg.lastFrame]]) {
      if (!file) continue;
      const count = await attachments().count();
      await pickFiles(page.getByRole('button', { name: button, exact: true }), file);
      await waitCount(attachments(), count + 1);
    }
    if (cfg.references?.length) {
      const count = await attachments().count();
      await fileInput.setInputFiles(cfg.references, { timeout: 10000 });
      await waitCount(attachments(), count + cfg.references.length);
    }
    for (const pick of cfg.picks || []) {
      // Toolbox library: Generations are listed newest first as "Select image image.jpg", Uploads by file name.
      const items = page.getByRole('button', { name: /^Select \w+ / });
      await openTab(pick.source === 'generation' ? 'Generations' : 'Uploads', items);
      const target = pick.index ? items.nth(pick.index - 1)
        : page.getByRole('button', { name: new RegExp('^Select \\w+ ' + escape(pick.name) + '$') }).first();
      if (!await target.count()) {
        return finish(false, { reason: 'no ' + pick.source + ' ' + (pick.name || '#' + pick.index) + ' in the Imagine library' });
      }
      const count = await attachments().count();
      // These rows animate and never count as stable for a pointer click.
      await target.dispatchEvent('click');
      await waitCount(attachments(), count + 1, 30000);
    }
    if (cfg.voice) {
      const use = page.getByRole('button', { name: 'Use ' + cfg.voice, exact: true });
      await openTab('Audio', page.getByRole('button', { name: /^Use [^@]/ }));
      if (!await use.count()) return finish(false, { reason: 'no Grok voice named ' + cfg.voice });
      await use.dispatchEvent('click');
      await page.getByRole('button', { name: 'Remove audio', exact: true }).waitFor({ timeout: 10000 });
    }
    if (cfg.mentions?.length) {
      await openReferences();
      for (const name of cfg.mentions) {
        if (!await useButton(name).count()) return finish(false, { reason: 'no Grok reference named @' + name });
        await useButton(name).click({ timeout: 10000 });
      }
    }
    await closeToolbox();
    if (cfg.loop || cfg.mentions?.length) {
      // Keep the chips: append the prompt after them.
      await composer.click({ timeout: 10000 });
      await page.keyboard.press('ControlOrMeta+End');
      await page.keyboard.insertText(' ' + cfg.prompt);
    } else {
      await composer.fill(cfg.prompt, { timeout: 10000 });
    }
    if (cfg.dryRun) {
      const setting = async name => {
        const button = page.getByRole('button', { name, exact: true });
        return await button.count() ? (await button.first().innerText()).trim() : null;
      };
      return finish(true, {
        dry_run: true, prompt_text: (await composer.innerText()).trim(),
        attachments: await attachments().evaluateAll(bs => bs.map(b => b.getAttribute('aria-label').slice(7))),
        settings: { resolution: await setting('Video resolution'), duration: await setting('Video duration'),
                    aspect_ratio: await setting('Aspect Ratio'), count: await setting('Image Count'),
                    audio: await page.getByRole('button', { name: 'Video audio', exact: true }).getAttribute('aria-pressed').catch(() => null) },
      });
    }
    const deadline = Date.now() + cfg.timeoutMs;
    if (cfg.mentions?.length) {
      // Grok files an @reference generation as an edit under the reference's own post and does
      // not render it there; the new post appears in the sidebar's recent media instead.
      const recent = await mediaKeys('media', '/generated/', true);
      await visible('[data-testid="chat-submit"], [aria-label="Submit"]').first().click({ timeout: 10000 });
      const posts = [...new Set((await awaitMedia(recent, 'media', '/generated/', deadline, Infinity, true))
        .map(src => src.match(/\/generated\/([\w-]+)\//)?.[1]).filter(Boolean))];
      const sources = [];
      for (const post of posts) {
        await page.goto(cfg.url + '/post/' + post, { waitUntil: 'domcontentloaded', timeout: 30000 });
        sources.push(...await awaitMedia(new Set(), cfg.kind, '/generated/' + post + '/',
                                         Math.max(deadline, Date.now() + 30000), 20000));
      }
      if (!sources.length) return finish(false, { reason: 'timed out waiting for generated ' + cfg.kind });
      return await saveAll(sources);
    }
    const before = await mediaKeys(cfg.kind);
    await visible('[data-testid="chat-submit"], [aria-label="Submit"]').first().click({ timeout: 10000 });
    const sources = await awaitMedia(before, cfg.kind, null, deadline);
    if (!sources.length) return finish(false, { reason: 'timed out waiting for generated ' + cfg.kind });
    return await saveAll(sources);
  } catch (error) {
    if (await signedOut().catch(() => false)) return loginFailure();
    return finish(false, { reason: (error.message || String(error)).split('\n')[0] });
  }
""")

REFERENCES_SCRIPT = _script(r"""
  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return loginFailure();
    await page.getByRole('button', { name: 'Upload', exact: true }).first()
      .or(visible('[role="dialog"]').filter({ hasText: /introducing|what.s new/i }).first())
      .first().waitFor({ timeout: 20000 });
    await dismissIntro();
    if (await signedOut()) return loginFailure();
    await openReferences();
    if (cfg.action === 'list') {
      if (cfg.category) {
        await visible('button').filter({ hasText: /^\s*(All|Character|Prop|Location)\s*$/ }).first().click({ timeout: 10000 });
        await page.getByRole('menuitemradio', { name: cfg.category, exact: true }).click({ timeout: 10000 });
        await page.waitForTimeout(1000);
      }
      return finish(true, { references: await referenceCards() });
    }
    if (cfg.action === 'delete') {
      const name = cfg.name.replace(/^@/, '');
      if (!await useButton(name).count()) return finish(false, { reason: 'no Grok reference named @' + name });
      await useButton(name).locator('..').getByRole('button', { name: 'Delete reference' }).click({ timeout: 10000 });
      const confirm = page.getByRole('dialog', { name: 'Delete @' + name + '?' });
      await confirm.getByRole('button', { name: 'Delete', exact: true }).click({ timeout: 10000 });
      await useButton(name).waitFor({ state: 'detached', timeout: 20000 });
      return finish(true, { deleted: name });
    }
    // New and Edit share one form: images, voices, title, description and category.
    const editing = cfg.action === 'edit';
    if (editing) {
      if (!await useButton(cfg.name).count()) return finish(false, { reason: 'no Grok reference named @' + cfg.name });
      await useButton(cfg.name).locator('..').getByRole('button', { name: 'Edit reference' }).dispatchEvent('click');
    } else {
      await page.getByRole('button', { name: 'New', exact: true }).click({ timeout: 10000 });
    }
    const dialog = page.getByRole('dialog').filter({
      has: page.getByRole('heading', { name: editing ? 'Edit reference' : 'New reference' }) });
    await dialog.waitFor({ timeout: 10000 });
    if (cfg.title) await dialog.getByRole('textbox', { name: 'Title' }).fill(cfg.title, { timeout: 10000 });
    if (typeof cfg.description === 'string') await dialog.getByRole('textbox', { name: 'Description' }).fill(cfg.description);
    const category = dialog.getByRole('combobox');
    if (cfg.category && (await category.innerText()).trim() !== cfg.category) {
      await category.click({ timeout: 10000 });
      await page.getByRole('option', { name: cfg.category, exact: true }).click({ timeout: 10000 });
    }
    if (cfg.replaceMedia) {
      // Each item's tile is itself a sortable role=button named "Remove media"; the real control is
      // the <button> inside it. Remove until none is left.
      const remove = dialog.locator('button[aria-label="Remove media"]');
      for (let i = 0; i < 40 && await remove.count(); i++) {
        await remove.first().dispatchEvent('click');
        await page.waitForTimeout(300);
      }
    }
    if (cfg.images.length) await dialog.locator('input[type="file"][accept^="image"]').setInputFiles(cfg.images);
    const deadline = Date.now() + cfg.timeoutMs;
    const clips = dialog.getByRole('button', { name: 'Edit audio', exact: true });
    for (const voice of cfg.voices) {
      // Each voice clip opens a Trim step (Cancel / Trim / Done) that hides the submit button; keep the whole clip.
      const count = await clips.count();
      await dialog.locator('input[type="file"][accept^="audio"]').setInputFiles(voice);
      await dialog.getByRole('button', { name: 'Done', exact: true }).click({ timeout: Math.max(1000, deadline - Date.now()) });
      // The clip's Edit audio button only shows on hover, so wait for it to exist.
      await waitCount(clips, count + 1, Math.max(1000, deadline - Date.now()));
    }
    const submit = dialog.getByRole('button', { name: editing ? 'Update' : 'Create', exact: true });
    while (!await submit.isEnabled()) {
      if (Date.now() > deadline) {
        return finish(false, { reason: 'Grok did not enable ' + (editing ? 'Update' : 'Create') + ' (a reference needs an image)' });
      }
      await page.waitForTimeout(500);
    }
    const hint = (await dialog.innerText()).match(/Type @(.+?) to use this reference/)?.[1];
    // A rejected reference (e.g. audio-not-allowed for a clip that is not usable as a voice) leaves
    // the dialog open with no message on the page, so read Grok's answer to the save request.
    const [response] = await Promise.all([
      page.waitForResponse(r => /\/rest\/media\/reference\/(?!list)/.test(r.url()) && r.request().method() !== 'GET',
                           { timeout: Math.max(1000, deadline - Date.now()) }).catch(() => null),
      submit.click({ timeout: 10000 }),
    ]);
    if (response && !response.ok()) {
      const message = (await response.json().catch(() => ({}))).message || 'HTTP ' + response.status();
      await dialog.getByRole('button', { name: 'Close', exact: true }).click({ timeout: 5000 }).catch(() => {});
      return finish(false, { reason: 'Grok rejected the reference: ' + message });
    }
    await dialog.waitFor({ state: 'hidden', timeout: Math.max(1000, deadline - Date.now()) });
    const name = hint || cfg.title || cfg.name;
    await useButton(name).waitFor({ timeout: 20000 });
    // Grok drops a new reference into the composer draft; leave the draft empty.
    await closeToolbox();
    await clearComposer();
    if (editing) return finish(true, { updated: name, references: await referenceCards() });
    return finish(true, { reference: name, references: await referenceCards() });
  } catch (error) {
    if (await signedOut().catch(() => false)) return loginFailure();
    return finish(false, { reason: (error.message || String(error)).split('\n')[0] });
  }
""")

LIBRARY_SCRIPT = _script(r"""
  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return loginFailure();
    await toolbox().or(visible('[role="dialog"]').filter({ hasText: /introducing|what.s new/i }).first())
      .first().waitFor({ timeout: 20000 });
    await dismissIntro();
    if (await signedOut()) return loginFailure();
    let items;
    if (cfg.source === 'voices') {
      const voices = page.getByRole('button', { name: /^Use [^@]/ });
      await openTab('Audio', voices);
      items = await voices.evaluateAll(buttons => buttons.map((b, i) => ({
        index: i + 1, name: b.getAttribute('aria-label').slice(4),
        duration: (b.parentElement.innerText.match(/\d+:\d\d/) || [null])[0] })));
    } else {
      const select = page.getByRole('button', { name: /^Select \w+ / });
      await openTab(cfg.source === 'generations' ? 'Generations' : 'Uploads', select);
      items = await select.evaluateAll(buttons => buttons.map((b, i) => {
        const [, type, name] = b.getAttribute('aria-label').match(/^Select (\w+) (.*)$/);
        return { index: i + 1, type, name };
      }));
    }
    await closeToolbox();
    return finish(true, { source: cfg.source, items });
  } catch (error) {
    if (await signedOut().catch(() => false)) return loginFailure();
    return finish(false, { reason: (error.message || String(error)).split('\n')[0] });
  }
""")

TEMPLATE_SCRIPT = _script(r"""
  // A template form is a list of labelled sections: image slots (Add reference), option groups
  // and text boxes. The boxes have no labels of their own, so each control takes the first text in
  // its nearest container that is not a control's own text; controls are tagged for later lookup.
  const templateSections = panel => panel.evaluate(p => {
    const own = c => (c.getAttribute('aria-label') || c.innerText || '').trim();
    const controls = [...p.querySelectorAll('[role="textbox"], textarea, input:not([type="file"]):not([type="hidden"]), button')]
      // Skip Submit, nested buttons, and the inline library picker that Add reference opens.
      .filter(c => !/^(Submit|Generations|Uploads|Upload media|Remove reference|Select \w+ .*)$/.test(own(c)) &&
                   !c.parentElement.closest('button'));
    const sections = [];
    const byKey = new Map();
    for (const control of controls) {
      let node = control.parentElement;
      let label = null;
      let extra = [];
      while (node && node !== p) {
        const taken = new Set([...node.querySelectorAll('button, [role="textbox"], textarea, input')]
          .flatMap(b => (b.innerText || '').split('\n').map(s => s.trim())));
        const free = node.innerText.split('\n').map(s => s.trim()).filter(s => s && !taken.has(s));
        if (free.length) {
          [label, ...extra] = free;
          break;
        }
        node = node.parentElement;
      }
      const kind = control.matches('button') ? (own(control) === 'Add reference' ? 'images' : 'choice') : 'text';
      let section = byKey.get(label + '|' + kind);
      if (!section) {
        section = { index: sections.length, label, kind, options: [],
                    optional: /optional/i.test([label, ...extra.slice(0, 1)].join(' ')) };
        if (kind === 'images') section.limit = extra.find(line => /\bimages?\b/.test(line)) || null;
        byKey.set(label + '|' + kind, section);
        sections.push(section);
      }
      control.setAttribute('data-aao-section', String(section.index));
      if (kind === 'choice') {
        control.setAttribute('data-aao-option', section.index + '-' + section.options.length);
        section.options.push(own(control).replace(/\s*\n\s*/g, ' '));
      }
    }
    return sections;
  });
  const selected = button => button.evaluate(b => ['aria-pressed', 'aria-checked', 'aria-selected']
    .some(name => b.getAttribute(name) === 'true') || b.dataset.state === 'on' || b.dataset.selected === 'true');
  let step = 'opening the template';
  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return loginFailure();
    const links = page.locator('a[href^="/imagine/template/"]');
    await links.first().waitFor({ timeout: 20000 });
    const templates = await links.evaluateAll(as => as.map(a => ({
      slug: a.getAttribute('href').split('/').pop(), name: a.innerText.trim() })));
    if (cfg.action === 'list') return finish(true, { templates });
    // Accept the slug with or without "imagine-", or the display name ("BG Removal & Change").
    const key = text => (text || '').toLowerCase().replace(/^imagine-/, '').replace(/[^a-z0-9]+/g, '');
    const match = templates.find(t => key(t.slug) === key(cfg.slug) || key(t.name) === key(cfg.query));
    if (!match) {
      return finish(false, { reason: 'no Imagine template ' + JSON.stringify(cfg.query) +
                                     ' (have: ' + templates.map(t => t.name).join(', ') + ')' });
    }
    cfg.slug = match.slug;
    await page.goto(cfg.url + '/template/' + cfg.slug, { waitUntil: 'domcontentloaded', timeout: 30000 });
    const panel = page.getByRole('complementary').last();
    const submit = panel.getByRole('button', { name: 'Submit', exact: true });
    await submit.waitFor({ timeout: 20000 });
    const sections = await templateSections(panel);
    const info = { slug: cfg.slug, name: (await panel.getByRole('heading').first().innerText()).trim(),
                   description: (await panel.locator('p').first().innerText().catch(() => '')).trim(),
                   sections: sections.map(({ index, ...section }) => section) };
    if (cfg.action === 'show') return finish(true, { template: info });
    const norm = text => (text || '').toLowerCase().replace(/\(optional\)/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
    // React re-renders the form after each upload or choice, dropping the tags, so tag it again.
    const find = async (label, kind) => {
      const of = (await templateSections(panel)).filter(section => section.kind === kind);
      if (label === null) return of[0];
      const want = norm(label);
      return of.find(section => norm(section.label) === want) || of.find(section => norm(section.label).startsWith(want));
    };
    const missing = (label, kind) => finish(false, {
      reason: 'no ' + kind + ' section' + (label ? ' ' + JSON.stringify(label) : '') + ' in ' + cfg.slug, template: info });
    const added = panel.getByRole('button', { name: 'Remove reference', exact: true });
    const uploadMedia = panel.getByRole('button', { name: 'Upload media', exact: true });
    for (const [label, file] of cfg.images) {
      step = 'adding image ' + file.split('/').pop();
      const section = await find(label, 'images');
      if (!section) return missing(label, 'images');
      const count = await added.count();
      // Add reference opens an inline picker (Generations, Uploads, Upload media) that stays open after
      // an upload and can sit under other parts of the form, so click Upload media in the DOM.
      if (!await uploadMedia.count()) {
        await panel.locator('[data-aao-section="' + section.index + '"]').first().click({ timeout: 10000 });
        await uploadMedia.first().waitFor({ state: 'attached', timeout: 10000 });
      }
      await pickFiles(uploadMedia.first(), file, true);
      await waitCount(added, count + 1);
    }
    for (const [label, option] of cfg.choices) {
      step = 'choosing ' + option;
      const section = await find(label, 'choice');
      if (!section) return missing(label, 'choice');
      const want = norm(option);
      let j = section.options.findIndex(o => norm(o) === want);
      if (j < 0) j = section.options.findIndex(o => norm(o).startsWith(want));
      if (j < 0) {
        return finish(false, { reason: 'no option ' + JSON.stringify(option) + ' under ' + section.label +
                                       ' (have: ' + section.options.join(', ') + ')', template: info });
      }
      const button = panel.locator('[data-aao-option="' + section.index + '-' + j + '"]');
      if (!await selected(button)) await button.click({ timeout: 10000 });
    }
    for (const [label, value] of cfg.texts) {
      step = 'filling ' + label;
      const section = await find(label, 'text');
      if (!section) return missing(label, 'text');
      await panel.locator('[data-aao-section="' + section.index + '"]').first().fill(value, { timeout: 10000 });
    }
    const state = async () => {
      const picked = [];
      for (const button of await panel.locator('[data-aao-option]').all()) {
        if (await selected(button)) picked.push(((await button.getAttribute('aria-label')) || await button.innerText()).replace(/\s*\n\s*/g, ' '));
      }
      return { ready: await submit.isEnabled(), images: await added.count(), selected: picked };
    };
    if (cfg.dryRun) return finish(true, { dry_run: true, template: info, ...await state() });
    if (!await submit.isEnabled()) {
      return finish(false, { reason: 'Submit is disabled: fill the required sections (see template show)', template: info, ...await state() });
    }
    const deadline = Date.now() + cfg.timeoutMs;
    // Results are collected like @reference generations: new /generated/ media on the page or in
    // the sidebar's recent media.
    const before = await mediaKeys('media', '/generated/', true);
    await submit.click({ timeout: 10000 });
    const sources = await awaitMedia(before, 'media', '/generated/', deadline, Infinity, true);
    if (!sources.length) return finish(false, { reason: 'timed out waiting for the template results' });
    return await saveAll(sources, { template: info.name });
  } catch (error) {
    if (await signedOut().catch(() => false)) return loginFailure();
    return finish(false, { reason: step + ': ' + (error.message || String(error)).split('\n')[0] });
  }
""")

DRAW_SCRIPT = _script(r"""
  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return loginFailure();
    const panel = page.getByRole('complementary', { name: 'Draw' });
    await panel.waitFor({ timeout: 20000 });
    const clear = page.getByRole('button', { name: 'Clear canvas' });
    if (await clear.isEnabled()) {
      await clear.click({ timeout: 10000 });
      const confirm = visible('[role="dialog"], [role="alertdialog"]').getByRole('button', { name: /^(clear|confirm|delete)$/i });
      if (await confirm.first().isVisible().catch(() => false)) await confirm.first().click({ timeout: 5000 });
    }
    if (cfg.style) {
      await panel.getByRole('button', { name: /^(Manga|Illustration|Painting|Realistic)$/ }).click({ timeout: 10000 });
      await page.getByRole('menuitemradio', { name: cfg.style, exact: true }).click({ timeout: 10000 });
    }
    if (cfg.aspect) {
      await panel.getByRole('button', { name: /^\d+:\d+$/ }).click({ timeout: 10000 });
      await page.getByRole('menuitemradio', { name: new RegExp('^' + escape(cfg.aspect) + '(\\s|$)') }).click({ timeout: 10000 });
      await page.waitForTimeout(800);
    }
    const slider = panel.getByRole('slider', { name: 'Brush size' });
    const brushSize = async () => Number(await slider.getAttribute('aria-valuenow'));
    const setBrush = async size => {
      await slider.focus();
      let now = await brushSize();
      const key = now < size ? 'ArrowRight' : 'ArrowLeft';
      for (let i = 0; i < 400 && now !== size; i++) {
        await page.keyboard.press(key);
        const next = await brushSize();
        if (next === now || (key === 'ArrowRight' ? next > size : next < size)) break;
        now = next;
      }
    };
    const setColor = async hex => {
      const preset = panel.getByRole('button', { name: 'Use color ' + hex, exact: true });
      if (await preset.count()) return preset.click({ timeout: 10000 });
      // A hidden native <input type="color">; fill() sets it without opening the OS picker.
      await panel.getByRole('button', { name: 'Custom color' }).locator('input[type="color"]').first()
        .fill(hex, { timeout: 10000 });
    };
    if (cfg.brush) await setBrush(cfg.brush);
    const canvas = page.locator('canvas').filter({ visible: true }).first();
    const box = await canvas.boundingBox();
    let strokes = cfg.strokes || [];
    if (cfg.sketch) {
      // The Draw page redraws from its own stroke list, so a sketch is replayed as brush strokes.
      const palette = (await panel.getByRole('button', { name: /^Use color #/ })
        .evaluateAll(buttons => buttons.map(b => b.getAttribute('aria-label').slice(10).toLowerCase())))
        .filter(color => color !== '#ffffff');
      const traced = await page.evaluate(async ({ data, w, h, step, palette }) => {
        const image = new Image();
        image.src = data;
        await image.decode();
        const off = document.createElement('canvas');
        off.width = w;
        off.height = h;
        const ctx = off.getContext('2d', { willReadFrequently: true });
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        const scale = Math.min(w / image.naturalWidth, h / image.naturalHeight);
        const dw = image.naturalWidth * scale;
        const dh = image.naturalHeight * scale;
        ctx.drawImage(image, (w - dw) / 2, (h - dh) / 2, dw, dh);
        const px = ctx.getImageData(0, 0, w, h).data;
        const rgb = palette.map(hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)));
        const black = palette.indexOf('#000000');
        const classify = (r, g, b) => {
          if (0.2126 * r + 0.7152 * g + 0.0722 * b > 200) return -1;
          if (Math.max(r, g, b) - Math.min(r, g, b) < 48 && black >= 0) return black;
          let best = 0;
          let dist = Infinity;
          rgb.forEach(([pr, pg, pb], i) => {
            if (i === black) return;
            const d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2;
            if (d < dist) { dist = d; best = i; }
          });
          return best;
        };
        const runs = [];
        for (let y = Math.floor(step / 2); y < h; y += step) {
          let start = 0;
          let color = -1;
          for (let x = 0; x <= w; x++) {
            const i = (y * w + x) * 4;
            const k = x < w ? classify(px[i], px[i + 1], px[i + 2]) : -1;
            if (k === color) continue;
            if (color >= 0) runs.push({ color: palette[color], points: [[start / w, y / h], [(x - 1) / w, y / h]] });
            start = x;
            color = k;
          }
        }
        return runs;
      }, { data: cfg.sketch, w: Math.round(box.width), h: Math.round(box.height),
           step: Math.max(2, await brushSize()), palette });
      if (traced.length > cfg.maxStrokes) {
        return finish(false, { reason: 'the sketch needs ' + traced.length + ' strokes; use a simpler image or a larger --brush' });
      }
      strokes = strokes.concat(traced.sort((a, b) => (a.color < b.color ? -1 : a.color > b.color ? 1 : 0)));
    }
    if (strokes.length) await panel.getByRole('button', { name: 'Brush', exact: true }).click({ timeout: 10000 });
    let color = null;
    for (const stroke of strokes) {
      if (stroke.size && stroke.size !== await brushSize()) await setBrush(stroke.size);
      const hex = (stroke.color || '#000000').toLowerCase();
      if (hex !== color) {
        await setColor(hex);
        color = hex;
      }
      const points = stroke.points.map(([x, y]) => [box.x + x * box.width, box.y + y * box.height]);
      await page.mouse.move(...points[0]);
      await page.mouse.down();
      for (const point of points.slice(1)) await page.mouse.move(...point, { steps: 2 });
      if (points.length === 1) await page.mouse.move(points[0][0] + 0.5, points[0][1]);
      await page.mouse.up();
    }
    await panel.getByRole('textbox', { name: /Describe what to imagine/ }).fill(cfg.prompt, { timeout: 10000 });
    const deadline = Date.now() + cfg.timeoutMs;
    await panel.getByRole('button', { name: 'Generate', exact: true }).click({ timeout: 10000 });
    await page.waitForURL(/\/imagine\/post\/[\w-]+/, { timeout: cfg.timeoutMs });
    const post = page.url().match(/\/imagine\/post\/([\w-]+)/)[1];
    const sources = await awaitMedia(new Set(), 'image', '/generated/' + post + '/', deadline);
    if (!sources.length) return finish(false, { reason: 'timed out waiting for the drawn image', strokes: strokes.length });
    return await saveAll(sources, { strokes: strokes.length });
  } catch (error) {
    if (await signedOut().catch(() => false)) return loginFailure();
    return finish(false, { reason: (error.message || String(error)).split('\n')[0] });
  }
""")

CANVAS_SCRIPT = _script(r"""
  try {
    await page.goto(cfg.canvas || cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return loginFailure();
    if (cfg.action === 'list') {
      // A canvas is a project (workspace) holding an Imagine conversation; read the site's own endpoints.
      const canvases = await page.evaluate(async () => {
        const get = async path => (await fetch(path, { credentials: 'include' })).json();
        const found = [];
        for (const space of (await get('/rest/workspaces?pageSize=100')).workspaces || []) {
          const chats = (await get('/rest/app-chat/conversations?pageSize=50&workspaceId=' + space.workspaceId)).conversations || [];
          for (const chat of chats.filter(c => c.kind === 'CONVERSATION_KIND_IMAGINE')) {
            found.push({ project: space.name, title: chat.title, id: space.workspaceId, conversation: chat.conversationId,
                         updated: chat.modifyTime,
                         canvas_url: location.origin + '/imagine/agent/' + space.workspaceId + '?conversation=' + chat.conversationId });
          }
        }
        return found;
      });
      return finish(true, { canvases });
    }
    if (!cfg.canvas) {
      const create = page.getByRole('button', { name: 'New canvas' });
      await create.or(visible('[role="dialog"]').filter({ hasText: /introducing|what.s new/i }).first())
        .first().waitFor({ timeout: 20000 });
      await dismissIntro();
      await create.click({ timeout: 10000 });
      await page.waitForURL(/\/imagine\/agent\//, { timeout: 30000 });
    }
    const box = page.getByRole('textbox', { name: 'Message Grok' });
    await box.waitFor({ timeout: 30000 });
    // Board tools: Upload image puts local images on the board; Text adds a note where it is clicked.
    const board = page.getByRole('application');
    if (cfg.boardImages.length) {
      await page.getByRole('toolbar', { name: 'Canvas toolbar' }).waitFor({ timeout: 20000 });
      const shown = board.locator('img');
      const count = await shown.count();
      await pickFiles(page.getByRole('button', { name: 'Upload image', exact: true }), cfg.boardImages);
      await waitCount(shown, count + cfg.boardImages.length);
    }
    for (const [i, note] of cfg.notes.entries()) {
      const area = await board.boundingBox();
      await page.getByRole('button', { name: 'Text', exact: true }).click({ timeout: 10000 });
      await page.mouse.click(area.x + area.width * (0.1 + 0.06 * (i % 4)), area.y + area.height * (0.12 + 0.1 * (i % 7)));
      await page.waitForTimeout(500);
      await page.keyboard.insertText(note);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
      if (!(await board.innerText()).includes(note)) return finish(false, { reason: 'the note did not appear on the board: ' + note });
    }
    const placed = { notes: cfg.notes.length, board_images: cfg.boardImages.length };
    if (!cfg.prompt) return finish(true, { canvas_url: page.url(), ...placed });
    if (cfg.files.length) {
      // Attach files opens a menu ("Upload a file" / "Files") whose upload item feeds this any-type
      // input. Set it directly: a native file chooser becomes a modal that blocks playwright-cli.
      const attached = page.getByRole('region', { name: 'Attachments' }).getByRole('button', { name: /^Remove / });
      const already = await attached.count();
      await page.locator('input[type="file"]:not([accept]), input[type="file"][accept=""]').first()
        .setInputFiles(cfg.files, { timeout: 10000 });
      const uploadDeadline = Date.now() + 60000;
      while (await attached.count() < already + cfg.files.length) {
        if (Date.now() > uploadDeadline) return finish(false, { reason: 'Grok did not accept the attachments' });
        await page.waitForTimeout(500);
      }
      await page.waitForTimeout(1500);
    }
    const done = page.getByRole('toolbar', { name: 'Response actions' });
    const turns = await done.count();
    const before = await mediaKeys('media', '/generated/');
    await box.fill(cfg.prompt, { timeout: 10000 });
    await page.getByRole('button', { name: 'Send', exact: true }).click({ timeout: 10000 });
    const deadline = Date.now() + cfg.timeoutMs;
    while (await done.count() <= turns) {
      if (Date.now() > deadline) return finish(false, { reason: 'timed out waiting for the Grok reply', canvas_url: page.url() });
      await page.waitForTimeout(1000);
    }
    const lines = (await page.getByRole('article', { name: 'Grok' }).last().innerText())
      .split('\n').map(line => line.trim()).filter(Boolean);
    if (lines[0] === 'Grok') lines.shift();
    const thinking = /^Thought for /.test(lines[0] || '') ? lines.shift() : null;
    const sources = await awaitMedia(before, 'media', '/generated/', Math.min(deadline, Date.now() + 120000), 4000);
    return await saveAll(sources, { canvas_url: page.url(), response: lines.join('\n'), thinking, ...placed });
  } catch (error) {
    if (await signedOut().catch(() => false)) return loginFailure();
    return finish(false, { reason: (error.message || String(error)).split('\n')[0] });
  }
""")


def _validate_prompt(prompt, timeout_s):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("a nonempty prompt is required")
    _validate_timeout(timeout_s)


def _validate_timeout(timeout_s):
    if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be a positive number of seconds")


def _check(name, value, kind):
    if value is not None and value not in get_args(kind):
        raise ValueError(f"{name} must be one of {', '.join(get_args(kind))}")


def _files(paths, what):
    resolved = [Path(path).expanduser().resolve(strict=True) for path in paths or ()]
    if any(not path.is_file() for path in resolved):
        raise ValueError(f"all {what} must be files")
    return [path.as_posix() for path in resolved]


def _mentions(names):
    cleaned = [str(name).strip().lstrip("@") for name in names or ()]
    if any(not name for name in cleaned):
        raise ValueError("reference names must be nonempty")
    return cleaned


def _output(download_dir, prompt, kind):
    folder = Path(download_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (prompt or "").lower()).strip("-")[:40] or "grok"
    return folder.as_posix(), f"{slug}-{kind}-{uuid4().hex[:10]}"


def _run(script, payload, timeout_s, headed=None):
    with _browser:
        ok, out, error = web.open_target("grok", headed=headed)
        if not ok:
            return {"ok": False, "error": error or out}
        try:
            return web.run_script("grok", script.replace("__CFG__", json.dumps(payload), 1), timeout_s)
        finally:
            web.release("grok")


def chat(prompt: str, *, timeout_s: float = 120, chat_ref: str | None = None,
         expect: str | None = None) -> dict:
    """Send a Grok turn and return its reply, reasoning header, and conversation URL."""
    _validate_prompt(prompt, timeout_s)
    with _browser:
        result = web.ask("grok", prompt, expect, timeout_s=timeout_s, chat=chat_ref)
    return {**result, "prompt": prompt, "response": result.get("reply"),
            "thinking": result.get("thinking"), "conversation_url": result.get("url")}


def generate_image(prompt: str, *, quality: Quality | None = None, aspect_ratio: Aspect | None = None,
                   count: Count | None = None, references=(), mentions=(), picks=(), dry_run: bool = False,
                   download_dir=DEFAULT_OUT, timeout_s: float = 300) -> dict:
    """Generate Grok Imagine images and save them to download_dir.

    `references` are local images uploaded with the prompt; `mentions` are saved Imagine
    references (@Name) inserted before the prompt; `picks` attach items from the Imagine library
    ("generation:N" or "upload:NAME|N"). `dry_run` sets everything up and stops before Submit."""
    return _generate("image", prompt, download_dir, timeout_s, quality=quality, references=references,
                     mentions=mentions, picks=picks, dry_run=dry_run, aspect_ratio=aspect_ratio, count=count)


def generate_video(prompt: str, *, resolution: Resolution = "480p", duration: Duration = "6s",
                   aspect_ratio: Aspect | None = None, image=None, last_frame=None, middle_frame=None,
                   loop: bool = False, audio: bool | None = None, voice: str | None = None, mentions=(),
                   picks=(), dry_run: bool = False, download_dir=DEFAULT_OUT, timeout_s: float = 600) -> dict:
    """Generate a Grok Imagine video.

    `image` is the first frame; `middle_frame` and `last_frame` add an intermediate and a final
    frame. `loop` starts and ends on `image`. `audio` turns the Video audio switch on or off;
    `voice` attaches a preset voice from the Audio tab (see list_library("voices"))."""
    return _generate("video", prompt, download_dir, timeout_s, image=image, last_frame=last_frame,
                     middle_frame=middle_frame, loop=loop, audio=audio, voice=voice, mentions=mentions,
                     picks=picks, dry_run=dry_run, resolution=resolution, duration=duration,
                     aspect_ratio=aspect_ratio)


def _picks(picks):
    """Library picks: "generation:N" (1 = newest) or "upload:NAME" / "upload:N"."""
    parsed = []
    for pick in picks or ():
        source, _, which = str(pick).partition(":")
        source = {"generation": "generation", "generations": "generation",
                  "upload": "upload", "uploads": "upload"}.get(source.strip().lower())
        which = which.strip()
        if not source or not which:
            raise ValueError("a pick is generation:N or upload:NAME (or upload:N)")
        if which.isdigit() and int(which) > 0:
            parsed.append({"source": source, "index": int(which)})
        elif source == "upload":
            parsed.append({"source": source, "name": which})
        else:
            raise ValueError("generations are picked by position: generation:1 is the newest")
    return parsed


def list_library(source: Library = "generations", *, timeout_s: float = 120) -> dict:
    """List the toolbox library: your generations, your uploads, or the preset voices."""
    _check("source", source, Library)
    _validate_timeout(timeout_s)
    payload = {"source": source, "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(LIBRARY_SCRIPT, payload, timeout_s + 60)

def list_references(category: Category | None = None, *, timeout_s: float = 120) -> dict:
    """List the saved Imagine references (@Name), optionally only one category."""
    _check("category", category, Category)
    _validate_timeout(timeout_s)
    payload = {"action": "list", "category": category, "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(REFERENCES_SCRIPT, payload, timeout_s + 60)


def create_reference(title: str, *, images=(), voices=(), description: str | None = None,
                     category: Category = "Character", timeout_s: float = 120) -> dict:
    """Create a saved Imagine reference from local images and optional voice clips."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("a reference title is required")
    _check("category", category, Category)
    _validate_timeout(timeout_s)
    images, voices = _files(images, "reference images"), _files(voices, "voice clips")
    if not images:
        raise ValueError("a reference needs at least one image")
    payload = {"action": "create", "title": title.strip(), "description": description, "category": category,
               "images": images, "voices": voices, "timeoutMs": timeout_s * 1000,
               "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(REFERENCES_SCRIPT, payload, timeout_s + 90)


def edit_reference(name: str, *, title: str | None = None, description: str | None = None,
                   images=(), voices=(), replace_media: bool = False, timeout_s: float = 120) -> dict:
    """Edit a saved Imagine reference: rename it, change its description, add images or voice
    clips, or (replace_media) remove its current media first. Grok fixes the category at creation."""
    (name,) = _mentions([name])
    _validate_timeout(timeout_s)
    if title is not None and not title.strip():
        raise ValueError("a new title must be nonempty")
    images, voices = _files(images, "reference images"), _files(voices, "voice clips")
    if replace_media and not images:
        raise ValueError("replacing the media needs at least one new image")
    if title is None and description is None and not images and not voices:
        raise ValueError("nothing to change")
    payload = {"action": "edit", "name": name, "title": title.strip() if title else None,
               "description": description, "category": None, "images": images, "voices": voices,
               "replaceMedia": replace_media, "timeoutMs": timeout_s * 1000,
               "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(REFERENCES_SCRIPT, payload, timeout_s + 90)


def delete_reference(name: str, *, timeout_s: float = 120) -> dict:
    """Delete a saved Imagine reference by its @Name."""
    (name,) = _mentions([name])
    _validate_timeout(timeout_s)
    payload = {"action": "delete", "name": name, "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(REFERENCES_SCRIPT, payload, timeout_s + 60)


def _strokes(strokes):
    """Validate strokes: [{"points": [[x, y], ...], "color": "#rrggbb", "size": n}], x and y in 0..1."""
    if isinstance(strokes, (str, Path)):
        strokes = json.loads(Path(strokes).expanduser().read_text(encoding="utf-8-sig"))
    checked = []
    for stroke in strokes or ():
        points = stroke.get("points") if isinstance(stroke, dict) else None
        if not points or not all(isinstance(p, (list, tuple)) and len(p) == 2 and
                                 all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p) for p in points):
            raise ValueError("each stroke needs points [[x, y], ...] with x and y between 0 and 1")
        color = stroke.get("color", "#000000")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)):
            raise ValueError("stroke colors must be #rrggbb")
        size = stroke.get("size")
        if size is not None and (not isinstance(size, int) or size <= 0):
            raise ValueError("stroke size must be a positive integer")
        checked.append({"points": [list(p) for p in points], "color": color.lower(), "size": size})
    return checked


def draw(prompt: str, *, sketch=None, strokes=None, style: DrawStyle | None = None,
         aspect_ratio: DrawAspect | None = None, brush: int | None = None, max_strokes: int = 6000,
         download_dir=DEFAULT_OUT, timeout_s: float = 300) -> dict:
    """Generate from the Imagine Draw canvas.

    `sketch` is a local image replayed as brush strokes (colors snap to the Draw palette, one
    row per brush width); `strokes` is a list or JSON file of strokes in 0..1 canvas coordinates."""
    _validate_prompt(prompt, timeout_s)
    _check("style", style, DrawStyle)
    _check("aspect_ratio", aspect_ratio, DrawAspect)
    if brush is not None and (not isinstance(brush, int) or brush <= 0):
        raise ValueError("brush must be a positive integer")
    checked = _strokes(strokes)
    data = None
    if sketch is not None:
        (path,) = _files([sketch], "sketches")
        mime = mimetypes.guess_type(path)[0]
        if not mime or not mime.startswith("image/"):
            raise ValueError("the sketch must be an image file")
        data = f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"
    out, filename = _output(download_dir, prompt, "draw")
    payload = {"kind": "image", "url": f"{IMAGINE_URL}/draw", "loginUrl": web.LOGIN_URL, "prompt": prompt,
               "style": style, "aspect": aspect_ratio, "brush": brush, "strokes": checked, "sketch": data,
               "maxStrokes": max_strokes, "out": out, "filename": filename, "timeoutMs": timeout_s * 1000}
    # Replaying a sketch moves the mouse once per stroke.
    result = _run(DRAW_SCRIPT, payload, timeout_s + 600)
    return {"prompt": prompt, "mode": "Draw", "style": style, "aspect_ratio": aspect_ratio, **result}


def canvas_url(ref: str) -> str:
    """An Agent canvas URL from a URL or the agent ID in it."""
    ref = ref.strip()
    if re.fullmatch(r"[0-9a-fA-F-]{36}", ref):
        return f"{IMAGINE_URL}/agent/{ref}"
    if not re.match(r"^https://grok\.com/imagine/agent/[0-9a-fA-F-]{36}([?#].*)?$", ref):
        raise ValueError("a canvas is an https://grok.com/imagine/agent/<id> URL or its ID")
    return ref


def canvas(prompt: str | None = None, *, canvas_ref: str | None = None, files=(), notes=(), board_images=(),
           download_dir=DEFAULT_OUT, timeout_s: float = 600) -> dict:
    """Create an Imagine Agent canvas (New canvas), or open one, and optionally send a turn.

    `notes` are text notes and `board_images` local images placed on the board with its Text
    and Upload image tools before the turn. The agent works on the board and replies in chat;
    new media from the turn is downloaded."""
    if prompt is not None:
        _validate_prompt(prompt, timeout_s)
    else:
        _validate_timeout(timeout_s)
        if files:
            raise ValueError("attachments need a prompt")
    notes = [str(note) for note in notes or ()]
    if any(not note.strip() for note in notes):
        raise ValueError("notes must be nonempty")
    url = canvas_url(canvas_ref) if canvas_ref else None
    out, filename = _output(download_dir, prompt, "canvas")
    payload = {"kind": "media", "url": IMAGINE_URL, "canvas": url, "loginUrl": web.LOGIN_URL, "prompt": prompt,
               "files": _files(files, "attachments"), "notes": notes,
               "boardImages": _files(board_images, "board images"), "out": out, "filename": filename,
               "timeoutMs": timeout_s * 1000}
    result = _run(CANVAS_SCRIPT, payload, timeout_s + 1100)
    return {"prompt": prompt, "mode": "Agent", **result}


def list_canvases(*, timeout_s: float = 120) -> dict:
    """List Agent canvases: projects that hold an Imagine conversation, with their canvas URLs."""
    _validate_timeout(timeout_s)
    payload = {"action": "list", "kind": "media", "url": IMAGINE_URL, "canvas": None, "loginUrl": web.LOGIN_URL}
    return _run(CANVAS_SCRIPT, payload, timeout_s + 60)


def _template_slug(template: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(template).strip().lower()).strip("-")
    if not slug:
        raise ValueError("a template slug or name is required")
    return slug if slug.startswith("imagine-") else f"imagine-{slug}"


def _pairs(values, what):
    """SECTION=value pairs; a bare value has no section (the first matching section is used)."""
    pairs = []
    for value in values or ():
        if isinstance(value, (list, tuple)):
            label, item = value
        else:
            label, sep, item = str(value).partition("=")
            if not sep:
                label, item = None, label
        if not str(item).strip():
            raise ValueError(f"{what} needs a value")
        pairs.append([label.strip() if label else None, str(item)])
    return pairs


def list_templates(*, timeout_s: float = 120) -> dict:
    """List the Imagine templates (slug and name) shown on the Imagine page."""
    _validate_timeout(timeout_s)
    payload = {"action": "list", "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(TEMPLATE_SCRIPT, payload, timeout_s + 60)


def template_info(template: str, *, timeout_s: float = 120) -> dict:
    """Describe a template's form: its image slots, option groups and text boxes."""
    _validate_timeout(timeout_s)
    payload = {"action": "show", "slug": _template_slug(template), "query": str(template), "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL}
    return _run(TEMPLATE_SCRIPT, payload, timeout_s + 60)


def run_template(template: str, *, images=(), choices=(), texts=(), dry_run: bool = False,
                 download_dir=DEFAULT_OUT, timeout_s: float = 600) -> dict:
    """Fill an Imagine template and download its results.

    Each image is a path or "SECTION=path"; choices are "SECTION=OPTION" and texts are
    "SECTION=text". SECTION is the label template_info shows, matched case-insensitively by
    prefix; a bare image goes to the first image section."""
    _validate_timeout(timeout_s)
    slug = _template_slug(template)
    image_pairs = _pairs(images, "an image")
    for pair in image_pairs:
        (pair[1],) = _files([pair[1]], "template images")
    choice_pairs, text_pairs = _pairs(choices, "a choice"), _pairs(texts, "a text")
    if any(label is None for label, _ in choice_pairs + text_pairs):
        raise ValueError("choices and texts need SECTION=value")
    out, filename = _output(download_dir, slug.removeprefix("imagine-"), "template")
    payload = {"action": "run", "kind": "media", "slug": slug, "query": str(template), "images": image_pairs, "choices": choice_pairs,
               "texts": text_pairs, "dryRun": dry_run, "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL,
               "out": out, "filename": filename, "timeoutMs": timeout_s * 1000}
    result = _run(TEMPLATE_SCRIPT, payload, timeout_s + 1100)
    return {"mode": "Template", "slug": slug, **result}


AUDIO_DOWNLOAD_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  try {
    const response = page.locator('[data-testid="assistant-message"]').last();
    const attachment = response.locator('audio').filter({ visible: true });
    await attachment.first().waitFor({ timeout: 12000 });
    const source = await attachment.evaluateAll((players, filename) => {
      for (const player of players) {
        const src = player.currentSrc || player.src;
        if (!src) continue;
        const url = new URL(src, location.href);
        if (url.origin === 'https://assets.grok.com' && !url.username && !url.password &&
            decodeURIComponent(url.pathname.split('/').pop()) === filename) return url.href;
      }
      return null;
    }, cfg.filename);
    if (!source) return { ok: false, reason: 'Grok did not attach the requested standalone WAV' };
    const file = await page.request.get(source, { timeout: 45000, maxRedirects: 0 });
    let data;
    try {
      if (!file.ok()) throw Error('Audio download returned HTTP ' + file.status());
      const bytes = await file.body();
      if (bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE')
        throw Error('Grok did not return a valid WAV attachment');
      data = bytes.toString('base64');
    } finally {
      await file.dispose();
    }
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      page.evaluate(data => {
        const link = document.createElement('a');
        link.href = 'data:audio/wav;base64,' + data;
        link.download = 'Grok audio.wav';
        document.body.append(link);
        link.click();
        link.remove();
      }, data),
    ]);
    await download.saveAs(cfg.path);
    return { ok: true, path: cfg.path };
  } catch (error) {
    return { ok: false, reason: String(error.message).split('\n')[0] };
  }
}"""


def generate_audio(prompt: str, directory, timeout_s: float = 240) -> dict:
    """Ask Grok chat for an original WAV and save the playable attachment."""
    _validate_prompt(prompt, timeout_s)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"aao-{uuid4().hex[:10]}.wav"
    brief = (
        f"Create original audio from the brief below. Generate audio only in this chat and attach one "
        f"playable, downloadable WAV file named {filename}. Use the audio tool or run your code tool to "
        "synthesize the WAV in your sandbox if necessary. Return the actual attachment, with no video, "
        f"code listing, or instructions. Keep the result in this chat.\n\n{prompt}"
    )
    with _browser:
        ok, _, error = web.open_target("grok")
        if not ok:
            return {"ok": False, "reason": error or "Could not open Grok in Chrome"}
        try:
            reply = web._run_page("grok", brief, timeout_s=timeout_s)
            if not reply.get("ok"):
                return reply
            cfg = {"filename": filename, "path": (directory / "Grok audio.wav").as_posix()}
            return web.run_script("grok", AUDIO_DOWNLOAD_SCRIPT.replace("__CFG__", json.dumps(cfg), 1), 100)
        finally:
            web.release("grok")


def attach(config) -> dict:
    """Use a Playwright configuration with browser.cdpEndpoint for the Grok session."""
    path = Path(config).expanduser().resolve(strict=True)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    browser = data.get("browser") if isinstance(data, dict) else None
    endpoint = browser.get("cdpEndpoint") if isinstance(browser, dict) else None
    if not isinstance(endpoint, str) or not re.match(r"^(https?|wss?)://[^/]+", endpoint):
        raise ValueError("Playwright config requires a browser.cdpEndpoint URL")
    with _browser:
        if web.is_open("grok"):
            raise ValueError("close the existing session with aao web close grok first")
        ok, _, _ = web._pw("grok", "open", "--config", str(path), web.TARGETS["grok"]["url"])
    # Connection errors can contain the CDP endpoint's credentials.
    return {"ok": True} if ok else {"ok": False, "error": "could not attach to the configured CDP browser"}


def _generate(kind, prompt, download_dir, timeout_s, *, image=None, references=(), mentions=(), picks=(),
              last_frame=None, middle_frame=None, loop=False, audio=None, voice=None, dry_run=False, **options):
    _validate_prompt(prompt, timeout_s)
    allowed = {"quality": Quality, "aspect_ratio": Aspect, "count": Count,
               "resolution": Resolution, "duration": Duration}
    for name, value in options.items():
        _check(name, value, allowed[name])
    reference = None
    if image is not None:
        path = Path(image).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"reference image is not a file: {path}")
        reference = path.as_posix()
    if (last_frame or middle_frame) and not reference:
        raise ValueError("an intermediate or last frame needs a first frame (image)")
    if loop and not reference:
        raise ValueError("a loop needs the image it starts and ends on")
    if loop and last_frame:
        raise ValueError("a loop ends on its first frame, so it takes no last frame")
    if voice is not None and not str(voice).strip():
        raise ValueError("a voice name must be nonempty")
    uploads = _files(references, "reference images")
    names = _mentions(mentions)
    frames = {key: _files([value], "frames")[0] if value else None
              for key, value in (("lastFrame", last_frame), ("middleFrame", middle_frame))}
    out, filename = _output(download_dir, prompt, kind)
    radios = [options[key] for key in ("quality", "resolution", "duration") if options.get(key)]
    menus = [(label, options[key]) for label, key in (("Aspect Ratio", "aspect_ratio"), ("Image Count", "count"))
             if options.get(key)]
    payload = {"kind": kind, "mode": kind.title(), "url": IMAGINE_URL, "loginUrl": web.LOGIN_URL,
               "prompt": prompt, "radios": radios, "menus": menus, "image": reference, "mentions": names,
               "picks": _picks(picks), "loop": bool(loop), "audio": audio,
               "voice": str(voice).strip() if voice else None, "dryRun": bool(dry_run), **frames,
               "out": out, "filename": filename, "timeoutMs": timeout_s * 1000}
    if uploads:
        payload["references"] = uploads
    # The reference SDK observed video generation completing in a visible Chrome window.
    # Each download has its own timeout, and one result batch can contain twelve files.
    result = _run(IMAGINE_SCRIPT, payload, timeout_s + 1100, headed=True if kind == "video" else None)
    return {"prompt": prompt, "mode": kind.title(), **options, **result}
