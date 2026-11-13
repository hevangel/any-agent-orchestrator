"""`aao` command line."""

import argparse
import json
import sys

from orchestrator import free_tokens, pricing
from orchestrator.collect import COLLECTORS
from orchestrator.state import DATA_DIR, read_json


def _print(data, as_json):
    if as_json:
        print(json.dumps(data, indent=1))
        return True
    return False


def cmd_collect(args):
    for name in (COLLECTORS if args.what == "all" else [args.what]):
        print(f"collecting {name} ...", file=sys.stderr)
        data = COLLECTORS[name]()
        if not _print(data, args.json):
            if name == "machines":
                for m in data["machines"]:
                    host = m.get("host") or {}
                    mem = host.get("memory") or {}
                    extra = (f"cpu {host.get('cpu_percent')}%  ram free {round((mem.get('available') or 0) / 2**30, 1)} GiB"
                             if host else f"tasks {m.get('counts')}")
                    for g in host.get("gpus") or []:
                        load = f" {g['util_percent']:.0f}%" if g.get("util_percent") is not None else ""
                        vram = (f" vram {round(g['memory_used'] / 2**30, 1)}/{round(g['memory_total'] / 2**30, 1)} GiB"
                                if g.get("memory_total") and g.get("memory_used") is not None else "")
                        extra += f"  gpu {g['name']}{load}{vram}"
                    print(f"  {m['class']:5} {m['label']:24} {m['status']:12} {extra}  {m.get('error') or ''}")
            elif name == "quotas":
                for a in data["accounts"]:
                    wins = "  ".join(f"{w['name']} {w['remaining_pct']}% left" for w in a["windows"])
                    print(f"  {a['provider']:10} {a.get('plan') or '':10} {wins}  [{a['source']}]")
                for tool, err in data["errors"].items():
                    print(f"  ! {tool}: {err}")
            elif name == "usage":
                total = sum(c["cost_usd"] for c in data["by_day_machine_agent_model"])
                print(f"  {len(data['sessions'])} sessions since {data['since']}, API-equivalent ${total:,.2f}")
                for model, tokens in data["unpriced_models"].items():
                    print(f"  unpriced: {model} ({tokens:,} tokens)")
                for machine, err in data["errors"].items():
                    print(f"  ! {machine}: {err}")


def cmd_serve(args):
    from orchestrator.server import serve
    serve(args.host, args.port, refresh=not args.no_refresh)


def cmd_prices(args):
    if args.action == "update":
        from orchestrator import price_update

        report = price_update.update(providers=args.provider, dry_run=args.dry_run)
        for provider, count in report["parsed"].items():
            print(f"  {provider}: {count} models parsed")
        for kind, model, eff, old, new in report["changes"]:
            fmt = lambda p: "/".join("-" if p.get(k) is None else f"{p[k]:g}" for k in pricing.BUCKETS) if p else "none"  # noqa: E731
            print(f"  {kind:12} {model:32} from {eff}: {fmt(old)} -> {fmt(new)}")
        for provider, err in report["errors"].items():
            print(f"  ! {provider}: {err}")
        print("dry run: nothing written" if args.dry_run else f"{len(report['changes'])} change(s) written to {pricing.PRICES_FILE.name}")
        return 1 if report["errors"] else 0
    if args.action == "sync":
        print(f"LiteLLM snapshot: {pricing.sync_litellm()} models")
    elif args.action == "validate":
        errors = pricing.validate_table(read_json(pricing.PRICES_FILE, {}))
        print("\n".join(errors) or "api_prices.json OK")
        return 1 if errors else 0
    elif args.action == "lookup":
        print(json.dumps(pricing.PriceBook().lookup(args.model), indent=1))


