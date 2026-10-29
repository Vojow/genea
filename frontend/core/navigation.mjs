import { $, $$ } from "./dom.mjs";

export function bindPrimaryControls({ tree, graph }) {
  $$('[data-tree-mode]').forEach((button) => button.addEventListener("click", () => {
    $$('[data-tree-mode]').forEach((item) => item.classList.toggle("is-active", item === button));
    tree.setMode(button.dataset.treeMode);
  }));
  $$('[data-ancestor-layout]').forEach((button) => button.addEventListener("click", () => {
    $$('[data-ancestor-layout]').forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    tree.setAncestorLayout(button.dataset.ancestorLayout);
  }));
  $("#tree-fit").addEventListener("click", () => tree.fit());
  $("#tree-zoom-in").addEventListener("click", () => tree.zoomBy(.1));
  $("#tree-zoom-out").addEventListener("click", () => tree.zoomBy(-.1));
  $("#tree-search").addEventListener("input", (event) => tree.search(event.target.value));
  $("#tree-presentation-toggle").addEventListener("click", (event) => {
    const listVisible = tree.togglePresentation();
    event.currentTarget.setAttribute("aria-pressed", String(listVisible));
    event.currentTarget.textContent = listVisible ? "Widok drzewa" : "Widok listy";
  });
  $$('[data-graph-mode]').forEach((button) => button.addEventListener("click", () => {
    $$('[data-graph-mode]').forEach((item) => item.classList.toggle("is-active", item === button));
    graph.setMode(button.dataset.graphMode);
  }));
  $("#graph-fit").addEventListener("click", () => graph.fit());
  $("#graph-search").addEventListener("input", (event) => graph.search(event.target.value));
  $("#graph-presentation-toggle").addEventListener("click", (event) => {
    const listVisible = graph.togglePresentation();
    event.currentTarget.setAttribute("aria-pressed", String(listVisible));
    event.currentTarget.textContent = listVisible ? "Widok grafu" : "Widok listy";
  });
}
