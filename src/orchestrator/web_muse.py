"""Generate and download media in a new Muse conversation, and read the account's usage quota."""

import json
import os
import re
import shutil
from datetime import datetime, timezone
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


def generate_audio(prompt, directory, timeout_s=720, *, references=()):
    """Use Muse's video tool for a short soundtrack and save just its audio."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "reason": "Muse soundtrack export requires FFmpeg on PATH"}
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    visual = ("Use the attached images as inspiration for the soundtrack and visuals." if references
              else "Use a simple stationary visual.")
    brief = f"Make a short video with sound. The important part is the soundtrack. {visual}\n\n{prompt}"
    with TemporaryDirectory(prefix="muse-video-", dir=directory) as workspace:
        result = generate_video(brief, workspace, timeout_s, references=references)
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


# ---------------------------------------------------------------- quota (read-only)
# Settings > General > Usage shows the weekly plan limit and the top-up token balance. The page loads
# them with a Next.js server action whose response has a {"subscription": {...}} line; the action id
# changes with each deployment, so the script opens the dialog and catches that response instead of
# calling the action itself. The dialog text is kept as a fallback. A separate tab is used so a
# generation running in the shared Muse browser is never disturbed.
QUOTA_SCRIPT = r"""async page => {
  const tab = await page.context().newPage();
  try {
    await tab.goto('https://muse.ai/', { waitUntil: 'domcontentloaded' });
    const settings = tab.getByRole('button', { name: 'Settings', exact: true });
    try { await settings.waitFor({ timeout: 30000 }); }
    catch { return { ok: false, loggedIn: false, url: tab.url(), reason: 'Muse is not signed in (run: aao web login muse)' }; }
    const action = tab.waitForResponse(async r => r.request().method() === 'POST' && !!r.request().headers()['next-action']
        && (await r.text().catch(() => '')).includes('"subscription"'), { timeout: 30000 }).catch(() => null);
    await tab.keyboard.press('Control+Comma');
    const dialog = tab.getByRole('dialog', { name: 'Settings' });
    try { await dialog.waitFor({ timeout: 5000 }); }
    catch {
      await settings.click({ timeout: 10000 });
      await tab.getByRole('menuitem', { name: /^Settings/ }).click({ timeout: 10000 });
    }
    await dialog.getByText('Usage', { exact: true }).waitFor({ timeout: 20000 }).catch(() => {});
    const response = await action;
    const body = response ? await response.text().catch(() => '') : '';
    const text = await dialog.innerText().catch(() => '');
    return { ok: true, loggedIn: true, url: tab.url(), body, text };
  } catch (error) {
    return { ok: false, reason: 'Read Muse usage: ' + String(error.message).split('\n')[0], url: tab.url() };
  } finally {
    await tab.close().catch(() => {});
  }
}"""


def _subscription(body):
    """The `subscription` object from a Next.js server-action (RSC) response: one `N:{json}` per line."""
    for line in (body or "").splitlines():
        _, sep, payload = line.partition(":")
        if not sep or not payload.startswith("{"):
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("subscription"), dict):
            return data["subscription"]
    return None


def _iso(epoch):
    if not isinstance(epoch, (int, float)):
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(name, used_pct, *, resets_at=None, reset_text=None, seconds=None, used=None, remaining=None,
            limit=None, label=None):
    used_pct = None if used_pct is None else round(max(0.0, min(100.0, float(used_pct))), 1)
    return {"name": name, "seconds": seconds, "scope": None, "used_pct": used_pct,
            "remaining_pct": None if used_pct is None else round(100 - used_pct, 1), "resets_at": resets_at,
            "reset_text": reset_text, "used": used, "remaining": remaining, "limit": limit, "label": label}


def parse_subscription(sub):
    """Quota windows from Muse's subscription object: the plan limit is a percentage with a reset time;
    top-up tokens are a balance out of a total that never expires."""
    windows, balance = [], None
    usage = sub.get("usage") or {}
    subtitle = sub.get("statusSubtitle") or ""
    if usage.get("percentUsed") is not None:
        weekly = "week" in subtitle.lower()
        windows.append(_window("weekly" if weekly else "plan", usage["percentUsed"],
                               resets_at=_iso(usage.get("resetsAt")), reset_text=subtitle or None,
                               seconds=604800 if weekly else None, label=sub.get("usageRowValueLabel")))
    total, left = sub.get("topupTotal"), sub.get("topupBalance")
    if isinstance(total, (int, float)) and isinstance(left, (int, float)) and total > 0:
        label = sub.get("topupRowValueLabel")
        windows.append(_window((sub.get("topupRowLabel") or "Additional tokens").lower(), 100 * (total - left) / total,
                               reset_text="never expires", used=total - left, remaining=left, limit=total, label=label))
        # The raw counts are in the site's own unit; its label ("10B tokens left") is what the page shows.
        left_text = re.search(r"\(([^)]* left)\)", label or "")
        balance = [{"amount": left, "total": total, "currency": "top-up units",
                    "label": left_text.group(1) if left_text else label}]
    status = usage.get("quotaStatus")
    return {"plan": (sub.get("tier") or {}).get("name") or sub.get("subscriptionType"), "windows": windows,
            "balance": balance, "quota_status": status,
            "error": None if status in (None, "SUFFICIENT") else f"Muse quota status: {status}"}


_PCT = re.compile(r"(\d+(?:\.\d+)?)%\s*used")


def parse_settings_text(text):
    """Fallback: the Usage section of the Settings dialog as plain text (name, note, "N% used" rows)."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if "Usage" not in lines:
        return None
    rows = lines[lines.index("Usage") + 1:]
    windows, plan = [], None
    for i, line in enumerate(rows):
        match = _PCT.search(line)
        if not match or i < 2:
            continue
        name, note = rows[i - 2], rows[i - 1]
        if name.lower().endswith("plan"):
            plan = name
            weekly = "week" in note.lower()
            windows.append(_window("weekly" if weekly else "plan", float(match.group(1)), reset_text=note,
                                   seconds=604800 if weekly else None, label=line))
        else:
            windows.append(_window(name.lower(), float(match.group(1)), reset_text=note.lower(), label=line))
    if not windows:
        return None
    balance = [{"amount": None, "total": None, "currency": "top-up units", "label": w["label"]}
               for w in windows if w["name"] not in ("weekly", "plan")]
    return {"plan": plan, "windows": windows, "balance": balance or None, "quota_status": None, "error": None}


