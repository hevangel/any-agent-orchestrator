"""Generate and download an image in Gemini's signed-in Images view."""

import json
from pathlib import Path

from orchestrator import web

SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Gemini Images';
  try {
    await page.goto('https://gemini.google.com/images', { waitUntil: 'domcontentloaded' });
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return { ok: false, reason: 'Gemini login required' };
    const composer = page.locator('rich-textarea [contenteditable=true][data-placeholder="Describe your image"]');
    await composer.waitFor({ timeout: 30000 });
    const intro = page.getByRole('button', { name: 'Try it', exact: true });
    if (await intro.isVisible()) await intro.click();
    if (cfg.aspectRatio) {
      await page.getByRole('button', { name: /^(?:Aspect ratio(?:\s|:|$)|\d+:\d+$)/i }).first().click();
      await page.getByRole('menuitemradio', { name: cfg.aspectRatio, exact: true }).click();
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
    const download = replies.nth(previous).getByRole('button', {
      name: /^Download(?:(?: full[- ]size(?:d)?| generated)? image| full size)?$/i,
    }).first();
    await download.waitFor({ timeout: cfg.timeoutMs });
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


def generate(prompt, directory, timeout_s=720, *, aspect_ratio=None, references=()):
    cfg = {"prompt": prompt, "directory": Path(directory).resolve().as_posix(),
           "loginUrl": web.LOGIN_URL, "timeoutMs": timeout_s * 1000, "aspectRatio": aspect_ratio,
           "references": [Path(path).resolve().as_posix() for path in references]}
    ok, _, error = web.open_target("gemini")
    if not ok:
        return {"ok": False, "reason": error}
    try:
        return web.run_script("gemini", SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s + 120)
    finally:
        web.release("gemini")
