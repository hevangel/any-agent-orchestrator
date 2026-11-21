"""Generate and download media in a new Muse conversation."""

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from orchestrator import web
from orchestrator.machines import _run

TARGET = "muse"
SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Muse';
  try {
    await page.goto('https://muse.ai/', { waitUntil: 'domcontentloaded' });
    const composer = page.getByRole('textbox', { name: 'Message', exact: true }).filter({ visible: true }).last();
    await composer.waitFor({ timeout: 20000 });
    if (await composer.inputValue()) return { ok: false, reason: 'Muse already has an unsent draft' };
    if (cfg.references.length) {
      stage = 'Attach the Muse reference image';
      await page.locator('input[type=file][multiple]').setInputFiles(cfg.references, { timeout: 10000 });
      await page.getByRole('button', { name: 'Remove attachment', exact: true })
        .nth(cfg.references.length - 1).waitFor({ timeout: 30000 });
    }
    stage = 'Send the Muse prompt';
    await composer.fill(cfg.prompt);
    await page.getByRole('button', { name: 'Send', exact: true }).click({ timeout: 30000 });
    stage = 'Wait for Muse to generate the ' + cfg.kind;
    const choices = page.locator('.hatch-chat-groupable-bubble').filter({ hasText: cfg.filename })
      .getByTestId('hatch-sandbox-file-card-options');
    const preview = page.getByLabel(cfg.preview + cfg.filename, { exact: true });
    const stop = page.getByRole('button', { name: /stop/i }).filter({ visible: true }).first();
    const deadline = Date.now() + cfg.timeoutMs;
    while (Date.now() < deadline && !await preview.isVisible() && !await choices.last().isVisible()) {
      const unavailable = page.locator('[data-hatch-assistant-message-body]').filter({ hasText: cfg.unavailable });
      if (await unavailable.last().isVisible() && !await stop.isVisible()) {
        return { ok: false, reason: 'Muse reported that ' + cfg.kind + ' generation is unavailable for this request' };
      }
      await page.waitForTimeout(1200);
    }
    let download;
    if (await preview.isVisible()) {
      await preview.click();
      download = page.getByRole('button', { name: /download/i }).filter({ visible: true }).last();
    } else if (await choices.last().isVisible()) {
      await choices.last().click();
      download = page.getByRole('menuitem', { name: 'Download', exact: true });
    } else {
      return { ok: false, reason: 'Muse did not return the requested ' + cfg.kind + ' before the timeout', url: page.url() };
    }
    stage = 'Download the Muse ' + cfg.kind;
    const [file] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      download.click({ timeout: 10000 }),
    ]);
    if (!file.suggestedFilename().toLowerCase().endsWith(cfg.extension))
      return { ok: false, reason: 'Muse did not attach the requested ' + cfg.extension + ' file' };
    const path = cfg.directory + '/' + cfg.filename;
    await file.saveAs(path);
    return { ok: true, path, url: page.url() };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message).split('\n')[0], url: page.url() };
  }
}"""


def generate_video(prompt, directory, timeout_s=720, *, references=()):
    return _generate("video", prompt, directory, timeout_s, references=references)


def generate_image(prompt, directory, timeout_s=720, *, aspect_ratio=None, references=()):
    return _generate("image", prompt, directory, timeout_s, aspect_ratio=aspect_ratio, references=references)


def generate_audio(prompt, directory, timeout_s=720):
    """Use Muse's video tool for a short soundtrack and save just its audio."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "reason": "Muse soundtrack export requires FFmpeg on PATH"}
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    brief = ("Make a short video with sound. The important part is the soundtrack. "
             "Use a simple stationary visual.\n\n" + prompt)
    with TemporaryDirectory(prefix="muse-video-", dir=directory) as workspace:
        result = generate_video(brief, workspace, timeout_s)
        if not result.get("ok"):
            return result
        audio = directory / "Muse soundtrack.mp3"
        ok, _, error = _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                             "-i", result["path"], "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2", str(audio)],
                            timeout=60)
        if not ok:
            return {"ok": False, "reason": f"Could not extract the Muse soundtrack: {error or 'FFmpeg failed'}"}
    return {"ok": True, "path": str(audio)}


def _generate(kind, prompt, directory, timeout_s, *, aspect_ratio=None, references=()):
    directory = Path(directory).resolve()
    extension, preview = (".png", "Open generated image ") if kind == "image" else (".mp4", "Open animated video ")
    filename = f"aao-{directory.name[-12:]}{extension}"
    unavailable = f"AAO_{kind.upper()}_UNAVAILABLE_{directory.name[-12:]}"
    prompt = (f"Create one {kind} with your native {kind} generation tool. "
              + ("Do not draw with code or SVG. " if kind == "image" else "")
              + f"Attach the resulting {extension[1:].upper()} "
              f"as a downloadable sandbox file named {filename}. Keep it in this chat. "
              "Do not send it to anyone, publish it, purchase anything, or use a service that requires approval. "
              f"If {kind} generation is unavailable, write {unavailable}.\n\n{kind.title()} description:\n{prompt}")
    if references:
        prompt += f"\nUse the attached image or images as visual references for the {kind}."
    if aspect_ratio:
        prompt += f"\nUse a {aspect_ratio} aspect ratio for the output image."
    cfg = {"prompt": prompt, "directory": directory.as_posix(), "filename": filename, "kind": kind,
           "preview": preview, "extension": extension, "unavailable": unavailable, "timeoutMs": timeout_s * 1000,
           "references": [Path(path).resolve().as_posix() for path in references]}
    ok, _, error = web.open_target(TARGET, headed=True)
    if not ok:
        return {"ok": False, "reason": error}
    try:
        return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s + 100)
    finally:
        web.release(TARGET)
