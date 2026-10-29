const NODE_RADIUS = {
  identity: 14,
  household: 13,
  occurrence: 11,
  event: 9,
  source: 10,
  bridge: 11,
  scope: 28,
  cluster: 20,
};

function overlaps(left, right, padding = 3) {
  return !(
    left.x + left.width + padding < right.x
    || right.x + right.width + padding < left.x
    || left.y + left.height + padding < right.y
    || right.y + right.height + padding < left.y
  );
}

function nodeColor(kind) {
  return {
    identity: "#287eb0",
    occurrence: "#285c53",
    event: "#a95637",
    source: "#9b6e1f",
    household: "#58758c",
    bridge: "#fffdf8",
    scope: "#d8c69f",
    cluster: "#6f5689",
  }[kind] || "#58758c";
}

function edgeColor(kind) {
  return {
    candidate: "rgba(182,139,60,.72)",
    "same-person": "rgba(40,126,176,.62)",
    bridge: "rgba(112,83,143,.7)",
    relation: "rgba(40,92,83,.48)",
    "external-relation": "rgba(182,139,60,.78)",
    source: "rgba(155,110,31,.58)",
    participation: "rgba(169,86,55,.46)",
    "event-link": "rgba(120,112,98,.34)",
    "scope-link": "rgba(112,83,143,.42)",
    "cluster-link": "rgba(111,86,137,.38)",
    "cluster-member": "rgba(111,86,137,.28)",
  }[kind] || "rgba(120,112,98,.25)";
}

function distanceToSegment(x, y, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  if (!lengthSquared) return Math.hypot(x - ax, y - ay);
  const progress = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / lengthSquared));
  return Math.hypot(x - (ax + progress * dx), y - (ay + progress * dy));
}

function drawReviewMarker(ctx, item, x, y, radius, scale) {
  const state = item.review?.state;
  if (!state) return;
  const markerRadius = Math.max(3.2, 4.2 * Math.min(1.2, scale));
  const markerX = x + radius * scale * .72;
  const markerY = y - radius * scale * .72;
  ctx.save();
  ctx.beginPath();
  ctx.arc(markerX, markerY, markerRadius + 1.5, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255,253,248,.96)";
  ctx.fill();
  if (state === "partial_agent_review") {
    ctx.beginPath();
    ctx.arc(markerX, markerY, markerRadius, -.5 * Math.PI, .5 * Math.PI);
    ctx.lineTo(markerX, markerY);
    ctx.fillStyle = "#287eb0";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(markerX, markerY, markerRadius, .5 * Math.PI, 1.5 * Math.PI);
    ctx.lineTo(markerX, markerY);
    ctx.fillStyle = "#b68b3c";
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.arc(markerX, markerY, markerRadius, 0, Math.PI * 2);
    ctx.fillStyle = state === "human_reviewed"
      ? "#4e8b62"
      : state === "human_review_required"
        ? "#b68b3c"
        : state === "agent_approved"
          ? "#287eb0"
          : "#8b877e";
    ctx.fill();
  }
  ctx.restore();
}

export class CanvasGraphRenderer {
  constructor(canvas, context) {
    this.canvas = canvas;
    this.context = context;
    this.minimap = document.querySelector("#graph-minimap");
    this.minimapContext = this.minimap?.getContext("2d") || null;
    this.legendPanel = document.querySelector(".graph-legend-panel");
  }

  screenX(value, state) { return value * state.scale + state.offsetX; }
  screenY(value, state) { return value * state.scale + state.offsetY; }

  isVisible(item, state, margin = 150) {
    const x = this.screenX(item.x, state);
    const y = this.screenY(item.y, state);
    return x >= -margin && y >= -margin && x <= state.width + margin && y <= state.height + margin;
  }

  hit(state, x, y) {
    let result = null;
    let closest = Infinity;
    for (const item of state.nodes) {
      if (!this.isVisible(item, state, 30)) continue;
      const distance = Math.hypot(x - this.screenX(item.x, state), y - this.screenY(item.y, state));
      const radius = (NODE_RADIUS[item.kind] || 11) * Math.max(.8, state.scale);
      if (distance < radius + 6 && distance < closest) {
        result = item;
        closest = distance;
      }
    }
    return result;
  }

