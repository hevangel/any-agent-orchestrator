"""Generate a song and download its MP3 in the Google Flow Music web app."""

import json
from pathlib import Path

from orchestrator import web

TARGET = "flow-music"
SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Google Flow Music';
  try {
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded' });
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return { ok: false, reason: cfg.loginMessage };
    const login = page.getByRole('button', { name: /^(Log in|Continue with Google)$/i })
      .or(page.getByText('Free to start with a Google Account.', { exact: true }))
      .filter({ visible: true });
    const composer = page.getByRole('textbox', { name: 'Chat message', exact: true }).filter({ visible: true });
    const ready = composer.or(login).first();
    await ready.waitFor({ timeout: 30000 });
    if (new RegExp(cfg.loginUrl, 'i').test(page.url()) || await login.first().isVisible()) {
      return { ok: false, reason: cfg.loginMessage };
    }
    const draft = await composer.inputValue();
    if (draft && draft !== cfg.prompt) return { ok: false, reason: 'Flow Music already has an unsent draft' };
    stage = 'Send the Flow Music prompt';
    await composer.fill(cfg.prompt);
    const send = page.getByRole('button', { name: 'Send message', exact: true }).and(page.locator(':enabled'));
    // Keyboard activation works with the cookie preference banner still open.
    await send.press('Enter', { timeout: 15000 });
    const thread = page.locator('[data-mono-thread]');
    await thread.locator('[data-user-bubble]').getByText(cfg.prompt, { exact: true }).waitFor({ timeout: 30000 });

    stage = 'Wait for Flow Music to finish the song';
    const songCards = thread.locator('.render-group-clip [role=button]:not([aria-busy="true"])').filter({
      has: page.getByRole('button', { name: /^More options for /i, includeHidden: true }),
    });
    const songCard = songCards.nth(cfg.song - 1);
    const failed = thread.getByText(/^(Song generation failed|Something went wrong\. Please try again\.)/i).first();
    try {
      await songCard.or(failed).first().waitFor({ timeout: cfg.timeoutMs });
    } catch (error) {
      const available = await songCards.count();
      if (!available) throw error;
      return { ok: false, reason: 'Requested song ' + cfg.song + ', but only ' + available + ' finished', url: page.url() };
    }
    if (!await songCard.isVisible()) return { ok: false, reason: await failed.innerText(), url: page.url() };

    stage = 'Download the Flow Music song';
    await songCard.hover();
    await songCard.getByRole('button', { name: /^More options for /i }).click({ timeout: 10000 });
    const download = page.getByRole('menuitem', { name: 'Download', exact: true });
    await download.hover({ timeout: 10000 });
    const mp3 = page.getByRole('menuitem', { name: 'MP3', exact: true });
    const [file] = await Promise.all([
      page.waitForEvent('download', { timeout: 120000 }),
      mp3.click({ timeout: 10000 }),
    ]);
    const name = file.suggestedFilename().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_');
    if (!name.toLowerCase().endsWith('.mp3')) return { ok: false, reason: 'Flow Music did not download an MP3' };
    const path = cfg.directory + '/' + name;
    await file.saveAs(path);
    return { ok: true, path, url: page.url() };
  } catch (error) {
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return { ok: false, reason: cfg.loginMessage };
    return { ok: false, reason: stage + ': ' + String(error.message).split('\n')[0], url: page.url() };
  } finally {
    await page.keyboard.press('Escape').catch(() => {});
  }
}"""


def generate(prompt, directory, timeout_s=720, *, lyrics=None, style=None, length=None, instrumental=False, song=1):
    if song < 1 or (length is not None and length <= 0):
        raise ValueError("Flow Music song index and length must be positive")
    if instrumental and lyrics:
        raise ValueError("Choose lyrics or an instrumental track")
    brief = [prompt]
    if style:
        brief.append(f"Musical style: {style}")
    if length:
        brief.append(f"Target duration: {length} seconds")
    if instrumental:
        brief.append("Instrumental only. Use no vocals.")
    if lyrics:
        brief.append(f"Use these lyrics verbatim:\n{lyrics}")
    request = ("Create one song now" if song == 1 else f"Create {song} song versions now")
    payload = {
        "url": web.TARGETS[TARGET]["url"] + "session",
        "loginUrl": web.LOGIN_URL,
        "loginMessage": "Sign in with 'aao web login flow-music', then try again.",
        "prompt": f"{request} from the following brief. Use your music generation tool.\n\n" + "\n\n".join(brief),
        "song": song,
        "directory": Path(directory).resolve().as_posix(),
        "timeoutMs": timeout_s * 1000,
    }
    ok, _, error = web.open_target(TARGET)
    if not ok:
        return {"ok": False, "reason": error or "Could not open Google Flow Music in Chrome"}
    try:
        return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(payload), 1), timeout_s + 180)
    finally:
        web.release(TARGET)
