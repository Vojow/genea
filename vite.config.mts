import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";


const frontendRoot = resolve(import.meta.dirname, "frontend");
// Keep authored demo data separate from the backend runtime export destination.
const syntheticPresentationPath = resolve(import.meta.dirname, "examples/synthetic/empty-presentation.json");
const require = createRequire(import.meta.url);
const maplibreDist = resolve(dirname(require.resolve("maplibre-gl/package.json")), "dist");
const isBlindReview = process.env.GENEA_UI_TARGET === "blind";
// Static builds include only the authored synthetic payload after explicit opt-in.
const embedPresentationData = process.env.GENEA_EMBED_PRESENTATION_DATA === "1";
const configuredBasePath = process.env.GENEA_APP_BASE_PATH || "/";

function normalizedBasePath(value: string): string {
  if (!value.startsWith("/") || value.includes(":") || value.includes("..")) {
    throw new Error("GENEA_APP_BASE_PATH musi być bezpieczną ścieżką absolutną, np. / albo /genea/");
  }
  return value.endsWith("/") ? value : `${value}/`;
}

const appBasePath = normalizedBasePath(configuredBasePath);

const blindReviewRoutePlugin = {
  name: "genea-blind-review-route",
  configureServer(server: { middlewares: { use: (handler: (request: { url?: string }, response: unknown, next: () => void) => void) => void } }) {
    server.middlewares.use((request, _response, next) => {
      const pathname = request.url?.split("?", 1)[0];
      if (pathname && /^\/blind-review\/[^/]+\/?$/.test(pathname)) {
        request.url = `/blind-review/index.html${request.url?.slice(pathname.length) || ""}`;
      }
      next();
    });
  },
};

const presentationPayloadPlugin = {
  name: "genea-presentation-payload",
  configureServer(server: { middlewares: { use: (handler: (request: { url?: string }, response: { statusCode: number; setHeader: (name: string, value: string) => void; end: (content: Buffer) => void }, next: () => void) => void) => void } }) {
    server.middlewares.use((request, response, next) => {
      const pathname = request.url?.split("?", 1)[0];
      if (pathname === "/data/research.json" || pathname === `${appBasePath}data/research.json`) {
        response.statusCode = 200;
        response.setHeader("Content-Type", "application/json; charset=utf-8");
        response.setHeader("Cache-Control", "no-store");
        response.end(readFileSync(syntheticPresentationPath));
        return;
      }
      const fileName = pathname?.replace("/assets/", "");
      if (!fileName || !["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"].includes(fileName)) {
        next();
        return;
      }
      response.statusCode = 200;
      response.setHeader("Content-Type", "text/javascript; charset=utf-8");
      response.end(readFileSync(resolve(maplibreDist, fileName)));
    });
  },
  generateBundle(this: { emitFile: (asset: { type: "asset"; fileName: string; source: Buffer }) => void }) {
    if (embedPresentationData) {
      this.emitFile({
        type: "asset",
        fileName: "data/research.json",
        source: readFileSync(syntheticPresentationPath),
      });
    }
    for (const fileName of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
      this.emitFile({
        type: "asset",
        fileName: `assets/${fileName}`,
        source: readFileSync(resolve(maplibreDist, fileName)),
      });
    }
  },
};

export default defineConfig({
  root: frontendRoot,
  base: isBlindReview ? "/" : appBasePath,
  appType: isBlindReview ? "mpa" : "spa",
  publicDir: isBlindReview ? false : "public",
  optimizeDeps: isBlindReview ? undefined : { exclude: ["maplibre-gl"] },
  plugins: [vue(), isBlindReview ? blindReviewRoutePlugin : presentationPayloadPlugin],
  build: {
    outDir: isBlindReview
      ? resolve(import.meta.dirname, "output/blind-review")
      : resolve(frontendRoot, "dist"),
    emptyOutDir: true,
    rollupOptions: isBlindReview
      ? { input: { index: resolve(frontendRoot, "blind-review/index.html") } }
      : undefined,
  },
});
