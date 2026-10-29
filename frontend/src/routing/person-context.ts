import type { PresentedPersonContext, PresentationPayload } from "../types/presentation";


function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export class PersonContextIndex {
  readonly contexts: PresentedPersonContext[];
  readonly byPersonKey = new Map<string, PresentedPersonContext>();
  readonly byPublicProfileId = new Map<string, PresentedPersonContext>();
  readonly byRouteAlias = new Map<string, PresentedPersonContext>();
  readonly byCanonicalPersonId = new Map<string, PresentedPersonContext>();
  readonly byTreePersonId = new Map<string, PresentedPersonContext>();
  readonly byMapPersonId = new Map<string, PresentedPersonContext>();
  readonly byGraphIdentityId = new Map<string, PresentedPersonContext>();
  readonly byOccurrenceId = new Map<string, PresentedPersonContext>();

  constructor(readonly payload: PresentationPayload) {
    this.contexts = payload.personContexts || [];
    for (const context of this.contexts) {
      this.byPersonKey.set(context.personKey, context);
      this.byPublicProfileId.set(context.publicProfileId, context);
      for (const alias of context.aliases) this.byRouteAlias.set(alias, context);
      for (const id of context.canonicalPersonIds) {
        // A canonical component keeps its own context. A hypothesis group never
        // captures the component's canonical route.
        if (context.identityScope === "canonical") this.byCanonicalPersonId.set(id, context);
      }
      for (const id of context.treePersonIds) this.byTreePersonId.set(id, context);
      for (const id of context.mapPersonIds) this.byMapPersonId.set(id, context);
      for (const id of context.graphIdentityIds) this.byGraphIdentityId.set(id, context);
      if (context.identityScope === "canonical") {
        for (const id of context.occurrenceIds) this.byOccurrenceId.set(id, context);
      }
    }
    // Unassigned occurrences attached to a presentation group still need a
    // safe route back to that group. Assigned occurrences retain canonical
    // precedence and are never silently merged into the group.
    for (const context of this.contexts) {
      if (context.identityScope === "canonical") continue;
      for (const id of context.occurrenceIds) {
        if (!this.byOccurrenceId.has(id)) this.byOccurrenceId.set(id, context);
      }
    }
  }

  resolveRoute(id: string): PresentedPersonContext | null {
    return this.byPublicProfileId.get(id) || this.byRouteAlias.get(id) || null;
  }

  resolveAny(id: string): PresentedPersonContext | null {
    return this.resolveRoute(id)
      || this.byPersonKey.get(id)
      || this.byCanonicalPersonId.get(id)
      || this.byTreePersonId.get(id)
      || this.byMapPersonId.get(id)
      || this.byGraphIdentityId.get(id)
      || this.byOccurrenceId.get(id)
      || null;
  }

  profileRecord(context: PresentedPersonContext): Record<string, unknown> {
    const canonical = records(this.payload.people).find((item) => context.canonicalPersonIds.includes(text(item.id))) || {};
    const tree = records(this.payload.familyTree?.people).find((item) => context.treePersonIds.includes(text(item.id))) || {};
    const components = records(this.payload.people).filter((item) => context.canonicalPersonIds.includes(text(item.id)));
    return {
      ...canonical,
      ...tree,
      id: context.publicProfileId,
      name: context.label,
      personKey: context.personKey,
      identityScope: context.identityScope,
      canonicalPersonIds: [...context.canonicalPersonIds],
      treePersonIds: [...context.treePersonIds],
      mapPersonIds: [...context.mapPersonIds],
      graphIdentityIds: [...context.graphIdentityIds],
      occurrenceIds: [...context.occurrenceIds],
      sourceIds: [...context.sourceIds],
      aliases: [...context.aliases],
      warnings: [...(context.warnings || [])],
      componentPeople: components,
    };
  }
}
