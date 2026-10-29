import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { createTestRouter } from "../router";
import GraphInspector from "./GraphInspector.vue";
import EntityLink from "./EntityLink.vue";
import type { GraphInspectorModel } from "../types/presentation";

afterEach(() => { document.body.replaceChildren(); sessionStorage.clear(); });

describe("GraphInspector approved act materials", () => {
  it("renders a read-only source card and unique count without expanding the graph", async () => {
    document.body.innerHTML = '<aside id="graph-inspector"></aside><div id="app"></div>';
    const router = createTestRouter();
    await router.push("/graph");
    const inspection: GraphInspectorModel = {
      title: "Osoba testowa", subtitle: "Oczekuje potwierdzenia człowieka. Relacje niekanoniczne.",
      approvedActMaterialSummary: "1 akt · 2 wystąpienia",
      approvedActMaterials: [{ sourceId: "act-source", title: "Akt małżeństwa M3", occurrenceIds: ["act-occ", "second-occ"] }],
    };
    const wrapper = mount(GraphInspector, { attachTo: "#app", props: { inspection, priorityCandidates: [] }, global: { plugins: [router] } });
    const section = document.querySelector(".approved-act-materials");
    expect(section?.textContent).toContain("1 akt · 2 wystąpienia");
    expect(section?.textContent).toContain("Akt małżeństwa M3");
    expect(section?.querySelector("a")?.getAttribute("href")).toBe("/sources/act-source");
    const link = wrapper.findComponent(EntityLink);
    expect(link.props("sourceId")).toBe("act-source");
    expect(link.props("occurrenceId")).toBe("act-occ");
    expect(document.querySelector(".evidence-mode-action")).toBeNull();
    expect(document.querySelector(".evidence-graph-action")).toBeNull();
    expect(wrapper.emitted("toggleEvidence")).toBeUndefined();
    wrapper.unmount();
  });
});
