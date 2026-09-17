#!/usr/bin/env python3
"""Validate and independently assert the bounded real-agent journey contract."""
import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENVELOPE_VERSION = "real-agent-journey/v1"
MAX_JSON_BYTES = 64 * 1024
MAX_STRING_CHARS = 256
STAGES = ("provision", "agent", "assert", "cleanup")
STAGE_IDENTIFIERS = {
    "provision": ("source_template", "default_branch", "revision"),
    "agent": ("issue", "branch", "commit", "tests"),
    "assert": ("checkout_head",),
    "cleanup": ("owner", "target"),
}
DECISIONS = (
    "project",
    "stack",
    "task_tracker",
    "secrets_provider",
    "code_intelligence",
    "ci",
    "persistence_language",
    "branching",
    "testing",
    "approval_gates",
)
COMPONENTS = {
    "provision": "scripts/real-agent-journey-provision.py",
    "agent": "scripts/real-agent-journey-agent.py",
    "assert": "scripts/real-agent-journey-assert.py",
    "cleanup": "scripts/real-agent-journey-cleanup.py",
}
SUPPORTED_RUNTIMES = {"codex-cli": {"provider": "openai", "version": "0.148.0"}}
SAFE_STAGE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,200}")
PRIVATE_MARKER = re.compile(r"(?i)(?:token|secret|password|credential|api[_-]?key)")


def _load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load_helper("bootstrap_e2e", "bootstrap-e2e.py")
_bootstrap = bootstrap
CLEANUP_VERSIONS = {"real-agent-journey-cleanup/v1", "bootstrap-e2e-cleanup/v1"}
MAX_RESPONSE_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_OUTPUT_CHARS = 1200
MAX_CHECKS = 32
MAX_FAILURES = 8
SAFE_REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})")
SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
SAFE_SHA = re.compile(r"[0-9a-f]{40}")
SAFE_RUN = re.compile(r"[0-9]{1,20}")
SAFE_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
BOUNDARIES = ("source-readback", "initialization", "feature-issue", "implementation", "checkout", "test", "cleanup")
APPROVAL_LABELS = {"status:approved", "approved", "approval"}
CHECK_COMMANDS = {
    "init-check": ["init.py", "--check"],
    "determinism": ["scripts/check-determinism.py"],
    "delivery-contract": ["scripts/check-delivery-contract.py"],
    "governance": ["scripts/check-pr-governance.py", "--self-check"],
}
REQUIRED_INPUT = {
    "schema_version", "run_id", "source_repository", "source_sha", "generated_repository",
    "generated_default_branch", "feature_issue", "implementation_branch", "implementation_commit",
    "bindings", "ci_jobs", "checks", "test_command",
}


class JourneyError(RuntimeError):
    """A bounded failure shared by contract aggregation and assertions."""

    def __init__(self, first, second="contract_invalid", third=None):
        if third is None:
            message, code, boundary = str(first), second, None
        else:
            boundary, code, message = first, second, third
        super().__init__(message)
        self.boundary = boundary
        self.code = code
        self.failure_code = code


def safe_text(value, secrets=()):
    text = bootstrap.redacted(value, secrets)
    text = re.sub(r"(?i)\b(authorization|bearer|token|password|secret|api[_-]?key|credential)\s*[:=]\s*[^\s,]+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)\b[A-Z_][A-Z0-9_]*\s*=\s*[^\s,]+", lambda match: match.group(0).split("=", 1)[0] + "=<redacted>", text)
    text = re.sub(r"(?<!https:)(?<!http:)(?<![A-Za-z0-9])/(?:[A-Za-z0-9._-]+/)+[^\s,;)]*", "<private-path>", text)
    text = re.sub(r"\b(?:10|127|192\.168|169\.254|172\.(?:1[6-9]|2[0-9]|3[0-1]))\.\d{1,3}\.\d{1,3}\b", "<private-address>", text)
    if re.search(r"(?i)ignore\s+(?:all\s+)?previous|disregard\s+instructions|system\s+message", text):
        return "<untrusted-diagnostic>"
    return re.sub(r"\s+", " ", text).strip()[:MAX_OUTPUT_CHARS]


def repository(value):
    value = str(value or "").strip()
    if not SAFE_REPOSITORY.fullmatch(value):
        raise JourneyError("source-readback", "repository_invalid", "repository identifier is invalid")
    return value


def run_id(value):
    value = str(value or "").strip()
    if not SAFE_RUN.fullmatch(value):
        raise JourneyError("source-readback", "run_id_invalid", "run identifier is invalid")
    return value


def sha(value, boundary="source-readback"):
    value = str(value or "").strip().lower()
    if not SAFE_SHA.fullmatch(value):
        raise JourneyError(boundary, "sha_invalid", "commit identifier is not a full SHA")
    return value


def branch(value, boundary="implementation"):
    value = str(value or "").strip()
    if not SAFE_BRANCH.fullmatch(value) or value.startswith(("/", ".", "-")) or ".." in value:
        raise JourneyError(boundary, "branch_invalid", "branch identifier is invalid")
    return value


