"""Workflow files must be valid YAML with the triggers/settings the budget relies on."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
FILES = [ROOT / "examples" / "deploy" / "radar.yml", ROOT / ".github" / "workflows" / "tests.yml"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_workflow_parses(path):
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert "workflow_dispatch" in on
    for job in wf["jobs"].values():
        assert job.get("timeout-minutes"), "every job needs a timeout (budget)"


def test_deploy_workflow_budget_settings():
    wf = yaml.safe_load((ROOT / "examples" / "deploy" / "radar.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert on["schedule"][0]["cron"] == "17 0,6,12,18 * * *"
    assert wf["concurrency"] == {"group": "radar", "cancel-in-progress": False}
    assert len(wf["jobs"]) == 1 and wf["jobs"]["radar"]["timeout-minutes"] == 12
    assert on["workflow_dispatch"]["inputs"]["test_alert"]["type"] == "boolean"
