"""Projects this orchestrator manages: one JSON config per project in `projects/`.

A project is always a GitHub Project (v2) hosted by a parent repository. The parent repo
has its own code and may pin owned repositories as git submodules, which may pin their own,
to any depth. Repositories the project does not own (references) are pinned under an
`external/` folder at any level and are never worked on.

`sync` rebuilds a config's repository tree from the live `.gitmodules` files on GitHub;
`setup` creates or completes the GitHub Project from spec/github_project.md (fields, labels,
linked repositories, every issue added with an initial Status). Both are deterministic `gh`
calls, never an LLM.
"""

import json
import re
import shutil
import subprocess
from datetime import date, timedelta

from orchestrator.state import ROOT, read_json

PROJECTS_DIR = ROOT / "projects"
SCHEMA = "aao-project-v1"
ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$")

# spec/github_project.md
STATUS_OPTIONS = [
    ("Planned", "GRAY", "Not started; the ready queue dispatches from here"),
    ("Research", "BLUE", "Reading sources, writing or checking the spec"),
    ("Spec-reviewed", "PURPLE", "Spec reviewed; ready to build"),
    ("Building", "YELLOW", "Implementation in progress"),
    ("AI-reviewed", "GREEN", "Built and reviewed by an AI agent; last stage before release"),
    ("Human-reviewed", "ORANGE", "A human used it in a released version (set by a human only)"),
]
DEFAULT_STATUS = {"Todo", "In Progress", "Done"}          # what GitHub creates with a new project
AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor", "copilot": "Copilot", "kiro": "Kiro",
               "antigravity": "Antigravity", "zcode": "ZCode", "opencode": "OpenCode", "kilo": "Kilo",
               "muse": "Muse", "pi": "Pi", "hermes": "Hermes"}
WEB_AGENTS = ["Gemini", "Grok", "ChatGPT"]
NOT_AGENTS = {"herdr"}
LABELS = [  # name, color, description
    ("wip", "fbca04", "An agent is working on this issue right now"),
    ("needs-sync", "c5def5", "The latest aao: comment has field changes not yet applied to the project"),
    ("needs-human", "d93f0b", "Cannot progress without a person"),
    ("bug", "d73a4a", "[Bug]"), ("feature", "a2eeef", "[Feature]"), ("spec", "5319e7", "[Spec]"),
    ("docs", "0075ca", "[Docs]"), ("chore", "cfd3d7", "[Chore]"),
    ("needs:browser", "bfdadc", "Needs a browser channel"), ("needs:desktop", "bfdadc", "Needs a desktop app"),
    ("needs:gpu", "bfdadc", "Needs a GPU machine"), ("needs:secrets", "bfdadc", "Needs credentials"),
    ("os:windows", "bfdadc", "Needs Windows"), ("os:linux", "bfdadc", "Needs Linux"),
    ("local-only", "bfdadc", "Not for cloud agents"),
    ("size:s", "e6e6e6", "Small"), ("size:m", "c2c2c2", "Medium"), ("size:l", "8f8f8f", "Large"),
    ("spends-tokens", "f9d0c4", "Runs paid e2e tests"), ("touches-credentials", "f9d0c4", "Handles credentials"),
]
ITERATION_DAYS = 7
ITERATIONS_AHEAD = 4
COLORS = ["BLUE", "GREEN", "YELLOW", "ORANGE", "RED", "PINK", "PURPLE", "GRAY"]


# ------------------------------------------------------------------ configs

def config_path(project_id):
    return PROJECTS_DIR / f"{project_id}.json"


def load_all():
    docs = []
    for path in sorted(PROJECTS_DIR.glob("*.json")):
        doc = read_json(path)
        if isinstance(doc, dict):
            docs.append(doc)
    return docs


def load(project_id):
    doc = read_json(config_path(project_id))
    if not isinstance(doc, dict):
        raise SystemExit(f"no project config {config_path(project_id)}")
    return doc


def save(doc):
    """Committed configs: two-space indent, LF endings."""
    PROJECTS_DIR.mkdir(exist_ok=True)
    config_path(doc["id"]).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def iter_repos(node):
    """Yield (path, node) for the parent repo and every owned submodule, depth first."""
    def walk(n, prefix):
        yield prefix, n
        for sub in n.get("submodules") or []:
            yield from walk(sub, f"{prefix}/{sub['path']}" if prefix else sub["path"])
    yield from walk(node, "")


