#!/usr/bin/env python3
"""Check immutable action pins and lifecycle permission boundaries."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "bootstrap-e2e.yml"
TEMPLATE_WORKFLOW = ROOT / ".github" / "workflows" / "template-bootstrap-e2e.yml"
JOURNEY_WORKFLOW = ROOT / ".github" / "workflows" / "real-agent-journey.yml"
ASSERTIONS_WORKFLOW = ROOT / ".github" / "workflows" / "real-agent-journey-assertions.yml"
PINNED_ACTIONS = {
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",  # v4.2.2
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",  # v4.6.2
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",  # v4.3.0
    "actions/create-github-app-token": "fee1f7d63c2ff003460e3d139729b119787bc349",  # v2.2.2
}
USE = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)


def check():
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = USE.findall(text)
    if not uses:
        raise AssertionError("workflow has no actions")
    for reference in uses:
        action, separator, sha = reference.partition("@")
        if not separator or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise AssertionError("action is not pinned to a full commit SHA: %s" % reference)
        if action in PINNED_ACTIONS and PINNED_ACTIONS[action] != sha:
            raise AssertionError("action SHA is not the verified documented pin: %s" % reference)
    for action, sha in PINNED_ACTIONS.items():
        if "%s@%s" % (action, sha) not in uses:
            raise AssertionError("required action pin is missing: %s" % action)
    if "permissions: {}" not in text:
        raise AssertionError("workflow must default to no permissions")
    if ("permission-administration: write" not in text or
            "permission-contents: write" not in text or
            text.count("permission-workflows: write") != 1):
        raise AssertionError("bootstrap App token must request administration, contents, and workflows write")
    if text.count("actions/create-github-app-token@") != 2:
        raise AssertionError("bootstrap and cleanup must mint separate lifecycle tokens")
    if "group: bootstrap-e2e-report-${{ github.repository }}-${{ needs.prepare.outputs.sha ||" not in text:
        raise AssertionError("report must serialize by resolved SHA")
    if "- name: Delete this run's disposable repositories\n        if: always()" not in text:
        raise AssertionError("cleanup deletion must run always")
    cleanup = text.split("\n  cleanup:\n", 1)[1].split("\n  triage:\n", 1)[0]
    if "actions/checkout@" in cleanup:
        raise AssertionError("cleanup must not depend on repository checkout")
    bootstrap = text.split("\n  bootstrap:\n", 1)[1].split("\n  cleanup:\n", 1)[0]
    report = text.split("\n  report:\n", 1)[1]
    if "OPENAI_API_KEY" in bootstrap or "OPENAI_API_KEY" in cleanup or "OPENAI_API_KEY" in report:
        raise AssertionError("OPENAI_API_KEY must be isolated to triage")
    if "BOOTSTRAP_E2E_TOKEN: ${{ secrets." in bootstrap or "BOOTSTRAP_E2E_TOKEN: ${{ secrets." in cleanup:
        raise AssertionError("lifecycle token must be short-lived App output")
    print("bootstrap workflow static check OK")
    template = TEMPLATE_WORKFLOW.read_text(encoding="utf-8")
    template_uses = USE.findall(template)
    if not template_uses or any(not re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) for reference in template_uses):
        raise AssertionError("template bootstrap action is not pinned to a full commit SHA")
    for required in ("workflow_dispatch:", "permissions: {}", "fail-fast: false", "--template", "--stack",
                     "if: always()", "issues: write", "--run-id", "cleanup-template",
                     "actions/download-artifact@%s" % PINNED_ACTIONS["actions/download-artifact"]):
        if required not in template:
            raise AssertionError("template workflow is missing %s" % required)
    if "OPENAI_API_KEY" in template:
        raise AssertionError("template bootstrap must not receive OpenAI credentials")
    bootstrap = template.split("\n  bootstrap:\n", 1)[1].split("\n  cleanup:\n", 1)[0]
    report = template.split("\n  report:\n", 1)[1]
    if "GITHUB_TOKEN" in bootstrap or "BOOTSTRAP_E2E_TOKEN" in report:
        raise AssertionError("lifecycle and reporting credentials must remain separate")
    print("template bootstrap workflow static check OK")
    journey = JOURNEY_WORKFLOW.read_text(encoding="utf-8")
    journey_uses = USE.findall(journey)
    if not journey_uses or any(not re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) for reference in journey_uses):
        raise AssertionError("real-agent journey action is not pinned to a full commit SHA")
    for action, sha in PINNED_ACTIONS.items():
        if "%s@%s" % (action, sha) not in journey_uses:
            raise AssertionError("real-agent journey is missing required action pin: %s" % action)
    for required in ("schedule:", "workflow_dispatch:", "permissions: {}", "cancel-in-progress: false",
                     "if: always()", "JOURNEY_RUNTIME", "real-agent-journey.py collect",
                     "retention-days: 7", "JOURNEY_CONTRACT_VERSION: real-agent-journey/v1", "Install selected runtime", "--provision stage-input/provision.json",
                     "--agent stage-input/agent.json", "--workspace generated", "REAL_AGENT_JOURNEY_API_KEY"):
        if required not in journey:
            raise AssertionError("real-agent journey is missing %s" % required)
    if "release:" in journey:
        raise AssertionError("real-agent journey must not be a release gate")
    for adapter in ("provision", "agent", "assert", "cleanup"):
        path = ROOT / "scripts" / ("real-agent-journey-%s.py" % adapter)
        if not path.is_file() or "scripts/real-agent-journey-%s.py" % adapter not in journey:
            raise AssertionError("real-agent journey %s adapter is missing" % adapter)
    if journey.count("actions/create-github-app-token@") != 4:
        raise AssertionError("real-agent journey must mint one token per credential boundary")
    agent = journey.split("\n  agent:\n", 1)[1].split("\n  assert:\n", 1)[0]
    if "OPENAI_API_KEY" not in agent or "BOOTSTRAP_E2E_TOKEN" in agent:
        raise AssertionError("agent credentials are not isolated")
    for other in (journey.split("\n  provision:\n", 1)[1].split("\n  agent:\n", 1)[0],
                  journey.split("\n  assert:\n", 1)[1].split("\n  cleanup:\n", 1)[0],
                  journey.split("\n  cleanup:\n", 1)[1].split("\n  report:\n", 1)[0]):
        if "OPENAI_API_KEY" in other:
            raise AssertionError("agent API credentials crossed a stage boundary")
    print("real-agent journey workflow static check OK")

    assertions = ASSERTIONS_WORKFLOW.read_text(encoding="utf-8")
    assertion_uses = USE.findall(assertions)
    if not assertion_uses or any(not re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) for reference in assertion_uses):
        raise AssertionError("journey assertion action is not pinned to a full commit SHA")
    for required in ("workflow_call:", "permissions: {}", "actions: read", "contents: read",
                     "JOURNEY_READ_TOKEN", "if: always()", "retention-days: 7",
                     "scripts/real-agent-journey.py", "implementation-branch"):
        if required not in assertions:
            raise AssertionError("journey assertion workflow is missing %s" % required)
    if any(value in assertions for value in ("OPENAI_API_KEY", "status:approved", "bootstrap-e2e.py template")):
        raise AssertionError("journey assertion workflow contains an out-of-scope authority or lifecycle operation")
    print("real-agent journey assertion workflow static check OK")


if __name__ == "__main__":
    check()
