#!/usr/bin/env python3
"""Delete only the current run's independently verified journey repository."""
import argparse
import importlib.util
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("real_agent_journey", ROOT / "scripts" / "real-agent-journey.py")
journey = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journey)
BOOTSTRAP = journey.bootstrap


def run(args):
    run_id = str(args.run_id or "")
    owner = str(args.owner or "")
    template = str(args.template or "")
    repository = str(os.environ.get("JOURNEY_REPOSITORY", ""))
    token = os.environ.get("JOURNEY_TOKEN", "")
    identifiers = {}
    try:
        run_id = journey.validate_run_id(run_id)
        owner = journey.validate_owner(owner)
        template = journey.validate_repository(template)
        repository = journey.journey_repository(owner, run_id)
        identifiers = {"owner": owner, "target": repository}
        if not token:
            raise BOOTSTRAP.HarnessError("cleanup credential is missing", "cleanup_credential_missing")
        BOOTSTRAP.owner_identity(owner, token)
        current = BOOTSTRAP.api_request("GET", "repos/%s" % repository, token, expected=(200, 404))
        if current is not None:
            BOOTSTRAP.validate_template_identity(current, template, owner, repository.split("/", 1)[1])
            BOOTSTRAP.delete_repository(repository, token)
        evidence = journey.stage_envelope("cleanup", run_id, repository, "passed", identifiers)
        evidence["deleted"] = [] if current is None else [repository.split("/", 1)[1]]
    except Exception as error:
        evidence = journey.stage_envelope(
            "cleanup", run_id, repository, "failed", identifiers,
            getattr(error, "failure_code", "cleanup_failed"),
        )
    journey.write_json(args.output, evidence)
    if evidence["status"] != "passed":
        raise SystemExit(1)


def self_check():
    assert journey.journey_repository("acme", "123") == "acme/real-agent-journey-123"
    assert journey.component_path("cleanup").endswith("real-agent-journey-cleanup.py")
    print("real-agent journey cleanup adapter self-check OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--owner")
    parser.add_argument("--template", default="eff3ct0/factory-template")
    parser.add_argument("--run-id")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not all((args.owner, args.run_id, args.output)):
        parser.error("--owner, --run-id, and --output are required")
    run(args)


if __name__ == "__main__":
    main()
