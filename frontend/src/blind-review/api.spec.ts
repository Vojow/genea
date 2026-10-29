import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fieldAssetUrl,
  freezeField,
  loadReviewSession,
  parseReviewSession,
  revealField,
  submitField,
} from "./api";


afterEach(() => {
  vi.unstubAllGlobals();
});

describe("blind review API", () => {
  it("projects only the minimal session contract and discards forbidden source details", () => {
    const session = parseReviewSession({
      schema: "genea.reading-verification-session/v1",
      runId: "run-neutral",
      csrfToken: "csrf-secret",
      fields: [{
        id: "f001",
        kind: "nazwisko",
        visibleScale: "field",
        frozenScales: [],
        submittedScales: [],
        availableScales: ["field"],
        expectedValue: "Wartość zakazana",
        locator: "Archiwum 12/34",
        sourceId: "source-private",
      }],
      packageId: "package-private",
    });

    expect(session).toEqual({
      runId: "run-neutral",
      csrfToken: "csrf-secret",
      fields: [{
        id: "f001",
        kind: "nazwisko",
        visibleScale: "field",
        frozenScales: [],
        submittedScales: [],
        availableScales: ["field"],
      }],
    });
    expect(JSON.stringify(session)).not.toContain("Wartość zakazana");
    expect(JSON.stringify(session)).not.toContain("Archiwum 12/34");
    expect(JSON.stringify(session)).not.toContain("source-private");
  });

  it("loads the same-origin no-store session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      runId: "run-1",
      csrfToken: "csrf-1",
      fields: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadReviewSession()).resolves.toMatchObject({ runId: "run-1", fields: [] });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/presentation/reading-verification/session",
      expect.objectContaining({ credentials: "same-origin", cache: "no-store" }),
    );
  });

  it("sends only command metadata and the SHA-256 when freezing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await freezeField("f001", "field", "a".repeat(64), "csrf-1", "command-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/presentation/reading-verification/fields/f001/freeze");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-CSRF-Token": "csrf-1",
    });
    expect(JSON.parse(String(init.body))).toEqual({
      commandId: "command-1",
      scale: "field",
      submissionSha256: "a".repeat(64),
    });
    expect(String(init.body)).not.toContain("literalny odczyt");

    await submitField("f001", "field", "literalny odczyt", "csrf-1", "command-2");
    const [submissionUrl, submissionInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(submissionUrl).toBe("/api/presentation/reading-verification/fields/f001/submission");
    expect(JSON.parse(String(submissionInit.body))).toEqual({
      commandId: "command-2",
      scale: "field",
      literalReading: "literalny odczyt",
    });

    await revealField("f001", "context", "csrf-1", "command-3");
    const revealInit = fetchMock.mock.calls[2][1] as RequestInit;
    expect(JSON.parse(String(revealInit.body))).toEqual({ commandId: "command-3", scale: "context" });
  });

  it("encodes backing IDs in asset URLs", () => {
    expect(fieldAssetUrl("field/with space", "fullAct"))
      .toBe("/api/presentation/reading-verification/fields/field%2Fwith%20space/fullAct");
  });
});