def cmd_web(args):
    from orchestrator import drivers, web

    targets = list(web.TARGETS) if args.all or not args.target else [args.target]
    if args.action == "close":  # browsers stay open between commands (AAO_WEB_KEEP_OPEN); close them here
        names = {web.login_name(t) for t in targets if web.is_open(t)}
        for name in sorted(names):
            web.close_target(name)
            print(f"  closed {name}")
        return 0
    failed = 0
    for target in targets:
        if args.action == "login":
            result = web.login(target)
        elif args.action == "status":
            result = web.status(target)
        else:
            prompt, expect = drivers.check_prompt()
            try:
                result = web.ask(target, args.prompt or prompt, args.expect or expect, chat=args.chat,
                                 files=args.file, repo=args.repo, mode=args.mode, timeout_s=args.timeout)
            except (ValueError, FileNotFoundError) as exc:
                result = {"ok": False, "error": str(exc)}
        good = result.get("ok", result.get("loggedIn"))
        failed += not good
        detail = result.get("reason") or result.get("error") or result.get("url") or ""
        print(f"  {target:16} {'OK ' if good else 'NO '} {detail}")
        if result.get("taskStatus"):
            print(f"  {'':16} task: {result['taskStatus']}")
        if result.get("reply"):
            print(f"  {'':16} reply: {result['reply'][:500]}")
    return 1 if failed else 0


# Claude calls groups "sections" (chats and Code tab alike), ChatGPT calls them "projects"; every
# name works on the command line.
SECTION_ALIASES = {"sections": "groups", "new-section": "new-group", "delete-section": "delete-group",
                   "unsection": "ungroup"}
PROJECT_ALIASES = {"projects": "groups", "new-project": "new-group", "delete-project": "delete-group",
                   "unproject": "ungroup"}
NOTEBOOK_ALIASES = {"notebooks": "groups", "new-notebook": "new-group", "delete-notebook": "delete-group",
                    "unnotebook": "ungroup"}  # Gemini


def cmd_chats(args):
    from orchestrator import web_chats

    op = SECTION_ALIASES.get(args.op) or PROJECT_ALIASES.get(args.op) or NOTEBOOK_ALIASES.get(args.op) or args.op
    word = web_chats.RECIPES.get(args.target, {}).get("groupWord", "section")
    if op == "delete-group" and not args.yes:
        what = {"chatgpt": "(refused while it has chats: ChatGPT deletes a project's chats with it)",
                "grok": "(its chats stay as standalone chats)",
                "gemini": "(refused while it has sources or chats: Gemini deletes them in Gemini and NotebookLM)",
                }.get(args.target, "(its chats go back to Recents)")
        print(f"  deleting {word} {args.name!r} needs --yes {what}")
        return 1
    kwargs = {
        "list": {"group": getattr(args, "group", None), "status": getattr(args, "status", None)}, "groups": {},
        "read": {"chat": getattr(args, "chat", None)},
        "search": {"query": getattr(args, "query", None), "kind": getattr(args, "kind", None)},
        "pin": {"chat": getattr(args, "chat", None)}, "unpin": {"chat": getattr(args, "chat", None)},
        "move": {"chat": getattr(args, "chat", None), "group": getattr(args, "group", None),
                 "create": getattr(args, "create", False)},
        "ungroup": {"chat": getattr(args, "chat", None)},
        "new-group": {"group": getattr(args, "name", None), "chat": getattr(args, "chat", None)},
        "delete-group": {"group": getattr(args, "name", None)},
    }[op]
    if op == "new-group" and kwargs["chat"]:  # create the group by moving the chat into it
        op, kwargs = "move", {"chat": kwargs["chat"], "group": kwargs["group"], "create": True}
    if op == "groups":
        kwargs["allGroups"] = True  # every group, not only the ones the sidebar shows
    result = web_chats.session(args.target, "list" if op == "groups" else op, **kwargs)
    if args.json:
        print(json.dumps(result, indent=1))
    elif op in ("list", "groups") and result.get("sections") is not None:
        for s in result["sections"]:
            if op == "groups" and s["kind"] != "group":
                continue
            if args.group and s["name"] != args.group:
                continue
            flag = "".join(f" ({x})" for x, on in (("pinned", s.get("pinned")), ("collapsed", s["collapsed"]),
                                                    ("more not shown", s.get("more"))) if on)
            kind = word if s["kind"] == "group" else s["kind"]
            print(f"  [{kind}] {s['name']}  {len(s['items'])} item(s){flag}")
            if op == "list":
                for c in s["items"]:
                    extra = " · ".join(x for x in (c.get("status"), c.get("detail")) if x)
                    print(f"      {c['title'][:60]:60} {c.get('url') or ''}  {extra[:90]}".rstrip())
    elif op == "read" and result.get("ok"):
        state = result.get("taskStatus") or result.get("status") or ("working" if result.get("working") else "idle")
        print(f"  {result.get('title') or ''}  [{state}]  {result.get('url')}")
        print(f"    last prompt: {(result.get('lastPrompt') or '')[:300]}")
        print(f"    reply: {(result.get('reply') or '')[:1500]}")
        if result.get("actions"):
            print(f"    buttons: {', '.join(result['actions'])}")
    elif op == "search" and result.get("results") is not None:
        for c in result["results"]:
            print(f"  {c['type'] or '':13} {c['title'][:60]:60} {c['url'] or c['id']}")
        if not result["results"]:
            print("  no results")
    else:
        chat = result.get("chat") or {}
        arrow = "from" if op == "ungroup" else "->"
        what = " ".join(x for x in (chat.get("title") or chat.get("id"), result.get("group") and f"{arrow} {result['group']}") if x)
        state = "OK " if result.get("ok") else "NO "
        note = result.get("reason") or result.get("error") or ("done" if result.get("changed") else "")
        print(f"  {args.op:12} {state} {what}  {note}")
        if result.get("items"):
            print(f"      {result.get('menu')} menu: {', '.join(result['items'])}")
        for m in result.get("matches") or []:
            print(f"      {m['id']}  {m['title']}")
    return 0 if result.get("ok") else 1


