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


def generate_video(prompt, directory, timeout_s=720, *, aspect_ratio=None, references=(), files=()):
    return _generate("video", prompt, directory, timeout_s, aspect_ratio=aspect_ratio,
                     references=references, files=files)


def generate_image(prompt, directory, timeout_s=720, *, aspect_ratio=None, references=(), files=()):
    return _generate("image", prompt, directory, timeout_s, aspect_ratio=aspect_ratio,
                     references=references, files=files)


def generate_audio(prompt, directory, timeout_s=720, *, references=(), files=(), voice=None, tts=False):
    """Generate audio in Muse: direct TTS MP3 when `tts`/`voice` is given, else a video's soundtrack."""
    if tts or voice:
        return _generate_tts_audio(prompt, directory, timeout_s, voice=voice, references=references, files=files)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"ok": False, "reason": "Muse soundtrack export requires FFmpeg on PATH"}
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    visual = ("Use the attached images as inspiration for the soundtrack and visuals." if references
              else "Use a simple stationary visual.")
    brief = f"Make a short video with sound. The important part is the soundtrack. {visual}\n\n{prompt}"
    with TemporaryDirectory(prefix="muse-video-", dir=directory) as workspace:
        result = generate_video(brief, workspace, timeout_s, references=references, files=files)
        if not result.get("ok"):
            return result
        audio = directory / "Muse soundtrack.mp3"
        ok, _, error = _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                             "-i", result["path"], "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2", str(audio)],
                            timeout=60)
        if not ok:
            return {"ok": False, "reason": f"Could not extract the Muse soundtrack: {error or 'FFmpeg failed'}"}
    return {"ok": True, "path": str(audio)}


def _generate_tts_audio(prompt, directory, timeout_s, *, voice=None, references=(), files=()):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"aao-{directory.name[-12:]}.mp3"
    unavailable = f"AAO_AUDIO_UNAVAILABLE_{directory.name[-12:]}"
    voice_clause = f" Use the {voice} voice." if voice else ""
    tts_prompt = (
        f"Use your text-to-speech tool to speak the text below as one MP3 audio file.{voice_clause} "
        f"Attach the resulting MP3 as a downloadable sandbox file named {filename}. Keep it in this chat. "
        "Do not send it to anyone, publish it, purchase anything, or use a service that requires approval. "
        f"If text-to-speech generation is unavailable, write {unavailable}.\n\nText to speak:\n{prompt}"
    )
    attachments = [Path(path).resolve().as_posix() for path in (*references, *files)]
    cfg = {"prompt": tts_prompt, "directory": directory.as_posix(), "filename": filename, "kind": "audio",
           "preview": "Open generated audio ", "extension": ".mp3", "unavailable": unavailable,
           "timeoutMs": timeout_s * 1000, "references": attachments}
    ok, _, error = web.open_target(TARGET, headed=True)
    if not ok:
        return {"ok": False, "reason": error}
    try:
        return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s + 100)
    finally:
        web.release(TARGET)


def _generate(kind, prompt, directory, timeout_s, *, aspect_ratio=None, references=(), files=()):
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
    if references and not files:
        prompt += f"\nUse the attached image or images as visual references for the {kind}."
    elif references or files:
        prompt += f"\nUse the attached files as inputs for the {kind}."
    if aspect_ratio:
        prompt += f"\nCreate the output in exactly {aspect_ratio} aspect ratio."
    attachments = [Path(path).resolve().as_posix() for path in (*references, *files)]
    cfg = {"prompt": prompt, "directory": directory.as_posix(), "filename": filename, "kind": kind,
           "preview": preview, "extension": extension, "unavailable": unavailable, "timeoutMs": timeout_s * 1000,
           "references": attachments}
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


# ---------------------------------------------------------------- library & chats (muse.ai web)
LIBRARY_URL = "https://muse.ai/library/artifacts"

