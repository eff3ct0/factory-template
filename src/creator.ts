import { createHash } from "node:crypto";
import {
  chmod,
  copyFile,
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

export const CREATOR_SCHEMA_VERSION = 1;
export const CREATOR_DIRECTORY = ".factory-template-creator";
export const STATE_FILE = `${CREATOR_DIRECTORY}/state.json`;
export const STAGING_DIRECTORY = `${CREATOR_DIRECTORY}/.staging`;

const MAX_STATE_FILES = 10_000;
const MAX_STATE_BYTES = 10 * 1024 * 1024;

type JsonObject = Record<string, unknown>;

export interface PayloadFile {
  path: string;
  mode: string;
  size: number;
  sha256: string;
}

export interface PayloadManifest {
  schema_version: number;
  package_name: string;
  package_version: string;
  payload_version: string;
  files: PayloadFile[];
  payload_digest: string;
}

interface Placeholder {
  key: string;
  prompt: string;
  default: string;
  required: boolean;
  enum?: string[];
}

interface PlaceholderManifest {
  placeholders: Placeholder[];
}

interface CreatorState {
  schema_version: number;
  payload_version: string;
  payload_digest: string;
  config_digest: string;
  owned_files: Array<Pick<PayloadFile, "path" | "mode" | "size" | "sha256">>;
}

export interface CreatorConfig {
  values: Record<string, string>;
  digest: string;
}

export type Command = "plan" | "dry-run" | "apply" | "verify" | "doctor";
export type OperationAction = "create" | "update" | "noop" | "conflict";

export interface Diagnostic {
  code: string;
  message: string;
  path?: string;
}

export interface Operation {
  path: string;
  action: OperationAction;
  mode: string;
  size: number;
  sha256: string;
  reason: string;
}

export interface CreatorEnvelope {
  schema_version: number;
  command: Command;
  status: string;
  target: string;
  payload: {
    version: string;
    digest: string;
  };
  config_digest?: string;
  operations: Operation[];
  diagnostics: Diagnostic[];
  rollback?: {
    attempted: boolean;
    restored: boolean;
    message: string;
  };
}

export interface CreatorOptions {
  command: Command;
  target: string;
  configPath?: string;
  nonInteractive?: boolean;
  prompt?: (placeholder: Placeholder) => Promise<string>;
  failAfter?: number;
  interruptAfter?: number;
}

interface PlannedFile {
  relativePath: string;
  bytes: Buffer;
  mode: number;
  sha256: string;
  size: number;
}

export interface PreparedPlan {
  envelope: CreatorEnvelope;
  target: string;
  targetExisted: boolean;
  files: PlannedFile[];
  stateBytes?: Buffer;
  config?: CreatorConfig;
  state?: CreatorState;
  failAfter?: number;
  interruptAfter?: number;
}

export class CreatorError extends Error {
  readonly code: string;
  readonly path?: string;
  readonly plan?: PreparedPlan;
  readonly preserveStaging: boolean;

  constructor(code: string, message: string, options: { path?: string; plan?: PreparedPlan; preserveStaging?: boolean } = {}) {
    super(message);
    this.name = "CreatorError";
    this.code = code;
    this.path = options.path;
    this.plan = options.plan;
    this.preserveStaging = options.preserveStaging ?? false;
  }
}

const sha256 = (bytes: Uint8Array): string => createHash("sha256").update(bytes).digest("hex");
const canonicalJson = (value: unknown): Buffer => Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
const normalizePath = (value: string): string => value.replaceAll(path.sep, "/");
const compareStrings = (left: string, right: string): number => left < right ? -1 : left > right ? 1 : 0;

const diagnostic = (code: string, message: string, relativePath?: string): Diagnostic => ({
  code,
  message,
  ...(relativePath ? { path: relativePath } : {}),
});

const sortDiagnostics = (items: Diagnostic[]): Diagnostic[] => [...items].sort((left, right) =>
  compareStrings(`${left.code}\0${left.path ?? ""}\0${left.message}`, `${right.code}\0${right.path ?? ""}\0${right.message}`));

const sortOperations = (items: Operation[]): Operation[] => [...items].sort((left, right) => compareStrings(left.path, right.path));

const isObject = (value: unknown): value is JsonObject => typeof value === "object" && value !== null && !Array.isArray(value);

const parseJson = async (filePath: string, label: string): Promise<unknown> => {
  try {
    return JSON.parse(await readFile(filePath, "utf8"));
  } catch (error) {
    throw new CreatorError("invalid_json", `${label} is not valid JSON: ${(error as Error).message}`, { path: filePath });
  }
};

const assertSafeRelative = (relativePath: string): void => {
  const normalized = relativePath.replaceAll("\\", "/");
  if (!normalized || normalized.startsWith("/") || normalized.split("/").includes("..") || normalized.includes("\0")) {
    throw new CreatorError("unsafe_path", `unsafe creator path: ${relativePath}`, { path: relativePath });
  }
};

const resolveTarget = async (rawTarget: string): Promise<{ absolute: string; existed: boolean }> => {
  if (!rawTarget || rawTarget === "-") throw new CreatorError("invalid_target", "a target directory is required");
  if (rawTarget.replaceAll("\\", "/").split("/").includes("..")) {
    throw new CreatorError("target_traversal", "target path traversal is not allowed", { path: rawTarget });
  }
  const absolute = path.resolve(process.cwd(), rawTarget);
  let entry;
  try {
    entry = await lstat(absolute);
  } catch {
    entry = undefined;
  }
  if (entry?.isSymbolicLink()) throw new CreatorError("target_symlink", "target directory must not be a symlink", { path: rawTarget });
  if (entry && !entry.isDirectory()) throw new CreatorError("target_not_directory", "target exists but is not a directory", { path: rawTarget });
  if (entry && (entry.mode & 0o222) === 0) throw new CreatorError("target_unwritable", "target directory has no write permission", { path: rawTarget });

  let parent = absolute;
  while (true) {
    try {
      const parentEntry = await lstat(parent);
      if (!parentEntry.isDirectory() || parentEntry.isSymbolicLink()) {
        throw new CreatorError("target_symlink", "target parent must not contain a symlink", { path: parent });
      }
      if ((parentEntry.mode & 0o222) === 0) throw new CreatorError("target_unwritable", "target parent has no write permission", { path: parent });
      const realParent = await realpath(parent);
      if (realParent !== path.resolve(parent)) {
        throw new CreatorError("target_symlink", "target parent resolves outside its lexical path", { path: parent });
      }
      break;
    } catch (error) {
      if (error instanceof CreatorError) throw error;
      const next = path.dirname(parent);
      if (next === parent) throw new CreatorError("invalid_target", "target parent cannot be resolved", { path: absolute });
      parent = next;
    }
  }
  return { absolute, existed: Boolean(entry) };
};

const loadManifest = async (): Promise<{ manifest: PayloadManifest; payloadRoot: string; placeholders: PlaceholderManifest }> => {
  const payloadRoot = path.join(__dirname, "payload");
  const manifest = await parseJson(path.join(__dirname, "payload-manifest.json"), "payload manifest") as PayloadManifest;
  if (!isObject(manifest) || !Array.isArray(manifest.files) || typeof manifest.payload_digest !== "string") {
    throw new CreatorError("payload_invalid", "payload manifest has an unsupported shape");
  }
  const files = [...manifest.files].sort((left, right) => compareStrings(left.path, right.path));
  if (JSON.stringify(files) !== JSON.stringify(manifest.files)) {
    throw new CreatorError("payload_invalid", "payload manifest files are not in stable order");
  }
  const expectedDigest = `sha256:${sha256(Buffer.from(JSON.stringify({ payload_version: manifest.payload_version, files: manifest.files })))}`;
  if (expectedDigest !== manifest.payload_digest) {
    throw new CreatorError("payload_invalid", "payload manifest digest does not match its files");
  }
  const placeholders = await parseJson(path.join(payloadRoot, "placeholders.json"), "placeholder manifest") as PlaceholderManifest;
  if (!isObject(placeholders) || !Array.isArray(placeholders.placeholders)) {
    throw new CreatorError("payload_invalid", "placeholder manifest has an unsupported shape");
  }
  return { manifest, payloadRoot, placeholders };
};

const valueFromInput = (input: unknown): Record<string, unknown> => {
  if (!isObject(input)) throw new CreatorError("configuration_invalid", "configuration must be a JSON object");
  const candidate = input.values ?? input.answers ?? input;
  if (!isObject(candidate)) throw new CreatorError("configuration_invalid", "configuration values must be a JSON object");
  return candidate;
};

const validateValue = (placeholder: Placeholder, rawValue: unknown): string => {
  if (rawValue === undefined || rawValue === null) return "";
  if (!["string", "number", "boolean"].includes(typeof rawValue)) {
    throw new CreatorError("configuration_invalid", `${placeholder.key} must be a string, number, or boolean`);
  }
  const value = String(rawValue);
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value)) {
    throw new CreatorError("configuration_invalid", `${placeholder.key} contains a control character`);
  }
  if (placeholder.enum && !placeholder.enum.includes(value)) {
    throw new CreatorError("configuration_invalid", `${placeholder.key} must be one of: ${placeholder.enum.join(", ")}`);
  }
  return value;
};

