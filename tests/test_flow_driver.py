import json
import shutil
import subprocess

import pytest

from orchestrator import web, web_flow


def test_flow_shares_the_gemini_login():
    assert web.login_name("flow") == "gemini"
    assert web.TARGETS["flow"]["chat"] is False


def test_download_qualities_name_what_costs_credits():
    assert web_flow.QUALITIES["original"]["free"] and web_flow.QUALITIES["gif"]["free"]
    assert not web_flow.QUALITIES["1080p"]["free"] and not web_flow.QUALITIES["4k"]["free"]
    with pytest.raises(ValueError):
        web_flow.download("p", 1, quality="8k")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_flow_script_is_valid_javascript(tmp_path):
    src = tmp_path / "flow.js"
    src.write_text("const f = " + web_flow.SCRIPT.replace("__CFG__", "{}"), encoding="utf-8")
    done = subprocess.run(["node", "--check", str(src)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_upload_wait_accepts_new_file_when_rendered_tile_count_stays_constant(tmp_path):
    previous = [{"name": "shot.png", "thumbnail": "old-shot", "type": "image"},
                {"name": "another.png", "thumbnail": "old-another", "type": "image"}]
    cfg = {"recipe": web_flow.RECIPE, "op": "upload-wait", "before": previous, "filename": "shot.png",
           "timeoutMs": 250}
    fixture = tmp_path / "flow-upload.js"
    fixture.write_text(
        "const run = " + web_flow.SCRIPT.replace("__CFG__", json.dumps(cfg)) + "\n" + """
        const oldTiles = """ + json.dumps(previous) + """;
        let calls = 0;
        const page = {
          getByRole: () => ({}),
          keyboard: { press: async () => {} },
          waitForTimeout: async () => {},
          evaluate: async () => {
            if (++calls === 1) return oldTiles;
            return [{name: 'shot.png', thumbnail: 'new-shot', type: 'image'}, oldTiles[0]];
          },
        };
        run(page).then(result => console.log(result, calls));
        """,
        encoding="utf-8",
    )
    done = subprocess.run(["node", str(fixture)], capture_output=True, text=True, check=True)
    result, calls = done.stdout.strip().rsplit(" ", 1)
    assert json.loads(result)["uploaded"] == 1
    assert calls == "3"
