"""Generate and download an image using the signed-in ChatGPT web composer."""

import json
from pathlib import Path

from orchestrator import web

SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open ChatGPT';
  try {
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const signedIn = page.getByRole('button', { name: 'Open profile menu', exact: true });
    const signIn = page.getByRole('button', { name: /^(?:log|sign) in$/i }).first();
    if (!await signedIn.or(signIn).first().waitFor({ timeout: 20000 }).then(() => true, () => false)
        || !await signedIn.isVisible()) {
      return { ok: false, reason: 'Sign in to the AAO ChatGPT browser with aao web login chatgpt.' };
    }
    const mode = page.getByRole('button', { name: /^Switch mode, current mode: Codex/i });
    if (await mode.isVisible()) {
      await mode.click();
      await page.getByRole('menuitem', { name: /^ChatGPT\b/ }).click();
    }
    stage = 'Open the ChatGPT image composer';
    await page.getByRole('button', { name: 'Add files and more', exact: true }).click();
    await page.getByRole('button', { name: /^Create image\b/i }).click({ timeout: 10000 });
    const composer = page.getByRole('textbox', { name: 'Ask ChatGPT', exact: true });
    if (cfg.references.length) {
      stage = 'Attach the reference image';
      const form = composer.locator('xpath=ancestor::form[1]');
      await form.locator('input[type=file][multiple]:not([accept])').setInputFiles(cfg.references, { timeout: 10000 });
      await form.getByRole('button', { name: /^Remove\b/i }).nth(cfg.references.length - 1)
        .waitFor({ timeout: 30000 });
    }
    stage = 'Send the image prompt';
    await composer.fill(cfg.prompt);
    const previews = page.getByTestId('generated-image-preview');
    const previous = await previews.count();
    await page.getByRole('button', { name: 'Send', exact: true }).click({ timeout: 30000 });
    stage = 'Wait for ChatGPT to generate the image';
    await previews.nth(previous).click({ timeout: cfg.timeoutMs });
    stage = 'Download the ChatGPT image';
    const download = page.getByRole('dialog').getByRole('button', { name: 'Download', exact: true });
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
    if aspect_ratio:
        prompt += f"\n\nUse a {aspect_ratio} aspect ratio for the output image."
    cfg = {"prompt": prompt, "directory": Path(directory).resolve().as_posix(),
           "timeoutMs": timeout_s * 1000,
           "references": [Path(path).resolve().as_posix() for path in references]}
    ok, _, error = web.open_target("chatgpt")
    if not ok:
        return {"ok": False, "reason": error}
    try:
        return web.run_script("chatgpt", SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s + 120)
    finally:
        web.release("chatgpt")