def _print_question(q, indent="    "):
    """One step of a Codex question (request input): what it asks and what can be pressed."""
    print(f"{indent}step {q['step']}/{q['steps']}: {q.get('question') or q['title']}" + ("  [HUMAN: secrets needed]" if q.get("humanOnly") else ""))
    if q.get("description"):
        print(f"{indent}  {q['description'][:300]}")
    for i in q.get("items") or []:
        print(f"{indent}  {i['label']}: {i['value'][:120]}")
    for f in q.get("fields") or []:
        state = "filled" if f["filled"] else "EMPTY"
        print(f"{indent}  {f['kind']} {f['name']}: {state}" + (f"  ({f['scope']})" if f.get("scope") else ""))
    for o in q.get("options") or []:
        print(f"{indent}  option{' [x]' if o['selected'] else ''}: {o['label']}")
    if q.get("freeText"):
        print(f"{indent}  free text: {q['freeText']['label']}")
    acts = ", ".join(a["label"] + ("" if a["enabled"] else " (disabled)") for a in q.get("actions") or [])
    print(f"{indent}  actions: {acts}, Dismiss")


def cmd_codex(args):
    from orchestrator import web_codex

    kw = {"timeout_s": args.timeout}
    if args.op in ("read", "attention", "answer", "send", "wait", "archive"):
        kw["thread"] = getattr(args, "thread", None)
    if args.op == "answer":
        kw.update(choice=args.choice, text=args.text, noSend=args.no_send, step=args.step)
    if args.op in ("new", "send"):
        kw.update(prompt=args.prompt, wait=args.wait)
    if args.op == "new":
        kw.update(env=args.env, workIn=args.work_in, computer=args.computer, permission=args.permission,
                  model=args.model, effort=args.effort)
    result = web_codex.session(args.op, **kw)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        print(f"  NO {result.get('reason') or result.get('error')}")
        for m in result.get("matches") or []:
            print(f"      {m['id']}  {m['title']}")
        if result.get("choices"):
            print(f"      choices: {', '.join(result['choices'])}")
        if result.get("question"):
            _print_question(result["question"])
    for t in result.get("threads") or []:
        if args.op == "threads":
            print(f"  {t['title'][:48]:48} {t['runsOn'] or '?':8} {t['url']}")
            continue
        print(f"  {t['title']}  {t['url']}" + (f"  environment needs attention: {t['environmentAttention']}" if t.get("environmentAttention") else ""))
        for q in (t.get("attention") or {}).get("steps") or []:
            _print_question(q)
    if args.op == "attention" and result.get("ok") and not result.get("threads"):
        print(f"  nothing waiting ({result.get('checked')} thread(s) checked)")
    if args.op == "archive" and result.get("ok"):
        print(f"  archived {result.get('title') or result['id']}")
    if args.op in ("read", "new", "send", "wait") and result.get("id"):
        state = result.get("status") or ("working" if result.get("working") else "idle")
        print(f"  {result.get('title') or ''}  [{state}]  {result.get('url')}")
        if result.get("settings"):
            print("    " + "  ".join(f"{k}: {v}" for k, v in result["settings"].items() if v))
        e = result.get("edits")
        work = " ".join(x for x in (result.get("worked"), e and f"{e['summary']} +{e.get('added') or 0} -{e.get('removed') or 0}") if x)
        if work:
            print(f"    last turn: {work}")
        for f in (e or {}).get("files") or []:
            print(f"      {f['path']}  +{f['added']} -{f['removed']}")
        if e and e.get("notListed"):
            print(f"      ... {e['notListed']} more file(s) not listed")
        for pr in result.get("pullRequests") or []:
            print(f"    PR {pr}")
        for line in result.get("actions") or []:
            print(f"    · {line[:160]}")
        if result.get("turns") == 0:
            print("    (the page shows no messages for this thread)")
        if result.get("environmentAttention"):
            print(f"    environment needs attention: {result['environmentAttention']}")
        print(f"    last prompt: {(result.get('lastPrompt') or '')[:200]}")
        print(f"    reply: {(result.get('reply') or '')[:1500]}")
        for q in (result.get("attention") or {}).get("steps") or []:
            _print_question(q)
    if args.op == "answer" and result.get("ok"):
        what = f"answered {result['answered']!r}" if result.get("answered") else f"pressed {result.get('pressed')}"
        print(f"  {what} on step {result.get('step')} ({result.get('title')})")
        if result.get("next"):
            _print_question(result["next"])
        else:
            print("    no question open any more")
    return 0 if result.get("ok") else 1


