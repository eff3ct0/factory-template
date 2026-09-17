#!/usr/bin/env python3
"""Focused offline checks for the real-agent journey contract and assertions."""
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("real_agent_journey", ROOT / "scripts" / "real-agent-journey.py")
journey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journey)


SHA = "a" * 40
COMMIT = "b" * 40


def form_body():
    return "\n".join([
        "### Context / problem", "A small feature.", "", "### Acceptance criteria", "- [ ] It works.",
        "", "### Scope", "In: the feature", "Out: unrelated work", "", "### Verification", "Run the test.",
    ])


def fixture(tmp, cleanup_status="passed", commit=COMMIT, head=COMMIT, test_status=0):
    checkout = Path(tmp) / "checkout"
    (checkout / ".github/workflows").mkdir(parents=True)
    (checkout / ".github/ISSUE_TEMPLATE").mkdir(parents=True)
    (checkout / "docs").mkdir()
    for name in ("AGENT.md", "CLAUDE.md", "docs/agent-init.md"):
        (checkout / name).write_text("configured\n", encoding="utf-8")
    (checkout / "docs/bindings.md").write_text("> **Provider:** `github-issues`\n> **Provider:** `none`\n", encoding="utf-8")
    (checkout / ".github/workflows/ci.yml").write_text("jobs:\n  python:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
    (checkout / ".github/ISSUE_TEMPLATE/task.yml").write_text("""title: \"[Task] \"\nlabels:\n  - type:product\nbody:\n  - type: textarea\n    id: context\n    attributes:\n      label: Context / problem\n  - type: textarea\n    id: acceptance\n    attributes:\n      label: Acceptance criteria\n  - type: textarea\n    id: scope\n    attributes:\n      label: Scope\n  - type: textarea\n    id: verification\n    attributes:\n      label: Verification\n""", encoding="utf-8")
    cleanup = Path(tmp) / "cleanup.json"
    cleanup.write_text(json.dumps({"schema_version": "real-agent-journey-cleanup/v1", "owner": "acme",
                                   "run_id": "123", "status": cleanup_status,
                                   "deleted": ["real-agent-journey-123-python"] if cleanup_status == "passed" else [],
                                   "failures": []}), encoding="utf-8")
    data = {"schema_version": journey.ENVELOPE_VERSION, "run_id": "123",
            "source_repository": "eff3ct0/factory-template", "source_sha": SHA,
            "generated_repository": "acme/real-agent-journey-123-python", "generated_default_branch": "main",
            "feature_issue": 7, "implementation_branch": "feat/7-small-feature", "implementation_commit": commit,
            "bindings": {"task": "github-issues", "secrets": "none"}, "ci_jobs": ["python"],
            "checks": [{"name": "init-check", "status": "passed", "exit_code": 0}],
            "test_command": "python3 -c 'pass'"}
    return data, checkout, cleanup, head, test_status


def api_fixture(commit=COMMIT, source_sha=SHA, issue_body=None, issue_labels=None):
    def request(_method, path, _token):
        if path == "repos/eff3ct0/factory-template":
            return {"full_name": "eff3ct0/factory-template", "is_template": True, "default_branch": "main"}
        if path == "repos/eff3ct0/factory-template/git/ref/heads/main":
            return {"object": {"sha": source_sha}}
        if path == "repos/acme/real-agent-journey-123-python":
            return {"full_name": "acme/real-agent-journey-123-python", "default_branch": "main",
                    "template_repository": {"full_name": "eff3ct0/factory-template"}}
        if path == "repos/acme/real-agent-journey-123-python/branches/main":
            return {"commit": {"sha": source_sha}}
        if path == "repos/acme/real-agent-journey-123-python/issues/7":
            return {"number": 7, "repository_url": "https://api.github.com/repos/acme/real-agent-journey-123-python",
                    "title": "[Task] Small feature", "body": issue_body or form_body(),
                    "labels": [{"name": name} for name in (issue_labels or ["type:product"])]}
        if path == "repos/acme/real-agent-journey-123-python/branches/feat%2F7-small-feature":
            return {"commit": {"sha": commit}}
        if path.startswith("repos/acme/real-agent-journey-123-python/commits/"):
            return {"sha": commit, "commit": {"message": "feat: add small feature (#7)"}}
        raise AssertionError(path)
    return request


def runner_factory(head=COMMIT, test_status=0, timeout_test=False):
    def runner(command, **_kwargs):
        if timeout_test and command[0] != "git" and command[1:2] == ["-c"]:
            raise TimeoutError("test timed out")
        value = head if command[1:3] == ["rev-parse", "HEAD"] else "https://github.com/acme/real-agent-journey-123-python.git" if command[1:4] == ["remote", "get-url", "origin"] else ""
        if command[0] != "git" and command[0:2] == ["python3", "-c"]:
            value = ""
        return type("Result", (), {"returncode": test_status if command[0] != "git" and command[1:2] == ["-c"] else 0, "stdout": value, "stderr": ""})()
    return runner


def test_passing_fixture_and_bounded_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        data, checkout, cleanup, _, _ = fixture(tmp)
        result = journey.assert_journey(data, checkout, "token", "https://github.com/eff3ct0/factory-template/actions/runs/123",
                                        "https://github.com/eff3ct0/factory-template/actions/runs/123", cleanup,
                                        api_fixture(), runner_factory())
        assert result["status"] == "passed", result
        assert "token" not in json.dumps(result)
        assert "/home" not in json.dumps(result)


def test_earliest_boundaries_and_cleanup_are_preserved():
    cases = (("source", api_fixture(source_sha="c" * 40), runner_factory(), "source-readback"),
             ("issue", api_fixture(issue_body=form_body().replace("- [ ] It works.", "not observable")), runner_factory(), "feature-issue"),
             ("approval", api_fixture(issue_labels=["type:product", "status:approved"]), runner_factory(), "feature-issue"),
             ("commit", api_fixture(commit="c" * 40), runner_factory(), "implementation"),
             ("checkout", api_fixture(), runner_factory(head="c" * 40), "checkout"),
             ("test", api_fixture(), runner_factory(test_status=1), "test"))
    for _, api, runner, boundary in cases:
        with tempfile.TemporaryDirectory() as tmp:
            data, checkout, cleanup, _, _ = fixture(tmp)
            result = journey.assert_journey(data, checkout, "token", "https://github.com/eff3ct0/factory-template/actions/runs/123", "", cleanup, api, runner)
            assert result["status"] == "failed" and result["failure"]["boundary"] == boundary, result
    with tempfile.TemporaryDirectory() as tmp:
        data, checkout, cleanup, _, _ = fixture(tmp, cleanup_status="failed")
        result = journey.assert_journey(data, checkout, "token", "https://github.com/eff3ct0/factory-template/actions/runs/123", "", cleanup, api_fixture(), runner_factory())
        assert result["failure"]["boundary"] == "cleanup" and result["cleanup"]["status"] == "failed"
    with tempfile.TemporaryDirectory() as tmp:
        data, checkout, cleanup, _, _ = fixture(tmp, cleanup_status="failed")
        result = journey.assert_journey(data, checkout, "token", "https://github.com/eff3ct0/factory-template/actions/runs/123", "", cleanup,
                                        api_fixture(source_sha="c" * 40), runner_factory())
        assert result["failure"]["boundary"] == "source-readback" and result["cleanup"]["status"] == "failed"


def test_missing_and_malformed_inputs_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.json"
        path.write_text("{}", encoding="utf-8")
        try:
            journey.load_input(path)
        except journey.JourneyError:
            pass
        else:
            raise AssertionError("malformed input accepted")
        assert journey.cleanup_readback(Path(tmp) / "missing.json", "acme/repo", "acme", "123")["status"] == "unknown"


def test_timeout_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        data, checkout, cleanup, _, _ = fixture(tmp)
        result = journey.assert_journey(data, checkout, "token", "https://github.com/eff3ct0/factory-template/actions/runs/123", "", cleanup,
                                        api_fixture(), runner_factory(timeout_test=True))
        assert result["failure"]["boundary"] == "test" and result["failure"]["code"] == "test_failed"


def test_assert_stage_can_finish_before_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        data, checkout, _, _, _ = fixture(tmp)
        result = journey.assert_journey(data, checkout, "token",
                                        "https://github.com/eff3ct0/factory-template/actions/runs/123", "",
                                        request_fn=api_fixture(), runner=runner_factory(), require_cleanup=False)
        assert result["status"] == "passed", result


if __name__ == "__main__":
    test_passing_fixture_and_bounded_evidence()
    test_earliest_boundaries_and_cleanup_are_preserved()
    test_missing_and_malformed_inputs_fail_closed()
    test_timeout_fails_closed()
    test_assert_stage_can_finish_before_cleanup()
def test_contract_plan_is_provider_neutral():
    plan = journey.contract_plan("123", "eff3ct0/factory-template")
    assert plan["runtime"] is None
    assert plan["stages"] == ["provision", "agent", "assert", "cleanup"]
    assert plan["explicit_decisions"] == list(journey.DECISIONS)
    assert "status:approved" in plan["approval_boundary"]


def test_identity_and_decisions_fail_closed():
    try:
        journey.journey_repository("acme", "not-a-run")
    except journey.JourneyError as error:
        assert error.failure_code == "configuration_missing"
    else:
        raise AssertionError("invalid run identity accepted")
    try:
        journey.validate_decisions({"project": "Example"})
    except journey.JourneyError as error:
        assert error.failure_code == "decisions_incomplete"
    else:
        raise AssertionError("incomplete decision set accepted")


def test_runtime_and_adapter_contract():
    assert journey.validate_runtime("codex-cli") == "codex-cli"
    for runtime, code in (("", "runtime_missing"), ("mock", "runtime_unsupported")):
        try:
            journey.validate_runtime(runtime)
        except journey.JourneyError as error:
            assert error.failure_code == code
        else:
            raise AssertionError("invalid runtime accepted")
    for stage, path in journey.COMPONENTS.items():
        assert path == journey.component_path(stage)
        assert (ROOT / path).is_file(), path
    context = {"run_id": "123", "repository": "acme/real-agent-journey-123",
               "provision_token": "provision", "cleanup_token": "cleanup"}
    assert journey.stage_environment({}, "provision", context)["JOURNEY_TOKEN"] == "provision"
    assert journey.stage_environment({}, "cleanup", context)["JOURNEY_TOKEN"] == "cleanup"


def test_stage_schema_identity_and_approval_fail_closed():
    payload = {
        "schema_version": journey.ENVELOPE_VERSION, "stage": "agent", "run_id": "123",
        "repository": "acme/real-agent-journey-123", "status": "passed",
        "identifiers": {"issue": "7", "branch": "feature/7-small-feature",
                        "commit": COMMIT, "tests": "passed"},
    }
    assert journey.validate_stage("agent", payload, "123", "acme/real-agent-journey-123")["status"] == "passed"
    for invalid in ({**payload, "run_id": "124"}, {**payload, "schema_version": "other/v1"},
                    {**payload, "status": "failed", "failure_code": ""}):
        try:
            journey.validate_stage("agent", invalid, "123", "acme/real-agent-journey-123")
        except journey.JourneyError:
            pass
        else:
            raise AssertionError("invalid stage evidence accepted")


def test_aggregate_rejects_mismatch_and_cleanup_failure():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for stage in journey.STAGES:
            (directory / (stage + ".json")).write_text(json.dumps({
                "schema_version": journey.ENVELOPE_VERSION,
                "stage": stage,
                "run_id": "123",
                "repository": "acme/real-agent-journey-123",
                "status": "passed",
                "identifiers": {
                    "provision": {"source_template": "eff3ct0/factory-template", "default_branch": "main", "revision": "a" * 40},
                    "agent": {"issue": "12", "branch": "feature/12-example", "commit": "b" * 40, "tests": "passed"},
                    "assert": {"checkout_head": "b" * 40},
                    "cleanup": {"owner": "acme", "target": "acme/real-agent-journey-123"},
                }[stage],
            }), encoding="utf-8")
        (directory / "cleanup.json").write_text(json.dumps({
            "schema_version": journey.ENVELOPE_VERSION,
            "stage": "cleanup",
            "run_id": "123",
            "repository": "acme/other-repository",
            "status": "passed",
        }), encoding="utf-8")
        result = journey.aggregate(directory, "123", "acme/real-agent-journey-123")
        assert result["result"] == "failed"
        assert result["failure_code"] == "stage_identity_mismatch"
        assert result["cleanup_status"] == "failed"


def test_unsupported_runtime_keeps_cleanup_evidence():
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        for stage in journey.STAGES:
            identifiers = {
                "provision": {"source_template": "eff3ct0/factory-template", "default_branch": "main", "revision": SHA},
                "agent": {"issue": "12", "branch": "feature/12-example", "commit": COMMIT, "tests": "passed"},
                "assert": {"checkout_head": COMMIT},
                "cleanup": {"owner": "acme", "target": "acme/real-agent-journey-123"},
            }[stage]
            (directory / (stage + ".json")).write_text(json.dumps({
                "schema_version": journey.ENVELOPE_VERSION, "stage": stage, "run_id": "123",
                "repository": "acme/real-agent-journey-123", "status": "passed", "identifiers": identifiers,
            }), encoding="utf-8")
        result = journey.aggregate(directory, "123", "acme/real-agent-journey-123", "unsupported")
        assert result["result"] == "failed" and result["failure_code"] == "runtime_unsupported"
        assert result["cleanup_status"] == "passed"


if __name__ == "__main__":
    test_contract_plan_is_provider_neutral()
    test_identity_and_decisions_fail_closed()
    test_aggregate_rejects_mismatch_and_cleanup_failure()
    test_unsupported_runtime_keeps_cleanup_evidence()
    print("real-agent journey offline tests OK")
