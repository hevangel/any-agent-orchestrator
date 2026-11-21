"""Grok chat and Imagine through AAO's shared Playwright CLI session."""

import json
import math
import re
from pathlib import Path
from threading import Lock
from typing import Literal, get_args
from uuid import uuid4

from orchestrator import web

Aspect = Literal["2:3", "3:2", "1:1", "9:16", "16:9"]
Quality = Literal["Speed", "Quality"]
Count = Literal["Auto", "4", "8", "12"]
Resolution = Literal["480p", "720p"]
Duration = Literal["6s", "10s"]
DEFAULT_OUT = "downloads"
_browser = Lock()

# The SDK reference provides the Imagine selectors and the result-set settling behavior.
IMAGINE_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  const output = { files: [], media_urls: [] };
  const finish = (ok, extra = {}) => ({ ok, conversation_url: page.url(), ...output, ...extra });
  const visible = selector => page.locator(selector).filter({ visible: true });
  const choice = label => page.getByRole('radio', { name: label, exact: true })
    .or(page.getByRole('button', { name: label, exact: true })).filter({ visible: true }).first();
  const signIn = /^\s*(sign in|log in|sign up)\s*$/i;
  const signedOut = async () => new RegExp(cfg.loginUrl, 'i').test(page.url()) ||
    await page.getByRole('button', { name: signIn }).or(page.getByRole('link', { name: signIn })).first().isVisible();
  const mediaSources = () => page.evaluate(kind => {
    const sources = new Set();
    for (const element of document.querySelectorAll(kind === 'image' ? 'img' : 'video')) {
      const src = element.currentSrc || element.src;
      if (!src) continue;
      const alt = (element.alt || '').trim();
      const url = new URL(src, location.href);
      if (kind === 'image' && (['pfp', 'Company Logo', 'Powered by Onetrust'].includes(alt) ||
        element.closest('footer') || /^Thumbnail \d+$/i.test(alt) ||
        (alt !== 'Generated image' && src.startsWith('data:image/') && element.naturalWidth < 384) ||
        !(alt === 'Generated image' || src.startsWith('data:image/') || url.hostname === 'assets.grok.com'))) continue;
      const grokAsset = url.protocol === 'https:' && /(^|\.)(grok\.com|x\.ai)$/.test(url.hostname) &&
        !url.username && !url.password;
      if (grokAsset || src.startsWith('data:' + kind + '/') || (url.protocol === 'blob:' && url.origin === location.origin)) {
        sources.add(src);
      }
    }
    return [...sources];
  }, cfg.kind);

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
    if (!mime || !mime.startsWith(cfg.kind + '/') || !extensions[mime]) throw Error('unsupported media type');
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

  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (await signedOut()) return finish(false, { loggedIn: false, reason: 'Grok sign-in required' });
    const composer = visible('[role="textbox"][contenteditable="true"]').first();
    const dialog = visible('[role="dialog"]').filter({ hasText: /introducing|what.s new/i });
    const ready = choice(cfg.mode).or(dialog.first()).first();
    await ready.waitFor({ timeout: 20000 });
    if (await dialog.first().isVisible()) {
      await dialog.first().getByRole('button', { name: /^(continue|got it|close|dismiss|skip|maybe later)$/i })
        .first().click({ timeout: 5000 });
    }
    if (await signedOut()) return finish(false, { loggedIn: false, reason: 'Grok sign-in required' });
    await choice(cfg.mode).click({ timeout: 10000 });
    for (const label of cfg.radios) {
      const direct = choice(label);
      const settingName = label.endsWith('p') ? 'Video resolution' : 'Video duration';
      const setting = page.getByRole('button', { name: settingName, exact: true });
      if (cfg.kind === 'video' && await setting.isVisible()) {
        if ((await setting.innerText()).trim() !== label) {
          await setting.click({ timeout: 10000 });
          await page.getByRole('menuitemradio', { name: label, exact: true }).click({ timeout: 10000 });
        }
      } else {
        await direct.click({ timeout: 10000 });
      }
    }
    if (cfg.references?.length) {
      await page.locator('input[type="file"]').first().setInputFiles(cfg.references, { timeout: 10000 });
      await page.getByRole('button', { name: 'Remove image', exact: true })
        .nth(cfg.references.length - 1).waitFor({ timeout: 20000 });
    }
    for (const [label, value] of cfg.menus) {
      await visible('[aria-label="' + label + '"]').first().click({ timeout: 10000 });
      const pattern = new RegExp('(^|[^0-9])' + value + '($|[^0-9])', 'i');
      const option = visible('[role="menuitem"], [role="menuitemradio"], [role="option"], [role="radio"]')
        .filter({ hasText: pattern }).first();
      await option.click({ timeout: 10000 });
    }
    if (cfg.image) {
      await page.locator('input[type="file"]').first().setInputFiles(cfg.image, { timeout: 10000 });
      await page.getByRole('button', { name: 'Remove image', exact: true }).waitFor({ timeout: 20000 });
    }
    await composer.fill(cfg.prompt, { timeout: 10000 });
    const before = new Set(await mediaSources());
    await visible('[data-testid="chat-submit"], [aria-label="Submit"]').first().click({ timeout: 10000 });

    const deadline = Date.now() + cfg.timeoutMs;
    let current = [];
    let changed = Date.now();
    while (Date.now() < deadline) {
      const fresh = (await mediaSources()).filter(src => !before.has(src)).sort();
      if (fresh.length !== current.length || fresh.some((src, i) => src !== current[i])) {
        current = fresh;
        changed = Date.now();
      } else if (current.length && Date.now() - changed >= 6000) {
        break;
      }
      await page.waitForTimeout(500);
    }
    if (!current.length) return finish(false, { reason: 'timed out waiting for generated ' + cfg.kind });
    output.media_urls = current.filter(src => src.startsWith('https:'));
    const errors = [];
    for (const [index, source] of current.entries()) {
      try {
        output.files.push(await download(source, index + 1));
      } catch (error) {
        errors.push((error.message || String(error)).split('\n')[0]);
      }
    }
    return errors.length ? finish(false, { reason: 'some media could not be downloaded', errors }) : finish(true);
  } catch (error) {
    if (await signedOut().catch(() => false)) return finish(false, { loggedIn: false, reason: 'Grok sign-in required' });
    return finish(false, { reason: (error.message || String(error)).split('\n')[0] });
  }
}"""


def _validate_prompt(prompt, timeout_s):
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("a nonempty prompt is required")
    if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be a positive number of seconds")


def chat(prompt: str, *, timeout_s: float = 120, chat_ref: str | None = None,
         expect: str | None = None) -> dict:
    """Send a Grok turn and return its reply, reasoning header, and conversation URL."""
    _validate_prompt(prompt, timeout_s)
    with _browser:
        result = web.ask("grok", prompt, expect, timeout_s=timeout_s, chat=chat_ref)
    return {**result, "prompt": prompt, "response": result.get("reply"),
            "thinking": result.get("thinking"), "conversation_url": result.get("url")}


def generate_image(prompt: str, *, quality: Quality | None = None, aspect_ratio: Aspect | None = None,
                   count: Count | None = None, references=(),
                   download_dir=DEFAULT_OUT, timeout_s: float = 300) -> dict:
    """Generate Grok Imagine images and save them to download_dir."""
    return _generate("image", prompt, download_dir, timeout_s, quality=quality, references=references,
                     aspect_ratio=aspect_ratio, count=count)


def generate_video(prompt: str, *, resolution: Resolution = "480p", duration: Duration = "6s",
                   aspect_ratio: Aspect | None = None, image=None,
                   download_dir=DEFAULT_OUT, timeout_s: float = 600) -> dict:
    """Generate a Grok Imagine video, optionally animating a local image."""
    return _generate("video", prompt, download_dir, timeout_s, image=image, resolution=resolution,
                     duration=duration, aspect_ratio=aspect_ratio)


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


def _generate(kind, prompt, download_dir, timeout_s, *, image=None, references=(), **options):
    _validate_prompt(prompt, timeout_s)
    allowed = {"quality": Quality, "aspect_ratio": Aspect, "count": Count,
               "resolution": Resolution, "duration": Duration}
    for name, value in options.items():
        if value is not None and value not in get_args(allowed[name]):
            raise ValueError(f"{name} must be one of {', '.join(get_args(allowed[name]))}")
    reference = None
    if image is not None:
        path = Path(image).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"reference image is not a file: {path}")
        reference = path.as_posix()
    folder = Path(download_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "grok"
    radios = [options[key] for key in ("quality", "resolution", "duration") if options.get(key)]
    menus = [(label, options[key]) for label, key in (("Aspect Ratio", "aspect_ratio"), ("Image Count", "count"))
             if options.get(key)]
    payload = {"kind": kind, "mode": kind.title(), "url": "https://grok.com/imagine", "loginUrl": web.LOGIN_URL,
               "prompt": prompt, "radios": radios, "menus": menus, "image": reference,
               "out": folder.as_posix(), "filename": f"{slug}-{kind}-{uuid4().hex[:10]}",
               "timeoutMs": timeout_s * 1000}
    if references:
        paths = [Path(path).expanduser().resolve(strict=True) for path in references]
        if any(not path.is_file() for path in paths):
            raise ValueError("all reference images must be files")
        payload["references"] = [path.as_posix() for path in paths]
    with _browser:
        # The reference SDK observed video generation completing in a visible Chrome window.
        ok, out, error = web.open_target("grok", headed=True if kind == "video" else None)
        if not ok:
            return {"ok": False, "error": error or out}
        try:
            # Each download has its own timeout, and one result batch can contain twelve files.
            result = web.run_script("grok", IMAGINE_SCRIPT.replace("__CFG__", json.dumps(payload), 1), timeout_s + 1100)
        finally:
            web.release("grok")
    return {"prompt": prompt, "mode": kind.title(), **options, **result}