def _print_session(r):
    from orchestrator import web_code

    state = "working" if r.get("working") else "idle"
    status = f" · {r['rowStatus']}" if r.get("rowStatus") else ""
    print(f"  {r.get('title') or ''}  [{state}{status}]  {r.get('url') or ''}")
    print(f"    runs on {web_code.where_label(r)}")
    if r.get("usage"):
        print(f"    usage {r['usage']}")
    if r.get("usageLimit"):
        print(f"    banner (may be left from an earlier turn): {r['usageLimit']}")
    where = " · ".join(x for x in (r.get("environment"), r.get("connection")) if x)
    if where or r.get("repos") or r.get("branch"):
        print(f"    environment {where or '-'}  repo {', '.join(r.get('repos') or []) or '-'}  branch {r.get('branch') or '-'}")
    for pr in r.get("pullRequests") or []:
        print(f"    PR {pr['url']} {pr.get('state') or ''}")
    if r.get("lastPrompt"):
        print(f"    last prompt: {r['lastPrompt'][:200]}")
    if r.get("reply"):
        print(f"    reply: {r['reply'][:1500]}")


def cmd_code(args):
    from orchestrator import web_chats, web_code

    if args.op == "sessions":
        result = web_code.sessions()
        if args.json:
            print(json.dumps(result, indent=1))
        for s in result.get("sections") or []:
            if not args.json:
                print(f"  [{'section' if s['kind'] == 'group' else s['kind']}] {s['name']}")
                for c in s["items"]:
                    extra = " · ".join(x for x in (c.get("status"), c.get("pr") and f"PR {c['pr']}") if x)
                    print(f"      {c['title'][:42]:42} {web_code.where_label(c)[:30]:30} {c['id']:32} {extra}")
        if not result.get("ok") and not args.json:
            print(f"  NO {result.get('reason') or result.get('error')}")
        return 0 if result.get("ok") else 1
    if args.op in ("projects", "project", "project-send"):
        op = {"project": "project-read"}.get(args.op, args.op)
        kw = {"timeout_s": args.timeout}
        if args.op != "projects":
            kw.update(project=args.project, last=args.last if args.op == "project" else 6)
        if args.op == "project-send":
            kw.update(prompt=args.prompt, wait=args.wait)
        result = web_code.session(op, **kw)
        if args.json:
            print(json.dumps(result, indent=1))
            return 0 if result.get("ok") else 1
        if not result.get("ok"):
            print(f"  NO {result.get('reason') or result.get('error')}")
        for p in result.get("projects") or []:
            print(f"  {p['name'][:40]:40} {p['detail'][:30]:30} {p['url']}")
        if result.get("name"):
            print(f"  project {result['name']}  (coordinator {result.get('model') or '?'})  {result['url']}")
            if result.get("usage"):
                print(f"    usage {result['usage']}")
            if result.get("usageLimit"):
                print(f"    banner (may be left from an earlier turn): {result['usageLimit']}")
            print("    " + "  ".join(f"{g['name']}: {g['count']}" for g in result.get("groups") or []))
            for t in result.get("threads") or []:
                print(f"    thread [{t['status']}] {t['title']}")
            for m in result.get("messages") or []:
                print(f"    {m['from']}: {m['text'][:600]}")
        return 0 if result.get("ok") else 1
    kw = {"timeout_s": args.timeout}
    if args.op in ("send", "new"):
        kw.update(prompt=args.prompt, wait=args.wait)
    if args.op == "new":
        kw.update(repo=args.repo, env=args.env, permission=args.permission)
    result = web_code.session(args.op, getattr(args, "session", None), **kw)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        if not result.get("ok"):
            print(f"  NO {result.get('reason') or result.get('error')}")
            for c in result.get("choices") or []:
                print(f"      {c}")
            for m in result.get("matches") or []:
                print(f"      {m['id']}  {m['title']}")
        if result.get("url") or result.get("title"):
            _print_session(result)
    return 0 if result.get("ok") else 1


