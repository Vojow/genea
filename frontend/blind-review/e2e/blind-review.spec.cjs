const { createHash } = require("node:crypto");
const { expect, test } = require("@playwright/test");


const pixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

test("stages all scales and sends literal text only through submission", async ({ page }) => {
  const browserErrors = [];
  const assetRequests = [];
  const mutationRequests = [];
  const forbiddenRequests = [];
  const forbiddenValue = "Tajna wartość oczekiwana";
  const forbiddenLocator = "Archiwum ukryte 12/34";
  const literalField = "Dopierała literalnie";
  const literalContext = "Dopierała z kontekstem";
  const state = { visibleScale: "field", frozenScales: [], submittedScales: [] };
  const scales = ["field", "context", "fullAct"];

  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => browserErrors.push(`pageerror: ${error.message}`));
  page.on("request", (request) => {
    const url = request.url();
    if (/data\/research\.json|maplibre|api\/private|api\/presentation\/volume-index/.test(url)) {
      forbiddenRequests.push(url);
    }
  });

  await page.route("**/api/presentation/reading-verification/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "GET" && path.endsWith("/session")) {
      const visibleIndex = scales.indexOf(state.visibleScale);
      return route.fulfill({
        contentType: "application/json; charset=utf-8",
        json: {
          schema: "genea.reading-verification-session/v1",
          runId: "run-e2e",
          csrfToken: "csrf-e2e",
          fields: [{
            id: "f001",
            kind: "nazwisko",
            visibleScale: state.visibleScale,
            frozenScales: state.frozenScales,
            submittedScales: state.submittedScales,
            availableScales: scales.slice(0, visibleIndex + 1),
            expectedValue: forbiddenValue,
            locator: forbiddenLocator,
            sourceId: "source-forbidden",
          }],
        },
      });
    }
    const assetMatch = path.match(/\/fields\/f001\/(field|context|fullAct)$/);
    if (request.method() === "GET" && assetMatch) {
      assetRequests.push(assetMatch[1]);
      return route.fulfill({ status: 200, contentType: "image/png", body: pixelPng });
    }
    const commandMatch = path.match(/\/fields\/f001\/(freeze|submission|reveal)$/);
    if (request.method() === "POST" && commandMatch) {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-e2e");
      const body = request.postDataJSON();
      mutationRequests.push({ action: commandMatch[1], body });
      if (commandMatch[1] === "freeze") state.frozenScales.push(body.scale);
      else if (commandMatch[1] === "submission") state.submittedScales.push(body.scale);
      else state.visibleScale = body.scale;
      return route.fulfill({ contentType: "application/json", json: { ok: true } });
    }
    return route.fulfill({ status: 404, contentType: "application/json", json: { ok: false } });
  });

  await page.goto("/blind-review/run-e2e");
  await expect(page.getByRole("heading", { name: "Odczytaj dokładnie to, co widzisz" })).toBeVisible();
  await expect(page.locator(".source-frame img")).toHaveAttribute("src", /\/f001\/field$/);
  expect(assetRequests).toEqual(["field"]);
  await expect(page.getByText(forbiddenValue)).toHaveCount(0);
  await expect(page.getByText(forbiddenLocator)).toHaveCount(0);

  const textarea = page.getByLabel("Literalny odczyt z widocznego obrazu");
  await textarea.fill(literalField);
  await page.getByRole("button", { name: "Zamroź ten odczyt" }).click();
  await expect(page.getByRole("button", { name: "Odsłoń: szerszy kontekst" })).toBeVisible();
  expect(mutationRequests[0]).toEqual({ action: "freeze", body: {
    commandId: expect.any(String),
    scale: "field",
    submissionSha256: createHash("sha256").update(literalField).digest("hex"),
  }});
  expect(mutationRequests[1]).toEqual({ action: "submission", body: {
    commandId: expect.any(String),
    scale: "field",
    literalReading: literalField,
  }});
  expect(JSON.stringify(mutationRequests.filter(({ action }) => action !== "submission"))).not.toContain(literalField);

  await page.getByRole("button", { name: "Odsłoń: szerszy kontekst" }).click();
  await expect(page.locator(".source-frame img")).toHaveAttribute("src", /\/f001\/context$/);
  await expect(textarea).toHaveValue("");
  await textarea.fill(literalContext);
  await page.getByRole("button", { name: "Zamroź ten odczyt" }).click();
  await page.getByRole("button", { name: "Odsłoń: pełny akt" }).click();
  await expect(page.locator(".source-frame img")).toHaveAttribute("src", /\/f001\/fullAct$/);
  expect(assetRequests).toEqual(["field", "context", "fullAct"]);
  expect(mutationRequests.filter(({ action }) => action === "submission").map(({ body }) => body.literalReading))
    .toEqual([literalField, literalContext]);
  expect(JSON.stringify(mutationRequests.filter(({ action }) => action !== "submission"))).not.toContain(literalContext);
  await expect(page.getByRole("button", { name: "Opcjonalnie zamroź pełny akt" })).toBeVisible();
  await textarea.fill("Pełny akt kontrolnie");
  await page.getByRole("button", { name: "Opcjonalnie zamroź pełny akt" }).click();
  await expect(page.getByText(/przekazany jako propozycja/i)).toBeVisible();
  expect(mutationRequests.at(-1)).toMatchObject({
    action: "submission",
    body: { scale: "fullAct", literalReading: "Pełny akt kontrolnie" },
  });

  const stored = await page.evaluate(() => sessionStorage.getItem("genea:blind-review:run-e2e"));
  expect(stored).toContain(literalField);
  expect(stored).toContain(literalContext);
  expect(forbiddenRequests).toEqual([]);
  expect(browserErrors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  for (const button of await page.locator("button:visible").all()) {
    expect((await button.boundingBox())?.height || 0).toBeGreaterThanOrEqual(44);
  }
});