LIBRARY_LIST_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Muse Library';
  try {
    if (!page.url().includes('/library')) {
      await page.goto(cfg.libraryUrl, { waitUntil: 'domcontentloaded' });
    }
    const settings = page.getByRole('button', { name: 'Settings', exact: true });
    try { await settings.waitFor({ timeout: 25000 }); }
    catch { return { ok: false, loggedIn: false, url: page.url(), reason: 'Muse is not signed in (run: aao web login muse)' }; }
    await page.waitForTimeout(2000);
    stage = 'Read Muse Library items';
    const items = await page.evaluate((limit) => {
      const buttons = [...document.querySelectorAll('[data-testid="hatch-sandbox-file-card-options"]')];
      const out = [];
      for (const btn of buttons) {
        const card = btn.closest('.hatch-chat-groupable-bubble') || btn.parentElement?.parentElement || btn.parentElement;
        const text = (card?.innerText || '').trim();
        const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
        const title = lines.find(l => /\.(png|jpe?g|webp|mp4|mov|webm|mp3|wav|m4a|ogg|pdf|txt|md|json|py|js)$/i.test(l))
          || lines[0] || 'Untitled';
        const lower = title.toLowerCase();
        const kind = /\.(mp4|mov|webm)$/.test(lower) ? 'video'
          : /\.(mp3|wav|m4a|ogg)$/.test(lower) ? 'audio'
          : /\.(png|jpe?g|webp)$/.test(lower) ? 'image'
          : 'document';
        out.push({ index: out.length + 1, kind, title, url: location.href });
        if (limit && out.length >= limit) break;
      }
      return out;
    }, cfg.limit || null);
    const media = items.filter(i => i.kind !== 'document');
    const documents = items.filter(i => i.kind === 'document');
    return {
      ok: true,
      url: page.url(),
      media: cfg.kind === 'documents' ? [] : media,
      documents: cfg.kind === 'media' ? [] : documents,
    };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message || error).split('\n')[0], url: page.url() };
  }
}"""

LIBRARY_DOWNLOAD_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Muse Library';
  try {
    if (!page.url().includes('/library')) {
      await page.goto(cfg.libraryUrl, { waitUntil: 'domcontentloaded' });
    }
    const settings = page.getByRole('button', { name: 'Settings', exact: true });
    try { await settings.waitFor({ timeout: 25000 }); }
    catch { return { ok: false, loggedIn: false, url: page.url(), reason: 'Muse is not signed in (run: aao web login muse)' }; }
    await page.waitForTimeout(2000);
    stage = 'Locate Muse Library items';
    const buttons = page.locator('[data-testid="hatch-sandbox-file-card-options"]');
    const total = await buttons.count();
    if (!total) return { ok: false, reason: 'No downloadable items found in Muse Library', url: page.url() };
    const indices = [];
    if (cfg.downloadAll) {
      const cap = cfg.limit ? Math.min(total, cfg.limit) : total;
      for (let i = 0; i < cap; i++) indices.push(i);
    } else {
      const titles = await buttons.evaluateAll(bs => bs.map(b => {
        const card = b.closest('.hatch-chat-groupable-bubble') || b.parentElement?.parentElement || b.parentElement;
        return (card?.innerText || '').trim();
      }));
      for (const want of cfg.items) {
        if (/^\d+$/.test(String(want))) {
          const idx = Number(want) - 1;
          if (idx >= 0 && idx < total) indices.push(idx);
        } else {
          const needle = String(want).toLowerCase();
          const idx = titles.findIndex(t => t.toLowerCase().includes(needle));
          if (idx >= 0) indices.push(idx);
        }
      }
    }
    if (!indices.length) return { ok: false, reason: 'No matching items found in Muse Library', url: page.url() };
    const files = [];
    for (const idx of indices) {
      stage = 'Download Muse Library item ' + (idx + 1);
      await buttons.nth(idx).click({ timeout: 10000 });
      const downloadItem = page.getByRole('menuitem', { name: 'Download', exact: true });
      await downloadItem.waitFor({ timeout: 10000 });
      const [dl] = await Promise.all([
        page.waitForEvent('download', { timeout: 30000 }),
        downloadItem.click({ timeout: 10000 }),
      ]);
      const name = dl.suggestedFilename() || ('muse-library-' + (idx + 1));
      const dest = cfg.directory + '/' + name;
      await dl.saveAs(dest);
      files.push(dest);
    }
    return { ok: true, files, url: page.url() };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message || error).split('\n')[0], url: page.url() };
  }
}"""


def list_library(kind="all", limit=None, timeout_s=90):
    """List generated media and documents in Muse's Library (`https://muse.ai/library/artifacts`)."""
    opened = not web.is_open(TARGET)
    if opened:
        ok, _, error = web.open_target(TARGET, headed=False)
        if not ok:
            return {"ok": False, "target": TARGET, "reason": error}
    try:
        cfg = {"libraryUrl": LIBRARY_URL, "kind": kind, "limit": limit}
        return web.run_script(TARGET, LIBRARY_LIST_SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s)
    finally:
        if opened:
            web.close_target(TARGET)


