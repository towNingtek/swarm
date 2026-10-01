// Import-safe profile initialization: no processes, listeners, or top-level I/O.
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

export const PROFILE_MARKER = ".dsh-profile-manifest.json";
const FORMAT = 1;
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const migration = (reason) => new Error(`DSH profile migration required: ${reason}; existing data preserved. Back up and explicitly migrate the profile before restarting.`);

function inventory(root) {
  if (!fs.lstatSync(root).isDirectory()) throw new Error("Profile root must be a real directory");
  const entries = {};
  function walk(relative) {
    for (const name of fs.readdirSync(path.join(root, relative)).sort()) {
      const key = relative ? `${relative}/${name}` : name;
      if (key === PROFILE_MARKER) continue;
      const filename = path.join(root, key);
      const stat = fs.lstatSync(filename);
      if (stat.isSymbolicLink()) {
        const target = fs.readlinkSync(filename);
        const resolved = fs.realpathSync(filename);
        const inside = path.relative(fs.realpathSync(root), resolved);
        if (path.isAbsolute(target) || inside === ".." || inside.startsWith(`..${path.sep}`) || path.isAbsolute(inside)) {
          throw new Error(`Profile symlink escapes root: ${key}`);
        }
        entries[key] = { type: "symlink", target };
      } else if (stat.isDirectory()) {
        entries[key] = { type: "directory" };
        walk(key);
      } else if (stat.isFile()) {
        entries[key] = { type: "file", sha256: sha256(fs.readFileSync(filename)), executable: Boolean(stat.mode & 0o111) };
      } else {
        throw new Error(`Unsupported profile entry: ${key}`);
      }
    }
  }
  walk("");
  return entries;
}

function identity(manifest) {
  return sha256(JSON.stringify({ format: manifest.format, runtime: manifest.runtime, entries: manifest.entries }));
}

// Called explicitly during image build, after all dependency/vendor overlays.
export function writeProfileManifest(sourceDir, runtime) {
  if (typeof runtime !== "string" || !runtime.trim()) throw new Error("An explicit runtime version is required");
  const entries = inventory(sourceDir);
  if (!Object.keys(entries).length) throw new Error("Cannot seal an empty image profile");
  const manifest = { format: FORMAT, runtime, entries };
  manifest.hash = identity(manifest);
  fs.writeFileSync(path.join(sourceDir, PROFILE_MARKER), `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

function readManifest(root) {
  const marker = path.join(root, PROFILE_MARKER);
  if (!fs.lstatSync(marker).isFile()) throw new Error("Manifest must be a regular file");
  const manifest = JSON.parse(fs.readFileSync(marker, "utf8"));
  if (manifest.format !== FORMAT || typeof manifest.runtime !== "string" || !manifest.entries ||
      typeof manifest.entries !== "object" || Array.isArray(manifest.entries) ||
      !Object.keys(manifest.entries).length || manifest.hash !== identity(manifest)) {
    throw new Error("Invalid or unsupported profile manifest");
  }
  return manifest;
}

// Files DSH itself rewrites at runtime inside an installed profile: it rewrites
// the root config on every boot, and saves UI preferences into the patch. They
// are sealed in the image like everything else, but in an EXISTING profile only
// their presence as regular files is required; comparing their bytes would make
// every site refuse its second boot. Everything else stays byte-for-byte.
// Not a privilege boundary: the site owner already runs code as this uid.
export const DSH_MUTABLE_ENTRIES = Object.freeze(["cordis.yml", "cordis.patch.yml"]);

function verify(root, expected, exact) {
  const manifest = readManifest(root);
  if (manifest.hash !== expected.hash) throw new Error("Profile image/runtime version differs");
  const actual = inventory(root);
  for (const [key, entry] of Object.entries(expected.entries)) {
    if (!exact && DSH_MUTABLE_ENTRIES.includes(key)) {
      if (actual[key]?.type !== "file") throw new Error(`Profile entry missing or changed: ${key}`);
      continue;
    }
    if (JSON.stringify(actual[key]) !== JSON.stringify(entry)) throw new Error(`Profile entry missing or changed: ${key}`);
  }
  if (exact && Object.keys(actual).length !== Object.keys(expected.entries).length) throw new Error("Unexpected image profile entries");
}

function destinationState(profileDir) {
  let stat;
  try { stat = fs.lstatSync(profileDir); } catch (error) {
    if (error.code === "ENOENT") return "absent";
    throw error;
  }
  if (!stat.isDirectory()) throw migration("destination is not a real directory");
  return fs.readdirSync(profileDir).length ? "existing" : "empty";
}

// Extra customer files are retained; image-owned files are immutable for this
// format. Upgrades (including runtime-only upgrades) require explicit migration.
// copy is injectable for deterministic interrupted-copy tests.
export function ensureProfile({ sourceDir, profileDir, copy = fs.cpSync }) {
  let expected;
  try {
    expected = readManifest(sourceDir);
    verify(sourceDir, expected, true);
  } catch (error) {
    throw new Error(`Invalid image profile: ${error.message}; destination untouched`, { cause: error });
  }
  const state = destinationState(profileDir);
  if (state === "existing") {
    try { verify(profileDir, expected, false); } catch (error) { throw migration(error.message); }
    return { initialized: false, hash: expected.hash };
  }
  const parent = path.dirname(profileDir);
  fs.mkdirSync(parent, { recursive: true });
  const staging = fs.mkdtempSync(path.join(parent, `.${path.basename(profileDir)}.staging-`));
  try {
    copy(sourceDir, staging, { recursive: true, dereference: false, verbatimSymlinks: true });
    verify(staging, expected, true);
    // Same-parent rename is atomic. Never remove the destination first: rename
    // replaces an empty directory but refuses any concurrently added data.
    if (destinationState(profileDir) === "existing") throw migration("destination changed during initialization");
    fs.renameSync(staging, profileDir);
    return { initialized: true, hash: expected.hash };
  } finally {
    fs.rmSync(staging, { recursive: true, force: true });
  }
}
