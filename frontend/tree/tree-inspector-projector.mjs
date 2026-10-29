function unique(values) {
  return [...new Set((values || []).filter(Boolean))].sort();
}

function shortSourceLabel(item) {
  const parts = [item.type, item.context].filter(Boolean);
  return parts.length ? parts.join(" · ") : (item.sourceTitle || "Akt / materiał");
}

export function projectTreeInspector(base) {
  const uniqueSources = new Map();
  const addSource = (source, origin) => {
    if (!source?.sourceId && !source?.id) return;
    const sourceId = source.sourceId || source.id;
    const current = uniqueSources.get(sourceId) || {
      sourceId,
      title: source.sourceTitle || source.title || "Akt / materiał",
      shortLabel: shortSourceLabel(source),
      occurrenceIds: [],
      eventIds: [],
      roles: [],
      origins: [],
      review: source.review || null,
    };
    current.occurrenceIds = unique([...current.occurrenceIds, ...(source.occurrenceIds || [])]);
    current.eventIds = unique([...current.eventIds, ...(source.eventIds || [])]);
    current.roles = unique([...current.roles, ...(source.roles || [])]);
    current.origins = unique([...current.origins, origin]);
    if (!current.review && source.review) current.review = source.review;
    uniqueSources.set(sourceId, current);
  };

  for (const appearance of base.appearances || []) addSource(appearance, "appearance");
  for (const material of base.materials || []) addSource(material, "material");
  for (const candidate of base.candidates || []) addSource(candidate, "candidate");
  for (const union of base.unions || []) {
    for (const source of union.sources || []) addSource(source, "union");
  }
  for (const group of Object.values(base.family || {})) {
    for (const member of group || []) {
      for (const source of member.sources || []) addSource(source, "family");
    }
  }
  for (const relation of base.sourceRelations || []) {
    for (const source of relation.sources || []) addSource(source, "source-relation");
  }

  const importantFacts = (base.facts || []).filter((fact) => !fact.unresolved);
  const alerts = [
    ...(base.facts || []).filter((fact) => fact.unresolved).map((fact) => ({
      id: `fact:${fact.label}:${fact.value}`,
      kind: "unresolved_fact",
      label: fact.label,
      detail: fact.identityAmbiguous
        ? `${fact.value} · podstawa może być udokumentowana, ale przypisanie do jednej osoby pozostaje nierozstrzygnięte`
        : fact.value,
      sourceId: fact.sourceId || null,
    })),
    ...(base.candidates || []).map((candidate) => ({
      id: candidate.id,
      kind: "identity_candidate",
      label: candidate.occurrenceLabel,
      detail: candidate.basis,
      sourceId: candidate.sourceId,
    })),
  ];
  const tabs = [
    { id: "summary", label: "Podsumowanie", count: importantFacts.length },
    {
      id: "family",
      label: "Rodzina",
      count: Object.values(base.family || {}).flat().length
        + (base.unions || []).length
        + (base.sourceRelations || []).length,
    },
    { id: "acts", label: "Akty", count: uniqueSources.size },
    ...(alerts.length ? [{ id: "research", label: "Badania", count: alerts.length }] : []),
  ];

  return {
    ...base,
    importantFacts,
    alerts,
    uniqueSources: [...uniqueSources.values()].sort((first, second) => (
      first.shortLabel.localeCompare(second.shortLabel, "pl") || first.sourceId.localeCompare(second.sourceId)
    )),
    tabs,
  };
}
