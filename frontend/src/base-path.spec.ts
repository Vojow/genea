import { describe, expect, it } from "vitest";

import { appScopePath, appUrl } from "./base-path";


describe("application base path", () => {
  it("resolves private data and assets from the application base, not a nested route", () => {
    const base = "https://example.test/genea/";
    expect(appUrl("/data/research.json", base).href).toBe("https://example.test/genea/data/research.json");
    expect(appUrl("assets/maplibre-gl-worker.mjs", base).href).toBe("https://example.test/genea/assets/maplibre-gl-worker.mjs");
    expect(appUrl("/api/private/status", base).href).toBe("https://example.test/genea/api/private/status");
    expect(appScopePath(base)).toBe("/genea/");
  });
});
