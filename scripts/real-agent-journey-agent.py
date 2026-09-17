#!/usr/bin/env python3
"""Run the selected cold real-agent adapter and emit bounded stage evidence."""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNEY_SPEC = importlib.util.spec_from_file_location("real_agent_journey", ROOT / "scripts" / "real-agent-journey.py")
journey = importlib.util.module_from_spec(JOURNEY_SPEC)
JOURNEY_SPEC.loader.exec_module(journey)
AGENT_SPEC = importlib.util.spec_from_file_location("real_agent_e2e", ROOT / "scripts" / "real-agent-e2e.py")
agent = importlib.util.module_from_spec(AGENT_SPEC)
AGENT_SPEC.loader.exec_module(agent)


def load(path):
    raw = Path(path).read_bytes()
    if len(raw) > journey.MAX_JSON_BYTES:
        raise ValueError("stage evidence is too large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("stage evidence is not an object")
    return value


def run(args):
    run_id = journey.validate_run_id(args.run_id)
    repository = journey.validate_repository(args.repository)
    runtime = journey.validate_runtime(args.runtime)
    provision = journey.validate_stage("provision", load(args.provision), run_id, repository)
    source_sha = provision["identifiers"]["revision"]
    token = os.environ.get("JOURNEY_TOKEN", "")
    workspace = Path(args.workspace).resolve()
    decisions_path = Path(args.decisions).resolve()
    with tempfile.TemporaryDirectory(prefix="real-agent-adapter-") as directory:
        codex_evidence = Path(directory) / "codex.json"
        environment = {
            "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
            "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", ""),
            "AGENT_GITHUB_TOKEN": token,
        }
        command = [sys.executable, str(ROOT / "scripts" / "real-agent-e2e.py"), "run",
                   "--repository", repository, "--workspace", str(workspace),
                   "--expected-sha", source_sha, "--decisions", str(decisions_path),
                   "--evidence", str(codex_evidence)]
        try:
            result = subprocess.run(command, cwd=workspace, env=environment,
                                    capture_output=True, text=True, timeout=2700)
            outcome = load(codex_evidence) if codex_evidence.exists() else {}
            if result.returncode:
                raise RuntimeError("cold agent did not complete")
            if outcome.get("status") != "passed":
                raise RuntimeError("cold agent returned incomplete evidence")
            issue = outcome.get("issue")
            if type(issue) is not int or issue <= 0:
                raise RuntimeError("cold agent issue identity did not match")
            commit = outcome.get("commit", "")
            branch = outcome.get("branch", "")
            if not re.fullmatch(r"[0-9a-f]{40}", commit) or not journey.SAFE_BRANCH.fullmatch(branch):
                raise RuntimeError("cold agent identifiers were malformed")
            decisions = agent.validate_decisions(args.decisions)["decisions"]
            metadata = {
                "schema_version": journey.ENVELOPE_VERSION,
                "run_id": run_id,
                "source_repository": provision["identifiers"]["source_template"],
                "source_sha": source_sha,
                "generated_repository": repository,
                "generated_default_branch": provision["identifiers"]["default_branch"],
                "feature_issue": issue,
                "implementation_branch": branch,
                "implementation_commit": commit,
                "bindings": {"task": decisions["TASK_TRACKER"], "secrets": decisions["SECRETS_PROVIDER"]},
                "ci_jobs": [item.strip() for item in decisions["CI_STACKS"].split(",") if item.strip()],
                "checks": [{"name": "init-check", "status": "passed"}],
                "test_command": decisions["TEST_CMD"],
            }
            evidence = journey.stage_envelope(
                "agent", run_id, repository, "passed",
                {"issue": str(issue), "branch": branch, "commit": commit, "tests": "passed"},
            )
            evidence["runtime"] = runtime
            evidence["metadata"] = metadata
        except Exception as error:
            evidence = journey.stage_envelope(
                "agent", run_id, repository, "failed",
                failure_code=getattr(error, "code", "agent_failed"),
            )
    journey.write_json(args.output, evidence)
    if evidence["status"] != "passed":
        raise SystemExit(1)


def self_check():
    assert journey.validate_runtime("codex-cli") == "codex-cli"
    assert journey.component_path("agent").endswith("real-agent-journey-agent.py")
    print("real-agent journey agent adapter self-check OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--run-id")
    parser.add_argument("--runtime")
    parser.add_argument("--provision")
    parser.add_argument("--workspace")
    parser.add_argument("--decisions", default=str(ROOT / "scripts" / "real-agent-decisions.json"))
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not all((args.repository, args.run_id, args.runtime, args.provision, args.workspace, args.output)):
        parser.error("journey agent arguments are required")
    run(args)


if __name__ == "__main__":
    main()
