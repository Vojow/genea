const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function svgNode(name, attributes = {}) {
  const element = document.createElementNS(SVG_NAMESPACE, name);
  Object.entries(attributes).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") element.setAttribute(key, String(value));
  });
  return element;
}

function statusClass(status) {
  return String(status || "unchecked").replace(/[^a-z0-9_-]/gi, "-").toLowerCase();
}

export function ancestorFanAccessibleLabel(sector) {
  const lifeSpan = String(sector?.person?.lifeSpanLabel || "").trim();
  const generation = sector.depth === 0 ? "osoba fokusowa" : `przodek, pokolenie ${sector.depth}`;
  const duplicate = sector.duplicateOfSlotId
    ? "; ta sama osoba występuje w innej ścieżce przodków"
    : "";
  return `Wybierz: ${sector.fullLabel}${lifeSpan ? `, lata życia ${lifeSpan}` : ""}; ${generation}${duplicate}`;
}

export class AncestorFanRenderer {
  constructor(svg, { onPersonClick = null } = {}) {
    this.svg = svg;
    this.onPersonClick = onPersonClick;
  }

  setOnPersonClick(callback) {
    this.onPersonClick = callback;
  }

  render({ layout, selectedSlotId = null, selectedPersonId = null } = {}) {
    if (!this.svg || !layout) return;
    this.svg.classList.add("ancestor-fan-svg");
    this.svg.setAttribute("viewBox", layout.viewBox);
    this.svg.setAttribute("width", layout.width);
    this.svg.setAttribute("height", layout.height);
    this.svg.setAttribute("role", "group");
    this.svg.setAttribute("aria-label", "Wachlarz przodków. Użyj klawisza Tab, aby przechodzić między osobami.");
    this.svg.style.setProperty("--ancestor-fan-natural-width", `${layout.naturalWidth}px`);

    const edgeLayer = svgNode("g", { class: "ancestor-fan-edges", "aria-hidden": "true" });
    layout.edges.filter((edge) => edge.path).forEach((edge) => {
      const path = svgNode("path", {
        d: edge.path,
        class: `ancestor-fan-edge ${statusClass(edge.status)}`,
        "data-edge-id": edge.edgeId,
        "data-parent-slot-id": edge.parentSlotId,
        "data-child-slot-id": edge.childSlotId,
        "data-family-id": edge.familyId,
        "data-source-ids": edge.sourceIds.join(" "),
      });
      edgeLayer.append(path);
    });

    const sectorLayer = svgNode("g", { class: "ancestor-fan-sectors" });
    layout.sectors.forEach((sector) => {
      const isSelected = selectedSlotId
        ? sector.slotId === selectedSlotId
        : Boolean(selectedPersonId && sector.personId === selectedPersonId);
      const interactive = Boolean(sector.personId && sector.person);
      const group = svgNode("g", {
        class: [
          "ancestor-fan-sector",
          `is-${sector.kind}`,
          `status-${statusClass(sector.status || sector.person?.evidenceStatus)}`,
          isSelected ? "is-selected" : "",
          interactive ? "is-interactive" : "is-empty",
        ].filter(Boolean).join(" "),
        "data-slot-id": sector.slotId,
        "data-person-id": sector.personId,
        "data-person-backing-id": sector.person?.personId || null,
        "data-occurrence-backing-id": sector.person?.occurrenceId || null,
        "data-source-ids": (sector.person?.sourceIds || []).join(" "),
        "data-path-family-ids": (sector.pathFamilyIds || []).join(" "),
        "data-duplicate-of-slot-id": sector.duplicateOfSlotId,
        "data-full-label": sector.fullLabel,
        "data-label-maximum-length": sector.labelMaximumLength,
        role: interactive ? "button" : "img",
        tabindex: interactive ? 0 : null,
        "aria-label": ancestorFanAccessibleLabel(sector),
        "aria-current": isSelected ? "true" : null,
      });
      const title = svgNode("title");
      title.textContent = ancestorFanAccessibleLabel(sector);
      group.append(title);
      group.append(svgNode("path", { d: sector.path, class: "ancestor-fan-sector-shape" }));

      const label = svgNode("text", {
        x: sector.center.x,
        y: sector.center.y,
        class: "ancestor-fan-sector-label",
        "text-anchor": "middle",
        "dominant-baseline": "middle",
        transform: sector.kind === "ancestor"
          ? `rotate(${sector.labelRotation || 0} ${sector.center.x} ${sector.center.y})`
          : null,
        style: `font-size:${sector.labelFontSize || 11}px`,
        "aria-hidden": "true",
      });
      const name = svgNode("tspan", { x: sector.center.x, dy: sector.kind === "focus" ? "-.35em" : "0" });
      name.textContent = sector.label;
      label.append(name);
      const lifeSpan = String(sector.person?.lifeSpanLabel || "").trim();
      if (lifeSpan && sector.showLifeSpan) {
        const dates = svgNode("tspan", { x: sector.center.x, dy: "1.35em", class: "ancestor-fan-sector-dates" });
        dates.textContent = lifeSpan;
        label.append(dates);
      }
      group.append(label);
      if (sector.duplicateOfSlotId) {
        const duplicate = svgNode("text", {
          x: sector.center.x,
          y: sector.center.y + 16,
          class: "ancestor-fan-duplicate-marker",
          "text-anchor": "middle",
          "aria-hidden": "true",
        });
        duplicate.textContent = "↺";
        group.append(duplicate);
      }

      if (interactive) {
        const activate = (event) => this.onPersonClick?.(sector.personId, sector, event);
        group.addEventListener("click", activate);
        group.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          activate(event);
        });
      }
      sectorLayer.append(group);
    });
    this.svg.replaceChildren(edgeLayer, sectorLayer);
  }
}
