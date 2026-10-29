const { defineConfig } = require("@playwright/test");
const path = require("node:path");


module.exports = defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 15_000,
  expect: { timeout: 5_000 },
  reporter: "line",
  outputDir: "../../output/playwright/blind-review",
  use: {
    baseURL: "http://127.0.0.1:4174",
    browserName: "chromium",
    locale: "pl-PL",
    timezoneId: "Europe/Warsaw",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    {
      name: "phone-modern",
      use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
    },
  ],
  webServer: {
    command: "npm run dev:ui:blind -- --host 127.0.0.1 --port 4174 --strictPort",
    cwd: path.resolve(__dirname, "../.."),
    url: "http://127.0.0.1:4174/blind-review/run-e2e",
    reuseExistingServer: true,
    timeout: 15_000,
  },
});
