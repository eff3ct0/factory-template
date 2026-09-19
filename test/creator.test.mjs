import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, readdir, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cli = path.join(root, "dist", "index.js");
const { preparePlan } = await import("../dist/creator.js");

const run = async (args, options = {}) => {
  try {
    const result = await execFileAsync(process.execPath, [cli, ...args], { cwd: root, ...options });
    return { ...result, code: 0 };
  } catch (error) {
    return { stdout: error.stdout ?? "", stderr: error.stderr ?? "", code: error.code };
  }
};

const configFile = async (directory, values = {}) => {
  const file = path.join(directory, "answers.json");
  await writeFile(file, JSON.stringify({ values: {
    PROJECT_NAME: "Example project",
    TASK_TRACKER: "github-issues",
    ...values,
  }}));
  return file;
};

const json = (result) => JSON.parse(result.stdout);

test("plan and dry-run are deterministic and do not mutate an empty target", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-plan-"));
  const target = path.join(parent, "project");
  const config = await configFile(parent);
  const first = await run(["plan", "--target", target, "--config", config, "--non-interactive"]);
  const second = await run(["plan", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(first.code, 0);
  assert.equal(first.stdout, second.stdout);
  const dryRun = await run(["dry-run", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(dryRun.code, 0);
  assert.equal(json(dryRun).status, "dry-run");
  await assert.rejects(readdir(target));
  assert.equal(json(first).schema_version, 1);
  assert.ok(json(first).operations.every((operation) => operation.path));
});

test("interactive configuration prompts for missing required values through the shared validator", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-prompt-"));
  const target = path.join(parent, "project");
  const prompted = [];
  const prepared = await preparePlan({
    command: "plan",
    target,
    prompt: async (placeholder) => {
      prompted.push(placeholder.key);
      return placeholder.key === "PROJECT_NAME" ? "Prompted project" : "github-issues";
    },
  });
  assert.deepEqual(prompted, ["PROJECT_NAME", "TASK_TRACKER"]);
  assert.equal(prepared.envelope.status, "planned");
  assert.ok(prepared.envelope.config_digest);

  const invalid = await configFile(parent, { TASK_TRACKER: "not-a-task-provider" });
  const rejected = await run(["plan", "--target", path.join(parent, "invalid"), "--config", invalid, "--non-interactive"]);
  assert.notEqual(rejected.code, 0);
  assert.ok(json(rejected).diagnostics.some((item) => item.code === "configuration_invalid"));
});

test("apply, verify, and rerun are idempotent", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-apply-"));
  const target = path.join(parent, "project");
  const config = await configFile(parent);
  const applied = await run(["apply", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(applied.code, 0, applied.stderr);
  const verified = await run(["verify", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(verified.code, 0, verified.stderr);
  assert.equal(json(verified).status, "verified");
  const rerun = await run(["apply", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(rerun.code, 0, rerun.stderr);
  assert.equal(json(rerun).status, "noop");
  assert.ok(json(rerun).operations.every((operation) => operation.action === "noop"));
});

test("unknown files and symlink escapes fail without overwriting", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-conflict-"));
  const target = path.join(parent, "project");
  const config = await configFile(parent);
  await mkdir(target);
  await writeFile(path.join(target, "unknown.txt"), "keep me");
  const conflict = await run(["apply", "--target", target, "--config", config, "--non-interactive"]);
  assert.notEqual(conflict.code, 0);
  assert.match(json(conflict).diagnostics.map((item) => item.code).join(" "), /unknown_file_conflict/);
  assert.equal(await readFile(path.join(target, "unknown.txt"), "utf8"), "keep me");

  const outside = path.join(parent, "outside");
  await mkdir(outside);
  const linked = path.join(parent, "linked");
  await symlink(outside, linked);
  const escaped = await run(["plan", "--target", linked, "--config", config, "--non-interactive"]);
  assert.notEqual(escaped.code, 0);
  assert.match(json(escaped).diagnostics[0].code, /target_symlink/);

  const unwritable = path.join(parent, "unwritable");
  await mkdir(unwritable);
  await chmod(unwritable, 0o555);
  const blocked = await run(["plan", "--target", unwritable, "--config", config, "--non-interactive"]);
  assert.notEqual(blocked.code, 0);
  assert.match(json(blocked).diagnostics[0].code, /target_unwritable/);
  await chmod(unwritable, 0o755);
});

test("rejects a symlinked creator state directory without writing outside the target", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-state-link-"));
  const target = path.join(parent, "project");
  const outside = path.join(parent, "outside");
  const config = await configFile(parent);
  await mkdir(target);
  await mkdir(outside);
  await symlink(outside, path.join(target, ".factory-template-creator"));
  const result = await run(["apply", "--target", target, "--config", config, "--non-interactive"]);
  assert.notEqual(result.code, 0);
  assert.ok(json(result).diagnostics.some((item) => item.code === "symlink_escape"));
  await assert.rejects(readFile(path.join(outside, "state.json")));
});

test("rollback restores creator-owned files after an injected commit failure", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-rollback-"));
  const target = path.join(parent, "project");
  const config = await configFile(parent);
  const initial = await run(["apply", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(initial.code, 0, initial.stderr);
  const before = await readFile(path.join(target, "README.md"));
  const changedConfig = await configFile(parent, { PROJECT_NAME: "Changed project" });
  const failed = await run(["apply", "--target", target, "--config", changedConfig, "--non-interactive", "--failure-after", "1"]);
  assert.notEqual(failed.code, 0);
  assert.match(json(failed).rollback.message, /rolled back/);
  assert.deepEqual(await readFile(path.join(target, "README.md")), before);
});

test("doctor reports owned drift and payload identity mismatch", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-drift-"));
  const target = path.join(parent, "project");
  const config = await configFile(parent);
  const applied = await run(["apply", "--target", target, "--config", config, "--non-interactive"]);
  assert.equal(applied.code, 0, applied.stderr);
  await writeFile(path.join(target, "README.md"), "external change\n");
  const drift = await run(["doctor", "--target", target, "--config", config, "--non-interactive"]);
  assert.notEqual(drift.code, 0);
  assert.ok(json(drift).diagnostics.some((item) => item.code === "owned_file_drift"));

  const statePath = path.join(target, ".factory-template-creator", "state.json");
  const state = JSON.parse(await readFile(statePath, "utf8"));
  state.payload_digest = "sha256:changed";
  await writeFile(statePath, JSON.stringify(state));
  const mismatch = await run(["doctor", "--target", target, "--config", config, "--non-interactive"]);
  assert.notEqual(mismatch.code, 0);
  assert.ok(json(mismatch).diagnostics.some((item) => item.code === "payload_mismatch"));
});

test("doctor reports interrupted staging and incomplete configuration", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "creator-doctor-"));
  const target = path.join(parent, "project");
  const config = await configFile(parent);
  const interrupted = await run(["apply", "--target", target, "--config", config, "--non-interactive", "--interrupt-after", "1"]);
  assert.notEqual(interrupted.code, 0);
  const doctor = await run(["doctor", "--target", target, "--non-interactive"]);
  assert.notEqual(doctor.code, 0);
  const codes = json(doctor).diagnostics.map((item) => item.code);
  assert.ok(codes.includes("staging_interrupted"));
  assert.ok(codes.includes("incomplete_configuration"));
});
