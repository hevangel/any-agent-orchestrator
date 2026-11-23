"""`aao` command line."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from orchestrator import free_tokens, pricing
from orchestrator.collect import COLLECTORS
from orchestrator.media_references import IMAGE_FILES, VIDEO_REFERENCE_LIMITS, validate_image_reference
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


FLOW_MEDIA_OPTIONS = ("project", "model", "resolution", "quality", "duration", "count",
                      "characters", "start_frame", "end_frame", "aspect")
FLOW_MUSIC_OPTIONS = ("lyrics", "style", "length", "instrumental", "song")


def _web_reference_options(kind, target, paths):
    limit = VIDEO_REFERENCE_LIMITS[target]
    if len(paths) > limit:
        count = "one image reference" if limit == 1 else f"up to {limit} image references"
        raise ValueError(f"web {kind} {target} accepts {count}")
    references = []
    for name in paths:
        path = Path(name).expanduser().absolute()
        if not path.is_file():
            raise ValueError(f"Reference image file not found: {path}")
        try:
            with path.open("rb") as image:
                validate_image_reference(IMAGE_FILES.get(path.suffix.lower()), image.read(12), path.stat().st_size)
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        references.append(path)
    return {"image": references[0]} if target == "grok" else {"references": references}


def _web_media(args):
    from orchestrator import (
        web_chatgpt_image, web_flow, web_flow_music, web_gemini_image, web_gemini_video, web_grok, web_muse, web_muse_video,
    )

    target, kind = args.target, args.action
    audio_drivers = {"flow-music": web_flow_music.generate, "grok": web_grok.generate_audio,
                     "muse": web_muse.generate_audio}
    allowed = {"image": ("chatgpt", "flow", "gemini", "grok", "muse"), "video": VIDEO_REFERENCE_LIMITS,
               "audio": audio_drivers}
    try:
        if args.all or target not in allowed[kind]:
            raise ValueError(f"web {kind} needs one target: {', '.join(allowed[kind])}")
        flow_options = {name: getattr(args, name) for name in FLOW_MEDIA_OPTIONS if getattr(args, name) is not None}
        if "aspect" in flow_options:
            flow_options["aspect_ratio"] = flow_options.pop("aspect")
        if flow_options and target != "flow":
            raise ValueError("These generation settings apply to web image or video flow")
        music_options = {name: getattr(args, name) for name in FLOW_MUSIC_OPTIONS if getattr(args, name) is not None}
        if music_options and target != "flow-music":
            raise ValueError("These song settings apply to web audio flow-music")
        if any((args.chat, args.file, args.repo, args.mode, args.expect)):
            raise ValueError("Chat options apply to web ask. Use --reference for supported media targets")
        prompt = (args.prompt or "").strip()
        if not prompt:
            raise ValueError(f"web {kind} requires --prompt")
        timeout = 720 if args.timeout is None else args.timeout
        if timeout <= 0:
            raise ValueError("--timeout must be positive")
        reference_options = {}
        if args.reference:
            if kind != "video" and (kind, target) != ("image", "flow"):
                raise ValueError("--reference applies to web video and web image flow")
            reference_options = _web_reference_options(kind, target, args.reference)
        directory = Path(args.out).expanduser().resolve() / f"{target}-{kind}-{uuid4().hex[:12]}"
        directory.mkdir(parents=True, exist_ok=False)
        if kind == "audio":
            result = audio_drivers[target](prompt, directory, timeout_s=timeout, **music_options)
        elif target == "flow":
            result = web_flow.generate(kind, prompt, directory, timeout_s=timeout, **reference_options, **flow_options)
        elif target == "grok":
            driver = web_grok.generate_image if kind == "image" else web_grok.generate_video
            result = driver(prompt, download_dir=directory, timeout_s=timeout, **reference_options)
        elif target == "muse" and kind == "image":
            result = web_muse.generate_image(prompt, directory, timeout_s=timeout)
        else:
            if target == "gemini":
                driver = web_gemini_image if kind == "image" else web_gemini_video
            elif target == "chatgpt":
                driver = web_chatgpt_image
            else:
                driver = web_muse_video
            result = driver.generate(prompt, directory, timeout_s=timeout, **reference_options)
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}

    files = result.get("files") or ([result["path"]] if result.get("path") else [])
    result = {**result, "target": target, "kind": kind, "files": files}
    if not _print(result, args.json):
        for path in files:
            print(path)
        if not result.get("ok"):
            print(result.get("reason") or result.get("error") or "Web generation failed", file=sys.stderr)
            url = result.get("url") or result.get("conversation_url")
            if url:
                print(url, file=sys.stderr)
    return 0 if result.get("ok") else 1


def cmd_web(args):
    from orchestrator import drivers, web

    if args.action in ("image", "video", "audio"):
        return _web_media(args)
    if args.reference:
        print("--reference applies to web video and web image flow", file=sys.stderr)
        return 1
    if any(getattr(args, name) is not None for name in FLOW_MEDIA_OPTIONS + FLOW_MUSIC_OPTIONS):
        print("Flow generation settings apply to web image, video or audio", file=sys.stderr)
        return 1
    if args.action in ("sessions", "read"):
        from orchestrator import web_autoclaw

        if args.target != "autoclaw" or args.all:
            print(f"Use aao web {args.action} autoclaw", file=sys.stderr)
            return 1
        try:
            result = web_autoclaw.control(args.action, chat=args.chat)
        except ValueError as exc:
            result = {"ok": False, "reason": str(exc)}
        if args.json:
            _print(result, True)
        elif not result.get("ok"):
            print(result.get("reason") or result.get("error"), file=sys.stderr)
        elif args.action == "sessions":
            if not result["sessions"]:
                print("No AutoClaw sessions")
            for session in result["sessions"]:
                workspace = f" ({session['workspace']})" if session["workspace"] else ""
                print(f"  {session['status']:12} {session['title']}{workspace}")
        else:
            print(f"{result['session']} [{result['status']}]")
            for message in result["messages"]:
                print(f"\n{message['role']}: {message['text']}")
        return 0 if result.get("ok") else 1
    targets = list(web.TARGETS) if args.all or not args.target else [args.target]
    if any(target not in web.TARGETS for target in targets):
        print(f"Unknown web target: {args.target}", file=sys.stderr)
        return 1
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
        elif target == "autoclaw" and (args.target != target or args.all):
            continue  # AutoClaw requires explicit selection of an existing task.
        elif target == "autoclaw" and args.prompt is None:
            result = {"ok": False, "reason": "AutoClaw requires an explicit --prompt and --chat"}
        else:
            prompt, expect = drivers.check_prompt() if args.prompt is None else (args.prompt, None)
            try:
                result = web.ask(target, prompt, args.expect or expect, chat=args.chat,
                                 files=args.file, repo=args.repo, mode=args.mode,
                                 timeout_s=120 if args.timeout is None else args.timeout)
            except (ValueError, FileNotFoundError) as exc:
                result = {"ok": False, "error": str(exc)}
        good = result.get("ok", result.get("loggedIn"))
        failed += not good
        if args.json and args.target == target == "autoclaw" and not args.all and args.action != "login":
            _print(result, True)
            continue
        detail = result.get("reason") or result.get("error") or result.get("url") or ""
        print(f"  {target:16} {'OK ' if good else 'NO '} {detail}")
        if result.get("taskStatus"):
            print(f"  {'':16} task: {result['taskStatus']}")
        if result.get("thinking"):
            print(result["thinking"], file=sys.stderr)
        if result.get("reply"):
            reply = result["reply"] if target in {"grok", "autoclaw"} else result["reply"][:500]
            print(f"  {'':16} reply: {reply}")
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


def cmd_grok(args):
    from orchestrator import web, web_grok

    try:
        if args.op == "chat":
            result = web_grok.chat(args.prompt, chat_ref=args.chat, expect=args.expect, timeout_s=args.timeout)
        elif args.op == "image":
            result = web_grok.generate_image(args.prompt, quality=args.quality, aspect_ratio=args.aspect,
                                             count=args.count, download_dir=args.out, timeout_s=args.timeout)
        elif args.op == "video":
            result = web_grok.generate_video(args.prompt, resolution=args.res, duration=args.duration,
                                             aspect_ratio=args.aspect, image=args.image,
                                             download_dir=args.out, timeout_s=args.timeout)
        elif args.op == "attach":
            result = web_grok.attach(args.config)
        elif args.op == "install-browser":
            ok, out, error = web._run(["playwright-cli", "install-browser", "chromium"], timeout=600)
            result = {"ok": ok, "output": out, "error": error}
        else:
            from orchestrator import web_grok_api

            if args.op == "mcp":
                web_grok_api.mcp()
            else:
                web_grok_api.serve(args.port)
            return 0
    except ModuleNotFoundError as exc:
        if exc.name not in {"mcp", "fastapi", "pydantic", "uvicorn"}:
            raise
        result = {"ok": False, "error": "install the Grok server dependencies with uv sync --extra grok-api"}
    except (OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}

    if not _print(result, args.json):
        if result.get("thinking"):
            print(result["thinking"], file=sys.stderr)
        if result.get("response"):
            print(result["response"])
        for path in result.get("files", []):
            print(path)
        if result.get("output"):
            print(result["output"].rstrip())
        if not result.get("ok"):
            print(result.get("reason") or result.get("error") or "Grok operation failed", file=sys.stderr)
            for error in result.get("errors", []):
                print(error, file=sys.stderr)
        elif args.op == "attach":
            print("Attached the Grok session to the configured CDP browser")
    return 0 if result.get("ok") else 1


def _grok_parser(sub):
    from typing import get_args

    from orchestrator import web_grok

    p = sub.add_parser("grok", help="Grok chat, Imagine generation, and local integrations")
    p.add_argument("--json", action="store_true", help="return structured results for one operation")
    p.set_defaults(func=cmd_grok)
    ops = p.add_subparsers(dest="op", required=True)
    chat = ops.add_parser("chat", help="send a text prompt and print the full reply")
    chat.add_argument("prompt")
    chat.add_argument("--chat", help="continue an existing chat by ID, URL, or title")
    chat.add_argument("--expect", help="require a regex match in the reply")
    chat.add_argument("--timeout", type=float, default=120)
    image = ops.add_parser("image", help="generate and download Grok Imagine images")
    image.add_argument("--quality", choices=get_args(web_grok.Quality))
    image.add_argument("--count", choices=get_args(web_grok.Count))
    video = ops.add_parser("video", help="generate and download a Grok Imagine video")
    video.add_argument("--res", choices=get_args(web_grok.Resolution), default="480p")
    video.add_argument("--duration", choices=get_args(web_grok.Duration), default="6s")
    video.add_argument("--image", help="local reference image to animate")
    for media, timeout in ((image, 300), (video, 600)):
        media.add_argument("prompt")
        media.add_argument("--aspect", choices=get_args(web_grok.Aspect))
        media.add_argument("-o", "--out", default=web_grok.DEFAULT_OUT, help="download directory (default: downloads)")
        media.add_argument("--timeout", type=float, default=timeout, help="seconds to wait for generation")
    attach = ops.add_parser("attach", help="use an existing browser or Browserless endpoint over CDP")
    attach.add_argument("--config", required=True, help="Playwright config file containing browser.cdpEndpoint")
    ops.add_parser("install-browser", help="install Playwright Chromium for optional local use")
    serve = ops.add_parser("serve", help="serve Grok operations over loopback HTTP (grok-api extra)")
    serve.add_argument("--port", type=int, default=8000)
    ops.add_parser("mcp", help="serve Grok operations over stdio MCP (grok-api extra)")


def cmd_flow(args):
    from orchestrator import web_flow

    kw = {"timeout_s": args.timeout}
    if args.op == "download":
        result = web_flow.download(args.project, args.media, to=args.to, quality=args.quality, **kw)
    elif args.op == "upload":
        result = web_flow.upload(args.project, args.files, timeout_s=args.timeout)
    elif args.op == "new-character":
        result = web_flow.create_character(args.project, args.name, prompt=args.prompt, image=args.image,
                                           info=args.info, voice=args.voice, **kw)
    elif args.op in ("read-character", "edit-character"):
        kw.update(project=args.project, character=args.character)
        if args.op == "edit-character":
            kw.update(name=args.name, info=args.info, voice=args.voice, imagePrompt=args.prompt)
            if all(value is None for value in (args.name, args.info, args.voice, args.prompt)):
                print("Provide --name, --info, --voice or --prompt to edit the character", file=sys.stderr)
                return 1
            if any(value is not None and not value.strip() for value in (args.name, args.voice, args.prompt)):
                print("--name, --voice and --prompt require non-empty values", file=sys.stderr)
                return 1
        result = web_flow.session(args.op, **kw)
    elif args.op == "add-to-scene":
        result = web_flow.session(args.op, project=args.project, media=args.media, scene=args.scene, **kw)
    elif args.op == "save-frame":
        if args.clip < 1 or not 0 <= args.at < 100:
            print("--clip must be positive and --at must be between 0 and 100 percent", file=sys.stderr)
            return 1
        result = web_flow.session(args.op, project=args.project, media=args.media, clip=args.clip, at=args.at, **kw)
    elif args.op == "extend":
        if not args.prompt.strip():
            print("--prompt must describe what happens next", file=sys.stderr)
            return 1
        result = web_flow.session(args.op, project=args.project, media=args.media, prompt=args.prompt, **kw)
    elif args.op == "delete":
        if not args.yes:
            print("  NO deleting a project is permanent (its clips, ingredients and prompts); add --yes")
            return 1
        result = web_flow.session("delete", project=args.project, **kw)
    else:
        if args.op in ("read", "options", "characters"):
            kw["project"] = args.project
        if args.op == "read":
            kw["mediaType"] = args.type
        if args.op == "new":
            kw["title"] = args.title
        result = web_flow.session(args.op, **kw)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        print(f"  NO {result.get('reason') or result.get('error')}")
        for c in result.get("choices") or []:
            print(f"      {c}")
        if args.op in ("new-character", "edit-character", "extend", "save-frame") and result.get("url"):
            print(f"      Flow page: {result['url']}")
    elif args.op == "add-to-scene":
        print(f"  added video to {result['scene']}")
    elif args.op in ("extend", "save-frame"):
        print(f"  {result['url']}")
    for p in result.get("projects") or []:
        print(f"  {p['title'][:40]:40} {p['id']}")
    if result.get("title") is not None and result.get("url"):
        print(f"  project {result['title']}  {result['url']}  ({len(result.get('media') or [])} media)")
        for m in result.get("media") or []:
            print(f"    {m['index']:>3}  {m['type'] or '?':6} {m['name'][:60]}")
    if result.get("path"):
        print(f"  saved {result['path']}  ({result['quality']})")
    if result.get("uploaded"):
        print(f"  uploaded {result['uploaded']} file(s); newest media are listed first")
    if result.get("character"):
        character = result["character"]
        print(f"  character {character['name']}  {character['url']}")
        if args.op == "read-character":
            print(f"  voice {character['voice'] or '(none)'}")
            print(f"  info {character['info'] or '(empty)'}")
            print(f"  image {character['image'] or '(none)'}")
    for character in result.get("characters") or []:
        print(f"  {character['name']}")
    if result.get("deleted"):
        print(f"  deleted project {result['deleted']}")
    if args.op == "options" and result.get("ok"):
        for mode, options in result["modes"].items():
            print(f"  {mode.capitalize()}")
            for group in options["toggles"]:
                print("    " + "  ".join(f"[{t['label']}]" if t["selected"] else t["label"] for t in group))
            for model in options["dropdowns"]:
                print(f"    model {model['current']} <- {' | '.join(model['choices'])}")
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


def cmd_projects(args):
    from orchestrator import projects

    if args.op == "validate":
        errors = projects.validate_all()
        print("\n".join(errors) or "projects/*.json OK")
        return 1 if errors else 0
    if args.op == "list":
        for doc in projects.load_all():
            gp = doc["github_project"]
            repos = projects.controlled_repos(doc)
            print(f"  {doc['id']:28} {gp.get('url') or '(no GitHub Project yet)':45} {len(repos)} repo(s): {', '.join(repos)}")
        return 0
    doc = projects.load(args.project)
    if args.op == "sync":
        warnings = projects.sync(doc)
        repos = projects.controlled_repos(doc)
        for path, node in projects.iter_repos(doc["repo"]):
            ext = len(node.get("external") or [])
            print(f"  {path or '.':40} {node.get('name') or node.get('url')}{f'  ({ext} external)' if ext else ''}")
        for w in warnings:
            print("  ! " + w)
        if args.dry_run:
            print(f"  {len(repos)} owned repo(s); not written (--dry-run)")
        else:
            projects.save(doc)
            print(f"  {len(repos)} owned repo(s); wrote {projects.config_path(doc['id']).relative_to(projects.ROOT)}")
        return 0
    if args.op == "setup":
        projects.setup(doc, dry_run=args.dry_run)
        if not args.dry_run:
            projects.save(doc)
        return 0
    if args.op == "status":
        counts = projects.project_status(doc)
        if counts is None:
            print(f"  {doc['id']} has no GitHub Project yet: aao projects setup {doc['id']}")
            return 1
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {name:16} {n}")
        return 0
    return 1


def cmd_media_serve(_args):
    try:
        from orchestrator.media import serve
    except ModuleNotFoundError as exc:
        print(f"AAO media needs the optional HTTP dependencies: uv sync --extra grok-api ({exc})", file=sys.stderr)
        return 1
    return serve()


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

    p = sub.add_parser("web", help="Chat and media generation through playwright-cli with saved login profiles")
    p.add_argument("action", choices=["login", "status", "ask", "close", "image", "video", "audio", "sessions", "read"],
                   help="sessions/read: list AutoClaw tasks or read one; close: close kept-open browsers")
    p.add_argument("target", nargs="?", help="chatgpt, claude, gemini, grok, muse, flow, flow-music, zai, autoclaw, codex-cloud, claude-code-web")
    p.add_argument("--all", action="store_true")
    p.add_argument("--prompt")
    p.add_argument("--expect", help="regex the reply must match")
    p.add_argument("--chat", help="ask: continue a chat (id, URL or title); AutoClaw ask/read require an exact title")
    p.add_argument("--file", action="append", help="ask: attach a local file (repeatable; gemini; zip a folder)")
    p.add_argument("--reference", "--image", action="append", metavar="IMAGE",
                   help="video and Flow image: attach a PNG, JPEG, or WebP (8 MiB each)")
    p.add_argument("--project", help="Flow: use this project ID, URL or exact title")
    p.add_argument("--model", help="Flow: model name from aao flow options")
    p.add_argument("--aspect", help="Flow: image or video aspect ratio")
    p.add_argument("--resolution", help="Flow: video resolution, e.g. 720p")
    p.add_argument("--quality", help="Flow: image quality, or an alias for video resolution")
    p.add_argument("--duration", type=int, help="Flow: video duration in seconds, where available")
    p.add_argument("--count", type=int, help="Flow: number of results, 1 to 4")
    p.add_argument("--character", dest="characters", action="append", help="Flow: attach a saved character by name (repeatable)")
    p.add_argument("--start-frame", help="Flow: project image name or media index for a video's first frame")
    p.add_argument("--end-frame", help="Flow: project image name or media index for a video's last frame")
    p.add_argument("--lyrics", help="Flow Music: lyrics to include in the song brief")
    p.add_argument("--style", help="Flow Music: style to include in the song brief")
    p.add_argument("--length", type=int, help="Flow Music: target length in seconds")
    p.add_argument("--instrumental", action="store_true", default=None, help="Flow Music: request a song without vocals")
    p.add_argument("--song", type=int, help="Flow Music: request versions and download this 1-based result")
    p.add_argument("--repo", help="ask: attach a GitHub repository or branch URL (gemini 'Import code')")
    p.add_argument("--mode", help="ask: composer mode, e.g. gemini chat|task")
    p.add_argument("--timeout", type=int, help="seconds to wait (ask: 120, image/video/audio: 720)")
    p.add_argument("-o", "--out", default="downloads", help="image/video/audio: parent directory for saved media")
    p.add_argument("--json", action="store_true", help="media or AutoClaw status/sessions/read/ask: print structured results")
    p.set_defaults(func=cmd_web)

    _grok_parser(sub)

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

    p = sub.add_parser("flow", help="Google Flow projects, characters, settings and downloads")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=int, default=180, help="seconds to wait for a Flow result")
    ops = p.add_subparsers(dest="op", required=True)
    ops.add_parser("status", help="is the Google login still good for Flow")
    ops.add_parser("projects", help="list projects")
    o = ops.add_parser("new", help="create an empty project (free)")
    o.add_argument("--title")
    o = ops.add_parser("new-character", help="create a reusable character in an existing Flow project")
    o.add_argument("project", help="id, URL or exact title")
    o.add_argument("--name", required=True, help="character name")
    source = o.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt", help="generate a portrait from this description (may spend Flow credits)")
    source.add_argument("--image", help="upload a local portrait image")
    o.add_argument("--info", help="character personality or background")
    o.add_argument("--voice", help="Flow preset voice name")
    o = ops.add_parser("characters", help="list a project's saved characters")
    o.add_argument("project")
    o = ops.add_parser("read-character", help="read a character's name, image, info and voice")
    o.add_argument("project")
    o.add_argument("character", help="character ID, editor URL or exact name")
    o = ops.add_parser("edit-character", help="update a saved character")
    o.add_argument("project")
    o.add_argument("character", help="character ID, editor URL or exact name")
    o.add_argument("--name")
    o.add_argument("--info")
    o.add_argument("--voice")
    o.add_argument("--prompt", help="regenerate the portrait from this edit prompt (may spend credits)")
    o = ops.add_parser("add-to-scene", help="append a project video to a named scene")
    o.add_argument("project")
    o.add_argument("media", help="video index or exact title")
    o.add_argument("scene", help="exact name, or 'New scene'")
    o = ops.add_parser("save-frame", help="save a still from a video or scene to the project")
    o.add_argument("project")
    o.add_argument("media", help="video or scene index or exact title")
    o.add_argument("--clip", type=int, default=1, help="1-based clip within the scene")
    o.add_argument("--at", type=float, default=0, help="position as a percentage through the clip, from 0 to under 100")
    o = ops.add_parser("extend", help="extend a video or scene in Scenebuilder (spends credits)")
    o.add_argument("project")
    o.add_argument("media", help="video or scene index or exact title")
    o.add_argument("--prompt", required=True, help="describe what happens next")
    o = ops.add_parser("read", help="media in a project, newest first")
    o.add_argument("project", help="id, URL or exact title")
    o.add_argument("--type", choices=["video", "image", "scene", "character", "collection"], help="only this media type")
    o = ops.add_parser("download", help="save one media (original size is free; --quality 1080p/4k spend credits)")
    o.add_argument("project")
    o.add_argument("media", help="index from `read` or exact media name")
    o.add_argument("--to", default="temp/flow", help="folder to save into")
    o.add_argument("--quality", default="original", choices=["original", "gif", "1080p", "4k"])
    o = ops.add_parser("upload", help="upload reference images/videos into a project (free)")
    o.add_argument("project")
    o.add_argument("files", nargs="+", help="local image or video files")
    o = ops.add_parser("options", help="read live Image and Video settings and model choices")
    o.add_argument("--project", help="project to open (default: the first one)")
    o = ops.add_parser("delete", help="delete a project permanently")
    o.add_argument("project")
    o.add_argument("--yes", action="store_true", help="required: the clips, ingredients and prompts are gone for good")
    p.set_defaults(func=cmd_flow)

    p = sub.add_parser(
        "media-serve", help="serve AAO media jobs on 127.0.0.1:8771",
        description="Serve ArtCraft jobs. Set AAO_CRAFT_AI_TOKEN to enable the Craft AI media bridge.",
    )
    p.set_defaults(func=cmd_media_serve)

    p = sub.add_parser("accounts", help="list configured accounts, validate .env, pick the next account")
    p.add_argument("--agent", help="show which account would be used next for this agent")
    p.add_argument("--host", default="local")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("projects", help="managed projects (projects/*.json): list, validate, sync repo tree, set up the GitHub Project")
    ops = p.add_subparsers(dest="op", required=True)
    ops.add_parser("list", help="configured projects, their GitHub Project and owned repos")
    ops.add_parser("validate", help="check every projects/*.json")
    o = ops.add_parser("sync", help="rebuild a config's repo tree from the live .gitmodules on GitHub")
    o.add_argument("project", help="project id (file name without .json)")
    o.add_argument("--dry-run", action="store_true")
    o = ops.add_parser("setup", help="create or complete the GitHub Project: fields, labels, linked repos, every issue added")
    o.add_argument("project")
    o.add_argument("--dry-run", action="store_true", help="print what would change")
    ops.add_parser("status", help="live item counts by Status").add_argument("project")
    p.set_defaults(func=cmd_projects)

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
