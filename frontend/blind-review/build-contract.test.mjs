import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const outputRoot = path.join(repositoryRoot, "output", "blind-review");

async function filesBelow(directory) {
  const entries = await readdir(directory);
  const files = [];
  for (const entry of entries) {
    const target = path.join(directory, entry);
    if ((await stat(target)).isDirectory()) files.push(...await filesBelow(target));
    else files.push(target);
  }
  return files;
}

test("isolated blind review build excludes presentation data and legacy UI dependencies", async () => {
  const files = await filesBelow(outputRoot);
  const relativeFiles = files.map((file) => path.relative(outputRoot, file));
  assert.ok(relativeFiles.includes(path.join("blind-review", "index.html")));
  assert.ok(!relativeFiles.some((file) => file === path.join("data", "research.json")));
  assert.ok(!relativeFiles.some((file) => /maplibre/i.test(file)));

  const textAssets = files.filter((file) => /\.(?:html|js|css|mjs)$/.test(file));
  const bundleText = (await Promise.all(textAssets.map((file) => readFile(file, "utf8")))).join("\n");
  assert.doesNotMatch(bundleText, /data\/research\.json/);
  assert.doesNotMatch(bundleText, /maplibre-gl|PresentationPayload|\bpinia\b/);
});