def repository_owners(doc):
    """Accounts whose non-external repositories are managed by this project."""
    return {doc["github_project"]["owner"].lower(),
            *(login.lower() for login in doc.get("collaborators", []))}


def controlled_repos(doc):
    """Repositories owned by the project owner or its declared collaborators.

    A non-external submodule owned by someone else breaks the external/ rule (see tree_warnings);
    the project never links it or writes labels to it.
    """
    owners = repository_owners(doc)
    seen = []
    for _, n in iter_repos(doc["repo"]):
        if n.get("name") and n["name"] not in seen and n["name"].split("/")[0].lower() in owners:
            seen.append(n["name"])
    return seen


def validate(doc, filename=None):
    errors = []
    where = filename or doc.get("id") or "?"
    if doc.get("schema") != SCHEMA:
        errors.append(f"{where}: schema must be {SCHEMA}")
    if not ID.match(str(doc.get("id") or "")):
        errors.append(f"{where}: id must match {ID.pattern}")
    if filename and doc.get("id") and filename != f"{doc['id']}.json":
        errors.append(f"{where}: file name must be {doc['id']}.json")
    if not doc.get("title"):
        errors.append(f"{where}: missing title")
    collaborators = doc.get("collaborators", [])
    if not isinstance(collaborators, list) or any(
            not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", login)
            for login in collaborators):
        errors.append(f"{where}: collaborators must be a list of GitHub usernames")
    gp = doc.get("github_project")
    if not isinstance(gp, dict) or not gp.get("owner"):
        errors.append(f"{where}: github_project.owner is required")
    elif gp.get("number") is not None:
        if not isinstance(gp["number"], int) or not str(gp.get("url", "")).startswith("https://github.com/"):
            errors.append(f"{where}: github_project needs an integer number and a github.com url")
    repo = doc.get("repo")
    if not isinstance(repo, dict) or not REPO.match(str(repo.get("name") or "")):
        errors.append(f"{where}: repo.name must be owner/name")
        return errors

    def check(node, label):
        for sub in node.get("submodules") or []:
            p = sub.get("path") or ""
            if not p or p.startswith("/") or "\\" in p:
                errors.append(f"{where}: {label}: bad submodule path {p!r}")
            if is_external(p):
                errors.append(f"{where}: {label}/{p}: external/ references belong in 'external', not 'submodules'")
            if not sub.get("url"):
                errors.append(f"{where}: {label}/{p}: missing url")
            check(sub, f"{label}/{p}")
        for ext in node.get("external") or []:
            if not is_external(ext.get("path") or ""):
                errors.append(f"{where}: {label}: reference {ext.get('path')!r} is not under external/")
    check(repo, repo["name"])
    return errors


def validate_all():
    errors = []
    ids = set()
    for path in sorted(PROJECTS_DIR.glob("*.json")):
        doc = read_json(path)
        if not isinstance(doc, dict):
            errors.append(f"{path.name}: not a JSON object")
            continue
        errors += validate(doc, path.name)
        if doc.get("id") in ids:
            errors.append(f"{path.name}: duplicate id")
        ids.add(doc.get("id"))
    return errors


# ------------------------------------------------------------------ gh

class GhError(RuntimeError):
    pass


def gh(*args, stdin=None, parse=True, timeout=120):
    exe = shutil.which("gh")
    if exe is None:
        raise GhError("gh is not installed")
    done = subprocess.run([exe, *args], input=stdin, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=None if stdin is not None else subprocess.DEVNULL)
    if done.returncode != 0:
        raise GhError(f"gh {' '.join(args[:3])}: {(done.stderr or done.stdout).strip()[-400:]}")
    if not parse:
        return done.stdout
    return json.loads(done.stdout) if done.stdout.strip() else None


def graphql(query, **variables):
    out = gh("api", "graphql", "--input", "-", stdin=json.dumps({"query": query, "variables": variables}))
    if out.get("errors"):
        raise GhError("; ".join(e.get("message", "") for e in out["errors"]))
    return out["data"]


