const SCENARIO_BASES = new Set(["canonical", "external_tree", "local_source"]);
export const MAX_VISITED_PATHS = 32;

function isPublicId(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function uniquePublicIds(values) {
  return [...new Set(Array.isArray(values) ? values.filter(isPublicId) : [])];
}

function invalidScenario(message) {
  throw new TypeError(`Nieprawidłowy scenariusz drzewa przodków: ${message}.`);
}

export function validateFocusedAncestryScenario(scenario) {
  if (!scenario || typeof scenario !== "object") invalidScenario("brak danych");
  if (!isPublicId(scenario.key) || !isPublicId(scenario.label)) invalidScenario("brak publicznego klucza lub etykiety");
  if (!SCENARIO_BASES.has(scenario.basis)) invalidScenario("nieobsługiwana podstawa");
  if (!isPublicId(scenario.rootNodeId)) invalidScenario("brak publicznego korzenia");
  if (!Array.isArray(scenario.nodes) || !Array.isArray(scenario.edges) || !Array.isArray(scenario.gaps)) {
    invalidScenario("oczekiwano tablic węzłów, krawędzi i luk");
  }

  const nodeById = new Map();
  for (const item of scenario.nodes) {
    if (!item || !isPublicId(item.id) || !isPublicId(item.label) || nodeById.has(item.id)) {
      invalidScenario("węzeł nie ma unikalnego publicznego ID i etykiety");
    }
    nodeById.set(item.id, item);
  }
  if (!nodeById.has(scenario.rootNodeId)) invalidScenario("korzeń nie wskazuje widocznego węzła");

  const edgeById = new Map();
  const outgoing = new Map();
  for (const edge of scenario.edges) {
    if (!edge || !isPublicId(edge.id) || edgeById.has(edge.id)) invalidScenario("krawędź nie ma unikalnego publicznego ID");
    if (!isPublicId(edge.fromNodeId) || !isPublicId(edge.toNodeId)
      || !nodeById.has(edge.fromNodeId) || !nodeById.has(edge.toNodeId)) {
      invalidScenario("krawędź wskazuje nieobecny endpoint");
    }
    if (!isPublicId(edge.status)) invalidScenario("krawędź nie ma jawnego statusu");
    if (typeof edge.canonicalRelationship !== "boolean") invalidScenario("krawędź nie określa rodzaju relacji");
    edgeById.set(edge.id, edge);
    const values = outgoing.get(edge.fromNodeId) || [];
    values.push(edge);
    outgoing.set(edge.fromNodeId, values);
  }
  for (const gap of scenario.gaps) {
    if (!gap || !isPublicId(gap.nodeId) || !nodeById.has(gap.nodeId)) invalidScenario("luka wskazuje nieobecny endpoint");
  }

  return {
    source: scenario,
    nodeById,
    edgeById,
    outgoing,
  };
}

function defaultScenarioState(rootNodeId) {
  return {
    focusPath: [{ nodeId: rootNodeId, viaEdgeId: null }],
    selectedNodeId: null,
    openBranchNodeIds: [rootNodeId],
    bookmarkedNodeIds: [],
    visitedPaths: [],
  };
}

function validatedFocusPath(rawPath, model) {
  if (!Array.isArray(rawPath) || !rawPath.length) return null;
  const focusPath = [];
  for (let index = 0; index < rawPath.length; index += 1) {
    const step = rawPath[index];
    if (!step || !isPublicId(step.nodeId) || !model.nodeById.has(step.nodeId)) return null;
    if (index === 0) {
      if (step.nodeId !== model.source.rootNodeId) return null;
      focusPath.push({ nodeId: step.nodeId, viaEdgeId: null });
      continue;
    }
    if (!isPublicId(step.viaEdgeId)) return null;
    const edge = model.edgeById.get(step.viaEdgeId);
    if (!edge || edge.fromNodeId !== focusPath[index - 1].nodeId || edge.toNodeId !== step.nodeId
      || focusPath.some((item) => item.nodeId === step.nodeId)) return null;
    focusPath.push({ nodeId: step.nodeId, viaEdgeId: step.viaEdgeId });
  }
  return focusPath;
}

function focusPathKey(path) {
  return JSON.stringify(path.map(({ nodeId, viaEdgeId }) => [nodeId, viaEdgeId]));
}

function validatedVisitedPaths(values, model) {
  const result = [];
  const seen = new Set();
  for (const value of Array.isArray(values) ? values : []) {
    const focusPath = validatedFocusPath(value?.focusPath ?? value, model);
    if (!focusPath || focusPath.length < 2) continue;
    const key = focusPathKey(focusPath);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({
      focusPath,
      selectedNodeId: model.nodeById.has(value?.selectedNodeId) ? value.selectedNodeId : null,
    });
  }
  return result.slice(-MAX_VISITED_PATHS);
}

function publicScenarioState(value, model) {
  const fallback = defaultScenarioState(model.source.rootNodeId);
  if (!value || typeof value !== "object") return fallback;
  const focusPath = validatedFocusPath(value.focusPath, model) || fallback.focusPath;
  return {
    focusPath,
    selectedNodeId: model.nodeById.has(value.selectedNodeId) ? value.selectedNodeId : null,
    openBranchNodeIds: uniquePublicIds(value.openBranchNodeIds).filter((id) => model.nodeById.has(id)),
    bookmarkedNodeIds: uniquePublicIds(value.bookmarkedNodeIds).filter((id) => model.nodeById.has(id)),
    visitedPaths: validatedVisitedPaths(value.visitedPaths, model),
  };
}

function rememberVisitedPath(state, focusPath) {
  const key = focusPathKey(focusPath);
  const next = state.visitedPaths.filter((item) => focusPathKey(item.focusPath) !== key);
  next.push({
    focusPath: focusPath.map(({ nodeId, viaEdgeId }) => ({ nodeId, viaEdgeId })),
    selectedNodeId: state.selectedNodeId,
  });
  return next.slice(-MAX_VISITED_PATHS);
}

function initialScenarioState(initialState, scenarioKey, model) {
  const stored = initialState?.version === 1 ? initialState?.scenarios?.[scenarioKey] : null;
  return publicScenarioState(stored, model);
}

function pathToNode(model, targetNodeId) {
  if (!model.nodeById.has(targetNodeId)) invalidScenario("wskazany fokus nie istnieje");
  const rootNodeId = model.source.rootNodeId;
  const queue = [{ nodeId: rootNodeId, path: [{ nodeId: rootNodeId, viaEdgeId: null }] }];
  const visited = new Set([rootNodeId]);
  while (queue.length) {
    const current = queue.shift();
    if (current.nodeId === targetNodeId) return current.path;
    for (const edge of model.outgoing.get(current.nodeId) || []) {
      if (visited.has(edge.toNodeId)) continue;
      visited.add(edge.toNodeId);
      queue.push({
        nodeId: edge.toNodeId,
        path: [...current.path, { nodeId: edge.toNodeId, viaEdgeId: edge.id }],
      });
    }
  }
  invalidScenario("wskazany fokus nie ma jawnej ścieżki od korzenia");
}

export class FocusedAncestryModel {
  constructor({ initialState = null } = {}) {
    this.initialState = initialState;
    this.scenarioStates = new Map();
    this.scenario = null;
    this.model = null;
    this.state = null;
  }

  setScenario(scenario, { focusNodeId = null } = {}) {
    const model = validateFocusedAncestryScenario(scenario);
    if (this.scenario && this.state) this.scenarioStates.set(this.scenario.key, this.state);
    this.scenario = scenario;
    this.model = model;
    this.state = this.scenarioStates.get(scenario.key)
      || initialScenarioState(this.initialState, scenario.key, model);
    this.state = publicScenarioState(this.state, model);
    if (focusNodeId != null) {
      if (!isPublicId(focusNodeId)) invalidScenario("wskazany fokus nie jest publicznym ID");
      this.state = {
        ...this.state,
        focusPath: pathToNode(model, focusNodeId),
        selectedNodeId: focusNodeId,
        openBranchNodeIds: uniquePublicIds([...this.state.openBranchNodeIds, focusNodeId]),
      };
      if (this.state.focusPath.length > 1) {
        this.state = { ...this.state, visitedPaths: rememberVisitedPath(this.state, this.state.focusPath) };
      }
    }
    this.scenarioStates.set(scenario.key, this.state);
    return this.frame();
  }

  frame() {
    if (!this.model || !this.state) return null;
    const focusStep = this.state.focusPath.at(-1);
    const root = this.model.nodeById.get(focusStep.nodeId);
    const pathNodeIds = new Set(this.state.focusPath.map((item) => item.nodeId));
    const parents = (this.model.outgoing.get(root.id) || []).map((edge) => {
      const node = this.model.nodeById.get(edge.toNodeId);
      const cyclic = pathNodeIds.has(node.id);
      const grandparents = (cyclic ? [] : this.model.outgoing.get(node.id) || []).map((parentEdge) => ({
        edge: parentEdge,
        node: this.model.nodeById.get(parentEdge.toNodeId),
        cyclic: cyclic || pathNodeIds.has(parentEdge.toNodeId) || parentEdge.toNodeId === root.id,
      }));
      return { edge, node, cyclic, grandparents };
    });
    return {
      root,
      parents,
      breadcrumbs: this.state.focusPath.map((step, index) => ({
        ...step,
        index,
        node: this.model.nodeById.get(step.nodeId),
      })),
    };
  }

  parentsOf(nodeId) {
    return (this.model?.outgoing.get(nodeId) || []).map((edge) => ({ edge, node: this.model.nodeById.get(edge.toNodeId) }));
  }

  gapsFor(nodeId) {
    return (this.scenario?.gaps || []).filter((gap) => gap.nodeId === nodeId);
  }

  select(nodeId) {
    if (!this.model?.nodeById.has(nodeId)) return false;
    const currentKey = focusPathKey(this.state.focusPath);
    this.state = {
      ...this.state,
      selectedNodeId: nodeId,
      visitedPaths: this.state.visitedPaths.map((item) => (
        focusPathKey(item.focusPath) === currentKey ? { ...item, selectedNodeId: nodeId } : item
      )),
    };
    this.scenarioStates.set(this.scenario.key, this.state);
    return true;
  }

  toggleBookmark(nodeId) {
    if (!this.model?.nodeById.has(nodeId)) return false;
    const values = new Set(this.state.bookmarkedNodeIds);
    if (values.has(nodeId)) values.delete(nodeId); else values.add(nodeId);
    this.state = { ...this.state, bookmarkedNodeIds: [...values] };
    this.scenarioStates.set(this.scenario.key, this.state);
    return true;
  }

  expand(nodeId, viaEdgeId, bridgeEdgeId = null) {
    const edge = this.model?.edgeById.get(viaEdgeId);
    const current = this.state?.focusPath.at(-1);
    if (!edge || !current || edge.toNodeId !== nodeId) return false;
    const bridge = bridgeEdgeId == null ? null : this.model.edgeById.get(bridgeEdgeId);
    const visibleParentEdge = edge.fromNodeId === current.nodeId
      || (bridge?.fromNodeId === current.nodeId
        && bridge.toNodeId === edge.fromNodeId
        && (this.model.outgoing.get(bridge.toNodeId) || []).some((secondEdge) => secondEdge.id === edge.id));
    if (!visibleParentEdge || this.state.focusPath.some((step) => step.nodeId === nodeId)) return false;
    let path = this.state.focusPath;
    if (edge.fromNodeId !== current.nodeId) {
      if (!bridge || path.some((step) => step.nodeId === bridge.toNodeId)) return false;
      path = [...path, { nodeId: bridge.toNodeId, viaEdgeId: bridge.id }];
    }
    const focusPath = [...path, { nodeId, viaEdgeId }];
    this.state = {
      ...this.state,
      focusPath,
      openBranchNodeIds: uniquePublicIds([...this.state.openBranchNodeIds, edge.fromNodeId, nodeId]),
      visitedPaths: rememberVisitedPath(this.state, focusPath),
    };
    this.scenarioStates.set(this.scenario.key, this.state);
    return true;
  }

  backTo(index) {
    if (!Number.isInteger(index) || index < 0 || index >= this.state.focusPath.length - 1) return false;
    this.state = { ...this.state, focusPath: this.state.focusPath.slice(0, index + 1) };
    this.scenarioStates.set(this.scenario.key, this.state);
    return true;
  }

  visitedWindows() {
    if (!this.model || !this.state) return [];
    return this.state.visitedPaths.map((item, index) => ({
      index,
      focusPath: item.focusPath,
      nodes: item.focusPath.map((step) => this.model.nodeById.get(step.nodeId)),
      selectedNodeId: item.selectedNodeId,
      current: focusPathKey(item.focusPath) === focusPathKey(this.state.focusPath),
    }));
  }

  revisitVisitedPath(index) {
    if (!Number.isInteger(index)) return false;
    const stored = this.state?.visitedPaths[index];
    const focusPath = validatedFocusPath(stored?.focusPath, this.model);
    if (!stored || !focusPath) return false;
    this.state = {
      ...this.state,
      focusPath,
      selectedNodeId: this.model.nodeById.has(stored.selectedNodeId) ? stored.selectedNodeId : null,
      openBranchNodeIds: uniquePublicIds([
        ...this.state.openBranchNodeIds,
        ...focusPath.map((step) => step.nodeId),
      ]),
    };
    this.scenarioStates.set(this.scenario.key, this.state);
    return true;
  }

  serializedState() {
    if (this.scenario && this.state) this.scenarioStates.set(this.scenario.key, this.state);
    const scenarios = {};
    for (const [key, state] of this.scenarioStates) {
      if (!isPublicId(key)) continue;
      scenarios[key] = {
        focusPath: state.focusPath.map(({ nodeId, viaEdgeId }) => ({ nodeId, viaEdgeId })),
        selectedNodeId: state.selectedNodeId,
        openBranchNodeIds: uniquePublicIds(state.openBranchNodeIds),
        bookmarkedNodeIds: uniquePublicIds(state.bookmarkedNodeIds),
        visitedPaths: state.visitedPaths.map((item) => ({
          focusPath: item.focusPath.map(({ nodeId, viaEdgeId }) => ({ nodeId, viaEdgeId })),
          selectedNodeId: item.selectedNodeId,
        })),
      };
    }
    return { version: 1, scenarios };
  }
}