def quota_account(page):
    """A quotas-v1 account row from the quota script's result."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"provider": "muse", "account": None, "plan": None, "status": "unavailable", "error": None,
           "retrieved_at": now, "windows": [], "balance": None, "source": "muse-web"}
    if not page.get("ok"):
        return {**row, "error": page.get("reason") or page.get("error") or "Muse usage could not be read"}
    sub = _subscription(page.get("body"))
    parsed = parse_subscription(sub) if sub else parse_settings_text(page.get("text"))
    if not parsed or not parsed["windows"]:
        return {**row, "error": "Muse Settings showed no Usage section"}
    return {**row, **parsed, "status": "live", "source": "muse-web" if sub else "muse-web-page"}


def has_profile(settings=None):
    """True once `aao web login muse` has created the saved browser profile (checked without creating it)."""
    s = settings or web._settings()
    base = Path(os.path.expanduser(s.get("AAO_WEB_PROFILE_DIR", "~/.aao/browser-profiles")))
    return (base / web.login_name(TARGET)).is_dir()


def quota(timeout_s=90):
    """Read the signed-in Muse account's usage. Read-only: opens Settings, never clicks Upgrade or buys.
    A browser this call opens (headless) is closed again; an already open one is left as it was."""
    opened = not web.is_open(TARGET)
    if opened:
        ok, _, error = web.open_target(TARGET, headed=False)
        if not ok:
            return quota_account({"ok": False, "reason": error})
    try:
        return quota_account(web.run_script(TARGET, QUOTA_SCRIPT, timeout_s))
    finally:
        if opened:
            web.close_target(TARGET)
