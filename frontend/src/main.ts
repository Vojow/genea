import { createPinia } from "pinia";
import { createApp, nextTick } from "vue";
import "maplibre-gl/dist/maplibre-gl.css";
import "../styles.css";
import "../tree/focused-ancestry.css";
import "../tree/focused-ancestry-integration.css";
import "../tree/unified-family-tree.css";
import { setWorkerUrl } from "maplibre-gl";

import App from "./App.vue";
import { appUrl } from "./base-path";
import { bootstrapLegacyViews, showLoadFailure } from "./legacy-bootstrap";
import { registerGeneaPwa } from "./pwa";
import {
  createGeneaRouter,
  migrateLegacyHash,
  viewFromRoute,
} from "./router";
import { useAppStore } from "./stores/app";
import type { PresentationPayload } from "./types/presentation";


setWorkerUrl(appUrl("assets/maplibre-gl-worker.mjs").href);

async function loadPayload(): Promise<PresentationPayload> {
  const response = await fetch(appUrl("data/research.json"), { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as PresentationPayload;
}

async function start(): Promise<void> {
  try {
    migrateLegacyHash();
    const payload = await loadPayload();
    const pinia = createPinia();
    const router = createGeneaRouter();
    const app = createApp(App);
    app.use(pinia);
    app.use(router);

    const store = useAppStore(pinia);
    store.initialize(payload, "tree");
    document.addEventListener("genea:capture-navigation-context", store.captureNavigationContext);

    let runtime: ReturnType<typeof bootstrapLegacyViews> | null = null;
    router.afterEach((route) => {
      const view = viewFromRoute(route);
      store.activate(view);
      store.restoreNavigationContext(history.state);
      runtime?.activate(view);
      const context = store.activePersonKey ? store.personContext(store.activePersonKey) : null;
      if (runtime && context && ["tree", "graph", "map"].includes(String(route.name))) {
        runtime.focusPerson(view, context);
      }
    });

    await router.isReady();
    app.mount("#vue-shell");
    await nextTick();

    runtime = bootstrapLegacyViews(
      payload,
      store.inspectMap,
      store.inspectGraph,
      store.inspectTree,
    );
    runtime.activate(store.activeView);

  } catch (error) {
    showLoadFailure(error);
  }
}

void start();
registerGeneaPwa();
