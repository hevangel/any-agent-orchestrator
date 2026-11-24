"""Read-only quota readers for Google Flow and the Gemini web app (`aao web quota flow|gemini`). No LLM.

Both sites use the Google account of the `gemini` login, so they share one browser profile and session
and are read one after the other. Nothing here clicks Upgrade, Get extra usage or Add credits.

- Flow: the account menu (top-right "Account details") shows "<N> Google Flow credits" and
  "Resets on <day> at <time>".
- Gemini: gemini.google.com/usage ("Usage limits") shows the current short limit and the weekly limit as
  "<N>% used" with their reset times; one.google.com/ai/activity shows the Google AI credits balance.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator import web
from orchestrator.web_muse import _window

FLOW_SCRIPT = r"""async page => {
  const tab = await page.context().newPage();
  try {
    await tab.goto('https://flow.google.com/', { waitUntil: 'domcontentloaded' });
    const account = tab.getByRole('button', { name: 'Account details' });
    try { await account.waitFor({ timeout: 30000 }); }
    catch { return { ok: false, loggedIn: false, url: tab.url(), reason: 'Flow is not signed in (run: aao web login gemini)' }; }
    const badge = (await account.innerText().catch(() => '')).trim();
    await account.click({ timeout: 10000 });
    const menu = tab.locator('.cdk-overlay-container, [role=dialog]').filter({ hasText: /Google Flow credits/ }).first();
    await menu.waitFor({ timeout: 20000 }).catch(() => {});
    const text = await menu.innerText().catch(() => '');
    await tab.keyboard.press('Escape').catch(() => {});
    return { ok: true, loggedIn: true, url: tab.url(), badge, text };
  } catch (error) {
    return { ok: false, reason: 'Read Flow credits: ' + String(error.message).split('\n')[0], url: tab.url() };
  } finally {
    await tab.close().catch(() => {});
  }
}"""

GEMINI_SCRIPT = r"""async page => {
  const tab = await page.context().newPage();
  try {
    await tab.goto('https://gemini.google.com/usage', { waitUntil: 'domcontentloaded' });
    const heading = tab.getByText('Usage limits', { exact: true }).first();
    try { await heading.waitFor({ timeout: 30000 }); }
    catch {
      const signedOut = /accounts\.google\.com/.test(tab.url()) || await tab.getByRole('link', { name: 'Sign in' }).count();
      return { ok: false, loggedIn: !signedOut, url: tab.url(),
               reason: signedOut ? 'Gemini is not signed in (run: aao web login gemini)' : 'Gemini showed no Usage limits page' };
    }
    await tab.getByText(/% used/).first().waitFor({ timeout: 20000 }).catch(() => {});
    const text = await tab.locator('body').innerText();
    let credits = '';
    try {
      await tab.goto('https://one.google.com/ai/activity', { waitUntil: 'domcontentloaded' });
      await tab.getByText('AI credits', { exact: true }).first().waitFor({ timeout: 20000 });
      credits = await tab.locator('body').innerText();
    } catch {}
    return { ok: true, loggedIn: true, url: tab.url(), text, credits };
  } catch (error) {
    return { ok: false, reason: 'Read Gemini usage: ' + String(error.message).split('\n')[0], url: tab.url() };
  } finally {
    await tab.close().catch(() => {});
  }
}"""

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_CLOCK = r"(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?"


def _clock(hour, minute, ampm):
    hour, minute = int(hour), int(minute)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower().startswith("p") else 0)
    return hour, minute


def parse_reset(text, now=None):
    """'Resets at 14:34' (the next 14:34) or 'Resets on 25 Nov at 16:34' / 'Resets on Nov 25 at 4:34 PM'
    in the browser's (this machine's) local time, as UTC ISO; None when the text has no reset."""
    now = (now or datetime.now(timezone.utc)).astimezone()
    match = re.search(r"\bat\s+" + _CLOCK, text or "", re.I)
    if not match:
        return None
    hour, minute = _clock(*match.groups())
    day = re.search(r"\bon\s+(?:(\d{1,2})\s+([A-Za-z]{3})[a-z]*|([A-Za-z]{3})[a-z]*\s+(\d{1,2}))", text, re.I)
    if day:
        num, mon = (day.group(1), day.group(2)) if day.group(1) else (day.group(4), day.group(3))
        month = _MONTHS.get(mon.lower())
        if not month:
            return None
        when = now.replace(month=month, day=int(num), hour=hour, minute=minute, second=0, microsecond=0)
        if when < now - timedelta(days=1):
            when = when.replace(year=when.year + 1)
    else:
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
    return when.astimezone(timezone.utc).isoformat()


def _row(provider, source):
    return {"provider": provider, "account": None, "plan": None, "status": "unavailable", "error": None,
            "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "windows": [],
            "balance": None, "source": source}


_FLOW_CREDITS = re.compile(r"([\d,.]+)\s+Google Flow credits", re.I)


def parse_flow(text, now=None):
    """Windows and balance from Flow's account menu text, or None when it shows no credits."""
    credits = _FLOW_CREDITS.search(text or "")
    if not credits:
        return None
    left = float(credits.group(1).replace(",", ""))
    left = int(left) if left.is_integer() else left
    reset = re.search(r"Resets?\s+on[^\n]*", text, re.I)
    reset_text = reset.group(0).strip() if reset else None
    label = credits.group(0)
    # The menu shows only what is left, not the monthly allowance, so there is no percentage.
    window = _window("monthly credits", None, resets_at=parse_reset(reset_text, now) if reset_text else None,
                     reset_text=reset_text, seconds=2592000, remaining=left, label=label)
    return {"windows": [window], "balance": [{"amount": left, "currency": "Google Flow credits", "label": label}]}


