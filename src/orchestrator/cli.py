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
            result = web.ask(target, args.prompt or prompt, args.expect or expect, chat=args.chat)
        good = result.get("ok", result.get("loggedIn"))
        failed += not good
        detail = result.get("reason") or result.get("error") or result.get("url") or ""
        print(f"  {target:16} {'OK ' if good else 'NO '} {detail}")
        if result.get("reply"):
            print(f"  {'':16} reply: {result['reply'][:500]}")
    return 1 if failed else 0


# Claude calls groups "sections" (chats and Code tab alike); both names work on the command line.
SECTION_ALIASES = {"sections": "groups", "new-section": "new-group", "delete-section": "delete-group",
                   "unsection": "ungroup"}


def cmd_chats(args):
    from orchestrator import web_chats

    op = SECTION_ALIASES.get(args.op, args.op)
    if op == "delete-group" and not args.yes:
        print(f"  deleting section {args.name!r} needs --yes (its chats go back to Recents)")
        return 1
    kwargs = {
        "list": {}, "groups": {},
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
    result = web_chats.session(args.target, "list" if op == "groups" else op, **kwargs)
    if args.json:
        print(json.dumps(result, indent=1))
    elif op in ("list", "groups") and result.get("sections") is not None:
        for s in result["sections"]:
            if op == "groups" and s["kind"] != "group":
                continue
            if args.group and s["name"] != args.group:
                continue
            flag = " (collapsed)" if s["collapsed"] else ""
            kind = "section" if s["kind"] == "group" else s["kind"]
            print(f"  [{kind}] {s['name']}  {len(s['items'])} item(s){flag}")
            if op == "list":
                for c in s["items"]:
                    extra = " · ".join(x for x in (c.get("status"), c.get("detail")) if x)
                    print(f"      {c['title'][:60]:60} {c['url']}  {extra}".rstrip())
    elif op == "search" and result.get("results") is not None:
        for c in result["results"]:
            print(f"  {c['type'] or '':13} {c['title'][:60]:60} {c['url'] or c['id']}")
        if not result["results"]:
            print("  no results")
    else:
        chat = result.get("chat") or {}
        what = " ".join(x for x in (chat.get("title") or chat.get("id"), result.get("group") and f"-> {result['group']}") if x)
        state = "OK " if result.get("ok") else "NO "
        note = result.get("reason") or result.get("error") or ("done" if result.get("changed") else "")
        print(f"  {op:12} {state} {what}  {note}")
        for m in result.get("matches") or []:
            print(f"      {m['id']}  {m['title']}")
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
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("chats", help="chat/session list of a web app: list, search, pin, sections (groups)")
    p.add_argument("target", help="claude (chats) or claude-code-web (Code sessions)")
    p.add_argument("--json", action="store_true")
    ops = p.add_subparsers(dest="op", required=True)
    alias = {v: k for k, v in SECTION_ALIASES.items()}  # "groups" -> also accepted as "sections"
    o = ops.add_parser("list", help="sidebar sections (Pinned, your sections, Recents) and their items")
    o.add_argument("--group", "--section", dest="group", help="only this section")
    o = ops.add_parser("groups", aliases=[alias["groups"]], help="list the sections (groups)")
    o.set_defaults(group=None)
    o = ops.add_parser("search", help="search chats (and artifacts, sessions, projects)")
    o.add_argument("query")
    o.add_argument("--kind", help="search filter tab, e.g. Code, Projects, Artifacts")
    for name in ("pin", "unpin"):
        ops.add_parser(name).add_argument("chat", help="id, URL or exact title")
    ops.add_parser("ungroup", aliases=[alias["ungroup"]], help="take it out of its section").add_argument("chat")
    o = ops.add_parser("move", help="move a chat or session into a section")
    o.add_argument("chat")
    o.add_argument("group", metavar="section")
    o.add_argument("--create", action="store_true", help="create the section if it does not exist")
    o = ops.add_parser("new-group", aliases=[alias["new-group"]], help="create an empty section, or one holding --chat")
    o.add_argument("name")
    o.add_argument("--chat")
    o = ops.add_parser("delete-group", aliases=[alias["delete-group"]], help="delete a section; its items return to Recents")
    o.add_argument("name")
    o.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_chats)

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