def download_library(directory, items=(), download_all=False, limit=None, timeout_s=180):
    """Download selected (or all) items from Muse's Library to `directory`."""
    if not download_all and not items:
        raise ValueError("Provide --item or --all to download from Muse Library")
    out = Path(directory).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    opened = not web.is_open(TARGET)
    if opened:
        ok, _, error = web.open_target(TARGET, headed=True)
        if not ok:
            return {"ok": False, "target": TARGET, "reason": error}
    try:
        cfg = {"libraryUrl": LIBRARY_URL, "directory": out.as_posix(),
               "items": list(items), "downloadAll": bool(download_all), "limit": limit}
        return web.run_script(TARGET, LIBRARY_DOWNLOAD_SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s)
    finally:
        if opened:
            web.close_target(TARGET)


CHATS_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Muse';
  try {
    await page.goto('https://muse.ai/', { waitUntil: 'domcontentloaded' });
    const settings = page.getByRole('button', { name: 'Settings', exact: true });
    try { await settings.waitFor({ timeout: 25000 }); }
    catch { return { ok: false, loggedIn: false, url: page.url(), reason: 'Muse is not signed in (run: aao web login muse)' }; }
    await page.waitForTimeout(1500);
    if (cfg.op === 'search') {
      stage = 'Search Muse chats';
      const searchInput = page.getByPlaceholder('Search').filter({ visible: true }).first();
      if (!await searchInput.isVisible().catch(() => false)) {
        await page.getByTestId('hatch-nav-search').click({ timeout: 10000 });
      }
      await searchInput.fill(cfg.query);
      await page.waitForTimeout(1500);
    }
    const rows = page.locator('[data-testid="hatch-thread-row"]');
    const count = await rows.count();
    if (cfg.op === 'read') {
      stage = 'Open Muse chat';
      let targetIdx = -1;
      const titles = await rows.evaluateAll(rs => rs.map(r => (r.innerText || '').trim()));
      if (/^\d+$/.test(String(cfg.chat))) {
        targetIdx = Number(cfg.chat) - 1;
      } else {
        const needle = String(cfg.chat).toLowerCase();
        targetIdx = titles.findIndex(t => t.toLowerCase() === needle || t.toLowerCase().includes(needle));
      }
      if (targetIdx < 0 || targetIdx >= count) {
        return { ok: false, reason: 'Muse chat not found: ' + cfg.chat, url: page.url() };
      }
      await rows.nth(targetIdx).click({ timeout: 10000 });
      await page.waitForTimeout(2000);
      const messages = await page.evaluate(() => {
        return [...document.querySelectorAll('.hatch-chat-groupable-bubble')].map(b => {
          const isAsst = !!b.querySelector('[data-hatch-assistant-message-body]');
          return { role: isAsst ? 'assistant' : 'user', text: (b.innerText || '').trim() };
        }).filter(m => m.text);
      });
      return { ok: true, title: titles[targetIdx], url: page.url(), messages };
    }
    const items = await rows.evaluateAll((rs, limit) => {
      const out = [];
      for (const r of rs) {
        const lines = (r.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        out.push({ index: out.length + 1, title: lines[0] || '', subtitle: lines.slice(1).join(' — ') });
        if (limit && out.length >= limit) break;
      }
      return out;
    }, cfg.limit || null);
    return { ok: true, url: page.url(), chats: items };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message || error).split('\n')[0], url: page.url() };
  }
}"""


def list_chats(limit=None, timeout_s=60):
    """List Muse sidebar chats and channels."""
    return _run_chats({"op": "list", "limit": limit}, timeout_s)


def search_chats(query, limit=None, timeout_s=60):
    """Search Muse chats by query."""
    return _run_chats({"op": "search", "query": query, "limit": limit}, timeout_s)


def read_chat(chat, timeout_s=60):
    """Open a Muse chat by 1-based index or title and return its messages."""
    return _run_chats({"op": "read", "chat": chat}, timeout_s)


def _run_chats(cfg, timeout_s):
    opened = not web.is_open(TARGET)
    if opened:
        ok, _, error = web.open_target(TARGET, headed=False)
        if not ok:
            return {"ok": False, "target": TARGET, "reason": error}
    try:
        return web.run_script(TARGET, CHATS_SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s)
    finally:
        if opened:
            web.close_target(TARGET)