const resolveConfig = async (
  manifest: PlaceholderManifest,
  configPath: string | undefined,
  nonInteractive: boolean,
  prompt: ((placeholder: Placeholder) => Promise<string>) | undefined,
): Promise<CreatorConfig> => {
  let input: Record<string, unknown> = {};
  if (configPath) input = valueFromInput(await parseJson(path.resolve(process.cwd(), configPath), "configuration"));
  const values: Record<string, string> = {};
  for (const placeholder of manifest.placeholders) {
    const supplied = Object.prototype.hasOwnProperty.call(input, placeholder.key);
    const raw = supplied ? input[placeholder.key] : placeholder.default;
    if (!supplied && (raw === undefined || raw === null || raw === "") && placeholder.required) {
      values[placeholder.key] = "";
    } else {
      values[placeholder.key] = validateValue(placeholder, raw);
    }
  }

  const missing = (): Placeholder[] => manifest.placeholders.filter((placeholder) => placeholder.required && !values[placeholder.key]);
  if (missing().length > 0 && !nonInteractive && prompt) {
    for (const placeholder of missing()) values[placeholder.key] = validateValue(placeholder, await prompt(placeholder));
  }
  const missingKeys = missing().map((placeholder) => placeholder.key);
  if (missingKeys.length > 0) {
    throw new CreatorError("incomplete_configuration", `required configuration is missing: ${missingKeys.join(", ")}`);
  }
  if (values.FACTORY_REQUIRED === "true" && !values.FACTORY_SPEC) {
    throw new CreatorError("incomplete_configuration", "FACTORY_SPEC is required when FACTORY_REQUIRED is true");
  }
  const ordered = Object.fromEntries(Object.keys(values).sort().map((key) => [key, values[key]]));
  return { values, digest: `sha256:${sha256(canonicalJson(ordered))}` };
};

