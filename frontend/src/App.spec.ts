import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, describe, expect, it } from "vitest";

import App from "./App.vue";
import { createTestRouter, viewFromRoute } from "./router";
import { useAppStore } from "./stores/app";
import type { PresentationPayload } from "./types/presentation";


const payload: PresentationPayload = {
  meta: {
    schemaVersion: 1,
    generatedAt: "2026-08-26T12:00:00+00:00",
    mode: "read-only-research-view",
    methodNote: "Testowa metoda",
    counts: {
      people: 2,
      occurrences: 3,
      events: 4,
      relationships: 1,
      identityCandidates: 1,
      households: 1,
      sources: 5,
      claims: 2,
      conflicts: 1,
      researchBacklog: 7,
      mapPlaces: 6,
      mapObservations: 8,
    },
  },
  people: [],
  occurrences: [],
  events: [],
  relationships: [],
  identityCandidates: [],
  households: [],
  sources: [],
  claims: [],
  conflicts: [],
  familyTree: { people: [{}, {}, {}] },
  map: { places: [], observations: [] },
};

afterEach(() => {
  document.body.replaceChildren();
});

describe("Genea Vue shell", () => {
  it("renders stable workspace and contextual navigation without noisy counters", async () => {
    document.body.innerHTML = `
      <div id="app-header-host"></div>
      <div id="sources-view-host"></div>
      <div id="research-view-host"></div>
      <div id="routed-view-host"></div>
      <aside id="map-inspector"></aside>
      <aside id="graph-inspector"></aside>
      <aside id="tree-inspector"></aside>
      <div id="app"></div>
    `;
    const pinia = createPinia();
    const router = createTestRouter();
    const store = useAppStore(pinia);
    store.initialize(payload, "tree");
    router.afterEach((route) => store.activate(viewFromRoute(route)));
    await router.push("/tree");
    await router.isReady();

    const wrapper = mount(App, {
      attachTo: "#app",
      global: { plugins: [pinia, router] },
    });

    expect(document.querySelectorAll("#main-nav-host .nav-button")).toHaveLength(5);
    expect(document.querySelectorAll("#main-nav-host .nav-button small")).toHaveLength(0);
    expect(document.querySelector(".workspace-context-bar")?.getAttribute("data-context")).toBe("family");
    expect(Array.from(document.querySelectorAll(".workspace-context-nav-desktop a")).map((item) => item.textContent?.trim())).toEqual(["Drzewo", "Osoby", "Linie"]);
    expect(document.querySelector("#generated-at")?.textContent).toContain("eksport");

    const mapButton = document.querySelector<HTMLAnchorElement>(
      '[data-view="map"]',
    );
    mapButton?.click();
    await router.isReady();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(router.currentRoute.value.name).toBe("map");
    expect(store.activeView).toBe("map");
    expect(mapButton?.classList.contains("is-active")).toBe(true);
    expect(document.querySelector(".workspace-context-bar")?.getAttribute("data-context")).toBe("map");

    await router.push("/people/person_01");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(document.querySelector('[data-view="tree"]')?.classList.contains("is-active")).toBe(true);
    expect(document.querySelector(".catalog-trigger")?.classList.contains("is-active")).toBe(false);
    expect(document.querySelector(".workspace-context-bar")?.getAttribute("data-context")).toBe("family");
    expect(document.querySelector('.workspace-context-nav-desktop a[aria-current="page"]')?.textContent?.trim()).toBe("Osoby");
    wrapper.unmount();
  });
});
