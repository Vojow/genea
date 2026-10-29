import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTestRouter } from "../router";
import { useAppStore } from "../stores/app";
import ArchiveRequestsView from "./ArchiveRequestsView.vue";
import { parseArchiveRequests, safeArchiveUrl, type ArchiveRequest } from "./archiveRequests";

const draftBody = "Szanowni Państwo,\n\nProszę o sprawdzenie jednostki 123.\n<prywatna treść szkicu>\n\nZ poważaniem";
function request(overrides: Partial<ArchiveRequest> = {}): ArchiveRequest {
  return {
    id: "request-one", caseId: "CASE-ONE", title: "Odpis aktu", repository: "Archiwum w Łodzi", recipient: "Czytelnia",
    status: "draft", readiness: "needs_details", priority: 1, requestBody: draftBody,
    instructions: "Uzupełnij dane wnioskodawcy.\nSprawdź zasady udostępnienia.",
    targets: [{ signature: "123/4", title: "Księga urodzeń", dates: "1900–1905", url: "https://example.org/catalog/123", sourceId: "src_explicit" }],
    provenance: ["Notatka kuratora · synthetic"], missingFields: ["Adres zwrotny"], privacy: "private",
    responseNote: "", createdAt: "2026-10-22T10:00:00Z", updatedAt: "2026-10-22T11:00:00Z", ...overrides,
  };
}

const response = (requests: ArchiveRequest[] = [request()]) => ({ ok: true, json: async () => ({ requests }) });
const wrappers: VueWrapper[] = [];
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");

async function render(access = { available: true, authenticated: true, strict: false, csrf: "session-one" }) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useAppStore();
  store.setCuratorAccess(access);
  const router = createTestRouter();
  await router.push("/research/cases");
  const wrapper = mount(ArchiveRequestsView, { global: { plugins: [pinia, router] } });
  wrappers.push(wrapper);
  await flushPromises();
  return { wrapper, store };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response()));
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
  else Reflect.deleteProperty(navigator, "clipboard");
});