const renderPayload = (bytes: Buffer, config: CreatorConfig): Buffer => {
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) return bytes;
  let rendered = text;
  for (const [key, value] of Object.entries(config.values)) rendered = rendered.replaceAll(`<${key}>`, () => value);
  return Buffer.from(rendered, "utf8");
};

const safeMode = (mode: string): number => {
  const parsed = Number.parseInt(mode.slice(-4), 8);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > 0o7777) throw new CreatorError("payload_invalid", `invalid payload mode: ${mode}`);
  return parsed;
};

const readPayloadFiles = async (manifest: PayloadManifest, payloadRoot: string, config: CreatorConfig): Promise<PlannedFile[]> => {
  if (manifest.files.length > MAX_STATE_FILES) throw new CreatorError("payload_invalid", "payload contains too many files");
  const files: PlannedFile[] = [];
  for (const entry of manifest.files) {
    assertSafeRelative(entry.path);
    const source = path.join(payloadRoot, entry.path);
    const sourceStat = await lstat(source).catch(() => undefined);
    if (!sourceStat?.isFile() || sourceStat.isSymbolicLink()) throw new CreatorError("payload_invalid", `payload file is not a regular file: ${entry.path}`, { path: entry.path });
    const sourceBytes = await readFile(source);
    const sourceMode = sourceStat.mode & 0o7777;
    if (sourceBytes.byteLength !== entry.size || sha256(sourceBytes) !== entry.sha256 || sourceMode !== safeMode(entry.mode)) {
      throw new CreatorError("payload_mismatch", `packaged payload bytes or mode differ from the manifest: ${entry.path}`, { path: entry.path });
    }
    const bytes = renderPayload(sourceBytes, config);
    files.push({ relativePath: entry.path, bytes, mode: safeMode(entry.mode), sha256: sha256(bytes), size: bytes.byteLength });
  }
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  if (totalBytes > MAX_STATE_BYTES) throw new CreatorError("payload_invalid", "payload exceeds the bounded creator state limit");
  return files;
};

