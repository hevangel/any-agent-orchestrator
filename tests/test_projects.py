import json

from orchestrator import projects, server

GITMODULES = {
    "me/parent": """
[submodule "core"]
\tpath = core
\turl = https://github.com/me/core.git
[submodule "external/ref"]
\tpath = external/ref
\turl = https://github.com/other/ref.git
[submodule "libs/foreign"]
\tpath = libs/foreign
\turl = https://github.com/other/foreign.git
""",
    "me/core": """
[submodule "deep"]
\tpath = deep
\turl = git@github.com:me/deep.git
[submodule "x"]
\tpath = third_party/external/x
\turl = https://gitlab.com/x/x.git
""",
    "me/deep": """
[submodule "loop"]
\tpath = loop
\turl = https://github.com/me/parent
""",
}


def fetch(repo):
    return GITMODULES.get(repo, "")


def doc():
    return {"schema": projects.SCHEMA, "id": "parent", "title": "Parent",
            "github_project": {"owner": "me"}, "repo": {"name": "me/parent"}}


def test_parse_gitmodules_and_github_names():
    mods = projects.parse_gitmodules(GITMODULES["me/parent"])
    assert [m["path"] for m in mods] == ["core", "external/ref", "libs/foreign"]
    assert projects.github_name("https://github.com/me/core.git") == "me/core"
    assert projects.github_name("git@github.com:me/deep.git") == "me/deep"
    assert projects.github_name("https://gitlab.com/x/x.git") is None


def test_sync_builds_nested_tree_and_separates_external():
    d = doc()
    warnings = projects.sync(d, fetch)
    repo = d["repo"]
    assert [s["path"] for s in repo["submodules"]] == ["core", "libs/foreign"]
    assert [e["name"] for e in repo["external"]] == ["other/ref"]
    core = repo["submodules"][0]
    assert core["submodules"][0]["name"] == "me/deep"
    assert core["external"][0]["path"] == "third_party/external/x"      # external/ at any depth
    deep = core["submodules"][0]
    assert deep["submodules"][0]["name"] == "me/parent"                  # cycle recorded, not walked
    assert "submodules" not in deep["submodules"][0]
    # owned repos only: the foreign non-external submodule is flagged and never set up
    assert projects.controlled_repos(d) == ["me/parent", "me/core", "me/deep"]
    assert warnings == ["libs/foreign: other/foreign is not owned by me but is not under external/"]
    assert projects.validate(d, "parent.json") == []


def test_validate_rejects_bad_configs():
    d = doc()
    d["repo"]["submodules"] = [{"path": "external/x", "url": "https://github.com/a/b"}]
    d["github_project"]["number"] = "1"
    errors = projects.validate(d, "wrong.json")
    assert any("file name" in e for e in errors)
    assert any("external/ references" in e for e in errors)
    assert any("integer number" in e for e in errors)


def test_collaborator_repositories_are_managed_but_external_references_are_not():
    d = doc()
    d["collaborators"] = ["OTHER"]
    assert projects.sync(d, fetch) == []
    assert projects.controlled_repos(d) == ["me/parent", "me/core", "me/deep", "other/foreign"]
    assert "other/ref" not in projects.controlled_repos(d)
    assert projects.validate(d) == []


def test_validate_rejects_invalid_collaborators():
    for value in ("someone", [None], [""], ["a/b"], ["-bad"], ["a" * 40]):
        d = doc()
        d["collaborators"] = value
        assert any("collaborators" in error for error in projects.validate(d))


def test_initial_status_and_workstream():
    assert projects.initial_status({"state": "OPEN"}) == "Planned"
    assert projects.initial_status({"state": "CLOSED", "stateReason": "COMPLETED"}) == "AI-reviewed"
    assert projects.initial_status({"state": "CLOSED", "stateReason": "NOT_PLANNED"}) is None
    assert projects.title_workstream("[Art Style] Pixel art", ["Art Style"]) == "Art Style"
    assert projects.title_workstream("[Bug] x", ["Art Style"]) is None


def test_agent_options_follow_agents_json():
    names = projects.agent_options()
    assert {"Claude", "Codex", "ZCode", "OpenCode", "Gemini", "ChatGPT"} <= set(names)
    assert "Herdr" not in names and "herdr" not in names


def test_committed_project_configs_are_valid():
    assert projects.validate_all() == []
    ids = [p["id"] for p in projects.load_all()]
    assert {"ai_engineer", "silicon_wars_anime_girls"} <= set(ids)


def test_projects_api_payload(tmp_path, monkeypatch):
    d = doc()
    projects.sync(d, fetch)
    (tmp_path / "parent.json").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    payload = server.projects_payload()
    assert payload["projects"][0]["id"] == "parent"
    assert payload["projects"][0]["warnings"] == ["libs/foreign: other/foreign is not owned by me but is not under external/"]