describe("ArchiveRequestsView", () => {
  it("does not fetch while locked and automatically loads when unlocked", async () => {
    const { wrapper, store } = await render({ available: true, authenticated: false, strict: false, csrf: "" });
    expect(fetch).not.toHaveBeenCalled();
    expect(wrapper.find(".private-route-gate").exists()).toBe(true);
    expect(wrapper.text()).not.toContain(draftBody);
    store.setCuratorAccess({ authenticated: true, csrf: "session-two" });
    await flushPromises();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(wrapper.get(".archive-body").text()).toContain("Proszę o sprawdzenie");
    expect(wrapper.find(".private-route-gate").exists()).toBe(false);
  });

  it("explains static / disabled API availability without a fabricated empty list", async () => {
    const { wrapper } = await render({ available: false, authenticated: false, strict: false, csrf: "" });
    expect(fetch).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("podglądzie statycznym");
    expect(wrapper.text()).toContain("aktywnym prywatnym API");
    expect(wrapper.text()).not.toContain("Brak kwerend w prywatnym rejestrze");
    expect(wrapper.find(".archive-counts").exists()).toBe(false);
  });

  it("fetches with private no-store options and keeps body outside storage and presentation", async () => {
    const persistence = vi.spyOn(Storage.prototype, "setItem");
    const { wrapper, store } = await render();
    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toContain("/api/private/archive-requests");
    expect(fetch).toHaveBeenCalledWith(expect.any(URL), expect.objectContaining({ method: "GET", cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" } }));
    expect(wrapper.get(".archive-counts").text()).toContain("Wszystkie: 1 · Nie wysłano: 1 · Wyniki: 1");
    expect(wrapper.get(".archive-unsent").text()).toBe("Nie wysłano");
    expect(wrapper.text()).toContain("Adres zwrotny");
    expect(wrapper.text()).toContain("Wymaga uzupełnienia danych");
    expect(wrapper.get(".archive-body").element.textContent).toBe(draftBody);
    expect(wrapper.get(".archive-body").find("prywatna").exists()).toBe(false);
    expect(store.payload).toBeNull();
    expect(persistence).not.toHaveBeenCalled();
  });

  it("searches across cases and repositories, combines status, sorts priority and selects detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      request({ id: "second", caseId: "CASE-TWO", title: "Akta parafii", repository: "Biblioteka w Warszawie", status: "awaiting_reply", priority: 5, requestBody: "Druga treść" }),
      request(), request({ id: "ready", status: "ready", priority: 2, title: "Gotowy szkic" }),
    ])));
    const { wrapper } = await render();
    expect(wrapper.findAll(".archive-card").map((card) => card.attributes("data-request-id"))).toEqual(["request-one", "ready", "second"]);
    await wrapper.get("#archive-query").setValue("warszawie CASE-TWO");
    expect(wrapper.findAll(".archive-card")).toHaveLength(1);
    expect(wrapper.get("#archive-request-detail").attributes("data-request-id")).toBe("second");
    await wrapper.get("#archive-status").setValue("draft");
    expect(wrapper.text()).toContain("Brak kwerend pasujących do filtrów");
    expect(wrapper.find(".archive-detail").exists()).toBe(false);
    await wrapper.get("#archive-query").setValue("lodz");
    expect(wrapper.findAll(".archive-card")).toHaveLength(1);
    await wrapper.get("#archive-status").setValue("");
    await wrapper.get("#archive-query").setValue("");
    await wrapper.get('[data-request-id="ready"].archive-card').trigger("click");
    expect(wrapper.get("#archive-detail-title").text()).toBe("Gotowy szkic");
    expect(wrapper.get(".archive-unsent").text()).toBe("Nie wysłano");
    expect(wrapper.get(".archive-counts").text()).toContain("Nie wysłano: 2");
  });

  it("distinguishes a successfully loaded empty registry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([])));
    const { wrapper } = await render();
    expect(wrapper.text()).toContain("Brak kwerend w prywatnym rejestrze");
    expect(wrapper.get(".archive-counts").text()).toContain("Wszystkie: 0");
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it("renders all requests when a request has no assigned case", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      request(), request({ id: "unassigned", caseId: null, title: "Kwerenda bez sprawy", priority: 2 }),
    ])));
    const { wrapper } = await render();
    expect(wrapper.findAll(".archive-card")).toHaveLength(2);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.find('a[href="/research/cases/CASE-ONE"]').exists()).toBe(true);
    await wrapper.get('.archive-card[data-request-id="unassigned"]').trigger("click");
    expect(wrapper.get("#archive-detail-title").text()).toBe("Kwerenda bez sprawy");
    expect(wrapper.get(".archive-detail").text()).toContain("Bez przypisanej sprawy");
    expect(wrapper.get('.archive-card[data-request-id="unassigned"]').text()).toContain("Bez przypisanej sprawy");
    expect(wrapper.find('a[href^="/research/cases/"]').exists()).toBe(false);
    expect(wrapper.get(".archive-body").element.textContent).toBe(draftBody);
    await wrapper.get("#archive-query").setValue("Kwerenda bez sprawy");
    expect(wrapper.findAll(".archive-card")).toHaveLength(1);
  });

  it("links only explicit safe source IDs and http(s) catalog URLs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([request({ targets: [
      { signature: "src_not_a_source_id", title: "Bez identyfikatora", dates: "1900", url: "javascript:alert(1)" },
      { signature: "ABC", title: "Jawne źródło", dates: "1901", url: "https://example.org/catalog/abc", sourceId: "src_explicit" },
      { signature: "DEF", title: "Bezpieczna sygnatura", dates: "1902", url: "data:text/html,bad", sourceId: "../../bad" },
      { signature: "GHI", title: "Adres z danymi logowania", dates: "1903", url: "https://user:secret@example.org/" },
    ] })])));
    const { wrapper } = await render();
    expect(wrapper.find('a[href="/research/cases/CASE-ONE"]').exists()).toBe(true);
    expect(wrapper.findAll('a[href^="/sources/"]')).toHaveLength(1);
    expect(wrapper.find('a[href="/sources/src_explicit"]').exists()).toBe(true);
    const catalog = wrapper.get('a[href="https://example.org/catalog/abc"]');
    expect(catalog.attributes("rel")).toContain("noopener");
    expect(catalog.attributes("target")).toBe("_blank");
    expect(wrapper.findAll('a[href^="javascript:"], a[href^="data:"], a[href*="secret"]')).toHaveLength(0);
    expect(wrapper.text()).toContain("src_not_a_source_id");
    expect(wrapper.find('a[href*="src_not_a_source_id"]').exists()).toBe(false);
  });

  it("copies exact plain text and provides visible success and failure feedback", async () => {
    const { wrapper } = await render();
    await wrapper.get(".archive-actions button").trigger("click");
    await flushPromises();
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(draftBody);
    expect(wrapper.text()).toContain("Skopiowano treść kwerendy");
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error("private clipboard error"));
    await wrapper.get(".archive-actions button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Zaznacz treść kwerendy i skopiuj ją ręcznie");
    expect(wrapper.text()).not.toContain("private clipboard error");
  });

  it("downloads a plain text file with a safe filename and revokes its object URL", async () => {
    const NativeURL = URL;
    const createObjectURL = vi.fn().mockReturnValue("blob:synthetic");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", class extends NativeURL { static createObjectURL = createObjectURL; static revokeObjectURL = revokeObjectURL; });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("kwerenda-request-one.txt");
      expect(this.href).toBe("blob:synthetic");
    });
    const { wrapper } = await render();
    await wrapper.findAll(".archive-actions button")[1]!.trigger("click");
    expect(createObjectURL).toHaveBeenCalledWith(expect.objectContaining({ type: "text/plain;charset=utf-8" }));
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:synthetic");
    expect(wrapper.text()).toContain("Przygotowano plik tekstowy");
  });

  it("scrubs loaded content, filters and copy feedback when the session locks", async () => {
    const { wrapper, store } = await render();
    await wrapper.get("#archive-query").setValue("Czytelnia");
    await wrapper.get(".archive-actions button").trigger("click");
    await flushPromises();
    store.setCuratorAccess({ authenticated: false, csrf: "" });
    await nextTick();
    expect(wrapper.find(".archive-detail").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Proszę o sprawdzenie");
    expect(wrapper.text()).not.toContain("Skopiowano");
    expect(wrapper.find(".private-route-gate").exists()).toBe(true);
    store.setCuratorAccess({ authenticated: true, csrf: "session-two" });
    await flushPromises();
    expect((wrapper.get("#archive-query").element as HTMLInputElement).value).toBe("");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("aborts an in-flight request on lock and ignores late responses", async () => {
    let resolve!: (value: ReturnType<typeof response>) => void;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise((done) => { resolve = done; })));
    const { wrapper, store } = await render();
    const signal = vi.mocked(fetch).mock.calls[0]?.[1]?.signal;
    store.setCuratorAccess({ authenticated: false });
    expect(signal?.aborted).toBe(true);
    resolve(response());
    await flushPromises();
    expect(wrapper.text()).not.toContain("Proszę o sprawdzenie");
    expect(wrapper.find(".archive-detail").exists()).toBe(false);
  });

  it("clears old content immediately on session token change and reloads", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response()).mockReturnValueOnce(new Promise(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    const { wrapper, store } = await render();
    store.setCuratorAccess({ csrf: "new-session" });
    await nextTick();
    expect(wrapper.find(".archive-body").exists()).toBe(false);
    expect(wrapper.text()).toContain("Wczytywanie");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    expect(fetchMock.mock.calls[1]?.[1].signal.aborted).toBe(true);
  });

  it("does not restore copy feedback when a clipboard promise completes after lock", async () => {
    let resolve!: () => void;
    vi.mocked(navigator.clipboard.writeText).mockReturnValueOnce(new Promise<void>((done) => { resolve = done; }));
    const { wrapper, store } = await render();
    await wrapper.get(".archive-actions button").trigger("click");
    store.setCuratorAccess({ authenticated: false });
    resolve();
    await flushPromises();
    expect(wrapper.text()).not.toContain("Skopiowano");
    expect(wrapper.find(".archive-body").exists()).toBe(false);
  });

  it("scrubs strict content at the API deadline and refetches ordinary records while store.strict remains true", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    const ordinary = request({ requestBody: "Zwykła prywatna kwerenda" });
    const strict = request({ id: "strict", privacy: "strict_private", priority: 0, requestBody: "Ściśle prywatny szkic" });
    let resolveRefresh!: (value: ReturnType<typeof response>) => void;
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ requests: [ordinary, strict], strictExpiresIn: 5 }) })
      .mockReturnValueOnce(new Promise((done) => { resolveRefresh = done; }));
    vi.stubGlobal("fetch", fetchMock);
    const { wrapper, store } = await render({ available: true, authenticated: true, strict: true, csrf: "strict-session" });
    expect(wrapper.get(".archive-body").text()).toBe("Ściśle prywatny szkic");
    await vi.advanceTimersByTimeAsync(5000);
    await nextTick();
    expect(store.curatorAccess.strict).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).not.toContain("Ściśle prywatny szkic");
    expect(wrapper.find(".archive-body").exists()).toBe(false);
    resolveRefresh(response([ordinary]));
    await flushPromises();
    expect(wrapper.get(".archive-body").text()).toBe("Zwykła prywatna kwerenda");
    expect(wrapper.findAll(".archive-card")).toHaveLength(1);
  });

  it.each([undefined, null, 0, -1, Infinity, "300"])("omits strict records without a finite positive API expiry (%s)", async (strictExpiresIn) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      requests: [request(), request({ id: "strict", privacy: "strict_private", priority: 0, requestBody: "UNAUTHORIZED STRICT BODY" })], strictExpiresIn,
    }) }));
    const { wrapper } = await render({ available: true, authenticated: true, strict: true, csrf: "strict-session" });
    expect(wrapper.findAll(".archive-card")).toHaveLength(1);
    expect(wrapper.text()).not.toContain("UNAUTHORIZED STRICT BODY");
    expect(wrapper.get(".archive-body").element.textContent).toBe(draftBody);
  });

  it("omits strict records when the shared store does not grant strict access", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      requests: [request(), request({ id: "strict", privacy: "strict_private", priority: 0, requestBody: "UNAUTHORIZED STRICT BODY" })], strictExpiresIn: 300,
    }) }));
    const { wrapper } = await render();
    expect(wrapper.findAll(".archive-card")).toHaveLength(1);
    expect(wrapper.text()).not.toContain("UNAUTHORIZED STRICT BODY");
  });

  it("deducts network time from the strict expiry window", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date("2026-10-22T12:00:00Z"));
    let resolve!: (value: { ok: boolean; json: () => Promise<unknown> }) => void;
    const fetchMock = vi.fn().mockReturnValueOnce(new Promise((done) => { resolve = done; })).mockResolvedValueOnce(response());
    vi.stubGlobal("fetch", fetchMock);
    const { wrapper } = await render({ available: true, authenticated: true, strict: true, csrf: "strict-session" });
    await vi.advanceTimersByTimeAsync(4000);
    resolve({ ok: true, json: async () => ({ requests: [request({ privacy: "strict_private" })], strictExpiresIn: 5 }) });
    await flushPromises();
    expect(wrapper.find(".archive-body").exists()).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([0, 1])("guards body action %s synchronously if the deadline passes before timers run", async (actionIndex) => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    const start = new Date("2026-10-22T12:00:00Z");
    vi.setSystemTime(start);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({
      requests: [request({ privacy: "strict_private" })], strictExpiresIn: 5,
    }) }).mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    const { wrapper } = await render({ available: true, authenticated: true, strict: true, csrf: "strict-session" });
    // Changing system time deliberately does not execute the scheduled timeout.
    vi.setSystemTime(new Date(start.getTime() + 5001));
    await wrapper.findAll(".archive-actions button")[actionIndex]!.trigger("click");
    await flushPromises();
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(wrapper.find(".archive-body").exists()).toBe(false);
  });

  it("clears the strict deadline timer when locking or unmounting", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ requests: [request({ privacy: "strict_private" })], strictExpiresIn: 5 }) });
    vi.stubGlobal("fetch", fetchMock);
    const { wrapper, store } = await render({ available: true, authenticated: true, strict: true, csrf: "strict-session" });
    store.setCuratorAccess({ authenticated: false });
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    store.setCuratorAccess({ authenticated: true });
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("scrubs stale content on refresh failure, hides server details and allows retry", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response()).mockRejectedValueOnce(new Error("private server traceback"))
      .mockResolvedValueOnce(response());
    vi.stubGlobal("fetch", fetchMock);
    const { wrapper } = await render();
    await wrapper.get(".archive-toolbar button").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("aktywne prywatne API");
    expect(wrapper.text()).not.toContain("private server traceback");
    expect(wrapper.find(".archive-body").exists()).toBe(false);
    expect(wrapper.find(".archive-counts").exists()).toBe(false);
    await wrapper.get(".archive-toolbar button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".archive-body").exists()).toBe(true);
  });

  it.each([401, 403, 503])("presents HTTP %s as unavailable rather than an empty registry", async (httpStatus) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: httpStatus }));
    const { wrapper } = await render();
    expect(wrapper.get('[role="alert"]').text()).toContain("Nie udało się pobrać kwerend");
    expect(wrapper.text()).not.toContain("Brak kwerend w prywatnym rejestrze");
  });

  it("rejects malformed API data without exposing any partial request body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ requests: [request(), request({ id: "bad", status: "invented" as "draft" })] }) }));
    const { wrapper } = await render();
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    expect(wrapper.find(".archive-body").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Proszę o sprawdzenie");
  });
});

describe("private archive request contract", () => {
  it("retains backing IDs and rejects duplicate IDs and unknown readiness", () => {
    expect(parseArchiveRequests({ requests: [request()] })[0]?.targets[0]?.sourceId).toBe("src_explicit");
    expect(() => parseArchiveRequests({ requests: [request(), request()] })).toThrow();
    expect(() => parseArchiveRequests({ requests: [request({ readiness: "inferred" as "needs_details" })] })).toThrow();
  });
  it.each(["javascript:alert(1)", "data:text/html,test", "//example.org", "https://user:pass@example.org", "https://example.org/\npath", "https://"])('rejects unsafe URL %s', (url) => {
    expect(safeArchiveUrl(url)).toBeNull();
  });
});