const readState = async (target: string): Promise<CreatorState | undefined> => {
  const statePath = path.join(target, STATE_FILE);
  try {
    const raw = await readFile(statePath, "utf8");
    let value: CreatorState;
    try {
      value = JSON.parse(raw) as CreatorState;
    } catch (error) {
      throw new CreatorError("state_invalid", `creator state is not valid JSON: ${(error as Error).message}`, { path: STATE_FILE });
    }
    if (!isObject(value) || value.schema_version !== CREATOR_SCHEMA_VERSION || !Array.isArray(value.owned_files)) {
      throw new CreatorError("state_invalid", "creator state has an unsupported shape", { path: STATE_FILE });
    }
    if (value.owned_files.length > MAX_STATE_FILES) throw new CreatorError("state_invalid", "creator state lists too many files", { path: STATE_FILE });
    return value;
  } catch (error) {
    if (error instanceof CreatorError) throw error;
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    return undefined;
  }
};

const creatorDirectoryStatus = async (target: string): Promise<{ safe: boolean; diagnostic?: Diagnostic }> => {
  const creatorPath = path.join(target, CREATOR_DIRECTORY);
  const entry = await lstat(creatorPath).catch(() => undefined);
  if (!entry) return { safe: true };
  if (entry.isSymbolicLink()) {
    return { safe: false, diagnostic: diagnostic("symlink_escape", "creator state directory must not be a symlink", CREATOR_DIRECTORY) };
  }
  if (!entry.isDirectory()) {
    return { safe: false, diagnostic: diagnostic("path_conflict", "creator state path must be a directory", CREATOR_DIRECTORY) };
  }
  return { safe: true };
};

const collectEntries = async (directory: string, relative = ""): Promise<Array<{ path: string; directory: boolean; symlink: boolean }>> => {
  const result: Array<{ path: string; directory: boolean; symlink: boolean }> = [];
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((left, right) => compareStrings(left.name, right.name))) {
    const entryPath = normalizePath(path.posix.join(relative, entry.name));
    if (entry.isSymbolicLink()) result.push({ path: entryPath, directory: false, symlink: true });
    else if (entry.isDirectory()) {
      result.push({ path: entryPath, directory: true, symlink: false });
      result.push(...await collectEntries(path.join(directory, entry.name), entryPath));
    } else result.push({ path: entryPath, directory: false, symlink: false });
  }
  return result;
};

const parentPaths = (relativePath: string): Set<string> => {
  const result = new Set<string>();
  let current = path.posix.dirname(relativePath);
  while (current && current !== ".") {
    result.add(current);
    current = path.posix.dirname(current);
  }
  return result;
};

const fileOperation = (file: PlannedFile, action: OperationAction, reason: string): Operation => ({
  path: file.relativePath,
  action,
  mode: file.mode.toString(8).padStart(4, "0"),
  size: file.size,
  sha256: file.sha256,
  reason,
});

const stateBytesFor = (manifest: PayloadManifest, config: CreatorConfig, files: PlannedFile[]): Buffer => canonicalJson({
  schema_version: CREATOR_SCHEMA_VERSION,
  payload_version: manifest.payload_version,
  payload_digest: manifest.payload_digest,
  config_digest: config.digest,
  owned_files: files.map((file) => ({
    path: file.relativePath,
    mode: file.mode.toString(8).padStart(4, "0"),
    size: file.size,
    sha256: file.sha256,
  })),
});

const expectedStateFile = (stateBytes: Buffer): PlannedFile => ({
  relativePath: STATE_FILE,
  bytes: stateBytes,
  mode: 0o600,
  sha256: sha256(stateBytes),
  size: stateBytes.byteLength,
});

const baseEnvelope = (command: Command, target: string, manifest: PayloadManifest): CreatorEnvelope => ({
  schema_version: CREATOR_SCHEMA_VERSION,
  command,
  status: "planned",
  target,
  payload: { version: manifest.payload_version, digest: manifest.payload_digest },
  operations: [],
  diagnostics: [],
});

