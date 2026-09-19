import { createHash } from "node:crypto";
import { chmod, copyFile, mkdir, readFile, readdir, rm, stat, lstat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const PAYLOAD_VERSION = "1.0.0";

const canonicalJson = (value) => `${JSON.stringify(value, null, 2)}\n`;

const fail = (message) => {
  throw new Error(`payload packaging failed: ${message}`);
};

const normalizeRelative = (value) => {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0")) {
    fail(`invalid payload path: ${String(value)}`);
  }
  const normalized = value.replaceAll("\\", "/");
  if (path.posix.isAbsolute(normalized) || normalized.split("/").includes("..")) {
    fail(`payload path escapes the repository root: ${value}`);
  }
  return normalized;
};

const resolvePayloadPath = async (root, relative) => {
  const normalized = normalizeRelative(relative);
  const absolute = path.resolve(root, normalized);
  const relativeToRoot = path.relative(root, absolute);
  if (relativeToRoot.startsWith("..") || path.isAbsolute(relativeToRoot)) {
    fail(`payload path escapes the repository root: ${relative}`);
  }
  const entry = await lstat(absolute).catch(() => fail(`expected payload file is missing: ${normalized}`));
  if (!entry.isFile()) fail(`payload path is not a regular file: ${normalized}`);
  const realRoot = await stat(root).then(() => path.resolve(root));
  const realPath = path.resolve(absolute);
  if (realPath !== realRoot && !realPath.startsWith(`${realRoot}${path.sep}`)) {
    fail(`payload path escapes the repository root: ${normalized}`);
  }
  return { absolute, normalized, entry };
};

const walkFiles = async (root, directory, result) => {
  const absolute = path.join(root, directory);
  const entries = await readdir(absolute, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    const relative = path.posix.join(directory.replaceAll(path.sep, "/"), entry.name);
    if (entry.isDirectory()) {
      await walkFiles(root, relative, result);
    } else {
      result.add(relative);
    }
  }
};

export const payloadCandidates = async (root, ownership) => {
  const candidates = new Set();
  for (const category of Object.values(ownership.categories)) {
    if (category.disposition !== "inherited") continue;
    for (const entry of category.paths) {
      const relative = normalizeRelative(entry.path);
      if (entry.kind === "directory") await walkFiles(root, relative, candidates);
      else if (await lstat(path.join(root, relative)).then(() => true).catch(() => false)) {
        candidates.add(relative);
      }
    }
  }
  candidates.add("placeholders.json");
  candidates.add("archetype-ownership.json");
  return [...candidates].sort();
};

export const validateDeclaredPaths = (declared, expected) => {
  const seen = new Set();
  for (const relative of declared) {
    const normalized = normalizeRelative(relative);
    if (seen.has(normalized)) fail(`payload path is declared more than once: ${normalized}`);
    seen.add(normalized);
  }
  const expectedSet = new Set(expected);
  const missingDeclarations = expected.filter((relative) => !seen.has(relative));
  if (missingDeclarations.length > 0) {
    fail(`undeclared payload file is present: ${missingDeclarations.join(", ")}`);
  }
  const missingFiles = declared.filter((relative) => !expectedSet.has(relative));
  if (missingFiles.length > 0) {
    fail(`expected payload file is missing: ${missingFiles.join(", ")}`);
  }
};

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

export const manifestDigest = (payloadVersion, files) =>
  `sha256:${sha256(Buffer.from(JSON.stringify({ payload_version: payloadVersion, files })))}`;

export const assertManifestMatchesLock = (manifest, lock) => {
  if (canonicalJson(manifest) !== canonicalJson(lock)) {
    fail("payload manifest or digest differs from the checked-in integrity contract");
  }
};

export const buildPayload = async ({ root = ROOT, dist = path.join(root, "dist"), writeLock = false } = {}) => {
  const packageJson = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  const ownership = JSON.parse(await readFile(path.join(root, "archetype-ownership.json"), "utf8"));
  const declaration = JSON.parse(await readFile(path.join(root, "package", "payload-files.json"), "utf8"));
  const expected = await payloadCandidates(root, ownership);
  const declared = declaration.paths;
  validateDeclaredPaths(declared, expected);

  const files = [];
  for (const relative of [...declared].sort()) {
    const source = await resolvePayloadPath(root, relative);
    const bytes = await readFile(source.absolute);
    files.push({
      path: source.normalized,
      mode: source.entry.mode.toString(8).padStart(4, "0"),
      size: bytes.byteLength,
      sha256: sha256(bytes),
    });
  }
  const manifest = {
    schema_version: 1,
    package_name: packageJson.name,
    package_version: packageJson.version,
    payload_version: declaration.payload_version,
    files,
    payload_digest: manifestDigest(declaration.payload_version, files),
  };
  if (manifest.payload_version !== PAYLOAD_VERSION) {
    fail(`unsupported payload version: ${manifest.payload_version}`);
  }

  const lockPath = path.join(root, "package", "payload-manifest.json");
  if (writeLock) await writeFile(lockPath, canonicalJson(manifest));
  else {
    const lock = JSON.parse(await readFile(lockPath, "utf8"));
    assertManifestMatchesLock(manifest, lock);
  }

  const payloadDirectory = path.join(dist, "payload");
  await rm(payloadDirectory, { recursive: true, force: true });
  await mkdir(payloadDirectory, { recursive: true });
  for (const file of files) {
    const source = path.join(root, file.path);
    const destination = path.join(payloadDirectory, file.path);
    await mkdir(path.dirname(destination), { recursive: true });
    await copyFile(source, destination);
    await chmod(destination, parseInt(file.mode, 8));
  }
  await writeFile(path.join(dist, "payload-manifest.json"), canonicalJson(manifest));
  return manifest;
};

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const writeLock = process.argv.includes("--write-lock");
  buildPayload({ writeLock }).then((manifest) => {
    process.stdout.write(`payload manifest ${manifest.payload_digest}\n`);
  }).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
