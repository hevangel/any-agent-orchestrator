"""Generate and download an image in Gemini's signed-in Images view."""

import json
import re
from pathlib import Path

from orchestrator import web

SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = cfg.chatUrl ? 'Open Gemini chat' : 'Open Gemini Images';
  try {
    if (cfg.chatUrl) {
      if (!cfg.chatId || !page.url().includes(cfg.chatId)) {
        await page.goto(cfg.chatUrl, { waitUntil: 'domcontentloaded' });
      }
    } else {
      await page.goto('https://gemini.google.com/images', { waitUntil: 'domcontentloaded' });
    }
    if (/google\.com\/sorry/i.test(page.url())) {
      return { ok: false, blocked: true, reason: 'Google CAPTCHA check (google.com/sorry)', url: page.url() };
    }
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return { ok: false, reason: 'Gemini login required' };
    const composer = page.locator('rich-textarea [contenteditable=true]').first();
    await composer.waitFor({ timeout: 30000 });
    const intro = page.getByRole('button', { name: 'Try it', exact: true });
    if (await intro.isVisible()) await intro.click();
    if (cfg.aspectRatio) {
      const aspectBtn = page.getByRole('button', { name: /^(?:Aspect ratio(?:\s|:|$)|\d+:\d+$)/i }).first();
      if (!cfg.chatUrl || await aspectBtn.isVisible().catch(() => false)) {
        await aspectBtn.click();
        await page.getByRole('menuitemradio', { name: cfg.aspectRatio, exact: true }).click();
      }
    }
    if (cfg.references.length) {
      stage = 'Attach the reference image';
      const attachments = 'gem-media-attachment';
      const before = await page.locator(attachments).count();
      await page.getByRole('button', { name: 'Upload and tools', exact: true }).click();
      await page.locator('images-files-uploader input[type=file][accept*=".py"]')
        .setInputFiles(cfg.references, { timeout: 10000 });
      await page.keyboard.press('Escape');
      await page.waitForFunction(({ attachments, before, count }) => {
        const chips = [...document.querySelectorAll(attachments)];
        return chips.length >= before + count && !chips.some(chip =>
          [...chip.querySelectorAll('[role=progressbar], mat-progress-spinner, mat-progress-bar')]
            .some(progress => progress.getClientRects().length));
      }, { attachments, before, count: cfg.references.length }, { timeout: 30000 });
    }
    stage = 'Send the image prompt';
    await composer.fill(cfg.prompt);
    const replies = page.locator('model-response');
    const previous = await replies.count();
    await page.getByRole('button', { name: 'Send message', exact: true }).click({ timeout: 20000 });
    stage = 'Wait for Gemini to generate the image';
    const reply = replies.nth(previous);
    const download = reply.getByRole('button', {
      name: /^Download(?:(?: full[- ]size(?:d)?| generated)? image| full size)?$/i,
    }).first();
    const deadline = Date.now() + cfg.timeoutMs;
    let idleTextTicks = 0;
    while (true) {
      if (/google\.com\/sorry/i.test(page.url())) {
        return { ok: false, blocked: true, reason: 'Google CAPTCHA check (google.com/sorry)', url: page.url() };
      }
      if (await download.isVisible().catch(() => false)) break;
      if (Date.now() >= deadline) {
        return { ok: false, reason: stage + ': timed out waiting for Download button', url: page.url() };
      }
      const stopVisible = await page.locator("button[aria-label='Stop response']").first().isVisible().catch(() => false);
      const busyVisible = await reply.locator("[aria-busy='true']").first().isVisible().catch(() => false);
      if (!stopVisible && !busyVisible && await reply.count() > 0) {
        const text = await reply.evaluate(el => {
          const md = el.querySelector('message-content .markdown') || el.querySelector('message-content') || el;
          return (md.innerText || '').trim();
        }).catch(() => '');
        if (text) {
          idleTextTicks++;
          if (idleTextTicks >= 2 && !(await download.isVisible().catch(() => false))) {
            return {
              ok: false,
              reason: 'Gemini did not generate an image: ' + text.slice(0, 200),
              reply: text.slice(0, 400),
              url: page.url(),
            };
          }
        } else {
          idleTextTicks = 0;
        }
      } else {
        idleTextTicks = 0;
      }
      await page.waitForTimeout(1500);
    }
    stage = 'Download the Gemini image';
    const [file] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }), download.click(),
    ]);
    const path = cfg.directory + '/' + file.suggestedFilename();
    await file.saveAs(path);
    return { ok: true, path, url: page.url() };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message).split('\n')[0], url: page.url() };
  }
}"""


def _resolve_chat_url(chat):
    match = re.search(r"([0-9a-f]{16})", str(chat), re.I)
    if match:
        chat_id = match.group(1).lower()
        return chat_id, f"https://gemini.google.com/app/{chat_id}"
    from orchestrator import web_chats
    found = web_chats.resolve("gemini", chat)
    if not found.get("url"):
        return None, found
    return found.get("id"), found["url"]


def generate(prompt, directory, timeout_s=720, *, aspect_ratio=None, references=(), chat=None):
    ok, _, error = web.open_target("gemini")
    if not ok:
        return {"ok": False, "reason": error}
    try:
        chat_id, chat_url = (None, None)
        if chat:
            chat_id, resolved = _resolve_chat_url(chat)
            if isinstance(resolved, dict):
                return {"ok": False, **resolved}
            chat_url = resolved
        cfg = {"prompt": prompt, "directory": Path(directory).resolve().as_posix(),
               "loginUrl": web.LOGIN_URL, "timeoutMs": timeout_s * 1000, "aspectRatio": aspect_ratio,
               "references": [Path(path).resolve().as_posix() for path in references],
               "chatId": chat_id, "chatUrl": chat_url}
        return web.run_script("gemini", SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s + 120)
    finally:
        web.release("gemini")
