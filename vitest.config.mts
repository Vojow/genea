import { resolve } from "node:path";

import vue from "@vitejs/plugin-vue";
import { configDefaults, defineConfig } from "vitest/config";


export default defineConfig({
  root: resolve(import.meta.dirname, "frontend"),
  plugins: [vue()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.spec.ts"],
    // These integration specs depend on fixtures omitted from the public package.
    exclude: [
      ...configDefaults.exclude,
      "src/models/source-identity-groups.backend.spec.ts",
      "src/models/source-identity-groups.spec.ts",
      "src/views/SourceIdentityDetailView.spec.ts",
    ],
  },
});
