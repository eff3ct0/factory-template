#!/usr/bin/env python3
"""Independently read back and assert a completed journey."""
import argparse
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("real_agent_journey", ROOT / "scripts" / "real-agent-journey.py")
journey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journey)


def load(path):
    raw = Path(path).read_bytes()
    if len(raw) > journey.MAX_JSON_BYTES:
        raise ValueError("stage evidence is too large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("stage evidence is not an object")
    return value


def run(args):
    run_id = str(args.run_id or "")
    repository = str(args.repository or "")
    try:
        run_id = journey.validate_run_id(run_id)
        repository = journey.validate_repository(repository)
        agent_stage = load(args.agent)
        agent_result = journey.validate_stage("agent", agent_stage, run_id, repository)
        metadata = agent_stage.get("metadata")
        if not isinstance(metadata, dict) or set(metadata) != journey.REQUIRED_INPUT:
            raise ValueError("agent metadata is incomplete")
        if metadata.get("schema_version") != journey.ENVELOPE_VERSION or agent_result["status"] != "passed":
            raise ValueError("agent metadata is not a passed journey")
        result = journey.assert_journey(
            metadata, args.workspace, os.environ.get("JOURNEY_TOKEN", ""), args.workflow_url,
            args.artifact_url, require_cleanup=False,
        )
        evidence = journey.stage_envelope(
            "assert", run_id, repository, "passed" if result["status"] == "passed" else "failed",
            {"checkout_head": result.get("checkout", {}).get("head_sha", "")}
            if result["status"] == "passed" else {},
            (result.get("failure") or {}).get("code", "assert_failed"),
        )
    except Exception as error:
        evidence = journey.stage_envelope("assert", run_id, repository, "failed",
                                          failure_code=getattr(error, "code", "assert_failed"))
    journey.write_json(args.output, evidence)
    if evidence["status"] != "passed":
        raise SystemExit(1)


def self_check():
    assert journey.component_path("assert").endswith("real-agent-journey-assert.py")
    print("real-agent journey assertion adapter self-check OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--run-id")
    parser.add_argument("--agent")
    parser.add_argument("--workspace")
    parser.add_argument("--workflow-url")
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not all((args.repository, args.run_id, args.agent, args.workspace, args.workflow_url, args.output)):
        parser.error("journey assertion arguments are required")
    run(args)


if __name__ == "__main__":
    main()
