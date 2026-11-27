"""List and download items from Gemini's signed-in Library (`https://gemini.google.com/library`)."""

import json
from pathlib import Path
from typing import Literal

from orchestrator import web

TARGET = "gemini"
LIBRARY_URL = "https://gemini.google.com/library"
LibraryKind = Literal["all", "media", "documents"]

LIST_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Gemini Library';
  try {
    if (!page.url().includes('/library')) {
      await page.goto(cfg.libraryUrl, { waitUntil: 'domcontentloaded' });
    }
    if (/google\.com\/sorry/i.test(page.url())) {
      return { ok: false, blocked: true, reason: 'Google is showing its "unusual traffic" check (a CAPTCHA); a human must clear it in the browser', url: page.url() };
    }
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) {
      return { ok: false, loggedIn: false, reason: 'Gemini login required', url: page.url() };
    }
    const root = page.locator('declared-route-island-page');
    await root.waitFor({ timeout: 30000 });
    await page.waitForTimeout(2000);
    // Scroll the inner library container to trigger lazy loading until limit (or end)
    await root.evaluate(async (el, targetCount) => {
      const scroller = [...el.querySelectorAll('*')].find(
        e => e.scrollHeight > e.clientHeight + 20 && /(scroll|auto)/.test(getComputedStyle(e).overflowY)
      ) || el.closest('bard-sidenav-content') || document.scrollingElement || el;
      let prevCount = -1;
      let stable = 0;
      for (let i = 0; i < 40; i++) {
        const count = el.querySelectorAll('button[aria-label^="Preview or open"]').length;
        if (targetCount && count >= targetCount) break;
        scroller.scrollTo(0, scroller.scrollHeight);
        await new Promise(r => setTimeout(r, 900));
        const nextCount = el.querySelectorAll('button[aria-label^="Preview or open"]').length;
        if (nextCount === prevCount) {
          stable++;
          if (stable >= 2) break;
        } else {
          stable = 0;
        }
        prevCount = nextCount;
      }
      scroller.scrollTo(0, 0);
    }, cfg.limit || null);

    stage = 'Read Gemini Library items';
    const data = await root.evaluate(() => {
      const docLinks = [...document.querySelectorAll('declared-route-island-page a[href^="/app/"]')];
      const documents = docLinks.map((a, i) => {
        const aria = (a.getAttribute('aria-label') || '').trim();
        const title = aria.replace(/^Open\s+/i, '').trim() || (a.innerText || '').trim();
        const parts = (a.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
        const date = parts.length > 1 ? parts[parts.length - 1] : '';
        const href = a.getAttribute('href') || '';
        const id = href.replace(/^\/app\//, '');
        return {
          index: i + 1,
          kind: 'document',
          title,
          date,
          id,
          url: 'https://gemini.google.com' + href,
        };
      });

      const previews = [...document.querySelectorAll('declared-route-island-page button[aria-label^="Preview or open"]')];
      const media = previews.map((b, i) => {
        const card = b.parentElement;
        const aria = (b.getAttribute('aria-label') || '').trim();
        const title = aria.replace(/^Preview or open\s+/i, '').trim() || 'Untitled';
        const img = b.querySelector('img');
        const vid = b.querySelector('video');
        const hasPlayIcon = !!(card && card.querySelector('mat-icon, [class*="video"], [class*="play"]'));
        return {
          index: i + 1,
          kind: (vid || (hasPlayIcon && !img)) ? 'video' : 'image',
          title,
          thumbnail: img ? img.src : '',
        };
      });
      return { documents, media };
    });

    return { ok: true, url: page.url(), ...data };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message || error).split('\n')[0], url: page.url() };
  }
}"""

DOWNLOAD_SCRIPT = r"""async page => {
  const cfg = __CFG__;
  let stage = 'Open Gemini Library';
  try {
    if (!page.url().includes('/library')) {
      await page.goto(cfg.libraryUrl, { waitUntil: 'domcontentloaded' });
    }
    if (/google\.com\/sorry/i.test(page.url())) {
      return { ok: false, blocked: true, reason: 'Google is showing its "unusual traffic" check (a CAPTCHA); a human must clear it in the browser', url: page.url() };
    }
    if (new RegExp(cfg.loginUrl, 'i').test(page.url())) {
      return { ok: false, loggedIn: false, reason: 'Gemini login required', url: page.url() };
    }
    const root = page.locator('declared-route-island-page');
    await root.waitFor({ timeout: 30000 });
    await page.waitForTimeout(2000);
    await root.evaluate(async (el, targetCount) => {
      const scroller = [...el.querySelectorAll('*')].find(
        e => e.scrollHeight > e.clientHeight + 20 && /(scroll|auto)/.test(getComputedStyle(e).overflowY)
      ) || el.closest('bard-sidenav-content') || document.scrollingElement || el;
      let prevCount = -1;
      let stable = 0;
      for (let i = 0; i < 40; i++) {
        const count = el.querySelectorAll('button[aria-label^="Preview or open"]').length;
        if (targetCount && count >= targetCount) break;
        scroller.scrollTo(0, scroller.scrollHeight);
        await new Promise(r => setTimeout(r, 900));
        const nextCount = el.querySelectorAll('button[aria-label^="Preview or open"]').length;
        if (nextCount === prevCount) {
          stable++;
          if (stable >= 2) break;
        } else {
          stable = 0;
        }
        prevCount = nextCount;
      }
      scroller.scrollTo(0, 0);
    }, cfg.downloadAll && cfg.limit ? cfg.limit : null);

    const previews = root.locator('button[aria-label^="Preview or open"]');
    const total = await previews.count();
    if (!total) {
      return { ok: false, reason: 'No media items found in Gemini Library', url: page.url() };
    }

    const titles = await previews.evaluateAll(els =>
      els.map(b => (b.getAttribute('aria-label') || '').replace(/^Preview or open\s+/i, '').trim() || 'Untitled')
    );

    let indices = [];
    if (cfg.downloadAll) {
      const cap = cfg.limit ? Math.min(total, cfg.limit) : total;
      for (let i = 1; i <= cap; i++) indices.push(i);
    } else {
      for (const spec of cfg.items) {
        if (/^\d+$/.test(String(spec).trim())) {
          const idx = Number(String(spec).trim());
          if (idx < 1 || idx > total) {
            return { ok: false, reason: `Media index ${idx} out of range (1..${total})`, url: page.url() };
          }
          indices.push(idx);
        } else {
          const q = String(spec).trim().toLowerCase();
          const idx = titles.findIndex(t => t.toLowerCase().includes(q));
          if (idx === -1) {
            return { ok: false, reason: `No Gemini Library media matching "${spec}"`, url: page.url() };
          }
          indices.push(idx + 1);
        }
      }
    }

    const downloaded = [];
    const usedNames = new Set(cfg.existingFiles || []);
    const uniqueName = (suggested, idx) => {
      let name = suggested || `gemini-library-${idx}.bin`;
      if (!usedNames.has(name)) {
        usedNames.add(name);
        return name;
      }
      const dot = name.lastIndexOf('.');
      const stem = dot > 0 ? name.slice(0, dot) : name;
      const ext = dot > 0 ? name.slice(dot) : '';
      let n = 2;
      while (usedNames.has(`${stem}-${n}${ext}`)) n++;
      name = `${stem}-${n}${ext}`;
      usedNames.add(name);
      return name;
    };

    for (const idx of indices) {
      stage = `Open preview for media #${idx}`;
      const btn = previews.nth(idx - 1);
      await btn.scrollIntoViewIfNeeded();
      await btn.click({ timeout: 15000 });
      const dlBtn = page.locator('.cdk-overlay-pane button[aria-label*="Download" i], [role=dialog] button[aria-label*="Download" i]').first();
      await dlBtn.waitFor({ timeout: 15000 });
      stage = `Download media #${idx}`;
      const [file] = await Promise.all([
        page.waitForEvent('download', { timeout: 30000 }),
        dlBtn.click(),
      ]);
      const filename = uniqueName(file.suggestedFilename(), idx);
      const path = cfg.directory + '/' + filename;
      await file.saveAs(path);
      downloaded.push({ index: idx, title: titles[idx - 1], path });
      const closeBtn = page.locator('.cdk-overlay-pane button[aria-label="Close"], [role=dialog] button[aria-label="Close"]').first();
      if (await closeBtn.isVisible().catch(() => false)) {
        await closeBtn.click();
      } else {
        await page.keyboard.press('Escape');
      }
      await page.waitForTimeout(400);
    }

    return {
      ok: true,
      url: page.url(),
      files: downloaded.map(d => d.path),
      items: downloaded,
    };
  } catch (error) {
    return { ok: false, reason: stage + ': ' + String(error.message || error).split('\n')[0], url: page.url() };
  }
}"""


def list_library(*, kind: LibraryKind = "all", limit: int | None = None, timeout_s: float = 120) -> dict:
    """List documents and media items in Gemini's signed-in Library."""
    if kind not in ("all", "media", "documents"):
        raise ValueError(f"Unknown Gemini library type: {kind}")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive")
    if timeout_s <= 0:
        raise ValueError("--timeout must be positive")

    cfg = {"libraryUrl": LIBRARY_URL, "loginUrl": web.LOGIN_URL, "limit": limit}
    ok, _, error = web.open_target(TARGET)
    if not ok:
        return {"ok": False, "target": TARGET, "reason": error}
    try:
        result = web.run_script(TARGET, LIST_SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s)
    finally:
        web.release(TARGET)

    if not result.get("ok"):
        return {"target": TARGET, **result}

    documents = result.get("documents") or []
    media = result.get("media") or []
    if kind == "media":
        documents = []
    elif kind == "documents":
        media = []
    if limit is not None:
        documents = documents[:limit]
        media = media[:limit]

    return {
        "ok": True,
        "target": TARGET,
        "url": result.get("url") or LIBRARY_URL,
        "documents": documents,
        "media": media,
    }


def download_library(
    directory: str | Path,
    *,
    items: tuple[str, ...] | list[str] = (),
    download_all: bool = False,
    limit: int | None = None,
    timeout_s: float = 300,
) -> dict:
    """Download one or more media items from Gemini's signed-in Library."""
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned and not download_all:
        raise ValueError("Specify --item N (1-based media index from `aao web library gemini`) or --all")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be positive")
    if timeout_s <= 0:
        raise ValueError("--timeout must be positive")

    out_dir = Path(directory).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [p.name for p in out_dir.iterdir() if p.is_file()]

    cfg = {
        "libraryUrl": LIBRARY_URL,
        "loginUrl": web.LOGIN_URL,
        "directory": out_dir.as_posix(),
        "items": cleaned,
        "downloadAll": bool(download_all),
        "limit": limit,
        "existingFiles": existing,
    }
    ok, _, error = web.open_target(TARGET)
    if not ok:
        return {"ok": False, "target": TARGET, "reason": error}
    try:
        result = web.run_script(TARGET, DOWNLOAD_SCRIPT.replace("__CFG__", json.dumps(cfg), 1), timeout_s)
    finally:
        web.release(TARGET)

    return {"target": TARGET, **result}
