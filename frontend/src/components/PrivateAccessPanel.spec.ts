import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, describe, expect, it, vi } from "vitest";

import PrivateAccessPanel from "./PrivateAccessPanel.vue";


afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

function response(value: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(value),
  });
}

describe("private access panel", () => {
  it("keeps the password in a POST body and stores only the in-memory CSRF token", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ enabled: true, authenticated: false, strict: false, expiresIn: 0, locked: false }))
      .mockImplementationOnce(() => response({ authenticated: true, csrf: "csrf-test" }))
      .mockImplementationOnce(() => response({ enabled: true, authenticated: true, strict: false, expiresIn: 900, locked: false }))
      .mockImplementationOnce(() => response({ people: [{ id: "person-private-1", preferredName: "Osoba Prywatna", classification: "family_private" }] }))
      .mockImplementationOnce(() => response({
        people: [{ treePersonId: "root", name: "Osoba Prywatna", summary: "ur. 2000", privacy: "private_revealed", evidenceStatus: "private_unverified" }],
        mappedCount: 1,
        unmappedCount: 0,
        totalPrivatePeople: 1,
      }));
    vi.stubGlobal("fetch", fetchMock);
    const wrapper = mount(PrivateAccessPanel, { global: { plugins: [createPinia()] } });
    await flushPromises();

    await wrapper.get(".private-access-trigger").trigger("click");
    const input = wrapper.get<HTMLInputElement>("#private-password");
    await input.setValue("sekretne hasło testowe");
    await wrapper.get(".private-login").trigger("submit");
    await flushPromises();

    expect(String(fetchMock.mock.calls[1][0])).toBe("http://localhost:3000/api/private/unlock");
    const options = fetchMock.mock.calls[1][1] as RequestInit;
    expect(options.method).toBe("POST");
    expect(String(options.body)).toContain("sekretne hasło testowe");
    expect(String(fetchMock.mock.calls[1][0])).not.toContain("sekretne");
    expect(input.element.value).toBe("");
    expect(wrapper.get("#private-person-id").text()).toContain("Osoba Prywatna");
    expect(wrapper.text()).toContain("1 z 1 osób ma przypisaną kartę drzewa");
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toContain("http://localhost:3000/api/private/tree-overlay");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    wrapper.unmount();
  });

  it("stays hidden when the private API is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => response({}, 404)));
    const wrapper = mount(PrivateAccessPanel, { global: { plugins: [createPinia()] } });
    await flushPromises();
    expect(wrapper.find(".private-access-trigger").exists()).toBe(false);
    wrapper.unmount();
  });
});
