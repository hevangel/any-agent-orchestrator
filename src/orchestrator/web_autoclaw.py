"""Control existing AutoClaw tasks through Zwork's paired-device web UI."""

import json
import re

from orchestrator import web

TARGET = "autoclaw"

SCRIPT = r"""async page => {
  const cfg = __CFG__;
  const result = fields => JSON.stringify({ url: cfg.url, ...fields });
  const fail = (stage, reason, fields) => result({ ok: false, stage, reason, ...fields });
  const sites = [page, ...page.context().pages().filter(tab => tab !== page)]
    .filter(tab => tab.url().startsWith(cfg.url));
  // Pairing links can open a second tab. Prefer a live connection and reuse a remembered computer
  // if every tab is disconnected. Only inspect tabs on the AutoClaw origin.
  const findTab = async marker => {
    for (const tab of sites) {
      if (await tab.locator(marker).filter({ visible: true }).count().catch(() => 0)) return tab;
    }
  };
  page = await findTab('[data-testid=home-screen], [data-testid=chat-screen]')
    || await findTab('[data-testid=device-row]') || page;
  const test = name => page.getByTestId(name).filter({ visible: true });
  const home = test('home-screen');
  const chat = test('chat-screen');
  const device = test('device-row');
  const waitFor = (locator, timeout = 15000) =>
    locator.first().waitFor({ timeout }).then(() => true, () => false);

  // The pairing link can carry a secret fragment. Leave it to the browser and keep it out of output.
  if (!cfg.passive && !page.url().startsWith(cfg.url))
    await page.goto(cfg.url, { waitUntil: 'domcontentloaded' });
  const screens = home.or(chat);
  if (!cfg.passive) {
    await waitFor(screens.or(device).or(test('pair-screen')));
    if (!await screens.count() && await device.count()) {
      if (await device.isEnabled()) await device.click({ timeout: 5000 });
      await waitFor(screens.or(test('connect-error')));
    }
  }
  if (!await screens.count()) {
    return fail('connect', await device.count()
      ? 'Could not connect to the paired computer. Check that Zwork is running.'
      : 'Pair this browser in Zwork Settings → Remote Control (aao web login autoclaw).',
      { loggedIn: false });
  }
  const offline = () => test('conn-banner').first().count();
  if (cfg.action === 'status') {
    if (await offline()) return fail('connect', 'The paired computer is reconnecting or offline.', { loggedIn: false });
    return result({ loggedIn: true, ok: true });
  }

  if (!await home.count() && await chat.count()) await test('back-btn').click({ timeout: 5000 });
  const rows = home.getByTestId('session-row').filter({ visible: true });
  if (!await waitFor(rows.or(test('list-empty'))))
    return fail('sessions', 'AutoClaw did not load the session list.', { loggedIn: true });
  if (await offline()) return fail('connect', 'The paired computer is reconnecting or offline.', { loggedIn: true });
  const sessions = await rows.evaluateAll(elements => elements.map(row => ({
    title: row.querySelector('.srow-title')?.textContent.trim() || '',
    status: row.querySelector('[data-testid=bot-dot]')?.getAttribute('data-status') || 'idle',
    workspace: row.querySelector('.tag')?.textContent.trim() || '',
  })));
  if (cfg.action === 'sessions') return result({ loggedIn: true, ok: true, sessions });
  const title = page.locator('.srow-title').and(page.getByText(cfg.chat, { exact: true }));
  const selected = rows.filter({ has: title });
  const matches = await selected.count();
  if (matches !== 1) return fail('session', matches
    ? 'More than one session has this title. Give the session a unique title in Zwork.'
    : 'Session not found. Run aao web sessions autoclaw to see available titles.',
    { loggedIn: true, session: cfg.chat });
  const transcript = chat.getByTestId('transcript-view');
  const heading = chat.locator('.capsule-title').and(page.getByText(cfg.chat, { exact: true }));
  for (let attempt = 0; attempt < 2; attempt++) {
    await selected.click({ timeout: 5000 });
    if (!await waitFor(heading) || !await waitFor(transcript))
      return fail('session', 'The selected session did not open.', { loggedIn: true });
    const loaded = await test('chat-skeleton').first().waitFor({ state: 'hidden', timeout: 15000 })
      .then(() => true, () => false);
    const error = (await test('snapshot-error').allTextContents()).join('\n');
    if (loaded && !error) break;
    // Zwork offers a return-to-list action on transcript errors. Retry opening once before sending.
    const back = test('snapshot-back');
    if (attempt || !await back.count()) {
      const reason = error ? 'Zwork could not load the transcript. ' + error : 'Zwork timed out loading the transcript.';
      return fail('session', reason, { loggedIn: true, session: cfg.chat });
    }
    await back.click({ timeout: 5000 });
  }

  const messages = () => transcript.locator('[data-testid=tr-user], [data-testid=tr-output]')
    .evaluateAll(elements => elements.map(el => el.dataset.testid === 'tr-user'
      ? { role: 'user', text: el.textContent.trim() }
      : { role: 'assistant', text: el.innerText.trim() }));
  const status = () => chat.evaluate(el =>
    el.querySelector('[data-testid=chat-capsule] [data-testid=bot-dot]')?.dataset.status || 'idle');
  const attention = async state => state === 'attention' || await test('question-card')
    .or(test('approval-card').filter({ has: page.getByTestId('approve-btn') }))
    .or(test('answer-on-computer')).count();
  const busy = async state => ['running', 'queued'].includes(state) ||
    await test('pending-note').or(test('queued-note')).or(test('run-state')).count();
  const details = { loggedIn: true, session: cfg.chat };
  const before = await messages();
  const initial = await status();
  if (cfg.action === 'read') return result({ ...details, ok: true, status: initial, messages: before });
  if (await attention(initial)) return fail('needs-attention', 'AutoClaw is waiting for input in Chrome.', details);
  if (await busy(initial)) return fail('busy', 'The session is working on another turn.', details);
  if (initial === 'offline' || await offline())
    return fail('connect', 'The paired computer is reconnecting or offline.', details);
  const input = chat.getByTestId('send-input');
  if (await input.inputValue({ timeout: 5000 }) || await test('staged-chip').count())
    return fail('send', 'The session already has a draft or staged attachment in Chrome.', details);
  const errors = await test('send-error').allTextContents();
  const started = Date.now();
  const deadline = started + cfg.timeoutMs;
  await input.fill(cfg.prompt, { timeout: 5000 });
  await chat.getByTestId('send-btn').click({ timeout: 5000 }).catch(() => {});

  const userCount = before.filter(message => message.role === 'user').length;
  let reply = '', previous = null, sent = false;
  while (Date.now() < deadline) {
    const state = await status();
    const transcriptNow = await messages();
    const indexes = transcriptNow.flatMap((message, index) => message.role === 'user' ? [index] : []);
    const index = indexes[userCount];
    sent = index !== undefined && transcriptNow[index].text === cfg.prompt.trim();
    if (index !== undefined && (!sent || indexes.length !== userCount + 1))
      return fail('reply', 'Another message appeared in the session. Check the transcript in Chrome.', details);
    reply = sent ? transcriptNow.slice(index + 1).map(message => message.text).filter(Boolean).join('\n\n') : '';
    if (state === 'offline' || await offline())
      return fail('connect', 'The paired computer disconnected. Check the transcript before sending again.', { ...details, reply });
    if (await attention(state))
      return fail('needs-attention', 'AutoClaw is waiting for input in Chrome.', { ...details, reply });
    const newErrors = (await test('send-error').allTextContents()).filter(error => !errors.includes(error));
    if (newErrors.length) return fail('send', newErrors.join('\n'), { ...details, reply });
    if (sent && state === 'failed' && !await busy(state))
      return fail('reply', 'AutoClaw reported that the run failed.', { ...details, reply });
    if (reply && !await busy(state)) {
      if (reply === previous) return result({ ...details, ok: true, reply, seconds: (Date.now() - started) / 1000 });
      previous = reply;
    } else {
      previous = null;
    }
    await page.waitForTimeout(1000);
  }
  return fail(sent ? 'reply' : 'send', sent ? 'Timed out waiting for the reply.'
    : 'Could not confirm delivery. Check the transcript before sending again.', { ...details, reply });
}"""