const addPathDiagnostics = async (target: string, relativePath: string, diagnostics: Diagnostic[]): Promise<void> => {
  const segments = relativePath.split("/");
  let current = target;
  for (const segment of segments.slice(0, -1)) {
    current = path.join(current, segment);
    const entry = await lstat(current).catch(() => undefined);
    if (entry?.isSymbolicLink()) diagnostics.push(diagnostic("symlink_escape", "payload parent is a symlink", relativePath));
    else if (entry && !entry.isDirectory()) diagnostics.push(diagnostic("path_conflict", "payload parent is not a directory", relativePath));
  }
};

export const preparePlan = async (options: CreatorOptions): Promise<PreparedPlan> => {
  const { manifest, payloadRoot, placeholders } = await loadManifest();
  const target = await resolveTarget(options.target);
  const envelope = baseEnvelope(options.command, target.absolute, manifest);
  const diagnostics: Diagnostic[] = [];
  const creatorDirectory = await creatorDirectoryStatus(target.absolute);
  if (creatorDirectory.diagnostic) diagnostics.push(creatorDirectory.diagnostic);

  if (target.existed) {
    const entries = await collectEntries(target.absolute);
    const staging = entries.find((entry) => entry.path === STAGING_DIRECTORY || entry.path.startsWith(`${STAGING_DIRECTORY}/`));
    if (staging) diagnostics.push(diagnostic("staging_interrupted", "an interrupted staging directory requires recovery", STAGING_DIRECTORY));
    const state = creatorDirectory.safe ? await readState(target.absolute) : undefined;
    const knownStatePaths = new Set([CREATOR_DIRECTORY, STATE_FILE, STAGING_DIRECTORY]);
    const manifestPaths = new Set(manifest.files.map((file) => file.path));
    const allowedDirectories = new Set<string>([CREATOR_DIRECTORY, ...manifest.files.flatMap((file) => [...parentPaths(file.path)])]);
    for (const entry of entries) {
      if (knownStatePaths.has(entry.path) || entry.path.startsWith(`${STAGING_DIRECTORY}/`)) continue;
      if (entry.directory && allowedDirectories.has(entry.path)) continue;
      if (manifestPaths.has(entry.path) && state?.owned_files.some((file) => file.path === entry.path)) continue;
      diagnostics.push(diagnostic("unknown_file_conflict", "target contains a file or directory not owned by the creator", entry.path));
    }
    if (state && (state.payload_version !== manifest.payload_version || state.payload_digest !== manifest.payload_digest)) {
      diagnostics.push(diagnostic("payload_mismatch", "creator state does not match the packaged payload"));
    }
    if (!state && entries.some((entry) => entry.path !== CREATOR_DIRECTORY && !entry.path.startsWith(`${CREATOR_DIRECTORY}/`))) {
      diagnostics.push(diagnostic("unknown_file_conflict", "a non-empty target has no creator ownership state"));
    }
  }

  let config: CreatorConfig | undefined;
  try {
    config = await resolveConfig(placeholders, options.configPath, options.nonInteractive ?? false, options.prompt);
  } catch (error) {
    if (error instanceof CreatorError) diagnostics.push(diagnostic(error.code, error.message, error.path));
    else throw error;
  }
  envelope.config_digest = config?.digest;

  if (!config) {
    envelope.status = "error";
    envelope.diagnostics = sortDiagnostics(diagnostics);
    return { envelope, target: target.absolute, targetExisted: target.existed, files: [], failAfter: options.failAfter, interruptAfter: options.interruptAfter };
  }

  const files = await readPayloadFiles(manifest, payloadRoot, config);
  const state = target.existed && creatorDirectory.safe ? await readState(target.absolute) : undefined;
  const oldOwned = new Map((state?.owned_files ?? []).map((file) => [file.path, file]));
  const operations: Operation[] = [];
  for (const file of files) {
    await addPathDiagnostics(target.absolute, file.relativePath, diagnostics);
    const destination = path.join(target.absolute, file.relativePath);
    const existing = await lstat(destination).catch(() => undefined);
    const owned = oldOwned.get(file.relativePath);
    if (existing?.isSymbolicLink()) {
      operations.push(fileOperation(file, "conflict", "symlink"));
      diagnostics.push(diagnostic("owned_file_drift", "owned payload path is a symlink", file.relativePath));
      continue;
    }
    if (existing && !existing.isFile()) {
      operations.push(fileOperation(file, "conflict", "path-conflict"));
      diagnostics.push(diagnostic("owned_file_drift", "owned payload path is not a regular file", file.relativePath));
      continue;
    }
    if (!existing) {
      operations.push(fileOperation(file, "create", "missing"));
      continue;
    }
    const existingBytes = await readFile(destination);
    const same = sha256(existingBytes) === file.sha256 && (existing.mode & 0o7777) === file.mode;
    if (same) operations.push(fileOperation(file, "noop", "unchanged"));
    else if (owned && state?.config_digest !== config.digest && owned.sha256 === sha256(existingBytes)) {
      operations.push(fileOperation(file, "update", "configuration-changed"));
    } else {
      operations.push(fileOperation(file, "conflict", "owned-file-drift"));
      diagnostics.push(diagnostic("owned_file_drift", "owned file bytes or mode differ from the planned output", file.relativePath));
    }
  }

  const stateBytes = stateBytesFor(manifest, config, files);
  const stateFile = expectedStateFile(stateBytes);
  const existingState = await lstat(path.join(target.absolute, STATE_FILE)).catch(() => undefined);
  const stateSame = existingState?.isFile() && sha256(await readFile(path.join(target.absolute, STATE_FILE))) === stateFile.sha256;
  operations.push(fileOperation(stateFile, stateSame ? "noop" : (existingState ? "update" : "create"), stateSame ? "unchanged" : "state"));

  const sortedOperations = sortOperations(operations);
  const hasConflict = diagnostics.some((item) => ["unknown_file_conflict", "staging_interrupted", "payload_mismatch", "symlink_escape", "path_conflict"].includes(item.code)) || sortedOperations.some((operation) => operation.action === "conflict");
  envelope.operations = sortedOperations;
  envelope.diagnostics = sortDiagnostics(diagnostics);
  envelope.status = hasConflict ? "conflict" : sortedOperations.every((operation) => operation.action === "noop") ? "noop" : options.command === "dry-run" ? "dry-run" : "planned";
  return { envelope, target: target.absolute, targetExisted: target.existed, files: [...files, stateFile], stateBytes, config, state, failAfter: options.failAfter, interruptAfter: options.interruptAfter };
};

