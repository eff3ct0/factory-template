#!/usr/bin/env python3
"""Provision the exact run-scoped repository for the real-agent journey."""
import argparse
import importlib.util
import os
import re
import sys
import tempfile
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
    try:
        run_id = journey.validate_run_id(run_id)
        owner = journey.validate_owner(owner)
        template = journey.validate_repository(template)
        repository = journey.journey_repository(owner, run_id)
        details = BOOTSTRAP.template_details(template, token)
        BOOTSTRAP.owner_identity(owner, token)
        name = repository.split("/", 1)[1]
        BOOTSTRAP.template_repository(template, owner, name, token)
        readback = BOOTSTRAP.template_readback(template, owner, name, token)
        with tempfile.TemporaryDirectory(prefix="real-agent-provision-") as directory:
            askpass, git_environment = BOOTSTRAP.askpass_environment(token, directory)
            clone = Path(directory) / "checkout"
            try:
                BOOTSTRAP.git(["clone", "--config", "credential.helper=", "--branch",
                               readback["default_branch"], "--single-branch",
                               "https://github.com/%s" % repository, str(clone)], ROOT, git_environment)
                BOOTSTRAP.clean_checkout(clone, [token])
                if BOOTSTRAP.git(["rev-parse", "HEAD"], clone, BOOTSTRAP.released_environment()) != readback["initial_revision"]:
                    raise BOOTSTRAP.HarnessError("fresh checkout revision did not match readback", "checkout_mismatch")
            finally:
                askpass.unlink(missing_ok=True)
        if readback["initial_revision"] != details["initial_revision"]:
            raise BOOTSTRAP.HarnessError("generated repository revision did not match the source template",
                                          "revision_mismatch")
        evidence = journey.stage_envelope(
            "provision", run_id, repository, "passed",
            {"source_template": template, "default_branch": readback["default_branch"],
             "revision": readback["initial_revision"]},
        )
    except Exception as error:
        if not repository and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner) and re.fullmatch(r"[0-9]{1,20}", run_id):
            repository = "%s/real-agent-journey-%s" % (owner, run_id)
        evidence = journey.stage_envelope(
            "provision", run_id, repository, "failed",
            failure_code=getattr(error, "failure_code", "provision_failed"),
        )
    journey.write_json(args.output, evidence)
    if evidence["status"] != "passed":
        raise SystemExit(1)


def self_check():
    assert journey.component_path("provision").endswith("real-agent-journey-provision.py")
    assert journey.journey_repository("acme", "123") == "acme/real-agent-journey-123"
    print("real-agent journey provisioning adapter self-check OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--template")
    parser.add_argument("--owner")
    parser.add_argument("--run-id")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if not all((args.template, args.owner, args.run_id, args.output)):
        parser.error("--template, --owner, --run-id, and --output are required")
    run(args)


if __name__ == "__main__":
    main()
