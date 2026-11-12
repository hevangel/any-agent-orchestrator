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
    failed = 0
    for target in targets:
        if args.action == "login":
            result = web.login(target)
        elif args.action == "status":
            result = web.status(target)
        else:
            prompt, expect = drivers.check_prompt()
            result = web.ask(target, args.prompt or prompt, args.expect or expect)
        good = result.get("ok", result.get("loggedIn"))
        failed += not good
        detail = result.get("reason") or result.get("error") or result.get("url") or ""
        print(f"  {target:16} {'OK ' if good else 'NO '} {detail}")
    return 1 if failed else 0


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
    p.add_argument("action", choices=["login", "status", "ask"])
    p.add_argument("target", nargs="?", help="chatgpt, claude, gemini, grok, muse, zai, codex-cloud, claude-code-web")
    p.add_argument("--all", action="store_true")
    p.add_argument("--prompt")
    p.add_argument("--expect", help="regex the reply must match")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("accounts", help="list configured accounts, validate .env, pick the next account")
    p.add_argument("--agent", help="show which account would be used next for this agent")
    p.add_argument("--host", default="local")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("free-tokens", help="free token list tools")
    p.add_argument("action", choices=["validate"])
    p.set_defaults(func=cmd_free_tokens)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
