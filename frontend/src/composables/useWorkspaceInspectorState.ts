import { onBeforeUnmount, onMounted, ref, watch, watchEffect, type Ref } from "vue";

export type WorkspaceInspectorState = "collapsed" | "peek" | "expanded";

interface WorkspaceInspectorOptions {
  id: string;
  hostId: string;
  selectionKey: Ref<string>;
  revealEvent?: string;
}

export function useWorkspaceInspectorState(options: WorkspaceInspectorOptions) {
  const state = ref<WorkspaceInspectorState>("expanded");
  const storageKey = `genea:${options.id}-inspector-state:v1`;

  function reveal(): void {
    state.value = window.matchMedia("(max-width: 760px)").matches ? "peek" : "expanded";
  }

  function onRevealEvent(): void {
    reveal();
  }

  onMounted(() => {
    const stored = sessionStorage.getItem(storageKey);
    if (["collapsed", "peek", "expanded"].includes(stored || "")) state.value = stored as WorkspaceInspectorState;
    if (options.revealEvent) document.addEventListener(options.revealEvent, onRevealEvent);
  });

  onBeforeUnmount(() => {
    if (options.revealEvent) document.removeEventListener(options.revealEvent, onRevealEvent);
  });

  watch(options.selectionKey, (next, previous) => {
    if (next && next !== previous) reveal();
  });
  watch(state, (value) => sessionStorage.setItem(storageKey, value));
  watchEffect(() => {
    const host = document.getElementById(options.hostId);
    host?.classList.toggle("is-collapsed", state.value === "collapsed");
    host?.setAttribute("data-sheet-state", state.value);
  });

  return { state, reveal };
}