const pathFor = (target: string, relativePath: string): string => path.join(target, relativePath);

const removeEmptyParents = async (target: string, relativePath: string): Promise<void> => {
  let current = path.dirname(pathFor(target, relativePath));
  const stop = path.resolve(target);
  while (current !== stop && current.startsWith(`${stop}${path.sep}`)) {
    try {
      await rm(current, { recursive: false });
    } catch {
      break;
    }
    current = path.dirname(current);
  }
};

export const applyPlan = async (prepared: PreparedPlan): Promise<CreatorEnvelope> => {
  if (prepared.envelope.status === "conflict" || prepared.envelope.status === "error") {
    throw new CreatorError("plan_rejected", "apply refused a plan containing conflicts or errors", { plan: prepared });
  }
  if (prepared.envelope.status === "noop") return { ...prepared.envelope, command: "apply", status: "noop" };
  const stagingPath = pathFor(prepared.target, STAGING_DIRECTORY);
  const backupPath = path.join(stagingPath, "backup");
  const changed = prepared.envelope.operations.filter((operation) => operation.action === "create" || operation.action === "update");
  const existedBefore = new Map<string, boolean>();
  let committed = 0;
  try {
    await mkdir(prepared.target, { recursive: true });
    const creatorDirectory = await creatorDirectoryStatus(prepared.target);
    if (!creatorDirectory.safe) {
      throw new CreatorError(creatorDirectory.diagnostic?.code ?? "path_conflict", creatorDirectory.diagnostic?.message ?? "creator state path is unsafe", { plan: prepared, path: CREATOR_DIRECTORY });
    }
    await mkdir(path.dirname(stagingPath), { recursive: true });
    await mkdir(stagingPath);
    await mkdir(backupPath);
    for (const file of prepared.files) {
      const staged = pathFor(stagingPath, file.relativePath);
      await mkdir(path.dirname(staged), { recursive: true });
      await writeFile(staged, file.bytes, { mode: file.mode });
      await chmod(staged, file.mode);
      const stagedStat = await stat(staged);
      if (stagedStat.size !== file.size || sha256(await readFile(staged)) !== file.sha256 || (stagedStat.mode & 0o7777) !== file.mode) {
        throw new CreatorError("staging_verification_failed", `staged bytes or mode differ for ${file.relativePath}`, { path: file.relativePath });
      }
    }
    if (prepared.interruptAfter) {
      throw new CreatorError("staging_interrupted", "injected interruption left staging for doctor recovery", { plan: prepared, preserveStaging: true });
    }
    for (const operation of changed) {
      const destination = pathFor(prepared.target, operation.path);
      const staged = pathFor(stagingPath, operation.path);
      const backup = pathFor(backupPath, operation.path);
      const current = await lstat(destination).catch(() => undefined);
      existedBefore.set(operation.path, Boolean(current));
      if (current) {
        await mkdir(path.dirname(backup), { recursive: true });
        await copyFile(destination, backup);
        await chmod(backup, current.mode & 0o7777);
      }
      await mkdir(path.dirname(destination), { recursive: true });
      await rename(staged, destination);
      committed += 1;
      if (prepared.failAfter && committed >= prepared.failAfter) {
        throw new CreatorError("injected_failure", `injected failure after ${committed} committed operation(s)`, { plan: prepared });
      }
    }
    await rm(stagingPath, { recursive: true, force: true });
    return { ...prepared.envelope, command: "apply", status: changed.length === 0 ? "noop" : "applied" };
  } catch (error) {
    if (error instanceof CreatorError && error.preserveStaging) throw error;
    let restored = true;
    for (const operation of changed.slice(0, committed).reverse()) {
      const destination = pathFor(prepared.target, operation.path);
      const backup = pathFor(backupPath, operation.path);
      try {
        if (existedBefore.get(operation.path)) {
          await rm(destination, { force: true });
          await rename(backup, destination);
        } else {
          await unlink(destination);
          await removeEmptyParents(prepared.target, operation.path);
        }
      } catch {
        restored = false;
      }
    }
    try {
      await rm(stagingPath, { recursive: true, force: true });
      if (!prepared.targetExisted) await rm(prepared.target, { recursive: true, force: true });
    } catch {
      restored = false;
    }
    const wrapped = error instanceof CreatorError ? error : new CreatorError("apply_failed", (error as Error).message);
    throw new CreatorError(wrapped.code, wrapped.message, { plan: prepared, preserveStaging: !restored });
  }
};

