const STORAGE_KEY = "genea:graph-view:v1";
const LAYOUT_VERSION = 4;

export class GraphViewStateStore {
  constructor(storage = globalThis.sessionStorage) {
    this.storage = storage;
  }

  load() {
    try {
      const value = JSON.parse(this.storage?.getItem(STORAGE_KEY) || "null");
      if (!value || value.version !== 1) return null;
      const camera = value.camera
        && Number.isFinite(value.camera.scale)
        && Number.isFinite(value.camera.offsetX)
        && Number.isFinite(value.camera.offsetY)
        ? { scale: value.camera.scale, offsetX: value.camera.offsetX, offsetY: value.camera.offsetY }
        : null;
      return {
        mode: ["people", "fan", "clusters"].includes(value.mode) ? value.mode : "people",
        scopeId: typeof value.scopeId === "string" ? value.scopeId : "all",
        depth: value.depth === "all" ? "all" : Number.isInteger(value.depth) ? value.depth : 1,
        focusedNodeId: typeof value.focusedNodeId === "string" ? value.focusedNodeId : null,
        expandedIdentityIds: Array.isArray(value.expandedIdentityIds) ? value.expandedIdentityIds.filter((item) => typeof item === "string") : [],
        pinnedPositions: value.layoutVersion === LAYOUT_VERSION && Array.isArray(value.pinnedPositions)
          ? value.pinnedPositions.filter((item) => typeof item?.id === "string" && Number.isFinite(item.x) && Number.isFinite(item.y)).slice(0, 200)
          : [],
        ...(camera ? { camera } : {}),
      };
    } catch {
      return null;
    }
  }

  save(state) {
    try {
      this.storage?.setItem(STORAGE_KEY, JSON.stringify({ version: 1, layoutVersion: LAYOUT_VERSION, ...state }));
    } catch {
      // Brak storage nie może zablokować lokalnego UI.
    }
  }
}

export { STORAGE_KEY as GRAPH_VIEW_STORAGE_KEY };