def page(action, *, passive=False, chat=None, prompt=None, timeout_s=120):
    config = {"action": action, "url": web.TARGETS[TARGET]["url"], "passive": passive,
              "chat": chat, "prompt": prompt, "timeoutMs": timeout_s * 1000}
    return web.run_script(TARGET, SCRIPT.replace("__CFG__", json.dumps(config)), timeout_s + 75)


def control(action, *, chat=None, prompt=None, expect=None, timeout_s=120, settings=None,
            screenshot_path=None):
    if action in {"read", "ask"} and not (chat and chat.strip()):
        raise ValueError("Use --chat with an exact session title from aao web sessions autoclaw")
    if action == "ask" and not (prompt and prompt.strip()):
        raise ValueError("AutoClaw requires a non-empty --prompt")
    if timeout_s <= 0:
        raise ValueError("--timeout must be positive")
    try:
        matcher = re.compile(expect) if expect else None
    except re.error as exc:
        raise ValueError(f"Invalid --expect regex: {exc}") from exc
    ok, output, error = web.open_target(TARGET, settings=settings)
    if not ok:
        return {"target": TARGET, "ok": False, "error": error or output}
    try:
        text = prompt.strip().replace("\r\n", "\n").replace("\r", "\n") if prompt else None
        result = page(action, chat=chat, prompt=text, timeout_s=timeout_s)
        if matcher and result.get("ok") and not matcher.search(result.get("reply", "")):
            result.update(ok=False, stage="expect", reason=f"Reply does not match {expect}")
        return {"target": TARGET, **result}
    finally:
        if screenshot_path:
            web.screenshot(TARGET, screenshot_path)
        web.release(TARGET, settings=settings)
