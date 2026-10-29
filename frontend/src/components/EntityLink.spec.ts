import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { createTestRouter, entityPath, type EntityKind } from "../router";
import EntityLink from "./EntityLink.vue";


const kinds: EntityKind[] = ["person", "line", "place", "event", "source", "household", "cluster", "case", "question", "volume", "run"];

describe("EntityLink", () => {
  it("renders a real stable href for every entity kind", async () => {
    const router = createTestRouter();
    await router.push("/tree");
    for (const kind of kinds) {
      const wrapper = mount(EntityLink, {
        props: { kind, id: `${kind}-opaque`, label: `Label ${kind}` },
        global: { plugins: [router] },
      });
      expect(wrapper.get("a").attributes("href")).toBe(entityPath(kind, `${kind}-opaque`));
      wrapper.unmount();
    }
  });

  it("renders controlled private, not-exported, and missing states for every entity kind", async () => {
    const router = createTestRouter();
    await router.push("/tree");
    for (const kind of kinds) {
      const privateLink = mount(EntityLink, {
        props: { kind, id: "private-id", label: "Etykieta prywatna", availability: "private" },
        global: { plugins: [router] },
      });
      expect(privateLink.text()).not.toContain("Etykieta prywatna");
      expect(privateLink.text()).toContain("chroniony");
      expect(privateLink.find("a").exists()).toBe(true);
      privateLink.unmount();

      const unavailable = mount(EntityLink, {
        props: { kind, id: "outside", label: "Tajny opis", availability: "not_exported" },
        global: { plugins: [router] },
      });
      expect(unavailable.find("a").exists()).toBe(false);
      expect(unavailable.text()).toContain("poza eksportem");
      expect(unavailable.text()).not.toContain("Tajny opis");
      unavailable.unmount();

      const missing = mount(EntityLink, {
        props: { kind, id: "missing", label: "Nieznany obiekt", availability: "missing" },
        global: { plugins: [router] },
      });
      expect(missing.find("a").exists()).toBe(false);
      expect(missing.text()).toContain("brak referencji");
      missing.unmount();
    }
  });
});
