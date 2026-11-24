"""Generate a video using the signed-in Gemini Videos page."""

import json
from pathlib import Path

from orchestrator import web

TARGET = "gemini"
PREPARE = r"""async page => {
  const cfg = __CFG__;
  await page.goto('https://gemini.google.com/videos', { waitUntil: 'domcontentloaded' });
  if (new RegExp(cfg.loginUrl, 'i').test(page.url())) return { ok: false, reason: 'Gemini login required' };
  await page.locator('rich-textarea [contenteditable=true][data-placeholder="Describe your video"]')
    .waitFor({ timeout: 30000 });
  const intro = page.getByRole('button', { name: 'Try it', exact: true });
  if (await intro.isVisible()) await intro.click();
  if (cfg.references.length) {
    await page.getByRole('button', { name: 'Upload and tools', exact: true }).click();
    await page.getByRole('menuitem', { name: /^Upload files/ }).click({ timeout: 10000 });
  }
  return { ok: true };
}"""

SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Attach the Gemini video reference';
  try {
    const composer = page.locator('rich-textarea [contenteditable=true][data-placeholder="Describe your video"]');
    if (cfg.references.length) {
      const attachments = page.locator('gem-media-attachment');
      await attachments.nth(cfg.references.length - 1).waitFor({ timeout: 30000 });
      await page.waitForFunction(() => ![...document.querySelectorAll(
        'gem-media-attachment :is([role=progressbar], mat-progress-spinner, mat-progress-bar)'
      )].some(progress => progress.getClientRects().length), null, { timeout: 30000 });
    }
    stage = 'Send the Gemini video prompt';
    await composer.fill(cfg.prompt);
    const replies = page.locator('model-response');
    const before = await replies.count();
    await page.getByRole('button', { name: 'Send message', exact: true }).click({ timeout: 15000 });

    stage = 'Wait for the Gemini video';
    const reply = replies.nth(before);
    const download = reply.getByRole('button', { name: /^(Download|Download video|Download generated video)$/i });
    await download.first().waitFor({ timeout: cfg.timeoutMs });
    const [file] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      download.first().click(),
    ]);
    const path = cfg.directory + '/' + file.suggestedFilename();
    await file.saveAs(path);
    return { ok: true, path, url: page.url() };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message).split('\n')[0], url: page.url() };
  }
}"""


def generate(prompt, directory, timeout_s=720, *, references=()):
    directory = Path(directory).resolve()
    cfg = {"prompt": prompt, "directory": directory.as_posix(),
           "loginUrl": web.LOGIN_URL, "timeoutMs": timeout_s * 1000,
           "references": [Path(path).resolve().as_posix() for path in references]}
    ok, _, error = web.open_target(TARGET)
    if not ok:
        return {"ok": False, "reason": error}
    try:
        prepared = web.run_script(TARGET, PREPARE.replace("__CFG__", json.dumps(cfg), 1), 60)
        if not prepared.get("ok"):
            return prepared
        if cfg["references"]:
            # playwright-cli tracks Gemini's native file chooser across commands.
            uploaded, _, error = web._pw(TARGET, "upload", *cfg["references"], timeout=60)
            if not uploaded:
                return {"ok": False, "reason": error or "Could not upload the Gemini video reference"}
        return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s + 120)
    finally:
        web.release(TARGET)