  hitEdge(state, x, y) {
    let result = null;
    let closest = Infinity;
    for (const edge of state.edges) {
      if (edge.kind !== "external-relation") continue;
      const a = state.nodeById.get(edge.from);
      const b = state.nodeById.get(edge.to);
      if (!a || !b || (!this.isVisible(a, state, 30) && !this.isVisible(b, state, 30))) continue;
      const ax = this.screenX(a.x, state);
      const ay = this.screenY(a.y, state);
      const bx = this.screenX(b.x, state);
      const by = this.screenY(b.y, state);
      const route = Number(edge.parallelIndex || 0);
      let distance;
      if (!route) {
        distance = distanceToSegment(x, y, ax, ay, bx, by);
      } else {
        const dx = bx - ax;
        const dy = by - ay;
        const length = Math.max(1, Math.hypot(dx, dy));
        const bend = Math.min(42, length * .22) * route;
        const controlX = (ax + bx) / 2 - (dy / length) * bend;
        const controlY = (ay + by) / 2 + (dx / length) * bend;
        distance = Infinity;
        let previousX = ax;
        let previousY = ay;
        for (let step = 1; step <= 16; step += 1) {
          const progress = step / 16;
          const inverse = 1 - progress;
          const nextX = inverse * inverse * ax + 2 * inverse * progress * controlX + progress * progress * bx;
          const nextY = inverse * inverse * ay + 2 * inverse * progress * controlY + progress * progress * by;
          distance = Math.min(distance, distanceToSegment(x, y, previousX, previousY, nextX, nextY));
          previousX = nextX;
          previousY = nextY;
        }
      }
      if (distance <= 7 && distance < closest) {
        result = edge;
        closest = distance;
      }
    }
    return result;
  }

  drawEdge(ctx, edge, a, b, state) {
    const ax = this.screenX(a.x, state);
    const ay = this.screenY(a.y, state);
    const bx = this.screenX(b.x, state);
    const by = this.screenY(b.y, state);
    const dx = bx - ax;
    const dy = by - ay;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const route = Number(edge.parallelIndex || 0);
    const bend = Math.min(42, distance * .22) * route;
    const controlX = (ax + bx) / 2 - (dy / distance) * bend;
    const controlY = (ay + by) / 2 + (dx / distance) * bend;
    const crossIslandEvidence = a.layoutMode === "islands"
      && b.layoutMode === "islands"
      && a.semanticClusterId !== b.semanticClusterId
      && ["event-link", "participation", "source"].includes(edge.kind);

    ctx.save();
    ctx.globalAlpha = edge.focusRole === "dimmed"
      ? .14
      : crossIslandEvidence && !state.focusedNodeId ? .18 : 1;
    ctx.beginPath();
    if (edge.kind === "external-relation") ctx.setLineDash([7, 5]);
    else if (edge.kind === "candidate") ctx.setLineDash([5, 5]);
    else if (edge.kind === "household" && edge.item?.residenceEvidenceStatus === "unconfirmed") ctx.setLineDash([4, 4]);
    else if (edge.kind === "bridge") ctx.setLineDash([3, 4]);
    else if (edge.kind === "source") ctx.setLineDash([2, 3]);
    else if (crossIslandEvidence) ctx.setLineDash([2, 6]);
    else ctx.setLineDash([]);
    ctx.strokeStyle = edgeColor(edge.kind);
    const selected = state.selectedEdge?.id === edge.id;
    ctx.lineWidth = (["candidate", "bridge", "same-person", "source", "external-relation"].includes(edge.kind) ? 1.6 : 1)
      + (edge.focusRole === "active" && state.focusedNodeId ? .5 : 0)
      + (selected ? 2 : 0);
    ctx.moveTo(ax, ay);
    if (route) ctx.quadraticCurveTo(controlX, controlY, bx, by);
    else ctx.lineTo(bx, by);
    ctx.stroke();
    ctx.restore();
  }