def cmd_accounts(args):
    from orchestrator import accounts, config

    problems = config.validate()
    for a in config.accounts():
        print(f"  {a.id:20} {a.agent:10} {a.tier:5} {a.plan:12} prio {a.priority:<4} hosts {','.join(a.hosts)}"
              f"{'  home ' + a.home if a.home else ''}")
    if args.agent:
        chosen, reasons = accounts.choose(args.agent, args.host)
        print(f"\nnext {args.agent} account on {args.host}: {chosen.id if chosen else '(default login)'}")
        for r in reasons:
            print("  - " + r)
    for p in problems:
        print("  ! " + p)
    return 1 if problems else 0


def cmd_free_tokens(args):
    errors = free_tokens.validate(read_json(DATA_DIR / "free_tokens.json", {}))
    print("\n".join(errors) or "free_tokens.json OK")
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aao", description="any-agent-orchestrator tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="run collectors and write state/*.json")
    p.add_argument("what", nargs="?", default="all", choices=["all", *COLLECTORS])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("serve", help="serve the dashboard with background collectors")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-refresh", action="store_true", help="serve existing snapshots only")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("prices", help="price table tools")
    p.add_argument("action", choices=["update", "sync", "validate", "lookup"],
                   help="update: parse official pricing pages into data/api_prices.json; sync: LiteLLM fallback snapshot")
    p.add_argument("model", nargs="?")
    p.add_argument("--dry-run", action="store_true", help="update: show changes without writing")
    p.add_argument("--provider", action="append", help="update: only this provider (repeatable)")
    p.set_defaults(func=cmd_prices)

    p = sub.add_parser("web", help="AI chat web apps through playwright-cli with saved login profiles")
    p.add_argument("action", choices=["login", "status", "ask", "close"],
                   help="close: close the kept-open browser(s); every other command reuses an open one")
    p.add_argument("target", nargs="?", help="chatgpt, claude, gemini, grok, muse, zai, codex-cloud, claude-code-web")
    p.add_argument("--all", action="store_true")
    p.add_argument("--prompt")
    p.add_argument("--expect", help="regex the reply must match")
    p.add_argument("--chat", help="ask: continue this chat (id, URL or exact title) instead of a new one")
    p.add_argument("--file", action="append", help="ask: attach a local file (repeatable; gemini; zip a folder)")
    p.add_argument("--repo", help="ask: attach a GitHub repository or branch URL (gemini 'Import code')")
    p.add_argument("--mode", help="ask: composer mode, e.g. gemini chat|task")
    p.add_argument("--timeout", type=int, default=120, help="ask: seconds to wait for the reply")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("chats", help="chat/session list of a web app: list, search, pin, sections/projects (groups)")
    p.add_argument("target", help="claude (chats), claude-code-web (Code sessions), chatgpt or grok (projects), gemini (notebooks)")
    p.add_argument("--json", action="store_true")
    ops = p.add_subparsers(dest="op", required=True)
    alias = {v: [k, next(p for p, g in PROJECT_ALIASES.items() if g == v), next(n for n, g in NOTEBOOK_ALIASES.items() if g == v)]
             for k, v in SECTION_ALIASES.items()}
    o = ops.add_parser("list", help="sidebar sections (Pinned, your sections, Recents) and their items")
    o.add_argument("--group", "--section", "--project", "--notebook", dest="group",
                   help="only this section (a ChatGPT project or Gemini notebook: all its chats)")
    o.add_argument("--status", help="gemini: only chats whose task state is this, e.g. 'Needs attention'")
    o = ops.add_parser("groups", aliases=alias["groups"], help="list the sections / projects (groups)")
    o.set_defaults(group=None)
    o = ops.add_parser("search", help="search chats (and artifacts, sessions, projects)")
    o.add_argument("query")
    o.add_argument("--kind", help="search filter tab, e.g. Code, Projects, Artifacts")
    for name in ("pin", "unpin"):
        ops.add_parser(name).add_argument("chat", help="id, URL or exact title")
    ops.add_parser("ungroup", aliases=alias["ungroup"], help="take it out of its section").add_argument("chat")
    ops.add_parser("read", help="gemini: a chat's last prompt, reply and task state").add_argument("chat")
    o = ops.add_parser("move", help="move a chat or session into a section")
    o.add_argument("chat")
    o.add_argument("group", metavar="section")
    o.add_argument("--create", action="store_true", help="create the section if it does not exist")
    o = ops.add_parser("new-group", aliases=alias["new-group"], help="create an empty section, or one holding --chat")
    o.add_argument("name")
    o.add_argument("--chat")
    o = ops.add_parser("delete-group", aliases=alias["delete-group"], help="delete a section; its items return to Recents")
    o.add_argument("name")
    o.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_chats)

    p = sub.add_parser("codex", help="Codex on chatgpt.com: threads, read, questions waiting for an answer")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=int, default=300)
    ops = p.add_subparsers(dest="op", required=True)
    ops.add_parser("threads", help="sidebar threads and where they run")
    ops.add_parser("read", help="last prompt, reply, work time, file changes and open questions").add_argument("thread")
    o = ops.add_parser("attention", help="questions waiting for an answer (every step), one thread or all")
    o.add_argument("thread", nargs="?")
    o = ops.add_parser("answer", help="answer a thread's open question: an option, a button, or free text (never a secret)")
    o.add_argument("thread")
    g = o.add_mutually_exclusive_group(required=True)
    g.add_argument("--choice", help="option or button label, e.g. red, Next, Skip, Finish review, Dismiss")
    g.add_argument("--text", help="free-text answer (only where the question offers 'write your own response')")
    o.add_argument("--no-send", action="store_true", help="with an option: select it but do not press Send")
    o.add_argument("--step", type=int, help="step to answer (default: the one shown)")
    o = ops.add_parser("new", help="start a thread (only from the new-thread page)")
    o.add_argument("--prompt", required=True)
    o.add_argument("--env", help="environment as listed, e.g. 'Default environment', 'ai_engineer'")
    o.add_argument("--work-in", choices=["Cloud", "Remote"])
    o.add_argument("--computer", help="with --work-in Remote: the computer to run on")
    o.add_argument("--permission", choices=["Ask for approval", "Approve for me", "Full access"])
    o.add_argument("--model", help="e.g. 'GPT-6.1 Sol'")
    o.add_argument("--effort", help="e.g. Light, Medium, High")
    o.add_argument("--wait", action="store_true", help="wait for the turn to end and print the reply")
    o = ops.add_parser("send", help="follow up in a thread")
    o.add_argument("thread")
    o.add_argument("--prompt", required=True)
    o.add_argument("--wait", action="store_true")
    ops.add_parser("wait", help="wait until the thread's turn ends or a question opens").add_argument("thread")
    ops.add_parser("archive", help="archive a thread").add_argument("thread")
    p.set_defaults(func=cmd_codex)

    p = sub.add_parser("code", help="Claude Code on the web: list, read, prompt and start sessions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=int, default=600, help="seconds to wait for a turn (--wait, wait)")
    ops = p.add_subparsers(dest="op", required=True)
    ops.add_parser("sessions", help="sidebar sessions with status and location (pin/group: aao chats claude-code-web)")
    ops.add_parser("read", help="status, environment, repo, branch, PRs and the last reply").add_argument("session")
    ops.add_parser("wait", help="wait until the session finishes its turn").add_argument("session")
    o = ops.add_parser("send", help="send a prompt to an existing session")
    o.add_argument("session")
    o.add_argument("--prompt", required=True)
    o.add_argument("--wait", action="store_true", help="wait for the turn to finish and print the reply")
    o = ops.add_parser("new", help="start a new session")
    o.add_argument("--prompt", required=True)
    o.add_argument("--repo", help="owner/name as listed in the repository picker")
    o.add_argument("--env", help="environment name (e.g. full, Default, Auto)")
    o.add_argument("--permission", help="Auto, Accept edits or Plan")
    o.add_argument("--wait", action="store_true")
    ops.add_parser("projects", help="list Projects (beta)")
    o = ops.add_parser("project", help="a project: threads and their status, waiting/resolved counts, recent messages")
    o.add_argument("project", help="project name or id (chan_...)")
    o.add_argument("--last", type=int, default=6, help="recent messages to show")
    o = ops.add_parser("project-send", help="message a project's coordinator (it starts or routes to threads)")
    o.add_argument("project")
    o.add_argument("--prompt", required=True)
    o.add_argument("--wait", action="store_true", help="wait for the coordinator's reply")
    p.set_defaults(func=cmd_code)

    p = sub.add_parser("accounts", help="list configured accounts, validate .env, pick the next account")
    p.add_argument("--agent", help="show which account would be used next for this agent")
    p.add_argument("--host", default="local")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("free-tokens", help="free token list tools")
    p.add_argument("action", choices=["validate"])
    p.set_defaults(func=cmd_free_tokens)

    args = parser.parse_args(argv)
    # Windows consoles default to a legacy code page; page text has "·", "—", emoji
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
