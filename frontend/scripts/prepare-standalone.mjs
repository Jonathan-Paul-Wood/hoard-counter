import {
  cp,
  mkdir,
  access,
  readdir,
  readlink,
  lstat,
  unlink,
  symlink,
} from "node:fs/promises";
import { resolve, dirname, sep } from "node:path";

// Next traces application code but deliberately leaves static assets separate.
// Include them so `pnpm start` serves the same self-contained output as Docker.
await mkdir(".next/standalone/.next", { recursive: true });
await cp(".next/static", ".next/standalone/.next/static", { recursive: true });
try {
  await access("public");
  await cp("public", ".next/standalone/public", { recursive: true });
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}

// Next's tracer emits file symlinks for some pnpm directory links on Windows.
// Convert only links inside this build output to directory junctions; Linux
// container builds keep the original portable symlinks.
if (process.platform === "win32") {
  const root = resolve(".next/standalone");
  async function repairLinks(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) await repairLinks(path);
      else if (entry.isSymbolicLink()) {
        const target = resolve(dirname(path), await readlink(path));
        if (!target.startsWith(root + sep)) continue;
        if ((await lstat(target)).isDirectory()) {
          await unlink(path);
          await symlink(target, path, "junction");
        }
      }
    }
  }
  await repairLinks(root);
}