def flow_account(page, now=None):
    """A quotas-v1 row from the Flow script's result."""
    row = _row("flow", "flow-web")
    if not page.get("ok"):
        return {**row, "error": page.get("reason") or page.get("error") or "Flow credits could not be read"}
    parsed = parse_flow(page.get("text"), now)
    if not parsed:
        return {**row, "error": "Flow's account menu showed no Google Flow credits"}
    badge = (page.get("badge") or "").strip()
    return {**row, **parsed, "plan": badge if re.fullmatch(r"[A-Z][A-Za-z ]{1,20}", badge) else None, "status": "live"}


_USED = re.compile(r"^(\d+(?:\.\d+)?)%\s*used$", re.I)


def parse_gemini(text, credits_text="", now=None):
    """Windows from the Usage limits page: 'Current usage' (the short limit) and 'Weekly limit', each a
    '<N>% used' line with a 'Resets ...' line next to it. Extra usage and AI credits become the balance."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if "Usage limits" not in lines:
        return None
    lines = lines[lines.index("Usage limits") + 1:]
    plan = lines[0] if lines and re.fullmatch(r"[A-Z][A-Z ]{1,20}", lines[0]) else None
    names = {"current usage": ("current", None), "weekly limit": ("weekly", 604800), "daily limit": ("daily", 86400),
             "monthly limit": ("monthly", 2592000)}
    windows, section = [], None
    for i, line in enumerate(lines):
        if line.lower() in names:
            section = names[line.lower()]
            continue
        used = _USED.match(line)
        if not used or not section:
            continue
        near = [x for x in lines[max(0, i - 3):i + 2] if x.lower().startswith("resets")]
        reset_text = near[-1] if near else None
        name, seconds = section
        windows.append(_window(name, float(used.group(1)), resets_at=parse_reset(reset_text, now) if reset_text else None,
                               reset_text=reset_text, seconds=seconds, label=line))
        section = None
    if not windows:
        return None
    balance = []
    if "Extra usage" in lines:
        state = lines[lines.index("Extra usage") + 1] if lines.index("Extra usage") + 1 < len(lines) else ""
        if state in ("On", "Off"):
            balance.append({"amount": None, "currency": "extra usage", "label": f"Extra usage {state.lower()}"})
    clines = [line.strip() for line in (credits_text or "").splitlines() if line.strip()]
    if "AI credits" in clines:
        after = clines[clines.index("AI credits") + 1:]
        amount = next((x for x in after if re.fullmatch(r"[\d,]+", x)), None)
        if amount is not None:
            n = int(amount.replace(",", ""))
            balance.append({"amount": n, "currency": "Google AI credits", "label": f"{n:,} Google AI credits"})
    return {"plan": plan, "windows": windows, "balance": balance or None}


def gemini_account(page, now=None):
    """A quotas-v1 row from the Gemini script's result."""
    row = _row("gemini", "gemini-web")
    if not page.get("ok"):
        return {**row, "error": page.get("reason") or page.get("error") or "Gemini usage could not be read"}
    parsed = parse_gemini(page.get("text"), page.get("credits"), now)
    if not parsed:
        return {**row, "error": "Gemini's Usage limits page showed no limits"}
    return {**row, **parsed, "status": "live"}


def has_profile(settings=None):
    """True once `aao web login gemini` has created the shared Google profile (checked without creating it)."""
    s = settings or web._settings()
    base = Path(os.path.expanduser(s.get("AAO_WEB_PROFILE_DIR", "~/.aao/browser-profiles")))
    return (base / web.login_name("gemini")).is_dir()


def _read(target, script, to_row, timeout_s):
    """Run one read in the shared Google browser: a browser this call opens (headless) is closed again;
    an already open one is left as it was (the read uses its own tab)."""
    opened = not web.is_open(target)
    if opened:
        ok, _, error = web.open_target(target, headed=False)
        if not ok:
            return to_row({"ok": False, "reason": error})
    try:
        return to_row(web.run_script(target, script, timeout_s))
    finally:
        if opened:
            web.close_target(target)


def flow_quota(timeout_s=90):
    """Read the signed-in Google account's Flow credits. Read-only."""
    return _read("flow", FLOW_SCRIPT, flow_account, timeout_s)


def gemini_quota(timeout_s=120):
    """Read the Gemini app's usage limits and the Google AI credits balance. Read-only."""
    return _read("gemini", GEMINI_SCRIPT, gemini_account, timeout_s)