export const doctor = async (prepared: PreparedPlan): Promise<CreatorEnvelope> => {
  const diagnostics = [...prepared.envelope.diagnostics];
  if (prepared.envelope.config_digest === undefined) diagnostics.push(diagnostic("incomplete_configuration", "doctor could not validate the target without complete configuration"));
  return {
    ...prepared.envelope,
    command: "doctor",
    status: diagnostics.length > 0 ? "unhealthy" : "healthy",
    diagnostics: sortDiagnostics(diagnostics),
  };
};

export const verify = async (prepared: PreparedPlan): Promise<CreatorEnvelope> => {
  const missing = prepared.envelope.operations.some((operation) => operation.action === "create" || operation.action === "update");
  const conflict = prepared.envelope.status === "conflict" || prepared.envelope.status === "error";
  return {
    ...prepared.envelope,
    command: "verify",
    status: conflict ? "failed" : missing ? "not-created" : "verified",
  };
};

export const envelopeJson = (envelope: CreatorEnvelope): string => `${JSON.stringify({
  schema_version: envelope.schema_version,
  command: envelope.command,
  status: envelope.status,
  target: envelope.target,
  payload: envelope.payload,
  ...(envelope.config_digest ? { config_digest: envelope.config_digest } : {}),
  operations: sortOperations(envelope.operations),
  diagnostics: sortDiagnostics(envelope.diagnostics),
  ...(envelope.rollback ? { rollback: envelope.rollback } : {}),
}, null, 2)}\n`;

export const errorEnvelope = (command: Command, target: string, error: CreatorError): CreatorEnvelope => {
  const envelope = error.plan?.envelope ?? {
    schema_version: CREATOR_SCHEMA_VERSION,
    command,
    status: "error",
    target,
    payload: { version: "unknown", digest: "unknown" },
    operations: [],
    diagnostics: [],
  };
  return {
    ...envelope,
    command,
    status: "error",
    diagnostics: sortDiagnostics([...envelope.diagnostics, diagnostic(error.code, error.message, error.path)]),
    rollback: error.plan ? { attempted: true, restored: !error.preserveStaging, message: error.preserveStaging ? "staging was preserved for doctor recovery" : "creator-owned changes were rolled back" } : undefined,
  };
};
