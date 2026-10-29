import { familyConnectorGeometry } from "./family-tree-geometry.mjs";
import { familyRelationshipCanonical, familyRelationshipQualifier } from "./family-tree-model.mjs";

export class FamilyTreeRenderer {
  constructor(svg) {
    this.svg = svg;
  }

  drawConnectors({ tree, people, positions, cardWidth, cardHeight, focusIds }) {
    const visible = new Set(people.map((person) => person.id));
    const focus = new Set(focusIds || []);
    const namespace = "http://www.w3.org/2000/svg";
    const paths = [];
    for (const family of tree.families || []) {
      const geometry = familyConnectorGeometry({ family, visibleIds: visible, positions, cardWidth, cardHeight });
      if (!geometry) continue;
      const onFocus = geometry.children.some((child) => focus.has(child.id))
        && geometry.parents.some((parent) => focus.has(parent.id));
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", geometry.path);
      path.setAttribute("class", `tree-link ${family.status || ""} ${onFocus ? "is-focus" : ""}`);
      path.setAttribute("data-family-id", family.id);
      if (!familyRelationshipCanonical(family)) {
        path.classList.add("is-conditional");
        path.setAttribute("stroke-dasharray", "7 5");
        path.setAttribute("data-canonical-relationship", "false");
        path.setAttribute("data-relationship-identity-status", family.relationshipIdentityStatus || "unresolved");
        const title = document.createElementNS(namespace, "title");
        title.textContent = `${familyRelationshipQualifier(family)} · ${family.status || "unresolved"}`;
        path.append(title);
      }
      paths.push(path);
    }
    const families = tree.families || [];
    const visibleUnions = (tree.unions || []).filter((union) => union.partnerIds.every((id) => visible.has(id)));
    visibleUnions.forEach((union, index) => {
      const linkedThroughChild = families.some((family) => {
        const parentIds = family.parentIds || [];
        const childIds = family.childIds || [];
        return union.partnerIds.every((id) => parentIds.includes(id)) && childIds.some((id) => visible.has(id));
      });
      if (linkedThroughChild) return;
      const [first, second] = union.partnerIds.map((id) => positions.get(id));
      if (!first || !second || first.y !== second.y) return;
      const firstX = first.x + cardWidth / 2;
      const secondX = second.x + cardWidth / 2;
      const centerY = first.y + cardHeight / 2 + ((index % 3) - 1) * 9;
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", `M ${firstX} ${centerY} H ${secondX}`);
      path.setAttribute("class", `tree-union ${union.status || ""}`);
      path.setAttribute("data-union-id", union.id);
      paths.push(path);
      const marker = document.createElementNS(namespace, "circle");
      marker.setAttribute("cx", (firstX + secondX) / 2);
      marker.setAttribute("cy", centerY);
      marker.setAttribute("r", 4);
      marker.setAttribute("class", `tree-union-marker ${union.status || ""}`);
      marker.setAttribute("data-union-id", union.id);
      paths.push(marker);
    });
    this.svg.replaceChildren(...paths);
  }

  sourceReportedPaths({ network, people, positions, cardWidth, cardHeight }) {
    const visible = new Set(people.map((person) => person.id));
    const namespace = "http://www.w3.org/2000/svg";
    const paths = [];
    for (const edge of network?.edges || []) {
      if (!visible.has(edge.fromTreeNodeId) || !visible.has(edge.toTreeNodeId)) continue;
      const from = positions.get(edge.fromTreeNodeId);
      const to = positions.get(edge.toTreeNodeId);
      if (!from || !to) continue;
      const fromX = from.x + cardWidth / 2;
      const fromY = from.y + cardHeight / 2;
      const toX = to.x + cardWidth / 2;
      const toY = to.y + cardHeight / 2;
      const bendY = fromY + (toY - fromY) / 2;
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", `M ${fromX} ${fromY} V ${bendY} H ${toX} V ${toY}`);
      path.setAttribute("class", `tree-source-reported-link is-external is-unresolved ${edge.outsideFilterConnector ? "is-filter-connector" : ""}`);
      if (edge.outsideFilterConnector) path.setAttribute("opacity", ".28");
      path.setAttribute("data-tree-edge-id", edge.treeEdgeId || edge.id || "");
      path.setAttribute("data-relation-type", edge.type || "source-reported");
      path.setAttribute("data-filter-connector", String(edge.outsideFilterConnector === true));
      path.setAttribute("data-canonical-relationship", String(edge.canonicalRelationship === true));
      const title = document.createElementNS(namespace, "title");
      title.textContent = edge.outsideFilterConnector
        ? `Łącznik poza filtrem · zewnętrzna relacja kandydacka · ${edge.type || "typ nieokreślony"}`
        : `Zewnętrzna relacja kandydacka · nierozstrzygnięta · ${edge.type || "typ nieokreślony"}`;
      path.append(title);
      paths.push(path);
    }
    return paths;
  }

