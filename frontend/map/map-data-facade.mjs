export class MapDataFacade {
  constructor(payload) {
    this.payload = payload;
    this.places = new Map((payload?.places || []).map((place) => [place.id, place]));
    this.lines = new Map((payload?.lines || []).map((line) => [line.id, line]));
  }

  normalizeLineIds(lineIds) {
    return new Set(typeof lineIds === "string" ? [lineIds] : [...(lineIds || [])]);
  }

  observations(lineIds, yearFrom, yearTo, includeUnmapped = false, personIds = null) {
    const selected = this.normalizeLineIds(lineIds);
    const selectedPeople = personIds ? this.normalizeLineIds(personIds) : null;
    const source = [
      ...(this.payload?.observations || []),
      ...(includeUnmapped ? this.payload?.unmappedObservations || [] : []),
    ];
    return source.filter((item) => {
      const year = item.yearFrom ?? item.yearTo;
      const mapped = !item.reason && this.places.has(item.placeId);
      return (includeUnmapped || mapped)
        && (selectedPeople ? selectedPeople.has(item.personId) : item.lineIds?.some((lineId) => selected.has(lineId)))
        && year != null && year >= yearFrom && year <= yearTo;
    });
  }

  sourceObservations(yearFrom, yearTo) {
    return (this.payload?.sourceObservations || []).filter((item) => {
      const year = item.yearFrom ?? item.yearTo;
      return !item.reason
        && this.places.has(item.placeId)
        && year != null && year >= yearFrom && year <= yearTo;
    });
  }

  movementObservations(lineIds, yearFrom, yearTo, personIds = null) {
    const byPerson = new Map();
    for (const item of this.observations(lineIds, yearFrom, yearTo, false, personIds)) {
      if (item.kind === "event" && item.presence !== "direct") continue;
      const events = byPerson.get(item.personId) || new Map();
      const current = events.get(item.eventId);
      if (!current || (item.kind === "residence" && current.kind !== "residence")) events.set(item.eventId, item);
      byPerson.set(item.personId, events);
    }
    const result = new Map();
    for (const [personId, events] of byPerson) {
      const ordered = [...events.values()].sort((first, second) =>
        (first.yearFrom ?? 9999) - (second.yearFrom ?? 9999) || String(first.dateFrom).localeCompare(String(second.dateFrom))
      );
      const distinct = [];
      for (const item of ordered) {
        if (!distinct.length || distinct.at(-1).placeId !== item.placeId) distinct.push(item);
      }
      result.set(personId, distinct);
    }
    return result;
  }

  viewModel(lineIds, yearFrom, yearTo, mode, personIds = null) {
    const selectedIds = [...this.normalizeLineIds(lineIds)].filter((lineId) => this.lines.has(lineId));
    const lines = selectedIds.map((lineId) => this.lines.get(lineId));
    const observations = this.observations(selectedIds, yearFrom, yearTo, false, personIds);
    const sourceObservations = mode === "movement" || personIds
      ? []
      : this.sourceObservations(yearFrom, yearTo);
    const routes = [];
    let visible = observations;
    if (mode === "movement") {
      const byPerson = this.movementObservations(selectedIds, yearFrom, yearTo, personIds);
      visible = [...byPerson.values()].flat();
      for (const items of byPerson.values()) {
        for (let index = 1; index < items.length; index += 1) {
          const routeLineIds = selectedIds.filter((lineId) =>
            items[index - 1].lineIds?.includes(lineId) && items[index].lineIds?.includes(lineId)
          );
          const lineId = routeLineIds[0] || selectedIds.find((candidate) => items[index].lineIds?.includes(candidate));
          routes.push({
            from: items[index - 1],
            to: items[index],
            personId: items[index].personId,
            personLabel: items[index].personLabel,
            lineId,
            lineIds: routeLineIds.length ? routeLineIds : lineId ? [lineId] : [],
            color: this.lines.get(lineId)?.color || "#285c53",
            verified: [items[index - 1], items[index]].every((item) => item.eventStatus === "verified" || item.eventStatus === "confirmed"),
          });
        }
      }
    }
    const groups = new Map();
    for (const item of visible) {
      const items = groups.get(item.placeId) || [];
      items.push(item);
      groups.set(item.placeId, items);
    }
    const sourceGroups = new Map();
    for (const item of sourceObservations) {
      const items = sourceGroups.get(item.placeId) || [];
      items.push(item);
      sourceGroups.set(item.placeId, items);
    }
    return {
      line: lines[0] || null,
      lines,
      lineIds: selectedIds,
      observations,
      sourceObservations,
      groups,
      sourceGroups,
      routes,
      mode,
      yearFrom,
      yearTo,
    };
  }
}