  drawNodeShape(ctx, item, x, y, radius, scale) {
    ctx.beginPath();
    if (item.kind === "event") {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(Math.PI / 4);
      const size = radius * scale * 1.4;
      ctx.rect(-size / 2, -size / 2, size, size);
      ctx.restore();
    } else if (item.kind === "cluster") {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(Math.PI / 4);
      const size = radius * scale * 1.45;
      ctx.roundRect(-size / 2, -size / 2, size, size, 3);
      ctx.restore();
    } else if (item.kind === "household") {
      const size = radius * scale * 1.55;
      ctx.rect(x - size / 2, y - size / 2, size, size);
    } else if (item.kind === "source") {
      const width = radius * scale * 1.45;
      const height = radius * scale * 1.8;
      ctx.rect(x - width / 2, y - height / 2, width, height);
      ctx.moveTo(x + width * .1, y - height / 2);
      ctx.lineTo(x + width / 2, y - height * .1);
    } else {
      ctx.arc(x, y, radius * scale, 0, Math.PI * 2);
    }
  }

  labelBox(ctx, item, x, y, radius, selected, matches, occupied, nodeObstacles, state) {
    const label = item.label.length > 30 ? `${item.label.slice(0, 28)}…` : item.label;
    // Przy szerokim przeglądzie zachowujemy wszystkie imiona/etykiety, ale
    // drugą linię (rola, rok, akt) pokazujemy dopiero po zbliżeniu lub fokusie.
    // Dzięki temu informacja nie znika, a kilkadziesiąt kart nie zamienia się
    // w ścianę dwuwierszowego tekstu.
    const showDetail = state.scale >= .58
      || selected
      || matches
      || item.focusRole === "focused"
      || item.focusRole === "first-circle";
    const detail = showDetail && item.labelDetail
      ? (item.labelDetail.length > 42 ? `${item.labelDetail.slice(0, 40)}…` : item.labelDetail)
      : null;
    const primaryFont = `${selected || item.kind === "scope" ? 600 : 500} ${selected || item.kind === "scope" ? 11 : 9}px Avenir Next, Avenir, sans-serif`;
    ctx.font = primaryFont;
    const primaryWidth = ctx.measureText(label).width;
    ctx.font = "500 8px Avenir Next, Avenir, sans-serif";
    const detailWidth = detail ? ctx.measureText(detail).width : 0;
    const width = Math.max(primaryWidth, detailWidth) + 10;
    const height = detail ? 25 : 14;
    const scaledRadius = radius * Math.max(.65, state.scale);
    const placements = [
      { x: x - width / 2, y: y + scaledRadius + 4 },
      { x: x - width / 2, y: y - scaledRadius - height - 4 },
      { x: x + scaledRadius + 6, y: y - height / 2 },
      { x: x - scaledRadius - width - 6, y: y - height / 2 },
    ];
    const available = (candidate) => {
      const sized = candidateWithSize(candidate, width, height);
      return sized.x >= 2
        && sized.y >= 2
        && sized.x + sized.width <= state.width - 2
        && sized.y + sized.height <= state.height - 2
        && !occupied.some((other) => overlaps(sized, other))
        && !nodeObstacles.some((other) => other.id !== item.id && overlaps(sized, other, 2));
    };
    let box = placements.find(available);
    let displaced = false;
    if (!box) {
      const seed = Math.abs(hashCode(item.id));
      const startAngle = (seed % 360) * Math.PI / 180;
      for (let index = 0; index < 48; index += 1) {
        const angle = startAngle + index * 2.399963229728653;
        const distance = 30 + Math.sqrt(index + 1) * 18;
        const candidate = {
          x: x + Math.cos(angle) * distance - width / 2,
          y: y + Math.sin(angle) * distance - height / 2,
        };
        if (available(candidate)) {
          box = candidate;
          break;
        }
      }
      box ||= { x: x - width / 2, y: y + scaledRadius + 42 };
      displaced = true;
    }
    box = candidateWithSize(box, width, height);
    occupied.push(box);

    if (displaced) {
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x, y + scaledRadius);
      ctx.lineTo(box.x + box.width / 2, box.y);
      ctx.strokeStyle = "rgba(120,112,98,.35)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
    }
    ctx.fillStyle = item.focusRole === "dimmed" ? "rgba(248,245,238,.76)" : "rgba(255,253,248,.94)";
    ctx.fillRect(box.x, box.y, box.width, box.height);
    ctx.strokeStyle = selected
      ? "rgba(40,92,83,.55)"
      : matches ? "rgba(155,110,31,.62)" : "rgba(208,198,181,.72)";
    ctx.lineWidth = 1;
    ctx.strokeRect(box.x, box.y, box.width, box.height);
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = item.focusRole === "dimmed" ? "#77736b" : "#393933";
    ctx.font = primaryFont;
    ctx.fillText(label, box.x + box.width / 2, box.y + 10);
    if (detail) {
      ctx.font = "500 8px Avenir Next, Avenir, sans-serif";
      ctx.fillStyle = item.focusRole === "dimmed" ? "#8b877e" : "#6f6b63";
      ctx.fillText(detail, box.x + box.width / 2, box.y + 20);
    }
    return displaced;
  }

  drawMinimap(state) {
    if (!this.minimap || !this.minimapContext || this.minimap.offsetParent === null || !state.nodes.length) return;
    const rect = this.minimap.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width));
    const height = Math.max(1, Math.floor(rect.height));
    if (this.minimap.width !== Math.floor(width * ratio) || this.minimap.height !== Math.floor(height * ratio)) {
      this.minimap.width = Math.floor(width * ratio);
      this.minimap.height = Math.floor(height * ratio);
    }
    const ctx = this.minimapContext;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const xs = state.nodes.map((item) => item.x);
    const ys = state.nodes.map((item) => item.y);
    const minX = Math.min(...xs) - 30;
    const maxX = Math.max(...xs) + 30;
    const minY = Math.min(...ys) - 30;
    const maxY = Math.max(...ys) + 30;
    const scale = Math.min(width / Math.max(1, maxX - minX), height / Math.max(1, maxY - minY));
    const mx = (x) => (x - minX) * scale;
    const my = (y) => (y - minY) * scale;
    ctx.fillStyle = "rgba(255,253,248,.9)";
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(120,112,98,.18)";
    ctx.lineWidth = 1;
    for (const edge of state.edges) {
      const a = state.nodeById.get(edge.from);
      const b = state.nodeById.get(edge.to);
      if (!a || !b) continue;
      ctx.beginPath();
      ctx.moveTo(mx(a.x), my(a.y));
      ctx.lineTo(mx(b.x), my(b.y));
      ctx.stroke();
    }
    for (const item of state.nodes) {
      ctx.beginPath();
      ctx.arc(mx(item.x), my(item.y), item.kind === "identity" ? 2.5 : 1.6, 0, Math.PI * 2);
      ctx.fillStyle = nodeColor(item.kind);
      ctx.fill();
    }
    const viewport = {
      x: (-state.offsetX / state.scale - minX) * scale,
      y: (-state.offsetY / state.scale - minY) * scale,
      width: (state.width / state.scale) * scale,
      height: (state.height / state.scale) * scale,
    };
    ctx.strokeStyle = "rgba(40,92,83,.8)";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(viewport.x, viewport.y, viewport.width, viewport.height);
  }

  drawLayoutGuides(ctx, state) {
    if (!state.layoutGuides?.length || state.scale < .1) return;
    const centerX = this.screenX(0, state);
    const centerY = this.screenY(0, state);
    for (const guide of state.layoutGuides) {
      const radius = guide.radius * state.scale;
      if (centerX + radius < 0 || centerX - radius > state.width || centerY + radius < 0 || centerY - radius > state.height) continue;
      ctx.save();
      ctx.beginPath();
      ctx.setLineDash(guide.kind === "fan" || guide.kind === "scope" ? [7, 7] : guide.kind === "core" ? [2, 4] : [3, 6]);
      ctx.arc(centerX, centerY, radius, guide.startAngle ?? 0, guide.endAngle ?? Math.PI * 2);
      ctx.strokeStyle = guide.kind === "fan" || guide.kind === "scope" ? "rgba(155,110,31,.24)" : "rgba(40,92,83,.14)";
      ctx.lineWidth = guide.kind === "fan" || guide.kind === "scope" ? 1.4 : 1;
      ctx.stroke();
      const labelY = guide.kind === "descendant" ? centerY + radius - 14 : centerY - radius + 14;
      if (labelY >= 8 && labelY <= state.height - 8) {
        ctx.setLineDash([]);
        ctx.font = "600 9px Avenir Next, Avenir, sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        const width = ctx.measureText(guide.label).width + 10;
        const labelX = Math.max(8, Math.min(state.width - width - 8, centerX + 10));
        ctx.fillStyle = "rgba(248,245,238,.88)";
        ctx.fillRect(labelX - 4, labelY - 7, width, 14);
        ctx.fillStyle = guide.kind === "fan" || guide.kind === "scope" ? "rgba(123,84,25,.78)" : "rgba(40,92,83,.68)";
        ctx.fillText(guide.label, labelX, labelY);
      }
      ctx.restore();
    }
  }

  drawClusterHulls(ctx, state, visibleNodes) {
    const groups = new Map();
    for (const item of visibleNodes) {
      if (!(item.semanticClusterId?.startsWith("satellite:") || item.semanticClusterId?.startsWith("island:")) || !item.semanticClusterLabel) continue;
      const group = groups.get(item.semanticClusterId) || [];
      group.push(item);
      groups.set(item.semanticClusterId, group);
    }
    for (const items of groups.values()) {
      if (items.length < 3) continue;
      const xs = items.map((item) => this.screenX(item.x, state));
      const ys = items.map((item) => this.screenY(item.y, state));
      const padding = Math.max(20, 34 * state.scale);
      const left = Math.min(...xs) - padding;
      const top = Math.min(...ys) - padding;
      const right = Math.max(...xs) + padding;
      const bottom = Math.max(...ys) + padding;
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(left, top, right - left, bottom - top, 18);
      ctx.fillStyle = "rgba(216,198,159,.08)";
      ctx.fill();
      ctx.setLineDash([5, 6]);
      ctx.strokeStyle = "rgba(155,110,31,.26)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = "600 9px Avenir Next, Avenir, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      const label = items[0].semanticClusterLabel;
      const width = ctx.measureText(label).width + 12;
      ctx.fillStyle = "rgba(248,245,238,.94)";
      ctx.fillRect(left + 10, top - 8, width, 16);
      ctx.fillStyle = "rgba(123,84,25,.82)";
      ctx.fillText(label, left + 16, top + 4);
      ctx.restore();
    }
  }

  draw(state, searchText) {
    if (!state.width || !state.height) return;
    this.canvas.dataset.scale = state.scale.toFixed(4);
    this.canvas.dataset.offsetX = state.offsetX.toFixed(2);
    this.canvas.dataset.offsetY = state.offsetY.toFixed(2);
    const ctx = this.context;
    const visibleNodes = state.nodes.filter((item) => this.isVisible(item, state));
    const visibleIds = new Set(visibleNodes.map((item) => item.id));
    const visibleEdges = state.edges.filter((edge) => visibleIds.has(edge.from) || visibleIds.has(edge.to));
    this.canvas.dataset.renderedNodeCount = String(visibleNodes.length);
    this.canvas.dataset.culledNodeCount = String(state.nodes.length - visibleNodes.length);
    this.canvas.dataset.renderedEdgeCount = String(visibleEdges.length);
    ctx.clearRect(0, 0, state.width, state.height);
    ctx.save();
    ctx.lineCap = "round";
    this.drawLayoutGuides(ctx, state);
    this.drawClusterHulls(ctx, state, visibleNodes);
    for (const edge of visibleEdges) {
      const a = state.nodeById.get(edge.from);
      const b = state.nodeById.get(edge.to);
      if (a && b) this.drawEdge(ctx, edge, a, b, state);
    }

    const occupied = [];
    if (this.legendPanel?.open) {
      occupied.push({
        x: 8,
        y: 8,
        width: Math.min(440, Math.max(0, state.width - 16)),
        height: 112,
      });
    }
    if (this.minimap?.offsetParent !== null) {
      occupied.push({ x: 8, y: Math.max(0, state.height - 112), width: 162, height: 104 });
    }
    const nodeObstacles = visibleNodes.map((item) => {
      const radius = (NODE_RADIUS[item.kind] || 11) * Math.max(.65, state.scale) + 3;
      return {
        id: item.id,
        x: this.screenX(item.x, state) - radius,
        y: this.screenY(item.y, state) - radius,
        width: radius * 2,
        height: radius * 2,
      };
    });
    let displacedLabelCount = 0;
    let renderedLabelCount = 0;
    for (const item of visibleNodes) {
      const x = this.screenX(item.x, state);
      const y = this.screenY(item.y, state);
      const selected = state.selected?.id === item.id;
      const matches = Boolean(item.searchMatch)
        || Boolean(state.query && searchText(item).toLocaleLowerCase("pl-PL").includes(state.query));
      const radius = NODE_RADIUS[item.kind] || 11;
      const nodeScale = Math.max(.65, state.scale);
      ctx.save();
      ctx.globalAlpha = item.focusRole === "dimmed" ? .24 : 1;
      if (selected || matches) {
        ctx.beginPath();
        if (matches) ctx.setLineDash([3, 3]);
        ctx.arc(x, y, radius * nodeScale + (selected ? 7 : 6), 0, Math.PI * 2);
        if (selected) {
          ctx.fillStyle = "rgba(40,92,83,.13)";
          ctx.fill();
        }
        ctx.strokeStyle = matches ? "#9b6e1f" : "rgba(40,92,83,.55)";
        ctx.lineWidth = matches ? 2 : 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
      }
      this.drawNodeShape(ctx, item, x, y, radius, nodeScale);
      ctx.fillStyle = nodeColor(item.kind);
      ctx.fill();
      if (item.kind === "household" && item.item?.residenceEvidenceStatus === "unconfirmed") ctx.setLineDash([4, 3]);
      ctx.strokeStyle = item.kind === "bridge"
        ? "#70538f"
        : item.kind === "scope"
          ? "#8d6d35"
          : item.kind === "household" && item.item?.residenceEvidenceStatus === "unconfirmed"
            ? "#b68b3c"
            : "rgba(255,255,255,.9)";
      ctx.lineWidth = item.kind === "scope" ? 2.5 : 2;
      ctx.stroke();
      if (item.kind === "scope") {
        ctx.beginPath();
        ctx.arc(x, y, radius * nodeScale + 7, 0, Math.PI * 2);
        ctx.setLineDash([3, 4]);
        ctx.strokeStyle = "rgba(141,109,53,.48)";
        ctx.lineWidth = 1.4;
        ctx.stroke();
      }
      ctx.restore();

      const roleStyle = {
        core: ["#287eb0", []],
        "fan-direct": ["#4e8b62", []],
        "fan-extended": ["#7e9d64", [3, 3]],
        bridge: ["#70538f", [2, 3]],
        candidate: ["#b68b3c", [4, 3]],
      }[item.visibilityRole];
      if (roleStyle) {
        ctx.save();
        ctx.globalAlpha = item.focusRole === "dimmed" ? .24 : 1;
        ctx.beginPath();
        ctx.setLineDash(roleStyle[1]);
        ctx.arc(x, y, radius * nodeScale + 4, 0, Math.PI * 2);
        ctx.strokeStyle = roleStyle[0];
        ctx.lineWidth = 1.8;
        ctx.stroke();
        ctx.restore();
      }
      drawReviewMarker(ctx, item, x, y, radius, nodeScale);
      const priorityLabel = item.kind === "identity"
        || item.kind === "cluster"
        || item.kind === "scope"
        || ["core", "bridge"].includes(item.visibilityRole)
        || (item.visibilityRole === "candidate" && state.scale >= .45);
      const adaptiveLabelVisible = Boolean(
        selected
        || matches
        || (item.labelVisible && (
          state.focusedNodeId
          || state.scopeId !== "all"
          || state.scale >= .68
          || priorityLabel
        )),
      );
      if (adaptiveLabelVisible) {
        renderedLabelCount += 1;
        if (this.labelBox(ctx, item, x, y, radius, selected, matches, occupied, nodeObstacles, state)) displacedLabelCount += 1;
      }
    }
    ctx.restore();
    this.canvas.dataset.displacedLabelCount = String(displacedLabelCount);
    this.canvas.dataset.renderedLabelCount = String(renderedLabelCount);
    this.drawMinimap(state);
  }
}

function candidateWithSize(candidate, width, height) {
  return { ...candidate, width, height };
}

function hashCode(value) {
  let hash = 0;
  for (const character of String(value)) hash = ((hash << 5) - hash + character.codePointAt(0)) | 0;
  return hash;
}