def api_request(method, path, token, expected=(200,)):
    if not token:
        raise JourneyError("source-readback", "github_token_missing", "GitHub readback credential is missing")
    request = urllib.request.Request(
        "https://api.github.com/" + path.lstrip("/"), method=method,
        headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        getattr(error, "read", lambda *_: b"")(MAX_RESPONSE_BYTES + 1)
        raise JourneyError("source-readback", "github_api_unavailable", "GitHub readback was unavailable") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise JourneyError("source-readback", "github_response_too_large", "GitHub readback exceeded its limit")
    if status not in expected:
        raise JourneyError("source-readback", "github_response_mismatch", "GitHub returned an unexpected response")
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JourneyError("source-readback", "github_response_malformed", "GitHub readback was malformed") from error


def load_input(path):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise JourneyError("source-readback", "input_malformed", "journey input is missing or malformed") from error
    if not isinstance(data, dict) or data.get("schema_version") != ENVELOPE_VERSION or set(data) != REQUIRED_INPUT:
        raise JourneyError("source-readback", "input_schema_invalid", "journey input schema is unsupported")
    return data


def validate_url(value, repository_name, run):
    parsed = urllib.parse.urlparse(value or "")
    prefix = "/%s/actions/runs/%s" % (repository_name, run)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise JourneyError("source-readback", "workflow_url_invalid", "workflow URL is not a public GitHub URL")
    if parsed.path.rstrip("/").lower() != prefix.lower() and not parsed.path.lower().startswith(prefix.lower() + "/"):
        raise JourneyError("source-readback", "workflow_url_mismatch", "workflow URL does not identify this run")
    return value


def issue_form(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise JourneyError("feature-issue", "issue_form_missing", "declared issue form is missing") from error
    title = next((line.split(":", 1)[1].strip().strip('"') for line in lines if line.startswith("title:")), "")
    labels = []
    in_labels = False
    body_start = None
    for index, line in enumerate(lines):
        if line == "labels:":
            in_labels = True
        elif line == "body:":
            in_labels = False
            body_start = index
        elif in_labels and line.startswith("  - "):
            labels.append(line[4:].strip().strip('"'))
    if body_start is None:
        raise JourneyError("feature-issue", "issue_form_invalid", "issue form has no body")
    starts = [index for index in range(body_start + 1, len(lines)) if re.fullmatch(r"  - type: [a-z]+", lines[index])]
    controls = []
    for offset, start in enumerate(starts):
        block = lines[start:starts[offset + 1] if offset + 1 < len(starts) else len(lines)]
        kind = block[0].split(": ", 1)[1]
        identifier = next((line.split(": ", 1)[1].strip() for line in block if line.startswith("    id: ")), "")
        label = next((line.split(": ", 1)[1].strip() for line in block if line.startswith("      label: ")), "")
        required = next((line.split(": ", 1)[1].strip() for line in block if line.startswith("      required: ")), "false")
        controls.append({"type": kind, "id": identifier, "label": label, "required": required == "true"})
    if not title or not controls or any(not control["id"] or not control["label"] for control in controls):
        raise JourneyError("feature-issue", "issue_form_invalid", "issue form controls are incomplete")
    return {"title": title, "labels": labels, "controls": controls}


def validate_issue_body(body, form):
    headings = [match.group(1).strip() for match in re.finditer(r"^### ([^\n]+)$", body or "", re.MULTILINE)]
    expected = [control["label"] for control in form["controls"] if control["type"] != "markdown"]
    if headings != expected:
        raise JourneyError("feature-issue", "issue_body_controls_mismatch", "feature issue headings do not match its form")
    sections = re.split(r"^### [^\n]+$", body or "", flags=re.MULTILINE)[1:]
    if len(sections) != len(expected) or any(not section.strip() for section in sections):
        raise JourneyError("feature-issue", "issue_body_required_missing", "feature issue has an empty required control")
    acceptance = next((control["label"] for control in form["controls"] if "accept" in control["id"].lower()), None)
    if not acceptance:
        raise JourneyError("feature-issue", "issue_form_no_acceptance", "feature issue form has no acceptance control")
    acceptance_index = expected.index(acceptance)
    if not re.search(r"(?m)^\s*[-*]\s*\[[ xX]\]\s+\S", sections[acceptance_index]):
        raise JourneyError("feature-issue", "acceptance_not_observable", "feature issue acceptance criteria are not observable")


def check_result(name, passed, detail):
    return {"name": name, "status": "passed" if passed else "failed", "detail": safe_text(detail)}


def command_result(name, command, checkout, secrets=(), runner=subprocess.run):
    try:
        result = runner(command, cwd=checkout, env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
                        capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return {"name": name, "status": "failed", "executed": False, "exit_code": None,
                "output": "<command-unavailable-or-timeout>"}
    output = safe_text((result.stdout + "\n" + result.stderr).strip(), secrets)
    return {"name": name, "status": "passed" if result.returncode == 0 else "failed", "executed": True,
            "exit_code": result.returncode if isinstance(result.returncode, int) and -255 <= result.returncode <= 255 else None,
            "output": output}


def git_read(command, checkout, boundary, runner=subprocess.run):
    try:
        result = runner(["git", *command], cwd=checkout,
                        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
                        capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise JourneyError(boundary, "git_readback_failed", "git readback failed")
    if result.returncode != 0:
        raise JourneyError(boundary, "git_readback_failed", "git readback failed")
    return (result.stdout or "").strip()


def cleanup_readback(path, expected_repository, expected_owner, expected_run):
    if not path:
        return {"status": "unknown", "deleted": [], "failures": ["cleanup evidence is unavailable"]}
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"status": "unknown", "deleted": [], "failures": ["cleanup evidence is malformed"]}
    if (not isinstance(data, dict) or data.get("schema_version") not in CLEANUP_VERSIONS or
            str(data.get("run_id")) != expected_run or str(data.get("owner", "")).lower() != expected_owner.lower()):
        return {"status": "failed", "deleted": [], "failures": ["cleanup identity readback failed"]}
    deleted = data.get("deleted", [])
    failures = data.get("failures", [])
    if not isinstance(deleted, list) or not all(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) for name in deleted):
        return {"status": "failed", "deleted": [], "failures": ["cleanup deletion list is malformed"]}
    safe_deleted = [name for name in deleted if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)]
    if not isinstance(failures, list) or not all(isinstance(value, str) for value in failures):
        return {"status": "failed", "deleted": safe_deleted[:MAX_FAILURES], "failures": ["cleanup failures are malformed"]}
    safe_failures = [safe_text(value) for value in failures[:MAX_FAILURES]]
    expected_name = expected_repository.split("/", 1)[1]
    if any(name != expected_name for name in safe_deleted):
        safe_failures.append("cleanup considered an unrelated repository")
    status = data.get("status") if data.get("status") in ("passed", "failed", "skipped", "not-attempted") else "unknown"
    if status == "passed" and safe_deleted != [expected_name]:
        safe_failures.append("cleanup did not delete the generated repository")
        status = "failed"
    return {"status": status, "deleted": safe_deleted[:MAX_FAILURES], "failures": safe_failures[:MAX_FAILURES]}


def assert_journey(data, checkout, token, workflow_url, artifact_url, cleanup_path="", request_fn=api_request, runner=subprocess.run,
                   require_cleanup=True):
    run = run_id(data["run_id"])
    source = repository(data["source_repository"])
    generated = repository(data["generated_repository"])
    source_sha = sha(data["source_sha"])
    generated_branch = branch(data["generated_default_branch"], "source-readback")
    implementation_branch = branch(data["implementation_branch"])
    implementation_sha = sha(data["implementation_commit"])
    owner, generated_name = generated.split("/", 1)
    assertions = []
    checks = []
    tests = []
    cleanup = {"status": "unknown", "deleted": [], "failures": ["cleanup not evaluated"]}
    failure = None
    source_branch = None
    initial_sha = None
    issue_title = None
    implementation_message = None
    checkout_head = None
    current_boundary = "source-readback"

    def get(path, boundary="source-readback"):
        try:
            return request_fn("GET", path, token)
        except JourneyError as error:
            if error.boundary == "source-readback" and boundary != "source-readback":
                raise JourneyError(boundary, error.code, str(error)) from error
            raise
        except Exception as error:
            raise JourneyError(boundary, "github_readback_failed", "GitHub readback failed") from error

    try:
        current_boundary = "source-readback"
        validate_url(workflow_url, source, run)
        if artifact_url:
            validate_url(artifact_url, source, run)
        source_details = get("repos/%s" % source)
        source_branch = source_details.get("default_branch") if isinstance(source_details, dict) else ""
        source_branch = branch(source_branch, "source-readback")
        if (not isinstance(source_details, dict) or source_details.get("full_name", "").lower() != source.lower()
                or source_details.get("is_template") is not True or not isinstance(source_branch, str)):
            raise JourneyError("source-readback", "source_identity_mismatch", "source template readback did not match")
        source_ref = get("repos/%s/git/ref/heads/%s" % (source, urllib.parse.quote(source_branch, safe="")))
        source_ref_sha = sha(((source_ref or {}).get("object") or {}).get("sha", ""))
        if source_ref_sha != source_sha:
            raise JourneyError("source-readback", "source_revision_mismatch", "source template revision did not match")
        generated_details = get("repos/%s" % generated)
        template = (generated_details or {}).get("template_repository") if isinstance(generated_details, dict) else None
        if (not isinstance(generated_details, dict) or generated_details.get("full_name", "").lower() != generated.lower()
                or generated_details.get("default_branch") != generated_branch
                or not isinstance(template, dict) or (template.get("full_name") or "").lower() != source.lower()
                or not generated_name.startswith("real-agent-journey-%s-" % run)):
            raise JourneyError("source-readback", "generated_identity_mismatch", "generated repository identity or ownership did not match")
        generated_ref = get("repos/%s/branches/%s" % (generated, urllib.parse.quote(generated_branch, safe="")))
        initial_sha = sha(((generated_ref or {}).get("commit") or {}).get("sha", ""))
        if initial_sha != source_sha:
            raise JourneyError("source-readback", "generated_revision_mismatch", "generated default branch did not start at source revision")
        assertions.append(check_result("github-source-and-repository", True, "%s -> %s@%s" % (source, generated, source_sha)))

        current_boundary = "initialization"
        required_files = ("AGENT.md", "CLAUDE.md", "docs/agent-init.md", "docs/bindings.md", ".github/workflows/ci.yml")
        missing = [path for path in required_files if not (Path(checkout) / path).is_file()]
        if missing:
            raise JourneyError("initialization", "required_artifact_missing", "required generated files are missing")
        binding_text = (Path(checkout) / "docs/bindings.md").read_text(encoding="utf-8")
        expected_bindings = data["bindings"]
        if (not isinstance(expected_bindings, dict) or not expected_bindings or
                any(not isinstance(value, str) or not value for value in expected_bindings.values())):
            raise JourneyError("initialization", "bindings_input_invalid", "explicit bindings are malformed")
        providers = set(re.findall(r"^> \*\*Provider:\*\* `([^`]+)`$", binding_text, re.MULTILINE))
        if not set(expected_bindings.values()) <= providers:
            raise JourneyError("initialization", "bindings_mismatch", "generated bindings do not reflect explicit selections")
        workflow_text = (Path(checkout) / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        jobs_part = workflow_text.split("jobs:\n", 1)[-1]
        jobs = re.findall(r"^  ([a-z0-9][a-z0-9_-]*):$", jobs_part, re.MULTILINE)
        expected_jobs = data["ci_jobs"]
        if (not isinstance(expected_jobs, list) or not expected_jobs or len(expected_jobs) > MAX_CHECKS or
                any(not isinstance(job, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,48}", job) for job in expected_jobs) or jobs != expected_jobs):
            raise JourneyError("initialization", "ci_jobs_mismatch", "generated CI jobs do not match explicit selections")
        if len(data["checks"]) > MAX_CHECKS:
            raise JourneyError("initialization", "checks_too_many", "too many documented checks")
        required_placeholders = re.compile(r"<(?:PROJECT_NAME|REPO_LANGUAGE|INTEGRATION_BRANCH|TASK_TRACKER)>")
        if any(required_placeholders.search((Path(checkout) / path).read_text(encoding="utf-8")) for path in ("AGENT.md", "docs/bindings.md")):
            raise JourneyError("initialization", "placeholders_remaining", "required initialization placeholders remain")
        for check in data["checks"]:
            if not isinstance(check, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,48}", str(check.get("name", ""))) or check.get("status") not in ("passed", "failed"):
                raise JourneyError("initialization", "checks_malformed", "documented check results are malformed")
            if check["name"] == "placeholder-scan":
                pending = any(re.search(r"<(?:PROJECT_NAME|REPO_LANGUAGE|INTEGRATION_BRANCH|TASK_TRACKER)>",
                                        (Path(checkout) / path).read_text(encoding="utf-8"))
                              for path in ("AGENT.md", "docs/bindings.md"))
                actual = {"name": check["name"], "status": "failed" if pending else "passed", "executed": True,
                          "exit_code": 1 if pending else 0, "output": "required placeholders remain" if pending else ""}
            else:
                command = CHECK_COMMANDS.get(check["name"])
                if command is None:
                    raise JourneyError("initialization", "check_unknown", "documented check is not allowlisted")
                actual = command_result(check["name"], [sys.executable, *command], checkout, runner=runner)
            checks.append(actual)
            if actual["status"] != "passed":
                raise JourneyError("initialization", "documented_check_failed", "a documented initialization check did not pass")
        if not checks or any(check["status"] != "passed" for check in checks):
            raise JourneyError("initialization", "documented_check_failed", "a documented initialization check did not pass")
        assertions.append(check_result("initialization-bindings-ci", True, "bindings and CI read back successfully"))

        current_boundary = "feature-issue"
        form = issue_form(Path(checkout) / ".github/ISSUE_TEMPLATE/task.yml")
        issue_number = data["feature_issue"]
        if type(issue_number) is not int or issue_number <= 0:
            raise JourneyError("feature-issue", "issue_number_invalid", "feature issue number is invalid")
        issue = get("repos/%s/issues/%d" % (generated, issue_number), "feature-issue")
        issue_title = issue.get("title") if isinstance(issue, dict) and isinstance(issue.get("title"), str) else None
        labels = {label.get("name").lower() for label in issue.get("labels", [])
                  if isinstance(label, dict) and isinstance(label.get("name"), str)} if isinstance(issue, dict) else set()
        if (not isinstance(issue, dict) or issue.get("number") != issue_number
                or (issue.get("repository_url") or "").lower() != ("https://api.github.com/repos/" + generated).lower()
                or "pull_request" in issue or not isinstance(issue_title, str) or not issue_title.startswith(form["title"])
                or not set(form["labels"]) <= labels or labels & APPROVAL_LABELS or any(label.startswith("status:") for label in labels)):
            raise JourneyError("feature-issue", "issue_identity_or_gate_invalid", "feature issue identity or approval boundary did not match")
        if not isinstance(issue.get("body"), str):
            raise JourneyError("feature-issue", "issue_body_malformed", "feature issue body is malformed")
        validate_issue_body(issue["body"], form)
        assertions.append(check_result("feature-issue-form", True, "issue #%s matches the declared form" % issue_number))

        current_boundary = "implementation"
        branch_ref = get("repos/%s/branches/%s" % (generated, urllib.parse.quote(implementation_branch, safe="")), "implementation")
        github_branch_sha = sha(((branch_ref or {}).get("commit") or {}).get("sha", ""), "implementation")
        commit = get("repos/%s/commits/%s" % (generated, implementation_sha), "implementation")
        message = commit.get("commit", {}).get("message", "") if isinstance(commit, dict) else ""
        if not isinstance(message, str):
            raise JourneyError("implementation", "commit_malformed", "GitHub commit readback was malformed")
        implementation_message = safe_text(message)
        if github_branch_sha != implementation_sha or not isinstance(commit, dict) or commit.get("sha") != implementation_sha:
            raise JourneyError("implementation", "branch_commit_mismatch", "GitHub branch and commit did not match")
        if not re.search(r"(?<![A-Za-z0-9])#%d(?![0-9])" % issue_number, message):
            raise JourneyError("implementation", "commit_issue_reference_missing", "implementation commit does not reference the feature issue")
        assertions.append(check_result("github-branch-commit", True, "%s@%s" % (implementation_branch, implementation_sha)))

        current_boundary = "checkout"
        head = sha(git_read(["rev-parse", "HEAD"], checkout, "checkout", runner), "checkout")
        checkout_head = head
        remote = git_read(["remote", "get-url", "origin"], checkout, "checkout", runner)
        if head != implementation_sha or generated.lower() not in remote.lower():
            raise JourneyError("checkout", "checkout_mismatch", "checkout HEAD or origin did not match GitHub readback")
        assertions.append(check_result("checkout-readback", True, "HEAD matches the GitHub implementation commit"))

        current_boundary = "test"
        command = data["test_command"]
        if (not isinstance(command, str) or len(command) > 256 or not command.strip() or
                any(token in command for token in ("&&", ";", "|", ">", "<", "`"))):
            raise JourneyError("test", "test_command_invalid", "test command is not a bounded argv command")
        try:
            argv = shlex.split(command)
        except ValueError as error:
            raise JourneyError("test", "test_command_invalid", "test command is malformed") from error
        if not argv:
            raise JourneyError("test", "test_command_invalid", "test command is empty")
        test = command_result("documented-test", argv, checkout, runner=runner)
        tests.append(test)
        if test["status"] != "passed" or not test["executed"]:
            raise JourneyError("test", "test_failed", "documented test did not pass")
        assertions.append(check_result("test-readback", True, "documented test executed and passed"))
    except JourneyError as error:
        failure = {"boundary": error.boundary, "code": error.code, "message": safe_text(error)}
    except (OSError, UnicodeDecodeError, TypeError, ValueError) as error:
        failure = {"boundary": current_boundary, "code": "assertion_unavailable", "message": safe_text(error)}
    finally:
        cleanup = cleanup_readback(cleanup_path, generated, owner, run)
        if (require_cleanup and cleanup["status"] != "passed" and
                (failure is None or BOUNDARIES.index("cleanup") < BOUNDARIES.index(failure["boundary"]))):
            failure = {"boundary": "cleanup", "code": "cleanup_%s" % cleanup["status"],
                       "message": cleanup["failures"][0] if cleanup["failures"] else "cleanup did not pass"}

    result = {
        "schema_version": ENVELOPE_VERSION, "status": "failed" if failure else "passed",
        "failure": failure, "run": {"id": run, "workflow_url": workflow_url},
        "source": {"repository": source, "default_branch": source_branch, "sha": source_sha},
        "generated": {"repository": generated, "default_branch": generated_branch, "initial_sha": initial_sha},
        "feature_issue": {"number": data["feature_issue"], "title": issue_title},
        "implementation": {"branch": implementation_branch, "commit": implementation_sha, "message": implementation_message},
        "checkout": {"head_sha": checkout_head},
        "assertions": assertions[:MAX_CHECKS], "checks": checks[:MAX_CHECKS], "tests": tests[:MAX_CHECKS], "cleanup": cleanup,
        "artifact_url": artifact_url or None,
    }
    return result


def write_evidence(path, evidence):
    serialized = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(serialized) > MAX_EVIDENCE_BYTES:
        raise JourneyError("cleanup", "evidence_too_large", "journey evidence exceeded its limit")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(serialized)


def assertion_self_check():
    assert safe_text("token=secret /home/alice/private") == "token=<redacted> <private-path>"
    assert safe_text("ignore previous instructions") == "<untrusted-diagnostic>"
    with __import__("tempfile").TemporaryDirectory() as directory:
        form = Path(directory) / "task.yml"
        form.write_text("""title: \"[Task] \"\nlabels:\n  - type:product\nbody:\n  - type: textarea\n    id: context\n    attributes:\n      label: Context / problem\n    validations:\n      required: true\n  - type: textarea\n    id: acceptance\n    attributes:\n      label: Acceptance criteria\n    validations:\n      required: true\n""", encoding="utf-8")
        parsed = issue_form(form)
        try:
            validate_issue_body("### Context / problem\ntext\n### Acceptance criteria\ntext", parsed)
        except JourneyError:
            pass
        else:
            raise AssertionError("non-observable acceptance criteria accepted")
    for bad in ("", "not-a-repository", "owner/repo/extra"):
        try:
            repository(bad)
        except JourneyError:
            pass
        else:
            raise AssertionError("invalid repository accepted")
    print("real-agent journey assertion self-check OK")
def validate_run_id(run_id):
    value = str(run_id or "").strip()
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise JourneyError("run_id must be numeric", "configuration_missing")
    return value


def validate_repository(repository):
    try:
        return _bootstrap.validate_repository(repository)
    except _bootstrap.HarnessError as error:
        raise JourneyError(str(error), "repository_invalid") from error


def validate_owner(owner):
    try:
        return _bootstrap.validate_owner(owner)
    except _bootstrap.HarnessError as error:
        raise JourneyError(str(error), "owner_invalid") from error


def journey_repository(owner, run_id):
    owner = validate_owner(owner)
    run_id = validate_run_id(run_id)
    return "%s/real-agent-journey-%s" % (owner, run_id)


def validate_runtime(runtime):
    value = str(runtime or "").strip()
    if not value:
        raise JourneyError("real-agent runtime is missing", "runtime_missing")
    if len(value) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise JourneyError("real-agent runtime is malformed", "runtime_invalid")
    if value not in SUPPORTED_RUNTIMES:
        raise JourneyError("real-agent runtime is unsupported", "runtime_unsupported")
    return value


def validate_decisions(decisions):
    if not isinstance(decisions, dict):
        raise JourneyError("explicit decisions must be an object", "decisions_invalid")
    missing = [key for key in DECISIONS if not isinstance(decisions.get(key), str) or not decisions[key].strip()]
    if missing:
        raise JourneyError("missing explicit decisions: %s" % ",".join(missing), "decisions_incomplete")
    for key in DECISIONS:
        value = decisions[key].strip()
        if len(value) > MAX_STRING_CHARS or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise JourneyError("decision is malformed: %s" % key, "decisions_invalid")
    return {key: decisions[key].strip() for key in DECISIONS}


def component_path(stage):
    if stage not in COMPONENTS:
        raise JourneyError("unknown journey stage: %s" % stage, "stage_invalid")
    return COMPONENTS[stage]


def stage_envelope(stage, run, repository_name, status, identifiers=None, failure_code=""):
    if stage not in STAGES:
        raise JourneyError("unknown journey stage: %s" % stage, "stage_invalid")
    if status not in ("passed", "failed", "blocked", "inconclusive"):
        raise JourneyError("stage evidence has an invalid status", "stage_invalid")
    failure_code = "" if status == "passed" else str(failure_code or "stage_failed")
    return {
        "schema_version": ENVELOPE_VERSION,
        "stage": stage,
        "run_id": str(run),
        "repository": str(repository_name),
        "status": status,
        "failure_code": failure_code if SAFE_STAGE.fullmatch(failure_code) else "stage_failed",
        "identifiers": identifiers or {},
    }


def stage_environment(base, stage, context):
    """Return the minimum environment for one child adapter.

    Lifecycle and read/write credentials are intentionally stage-specific. The
    agent adapter receives no lifecycle credential and no raw runner environment.
    """
    if stage not in STAGES:
        raise JourneyError("unknown journey stage: %s" % stage, "stage_invalid")
    environment = {name: base[name] for name in ("PATH", "HOME", "LANG", "LC_ALL") if name in base}
    environment.update({
        "JOURNEY_RUN_ID": validate_run_id(context.get("run_id")),
        "JOURNEY_REPOSITORY": validate_repository(context.get("repository")),
        "JOURNEY_STAGE": stage,
        "JOURNEY_CONTRACT_VERSION": ENVELOPE_VERSION,
    })
    if context.get("runtime"):
        environment["JOURNEY_AGENT_RUNTIME"] = validate_runtime(context["runtime"])
    credential = {
        "provision": "provision_token",
        "agent": "agent_token",
        "assert": "read_token",
        "cleanup": "cleanup_token",
    }[stage]
    if context.get(credential):
        environment["JOURNEY_TOKEN"] = context[credential]
    return environment


def read_json(path):
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise JourneyError("stage evidence is unavailable", "stage_missing") from error
    if len(raw) > MAX_JSON_BYTES:
        raise JourneyError("stage evidence is too large", "stage_oversized")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JourneyError("stage evidence is invalid JSON", "stage_malformed") from error
    if not isinstance(value, dict):
        raise JourneyError("stage evidence must be an object", "stage_malformed")
    return value


def validate_stage(stage, payload, run_id, repository):
    if not isinstance(payload, dict) or payload.get("schema_version") != ENVELOPE_VERSION:
        raise JourneyError("stage evidence has an unsupported schema", "stage_malformed")
    if payload.get("stage") != stage or payload.get("run_id") != run_id or payload.get("repository") != repository:
        raise JourneyError("stage evidence identity mismatch", "stage_identity_mismatch")
    status = payload.get("status")
    if status not in ("passed", "failed", "blocked", "inconclusive"):
        raise JourneyError("stage evidence has an invalid status", "stage_malformed")
    if status != "passed" and not SAFE_STAGE.fullmatch(str(payload.get("failure_code", ""))):
        raise JourneyError("failed stage evidence has no safe failure code", "stage_malformed")
    identifiers = payload.get("identifiers")
    if not isinstance(identifiers, dict):
        if status != "passed":
            return {"stage": stage, "status": status, "identifiers": {}}
        raise JourneyError("stage evidence has no bounded identifiers", "stage_metadata_missing")
    for key in STAGE_IDENTIFIERS[stage]:
        value = identifiers.get(key)
        if (not isinstance(value, str) or not SAFE_IDENTIFIER.fullmatch(value) or
                PRIVATE_MARKER.search(value)):
            raise JourneyError("stage evidence is missing identifier: %s" % key, "stage_metadata_missing")
        if key in ("source_template", "target"):
            validate_repository(value)
        elif key == "owner":
            validate_owner(value)
        elif key in ("revision", "commit", "checkout_head"):
            try:
                _bootstrap.validate_sha(value)
            except _bootstrap.HarnessError as error:
                raise JourneyError("stage evidence has an invalid revision", "stage_metadata_invalid") from error
    return {"stage": stage, "status": status, "identifiers": {
        key: identifiers[key] for key in STAGE_IDENTIFIERS[stage]
    }}


def aggregate(stage_dir, run_id, repository, runtime=None, workflow_url=None):
    try:
        run_id = validate_run_id(run_id)
    except JourneyError as error:
        return {
            "schema_version": ENVELOPE_VERSION, "run_id": None, "repository": None,
            "runtime": None, "stages": {}, "result": "failed", "failure_code": error.failure_code,
            "cleanup_status": "not-attempted", "workflow_url": workflow_url or None,
        }
    try:
        repository = validate_repository(repository)
    except JourneyError as error:
        return {
            "schema_version": ENVELOPE_VERSION, "run_id": run_id, "repository": None,
            "runtime": None, "stages": {}, "result": "failed", "failure_code": error.failure_code,
            "cleanup_status": "not-attempted", "workflow_url": workflow_url or None,
        }
    runtime_failure = ""
    if runtime is not None:
        try:
            runtime = validate_runtime(runtime)
        except JourneyError as error:
            runtime_failure = error.failure_code
            runtime = None
    stages = {}
    identifiers = {}
    failure_code = runtime_failure
    cleanup_status = "not-attempted"
    for stage in STAGES:
        try:
            payload = read_json(Path(stage_dir) / (stage + ".json"))
            result = validate_stage(stage, payload, run_id, repository)
        except JourneyError as error:
            if not failure_code:
                failure_code = error.failure_code
            if stage == "cleanup":
                cleanup_status = "failed"
            continue
        stages[stage] = result["status"]
        identifiers[stage] = result["identifiers"]
        if stage == "cleanup":
            cleanup_status = result["status"]
        if result["status"] != "passed" and not failure_code:
            failure_code = "%s_%s" % (stage, result["status"])
    passed = len(stages) == len(STAGES) and not failure_code and cleanup_status == "passed"
    return {
        "schema_version": ENVELOPE_VERSION,
        "run_id": run_id,
        "repository": repository,
        "source_template": identifiers.get("provision", {}).get("source_template"),
        "tested_revision": identifiers.get("provision", {}).get("revision"),
        "runtime": runtime,
        "stages": stages,
        "identifiers": identifiers,
        "result": "passed" if passed else "failed",
        "failure_code": "" if passed else (failure_code or "journey_incomplete"),
        "cleanup_status": cleanup_status,
        "workflow_url": workflow_url or None,
    }


def write_json(path, value):
    serialized = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(serialized) > MAX_JSON_BYTES:
        raise JourneyError("journey evidence is too large", "evidence_oversized")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialized)


def contract_plan(run_id, template, owner="", runtime=""):
    run_id = validate_run_id(run_id)
    template = validate_repository(template)
    return {
        "schema_version": ENVELOPE_VERSION,
        "run_id": run_id,
        "source_template": template,
        "generated_repository": journey_repository(owner, run_id) if owner else None,
        "runtime": validate_runtime(runtime) if runtime else None,
        "stages": list(STAGES),
        "explicit_decisions": list(DECISIONS),
        "components": dict(COMPONENTS),
        "approval_boundary": "agent and scripted user cannot add status:approved, merge, publish, or delete unrelated repositories",
    }


def contract_self_check():
    assert validate_run_id("123") == "123"
    assert journey_repository("acme", "123") == "acme/real-agent-journey-123"
    assert validate_runtime("codex-cli") == "codex-cli"
    for value, code in (("", "runtime_missing"), ("other-runtime", "runtime_unsupported")):
        try:
            validate_runtime(value)
        except JourneyError as error:
            assert error.failure_code == code
        else:
            raise AssertionError("unsupported runtime accepted")
    assert component_path("agent").endswith("real-agent-journey-agent.py")
    decisions = {key: key for key in DECISIONS}
    assert validate_decisions(decisions) == decisions
    try:
        validate_decisions({key: key for key in DECISIONS[:-1]})
    except JourneyError as error:
        assert error.failure_code == "decisions_incomplete"
    else:
        raise AssertionError("incomplete decisions accepted")
    environment = stage_environment(
        {"PATH": "/bin", "SECRET": "must-not-pass"}, "agent",
        {"run_id": "123", "repository": "acme/real-agent-journey-123", "runtime": "codex-cli",
         "lifecycle_token": "lifecycle", "agent_token": "agent"},
    )
    assert environment["JOURNEY_TOKEN"] == "agent"
    assert "SECRET" not in environment and environment["JOURNEY_TOKEN"] != "lifecycle"
    with __import__("tempfile").TemporaryDirectory() as directory:
        for stage in STAGES:
            write_json(Path(directory) / (stage + ".json"), {
                "schema_version": ENVELOPE_VERSION, "stage": stage, "run_id": "123",
                "repository": "acme/real-agent-journey-123", "status": "passed",
                "identifiers": {
                    "provision": {"source_template": "eff3ct0/factory-template", "default_branch": "main", "revision": "a" * 40},
                    "agent": {"issue": "12", "branch": "feature/12-example", "commit": "b" * 40, "tests": "passed"},
                    "assert": {"checkout_head": "b" * 40},
                    "cleanup": {"owner": "acme", "target": "acme/real-agent-journey-123"},
                }[stage],
            })
        result = aggregate(directory, "123", "acme/real-agent-journey-123", "codex-cli")
        assert result["result"] == "passed" and result["cleanup_status"] == "passed", result
        Path(directory, "cleanup.json").write_text("{}", encoding="utf-8")
        result = aggregate(directory, "123", "acme/real-agent-journey-123")
        assert result["result"] == "failed" and result["failure_code"] == "stage_malformed", result
    print("real-agent journey contract self-check OK")


def self_check():
    assertion_self_check()
    contract_self_check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--checkout")
    parser.add_argument("--cleanup-evidence", default="")
    parser.add_argument("--workflow-url", required=False, default="")
    parser.add_argument("--artifact-url", required=False, default="")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--evidence", default="journey/evidence.json")
    subparsers = parser.add_subparsers(dest="command")
    plan = subparsers.add_parser("plan")
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--template", required=True)
    plan.add_argument("--owner")
    plan.add_argument("--runtime")
    plan.add_argument("--output", required=True)
    collect = subparsers.add_parser("collect")
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--repository", required=True)
    collect.add_argument("--runtime")
    collect.add_argument("--stage-dir", required=True)
    collect.add_argument("--output", required=True)
    collect.add_argument("--workflow-url")
    args = parser.parse_args()
    try:
        if args.self_check:
            self_check()
            return
        if args.input or args.checkout or args.cleanup_evidence or args.artifact_url:
            if not args.input or not args.checkout or not args.workflow_url:
                parser.error("--input, --checkout, and --workflow-url are required")
            try:
                data = load_input(args.input)
            except JourneyError as error:
                write_evidence(args.evidence, {
                    "schema_version": ENVELOPE_VERSION, "status": "failed",
                    "failure": {"boundary": error.boundary, "code": error.code, "message": safe_text(error)},
                    "run": {"id": None, "workflow_url": args.workflow_url}, "assertions": [], "checks": [], "tests": [],
                    "cleanup": {"status": "unknown", "deleted": [], "failures": ["cleanup could not be evaluated"]},
                    "artifact_url": args.artifact_url or None,
                })
                raise
            try:
                result = assert_journey(data, args.checkout, os.environ.get(args.token_env, ""), args.workflow_url,
                                        args.artifact_url, args.cleanup_evidence)
            except (JourneyError, OSError, TypeError, ValueError) as error:
                if not isinstance(error, JourneyError):
                    error = JourneyError("source-readback", "assertion_unavailable", "journey assertion was unavailable")
                result = {
                    "schema_version": ENVELOPE_VERSION, "status": "failed",
                    "failure": {"boundary": error.boundary, "code": error.code, "message": safe_text(error)},
                    "run": {"id": data.get("run_id") if isinstance(data, dict) else None, "workflow_url": args.workflow_url},
                    "assertions": [], "checks": [], "tests": [],
                    "cleanup": {"status": "unknown", "deleted": [], "failures": ["cleanup could not be evaluated"]},
                    "artifact_url": args.artifact_url or None,
                }
                write_evidence(args.evidence, result)
                raise
            write_evidence(args.evidence, result)
            if result["status"] != "passed":
                raise JourneyError(result["failure"]["boundary"], result["failure"]["code"], result["failure"]["message"])
        elif args.command == "plan":
            write_json(args.output, contract_plan(args.run_id, args.template, args.owner or "", args.runtime or ""))
        elif args.command == "collect":
            write_json(args.output, aggregate(args.stage_dir, args.run_id, args.repository, args.runtime, args.workflow_url))
            if read_json(args.output)["result"] != "passed":
                raise JourneyError("real-agent journey failed", "journey_failed")
        else:
            parser.error("a command or --self-check is required")
    except (JourneyError, OSError, ValueError) as error:
        sys.exit(safe_text(error))


if __name__ == "__main__":
    main()