  drawSourceReportedNetwork(options) {
    const paths = this.sourceReportedPaths(options);
    this.svg.replaceChildren(...paths);
  }

  drawConditionalExternalOverlay({
    projection,
    canonicalPeople,
    candidatePeople,
    positions,
    cardWidth,
    cardHeight,
  }) {
    if (!projection?.network) return;
    const namespace = "http://www.w3.org/2000/svg";
    const sourcePaths = this.sourceReportedPaths({
      network: { ...projection.network, edges: projection.edges },
      people: candidatePeople,
      positions,
      cardWidth,
      cardHeight,
    });
    const visibleCanonicalIds = new Set(canonicalPeople.map((person) => person.id));
    const visibleCandidateIds = new Set(candidatePeople.map((person) => person.id));
    const bridges = [];
    for (const attachment of projection.attachments || []) {
      if (!visibleCandidateIds.has(attachment.fromTreeNodeId)
        || !visibleCanonicalIds.has(attachment.toTreePersonId)) continue;
      const from = positions.get(attachment.fromTreeNodeId);
      const to = positions.get(attachment.toTreePersonId);
      if (!from || !to) continue;
      const fromX = from.x;
      const fromY = from.y + cardHeight / 2;
      const toX = to.x + cardWidth;
      const toY = to.y + cardHeight / 2;
      const bendX = toX + (fromX - toX) / 2;
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", `M ${toX} ${toY} H ${bendX} V ${fromY} H ${fromX}`);
      path.setAttribute("class", "tree-source-reported-link tree-conditional-identity-attachment is-external is-unresolved");
      path.setAttribute("data-conditional-attachment-id", attachment.id);
      path.setAttribute("data-from-tree-node-id", attachment.fromTreeNodeId);
      path.setAttribute("data-to-tree-person-id", attachment.toTreePersonId);
      path.setAttribute("data-identity-status", attachment.identityStatus);
      path.setAttribute("data-identity-decision-tier", attachment.identityDecisionTier);
      path.setAttribute("data-canonical-relationship", "false");
      path.setAttribute("data-relationship-identity-status", "unresolved");
      path.setAttribute("data-automatic-merge", "false");
      const title = document.createElementNS(namespace, "title");
      title.textContent = "Powiązanie profilu; relacje zewnętrzne niezweryfikowane";
      path.append(title);
      bridges.push(path);
    }
    this.svg.append(...sourcePaths, ...bridges);
  }

  drawConditionalAncestryOverlay({ projection, positions, cardWidth, cardHeight }) {
    const namespace = "http://www.w3.org/2000/svg";
    const paths = [];
    for (const edge of projection?.edges || []) {
      const from = positions.get(edge.fromDisplayId);
      const to = positions.get(edge.toDisplayId);
      if (!from || !to) continue;
      const fromX = from.x + cardWidth / 2;
      const fromY = from.y + cardHeight;
      const toX = to.x + cardWidth / 2;
      const toY = to.y;
      const bendY = fromY + (toY - fromY) / 2;
      const path = document.createElementNS(namespace, "path");
      path.setAttribute("d", `M ${fromX} ${fromY} V ${bendY} H ${toX} V ${toY}`);
      path.setAttribute("class", `tree-source-reported-link tree-conditional-ancestry-link is-unresolved relation-${edge.relationKind}`);
      path.setAttribute("data-conditional-ancestry-edge-id", edge.id);
      path.setAttribute("data-direction", "child_to_parent");
      path.setAttribute("data-relation-kind", edge.relationKind);
      path.setAttribute("data-canonical-relationship", "false");
      path.setAttribute("data-path-uncertain", String(edge.pathUncertain === true));
      const title = document.createElementNS(namespace, "title");
      title.textContent = edge.relationKind === "source_stated"
        ? "Rodzicielstwo wskazane przez źródło · nierozstrzygnięte · niekanoniczne"
        : "Ustrukturyzowana hipoteza rodzicielstwa · nierozstrzygnięta · niekanoniczna";
      path.append(title);
      paths.push(path);
    }
    this.svg.append(...paths);
  }
}
