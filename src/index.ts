#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

interface PayloadManifest {
  package_name: string;
  package_version: string;
  payload_version: string;
  payload_digest: string;
}

const manifestPath = path.join(__dirname, "payload-manifest.json");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as PayloadManifest;
const args = new Set(process.argv.slice(2));

if (args.has("--version")) {
  if (args.has("--json")) {
    process.stdout.write(`${JSON.stringify({
      name: manifest.package_name,
      version: manifest.package_version,
      payloadVersion: manifest.payload_version,
      payloadDigest: manifest.payload_digest,
    })}\n`);
  } else {
    process.stdout.write(
      `${manifest.package_name} ${manifest.package_version}\n` +
      `payload ${manifest.payload_version}\n` +
      `payload digest ${manifest.payload_digest}\n`,
    );
  }
  process.exit(0);
}

if (args.has("--help")) {
  process.stdout.write("Usage: factory-template --version [--json]\n");
  process.exit(0);
}

process.stderr.write("Usage: factory-template --version [--json]\n");
process.exitCode = 1;