# ------------------------------------------------------------------ repo tree

def parse_gitmodules(text):
    """[{path, url}] from a .gitmodules file."""
    mods, cur = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("[submodule"):
            cur = {}
            mods.append(cur)
        elif cur is not None and "=" in line:
            key, _, value = line.partition("=")
            cur[key.strip()] = value.strip()
    return [{"path": m["path"].strip("/"), "url": m.get("url", "")} for m in mods if m.get("path")]


def github_name(url):
    m = re.match(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else None


def is_external(path):
    return path == "external" or path.startswith("external/") or "/external/" in f"/{path}"


def fetch_gitmodules(repo):
    try:
        return gh("api", f"repos/{repo}/contents/.gitmodules", "-H", "Accept: application/vnd.github.raw", parse=False)
    except GhError as exc:
        if "404" in str(exc) or "Not Found" in str(exc):
            return ""
        raise


def discover(repo, fetch=fetch_gitmodules, _seen=None):
    """Repository tree for owner/name: owned submodules recursively, external/ references listed only."""
    seen = _seen if _seen is not None else set()
    seen.add(repo.lower())
    node = {"name": repo, "url": f"https://github.com/{repo}", "submodules": [], "external": []}
    for mod in parse_gitmodules(fetch(repo)):
        name = github_name(mod["url"])
        entry = {"path": mod["path"], "url": mod["url"].removesuffix(".git") if name else mod["url"]}
        if name:
            entry = {"path": mod["path"], "name": name, "url": f"https://github.com/{name}"}
        if is_external(mod["path"]):
            node["external"].append(entry)
            continue
        if name and name.lower() not in seen:
            child = discover(name, fetch, seen)
            child.pop("name", None)
            child.pop("url", None)
            entry.update(child)
        node["submodules"].append(entry)
    if not node["external"]:
        node.pop("external")
    if not node["submodules"]:
        node.pop("submodules")
    return node


def tree_warnings(doc):
    """Owned-path submodules whose repo belongs to someone else break the external/ rule."""
    owner = doc["github_project"]["owner"].lower()
    owners = repository_owners(doc)
    out = []
    for path, n in iter_repos(doc["repo"]):
        if path and n.get("name") and n["name"].split("/")[0].lower() not in owners:
            owner_label = f"{owner} or its collaborators" if doc.get("collaborators") else owner
            out.append(f"{path}: {n['name']} is not owned by {owner_label} but is not under external/")
    return out


def sync(doc, fetch=fetch_gitmodules):
    """Rebuild doc['repo'] from the live .gitmodules files; return the warnings."""
    doc["repo"] = discover(doc["repo"]["name"], fetch)
    return tree_warnings(doc)


# ------------------------------------------------------------------ GitHub Project setup

def agent_options():
    agents = read_json(ROOT / "agents.json", {}).get("agents", [])
    names = [AGENT_NAMES.get(a["id"], a["id"].capitalize()) for a in agents if a["id"] not in NOT_AGENTS]
    return names + [w for w in WEB_AGENTS if w not in names]


def _project(owner, number):
    data = graphql("""query($owner:String!,$number:Int!){ user(login:$owner){ projectV2(number:$number){
      id number url title repositories(first:100){ nodes{ nameWithOwner } }
      fields(first:50){ nodes{ __typename
        ... on ProjectV2Field { id name dataType }
        ... on ProjectV2SingleSelectField { id name dataType options { id name color description } }
        ... on ProjectV2IterationField { id name dataType } } } } } }""", owner=owner, number=number)
    return data["user"]["projectV2"]


def _items(owner, number):
    """{issue url: {id, status}} for every item in the project."""
    out, after = {}, None
    while True:
        data = graphql("""query($owner:String!,$number:Int!,$after:String){ user(login:$owner){ projectV2(number:$number){
          items(first:100, after:$after){ pageInfo{ hasNextPage endCursor } nodes{ id
            content{ ... on Issue { url } ... on PullRequest { url } }
            status: fieldValueByName(name:"Status"){ ... on ProjectV2ItemFieldSingleSelectValue { name } }
            workstream: fieldValueByName(name:"Workstream"){ ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }""",
                       owner=owner, number=number, after=after)
        page = data["user"]["projectV2"]["items"]
        for n in page["nodes"]:
            if n.get("content") and n["content"].get("url"):
                out[n["content"]["url"]] = {"id": n["id"], "status": (n.get("status") or {}).get("name"),
                                            "workstream": (n.get("workstream") or {}).get("name")}
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def _issues(repo):
    """Every issue (not PR) in a repo, open and closed."""
    out, after = [], None
    owner, name = repo.split("/")
    while True:
        data = graphql("""query($owner:String!,$name:String!,$after:String){ repository(owner:$owner,name:$name){
          issues(first:100, after:$after){ pageInfo{ hasNextPage endCursor } nodes{ id number title url state stateReason } } } }""",
                       owner=owner, name=name, after=after)
        page = data["repository"]["issues"]
        out += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def initial_status(issue):
    if issue["state"] == "OPEN":
        return "Planned"
    if issue.get("stateReason") == "COMPLETED":
        return "AI-reviewed"   # Human-reviewed is set only by a human
    return None


def title_workstream(title, options):
    m = re.match(r"^\[([^\]]+)\]", title or "")
    return m.group(1) if m and m.group(1) in options else None


def setup(doc, dry_run=False, log=print):
    """Create or complete the project's GitHub Project. Idempotent; returns the updated config."""
    gp = doc["github_project"]
    owner = gp["owner"]
    act = (lambda msg: log("  would " + msg)) if dry_run else (lambda msg: log("  " + msg))

    if gp.get("number") is None:
        act(f"create GitHub Project '{doc['title']}' for {owner}")
        if dry_run:
            return doc
        created = gh("project", "create", "--owner", owner, "--title", doc["title"], "--format", "json")
        gp["number"], gp["url"] = created["number"], created["url"]
        readme = (f"# {doc['title']}\n\n{doc.get('description', '')}\n\nParent repository: "
                  f"[{doc['repo']['name']}](https://github.com/{doc['repo']['name']}). Workflow: "
                  "[GitHub Project orchestration layer](https://github.com/hevangel/any-agent-orchestrator/blob/main/spec/github_project.md).\n")
        gh("project", "edit", str(gp["number"]), "--owner", owner, "--description",
           (doc.get("description") or doc["title"])[:255], "--readme", readme, parse=False)
    proj = _project(owner, gp["number"])
    pid = proj["id"]
    fields = {f["name"]: f for f in proj["fields"]["nodes"] if f.get("name")}

    # repositories
    linked = {r["nameWithOwner"].lower() for r in proj["repositories"]["nodes"]}
    repos = controlled_repos(doc)
    for repo in repos:
        if repo.lower() not in linked:
            act(f"link {repo}")
            if not dry_run:
                gh("project", "link", str(gp["number"]), "--owner", owner, "--repo", repo, parse=False)

    # fields
    def ensure_select(name, wanted):
        """wanted: [(name, color, description)]; keeps existing options (by id) and appends missing ones."""
        f = fields.get(name)
        if f is None:
            act(f"create field {name} ({len(wanted)} options)")
            if not dry_run:
                graphql("""mutation($p:ID!,$n:String!,$o:[ProjectV2SingleSelectFieldOptionInput!]){
                  createProjectV2Field(input:{projectId:$p,dataType:SINGLE_SELECT,name:$n,singleSelectOptions:$o}){ clientMutationId } }""",
                        p=pid, n=name, o=[{"name": n, "color": c, "description": d} for n, c, d in wanted])
            return
        have = f.get("options") or []
        names = {o["name"] for o in have}
        if name == "Status" and names == DEFAULT_STATUS:
            act("replace the default Status options with " + ", ".join(n for n, _, _ in wanted))
            keep = []
        else:
            keep = [{"id": o["id"], "name": o["name"], "color": o["color"], "description": o.get("description") or ""} for o in have]
        missing = [w for w in wanted if w[0] not in {k["name"] for k in keep}]
        if not missing and len(keep) == len(have):
            return
        if keep:
            act(f"add {name} options " + ", ".join(n for n, _, _ in missing))
        if not dry_run:
            graphql("""mutation($f:ID!,$o:[ProjectV2SingleSelectFieldOptionInput!]){
              updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:$o}){ clientMutationId } }""",
                    f=f["id"], o=keep + [{"name": n, "color": c, "description": d} for n, c, d in missing])

    ensure_select("Status", STATUS_OPTIONS)
    ensure_select("Agent", [(n, "GRAY", "") for n in agent_options()])
    workstreams = doc.get("workstreams") or []
    if workstreams:
        ensure_select("Workstream", [(n, COLORS[i % len(COLORS)], "") for i, n in enumerate(workstreams)])
    for name, kind in (("Path", "TEXT"), ("Start date", "DATE"), ("End date", "DATE")):
        if name not in fields:
            act(f"create field {name} ({kind.lower()})")
            if not dry_run:
                graphql("""mutation($p:ID!,$n:String!,$t:ProjectV2CustomFieldType!){
                  createProjectV2Field(input:{projectId:$p,dataType:$t,name:$n}){ clientMutationId } }""", p=pid, n=name, t=kind)
    if "Iteration" not in fields:
        start = date.today() - timedelta(days=date.today().weekday())     # this week's Monday
        its = [{"startDate": (start + timedelta(days=ITERATION_DAYS * i)).isoformat(), "duration": ITERATION_DAYS,
                "title": f"Iteration {i + 1}"} for i in range(ITERATIONS_AHEAD)]
        act(f"create field Iteration ({ITERATION_DAYS}-day, {ITERATIONS_AHEAD} iterations from {start})")
        if not dry_run:
            graphql("""mutation($p:ID!,$c:ProjectV2IterationFieldConfigurationInput!){
              createProjectV2Field(input:{projectId:$p,dataType:ITERATION,name:"Iteration",iterationConfiguration:$c}){ clientMutationId } }""",
                    p=pid, c={"startDate": start.isoformat(), "duration": ITERATION_DAYS, "iterations": its})

    # labels: create the canonical set where missing; never recolor or rename existing labels
    for repo in repos:
        have = {l["name"].lower() for l in gh("label", "list", "-R", repo, "--limit", "500", "--json", "name")}
        for name, color, desc in LABELS:
            if name.lower() not in have:
                act(f"create label {name} in {repo}")
                if not dry_run:
                    gh("label", "create", name, "-R", repo, "--color", color, "--description", desc, parse=False)

    # items: every issue of every controlled repo, with an initial Status and Workstream
    proj = _project(owner, gp["number"]) if not dry_run else proj
    fields = {f["name"]: f for f in proj["fields"]["nodes"] if f.get("name")}
    items = _items(owner, gp["number"])

    def set_option(item_id, field_name, option_name):
        f = fields.get(field_name)
        opt = next((o["id"] for o in (f or {}).get("options") or [] if o["name"] == option_name), None)
        if f is None or opt is None:
            return
        graphql("""mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,
          value:{singleSelectOptionId:$o}}){ clientMutationId } }""", p=pid, i=item_id, f=f["id"], o=opt)

    added = 0
    for repo in repos:
        for issue in _issues(repo):
            item = items.get(issue["url"])
            if item is None:
                act(f"add {repo}#{issue['number']} {issue['title'][:60]}")
                added += 1
                if dry_run:
                    continue
                res = graphql("""mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }""",
                              p=pid, c=issue["id"])
                item = {"id": res["addProjectV2ItemById"]["item"]["id"], "status": None, "workstream": None}
            status = initial_status(issue)
            if not item["status"] and status:
                act(f"set {repo}#{issue['number']} Status={status}")
                if not dry_run:
                    set_option(item["id"], "Status", status)
            ws = title_workstream(issue["title"], workstreams)
            if not item["workstream"] and ws:
                act(f"set {repo}#{issue['number']} Workstream={ws}")
                if not dry_run:
                    set_option(item["id"], "Workstream", ws)
    log(f"  {added} issue(s) {'to add' if dry_run else 'added'}; project {gp.get('url')}")
    return doc


def project_status(doc):
    """Live item counts by Status (for `aao projects status`)."""
    gp = doc["github_project"]
    if gp.get("number") is None:
        return None
    counts = {}
    for item in _items(gp["owner"], gp["number"]).values():
        counts[item["status"] or "(none)"] = counts.get(item["status"] or "(none)", 0) + 1
    return counts
