import { eventTypeLabel, polishCount, polishDate, roleLabel, statusLabel as researchStatusLabel } from "../core/formatting.mjs";
import { aggregateSourceReviews } from "../core/review-status.mjs";
import { EXTERNAL_NETWORK_ROLE_LABELS, familyRelationshipCanonical, familyRelationshipQualifier } from "./family-tree-model.mjs";
import { projectTreeInspector } from "./tree-inspector-projector.mjs";
import { familyKnowledgePresentation } from "../core/family-knowledge.mjs";
import { projectApprovedActMaterials } from "../core/approved-act-materials.mjs";

function recordAppearances(person, dataIndex) {
  const grouped = new Map();
  for (const occurrenceId of person.occurrenceIds || []) {
    const occurrence = dataIndex.occurrences.get(occurrenceId);
    const source = occurrence?.sourceId ? dataIndex.sources.get(occurrence.sourceId) : null;
    if (!occurrence || !source) continue;
    const group = grouped.get(source.id) || { source, occurrences: [], events: [] };
    group.occurrences.push(occurrence);
    group.events.push(...(dataIndex.eventsByOccurrence.get(occurrenceId) || []));
    grouped.set(source.id, group);
  }
  return [...grouped.values()].map((group) => {
    const events = group.events.length ? group.events : (dataIndex.eventsBySource.get(group.source.id) || []);
    const event = events[0] || null;
    const occurrenceIds = new Set(group.occurrences.map((item) => item.id));
    const participantRoles = events.flatMap((item) => (item.participants || [])
      .filter((participant) => occurrenceIds.has(participant.occurrenceId))
      .map((participant) => participant.role));
    const roles = [...new Set([
      ...group.occurrences.map((item) => roleLabel(item.role)),
      ...participantRoles.map((item) => roleLabel(item)),
    ].filter(Boolean))];
    const date = event?.dateFrom || event?.date || group.source.date;
    const place = event?.place || null;
    return {
      sourceId: group.source.id,
      sourceTitle: group.source.title,
      type: eventTypeLabel(event?.type || group.source.type),
      occurrenceCount: group.occurrences.length,
      occurrenceLabel: polishCount(group.occurrences.length, "wystąpienie", "wystąpienia", "wystąpień"),
      context: [date ? polishDate(date) : null, place].filter(Boolean).join(" · "),
      rolesLabel: roles.length ? `Rola: ${roles.join(" · ")}` : "Rola nieokreślona",
      roles,
      occurrenceIds: [...occurrenceIds].sort(),
      eventIds: [...new Set(events.map((item) => item.id).filter(Boolean))].sort(),
      review: dataIndex.sourceReview(group.source.id),
    };
  });
}

function candidateAppearances(person, dataIndex) {
  return (person.candidateLinks || []).flatMap((candidate) => {
    const source = candidate.sourceId ? dataIndex.sources.get(candidate.sourceId) : null;
    const occurrence = candidate.occurrenceId ? dataIndex.occurrences.get(candidate.occurrenceId) : null;
    if (!source || !occurrence) return [];
    return [{
      id: candidate.id,
      sourceId: source.id,
      sourceTitle: source.title,
      occurrenceLabel: candidate.label || occurrence.name || "Osoba nierozstrzygnięta",
      rolesLabel: occurrence.role ? `Rola w kandydackim akcie: ${roleLabel(occurrence.role)}` : "Rola nieokreślona",
      basis: candidate.basis || "Brak zapisanego uzasadnienia kandydatury.",
      support: candidate.support || [],
      against: candidate.against || [],
      humanReviewDebt: candidate.humanReviewDebt || [],
      status: candidate.status || "unresolved",
      priorityLabel: candidate.highPriority ? "wysoki priorytet kontroli" : "kandydatura robocza",
      scoreSemantics: candidate.scoreSemantics || "research_priority_not_probability",
      review: dataIndex.sourceReview(source.id),
    }];
  });
}

function sourceRelationLabel(predicate, currentIsFrom) {
  const value = String(predicate || "").toLocaleLowerCase("pl").replaceAll("_", "-");
  if (value === "father-of") return currentIsFrom ? "Dziecko" : "Ojciec";
  if (value === "mother-of") return currentIsFrom ? "Dziecko" : "Matka";
  if (value === "parent-of") return currentIsFrom ? "Dziecko" : "Rodzic";
  if (["child-of-father", "child-of-mother", "child-of"].includes(value)) return currentIsFrom ? "Rodzic" : "Dziecko";
  if (/(marriage|spouse|husband|wife|małż|maz|mąż|zona|żona)/.test(value)) return "Małżonek / małżonka";
  if (/(sibling|brother|sister|brat|siostr)/.test(value)) return "Rodzeństwo";
  return predicate || "Relacja rodzinna";
}

function identityStatusResolved(value) {
  return ["confirmed", "resolved", "resolved_same", "accepted", "agent_approved"]
    .includes(String(value || "").toLocaleLowerCase("pl"));
}

function sourceRelationshipResolutionLabel(personIdentityResolved, relationshipCanonical) {
  if (!personIdentityResolved) return "Tożsamość nierozstrzygnięta · relacja wskazana w źródłach";
  if (relationshipCanonical) return "Tożsamość rozstrzygnięta · relacja kanoniczna";
  return "Tożsamość rozstrzygnięta · relacja wskazana w źródłach";
}

function sourceFamilyRelations(person, context, dataIndex) {
  const memberIds = new Set(person.occurrenceIds || []);
  const groups = new Map();
  const relationshipIsCanonical = (relationLabel, relatedContext) => {
    const relatedTreeIds = relatedContext?.treePersonIds || [];
    if (!relatedTreeIds.length) return false;
    if (relationLabel === "Małżonek / małżonka") {
      return (context.tree.unions || []).some((union) => (
        union.partnerIds.includes(person.id) && relatedTreeIds.some((id) => union.partnerIds.includes(id))
      ));
    }
    if (["Ojciec", "Matka", "Rodzic"].includes(relationLabel)) {
      return (context.tree.families || []).some((family) => (
        family.childIds.includes(person.id) && relatedTreeIds.some((id) => family.parentIds.includes(id))
      ));
    }
    if (relationLabel === "Dziecko") {
      return (context.tree.families || []).some((family) => (
        family.parentIds.includes(person.id) && relatedTreeIds.some((id) => family.childIds.includes(id))
      ));
    }
    if (relationLabel === "Rodzeństwo") {
      return (context.tree.families || []).some((family) => (
        family.childIds.includes(person.id) && relatedTreeIds.some((id) => family.childIds.includes(id))
      ));
    }
    return false;
  };
  for (const occurrenceId of memberIds) {
    for (const relation of dataIndex.relationshipsByOccurrence.get(occurrenceId) || []) {
      const currentIsFrom = memberIds.has(relation.fromOccurrenceId);
      const currentIsTo = memberIds.has(relation.toOccurrenceId);
      if (currentIsFrom === currentIsTo) continue;
      const otherId = currentIsFrom ? relation.toOccurrenceId : relation.fromOccurrenceId;
      const other = dataIndex.occurrences.get(otherId);
      if (!other) continue;
      const sourceId = relation.sourceId || other.sourceId || null;
      const relatedContext = dataIndex.occurrenceContext(otherId);
      const relatedTreeId = relatedContext?.treePersonIds?.[0];
      const relatedTreePerson = relatedTreeId ? dataIndex.treePeople.get(relatedTreeId) : null;
      const relatedCanonical = other.personId ? dataIndex.people.get(other.personId) : null;
      const personIdentityResolved = Boolean(
        other.personId
          && relatedContext?.identityScope === "canonical"
          && identityStatusResolved(relatedCanonical?.status),
      );
      const relationLabel = sourceRelationLabel(relation.type, currentIsFrom);
      const relationshipCanonical = relationshipIsCanonical(relationLabel, relatedContext);
      const groupPersonKey = personIdentityResolved ? `person:${other.personId}` : `occurrence:${otherId}`;
      const groupKey = `${relationLabel}:${groupPersonKey}`;
      const relationId = relation.id || `${relation.fromOccurrenceId}:${relation.type}:${relation.toOccurrenceId}`;
      const evidenceKey = sourceId || relationId;
      const evidence = {
        id: evidenceKey,
        sourceId,
        sourceTitle: dataIndex.sources.get(sourceId)?.title || "Akt i transkrypcja",
        relationshipIds: [relationId],
        occurrenceIds: [...new Set([relation.fromOccurrenceId, relation.toOccurrenceId].filter(Boolean))].sort(),
        eventIds: [...new Set(relation.backingEventIds || [])].sort(),
        statuses: [relation.verificationStatus || relation.status || "source-reported"],
        readingCertainties: relation.readingCertainty ? [relation.readingCertainty] : [],
        literals: relation.literal ? [relation.literal] : [],
        provenance: relation.provenance ? [relation.provenance] : [],
        derivationStatuses: relation.derivationStatus ? [relation.derivationStatus] : [],
      };
      const existing = groups.get(groupKey);
      if (!existing) {
        groups.set(groupKey, {
          id: groupKey,
          relationLabel,
          personName: dataIndex.occurrenceLabel(other),
          personId: relatedContext?.publicProfileId || null,
          lifeSpanLabel: relatedTreePerson?.lifeSpanLabel || dataIndex.people.get(other.personId)?.lifeSpanLabel || "?–?",
          personIdentityResolved,
          relationshipCanonical,
          resolutionLabel: sourceRelationshipResolutionLabel(personIdentityResolved, relationshipCanonical),
          sources: [evidence],
        });
        continue;
      }
      existing.relationshipCanonical ||= relationshipCanonical;
      existing.resolutionLabel = sourceRelationshipResolutionLabel(existing.personIdentityResolved, existing.relationshipCanonical);
      const currentEvidence = existing.sources.find((item) => item.id === evidenceKey);
      if (!currentEvidence) {
        existing.sources.push(evidence);
        continue;
      }
      currentEvidence.relationshipIds = [...new Set([...currentEvidence.relationshipIds, ...evidence.relationshipIds])].sort();
      currentEvidence.occurrenceIds = [...new Set([...currentEvidence.occurrenceIds, ...evidence.occurrenceIds])].sort();
      currentEvidence.eventIds = [...new Set([...currentEvidence.eventIds, ...evidence.eventIds])].sort();
      currentEvidence.statuses = [...new Set([...currentEvidence.statuses, ...evidence.statuses])].sort();
      currentEvidence.readingCertainties = [...new Set([...currentEvidence.readingCertainties, ...evidence.readingCertainties])].sort();
      currentEvidence.literals = [...new Set([...currentEvidence.literals, ...evidence.literals])].sort();
      currentEvidence.provenance = [...new Set([...currentEvidence.provenance, ...evidence.provenance])].sort();
      currentEvidence.derivationStatuses = [...new Set([...currentEvidence.derivationStatuses, ...evidence.derivationStatuses])].sort();
    }
  }
  return [...groups.values()]
    .map((item) => ({
      ...item,
      sources: item.sources.sort((first, second) => first.sourceTitle.localeCompare(second.sourceTitle, "pl") || first.id.localeCompare(second.id)),
    }))
    .sort((first, second) => first.relationLabel.localeCompare(second.relationLabel, "pl") || first.personName.localeCompare(second.personName, "pl"));
}

function structuralFamilyMember(relatedId, relationLabel, family, context, dataIndex, focusedId) {
  const relatedPerson = context.personById.get(relatedId);
  if (!relatedPerson) return null;
  const relatedContext = dataIndex.personContext(relatedPerson);
  const sources = (family.sourceIds || [])
    .map((id) => dataIndex.sources.get(id))
    .filter(Boolean)
    .map((source) => ({ id: source.id, title: source.title }));
  const canonicalRelationship = familyRelationshipCanonical(family);
  const evidence = family.evidence;
  const knownEdge = (family.familyKnowledgeEdges || []).find((edge) =>
    edge.familyKnowledgeStatus === "family_known" && edge.basis === "family_testimony" &&
    ((edge.parentId === focusedId && edge.childId === relatedId) ||
      (edge.childId === focusedId && edge.parentId === relatedId)));
  const status = knownEdge ? "family_known" : family.status || (!canonicalRelationship ? evidence?.tier || evidence?.status || "unresolved" : relatedPerson.evidenceStatus || "unchecked");
  const statusLabel = knownEdge ? "Potwierdzone rodzinnie" : !canonicalRelationship
    ? `${researchStatusLabel(status)} (${status})` : context.evidenceLabel(status);
  const evidenceNotice = knownEdge ? "Podstawa: przekaz rodzinny potwierdzony przez użytkownika." : !canonicalRelationship ? evidence?.evidenceRedacted === true
    ? "Podstawa relacji została ukryta przez reguły prywatności."
    : evidence
      ? `Podstawa relacji: ${evidence.tier || evidence.status || "source_reported"}. Dostępne odwołania: ${Object.entries(evidence.backingReferenceCounts || {})
        .filter(([, count]) => Number.isInteger(count) && count > 0).map(([kind, count]) => `${kind}: ${count}`).join(" · ") || "brak publicznych odwołań"}.`
      : "Brak publicznej podstawy relacji w tej projekcji."
    : null;
  const identityNotice = relatedContext?.identityScope === "hypothesis_group"
    ? "Profil obejmuje kilka odrębnych osób kanonicznych i wymaga rozstrzygnięcia."
    : null;
  return {
    id: `${family.id}:${relationLabel}:${relatedId}`,
    familyId: family.id,
    treePersonId: relatedId,
    personId: relatedContext?.publicProfileId || relatedId,
    personName: relatedPerson.name || "Osoba nieustalona",
    lifeSpanLabel: dataIndex.personLifeSpan(relatedPerson),
    relationLabel,
    status,
    statusLabel: [knownEdge ? null : familyRelationshipQualifier(family), statusLabel].filter(Boolean).join(" · "),
    ...(!canonicalRelationship ? { canonicalRelationship: false, relationshipIdentityStatus: family.relationshipIdentityStatus || "unresolved" } : {}),
    identityScope: relatedContext?.identityScope || "presentation_only",
    identityNotice: [identityNotice, evidenceNotice].filter(Boolean).join(" ") || null,
    sources,
    focus: () => context.focusPerson(relatedId),
  };
}

function structuralFamily(person, context, dataIndex) {
  const groups = { parents: [], coParents: [], children: [], siblings: [] };
  const seen = Object.fromEntries(Object.keys(groups).map((key) => [key, new Set()]));
  const unionPartnerIds = new Set((context.tree.unions || [])
    .filter((union) => union.partnerIds.includes(person.id))
    .flatMap((union) => union.partnerIds)
    .filter((id) => id !== person.id));
  const append = (group, relatedId, relationLabel, family) => {
    if (!relatedId || relatedId === person.id || seen[group].has(relatedId)) return;
    const member = structuralFamilyMember(relatedId, relationLabel, family, context, dataIndex, person.id);
    if (!member) return;
    seen[group].add(relatedId);
    groups[group].push(member);
  };

  for (const family of context.tree.families || []) {
    const parentIds = family.parentIds || [];
    const childIds = family.childIds || [];
    if (childIds.includes(person.id)) {
      for (const parentId of parentIds) append("parents", parentId, "Rodzic", family);
      for (const siblingId of childIds) append("siblings", siblingId, "Rodzeństwo", family);
    }
    if (parentIds.includes(person.id)) {
      for (const childId of childIds) append("children", childId, "Dziecko", family);
      for (const coParentId of parentIds) {
        if (!unionPartnerIds.has(coParentId)) append("coParents", coParentId, "Współrodzic", family);
      }
    }
  }
  return groups;
}

function externalClassificationSummary(person) {
  const classification = person.classification || {};
  if (classification.state === "conflict") {
    const alternatives = [...new Set((classification.alternatives || [])
      .map((item) => EXTERNAL_NETWORK_ROLE_LABELS[item.role] || item.role)
      .filter(Boolean))];
    return alternatives.length
      ? `Drzewo zewnętrzne daje sprzeczne klasyfikacje: ${alternatives.join("; ")}. Żadna nie została wybrana jako zwycięska.`
      : "Drzewo zewnętrzne daje sprzeczną klasyfikację. Żadna wersja nie została wybrana jako zwycięska.";
  }
  if (classification.state !== "classified") {
    return `Rola nie została sklasyfikowana (${classification.unclassifiedReason || "brak pełnej jawnej ścieżki"}).`;
  }
  return `${person.candidateRoleLabel || EXTERNAL_NETWORK_ROLE_LABELS.unclassified}. Klasyfikacja wynika wyłącznie z jawnej ścieżki zgłoszonej przez to drzewo.`;
}

export function buildSourceReportedTreeInspectorModel(person, context, dataIndex) {
  if (!person?.sourceReported) return null;
  const familyKnown = familyKnowledgePresentation(person.familyKnowledge);
  const personIdentityResolved = person.personIdentityResolved === true
    || person.profileIdentityStatus === "resolved_same"
    || person.identityStatus === "resolved_same";
  const network = context.network;
  const anchorLabel = network?.anchorState === "visible" && network?.anchor?.label
    ? network.anchor.label
    : network?.anchorState === "masked"
      ? "punkt odniesienia ukryty przez reguły prywatności"
      : "brak jawnego punktu odniesienia";
  const connectorNotice = person.outsideFilterConnector
    ? "Ten węzeł pozostaje przygaszony jako łącznik ścieżki poza aktywnym filtrem."
    : null;
  const profileId = person.publicProfileId || person.id;
  const approvedActs = projectApprovedActMaterials({ publicProfileId: profileId, dataIndex });
  const identityDecisionTier = personIdentityResolved
    && ["agent_approved_human_pending", "human_confirmed"].includes(person.identityDecisionTier)
    ? person.identityDecisionTier
    : null;
  const identityLabel = identityDecisionTier === "agent_approved_human_pending"
    ? "Profil zewnętrzny · tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka"
    : identityDecisionTier === "human_confirmed"
      ? "Profil zewnętrzny · tożsamość potwierdzona przez człowieka"
      : personIdentityResolved
        ? "Profil zewnętrzny · tożsamość rozstrzygnięta"
        : familyKnown ? "Profil zewnętrzny · dopasowanie do aktów nierozstrzygnięte" : "Zewnętrzny profil kandydacki";
  const identityNotice = identityDecisionTier === "agent_approved_human_pending"
    ? "Tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka. Relacje nadal są wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
    : identityDecisionTier === "human_confirmed"
      ? "Tożsamość profilu została potwierdzona przez człowieka. Relacje nadal są wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
      : personIdentityResolved
        ? "Tożsamość profilu została rozstrzygnięta. Relacje nadal są wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
        : familyKnown
          ? "Dopasowanie do konkretnych aktów i relacje pozostają nierozstrzygnięte. Dwie sieci zewnętrzne nie są agregowane jako niezależne potwierdzenie."
          : "Tożsamość i relacje pozostają nierozstrzygnięte. Dwie sieci zewnętrzne nie są agregowane jako niezależne potwierdzenie.";
  return projectTreeInspector({
    personId: profileId,
    identityScope: "presentation_only",
    identityLabel,
    identityNotice,
    familyKnown,
    identityComponentCount: 0,
    generation: person.isNetworkAnchor ? "Punkt odniesienia" : person.candidateRoleLabel,
    evidence: "imported_unverified",
    evidenceKind: "is-warning",
    name: person.name,
    fullRecordHref: context.fullRecordHref || null,
    summary: [externalClassificationSummary(person), `Punkt odniesienia komponentu: ${anchorLabel}.`, connectorNotice].filter(Boolean).join(" "),
    lifeSpanLabel: dataIndex.personLifeSpan(person) || "?–?",
    review: null,
    structureActions: [
      ...(context.activeFilter !== "all" ? [{
        label: "Pokaż pełny komponent",
        run: () => context.setFilter("all"),
      }] : []),
      ...(!person.isNetworkAnchor && context.canFilter(person.candidateRole) ? [{
        label: `Filtruj: ${person.candidateRoleLabel}`,
        run: () => context.setFilter(person.candidateRole),
      }] : []),
    ],
    family: { parents: [], coParents: [], children: [], siblings: [] },
    unions: [],
    sourceRelations: [],
    candidates: [],
    appearances: approvedActs.appearances,
    appearanceSummary: approvedActs.appearanceSummary,
    facts: [],
    materials: approvedActs.materials,
    unavailableCount: 0,
    materialTitle: "Podstawa i materiały",
    materialNote: "Restricted provenance drzewa zewnętrznego nie jest publikowane w kontrolowanym interfejsie.",
  });
}

export function buildFamilyTreeInspectorModel(person, context, dataIndex) {
  if (!person) return null;
  const personContext = dataIndex.personContext(person);
  const identityScope = personContext?.identityScope || "presentation_only";
  const identityComponentCount = personContext?.canonicalPersonIds?.length || 0;
  const identityLabel = identityScope === "canonical"
    ? "Osoba kanoniczna"
    : identityScope === "hypothesis_group"
      ? "Tożsamość do rozstrzygnięcia"
      : person.evidenceStatus === "unresolved"
        ? "Osoba w drzewie — dane do rozstrzygnięcia"
        : "Osoba w drzewie";
  const identityNotice = identityScope === "hypothesis_group"
    ? `Ten węzeł obejmuje ${identityComponentCount} odrębne osoby kanoniczne. Są pokazane razem jako hipoteza prezentacyjna, bez scalania.`
    : person.evidenceStatus === "unresolved"
      ? "Pozycja w rodzinie pozostaje widoczna, ale jej dane lub przypisanie wymagają rozstrzygnięcia."
      : null;
  const localIdentityReviews = [
    {
      decisionTier: "agent_approved_human_pending",
      label: "tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka",
      humanReviewLabel: "Kontrola człowieka: otwarta",
      humanReviewOpen: true,
    },
    {
      decisionTier: "human_confirmed",
      label: "Tożsamość potwierdzona przez człowieka",
      humanReviewLabel: "Kontrola człowieka: zamknięta",
      humanReviewOpen: false,
    },
  ].map((review) => ({
    ...review,
    occurrenceIds: (person.occurrenceIds || []).filter((occurrenceId) => (
      dataIndex.occurrences.get(occurrenceId)?.identityDecisionTier === review.decisionTier
    )),
  })).filter((review) => review.occurrenceIds.length > 0);
  const sourceIds = [...new Set([
    ...(person.sourceIds || []),
    ...(person.facts || []).map((fact) => fact.sourceId).filter(Boolean),
  ])];
  const appearances = recordAppearances(person, dataIndex);
  const candidates = candidateAppearances(person, dataIndex);
  const sourceRelations = sourceFamilyRelations(person, context, dataIndex);
  const family = structuralFamily(person, context, dataIndex);
  const review = aggregateSourceReviews(appearances.map((item) => dataIndex.sources.get(item.sourceId)));
  const appearanceSourceIds = new Set(appearances.map((item) => item.sourceId));
  const allAvailable = sourceIds.map((id) => dataIndex.sources.get(id)).filter(Boolean);
  const available = allAvailable
    .filter((source) => !appearanceSourceIds.has(source.id))
    .map((source) => ({ id: source.id, title: source.title }));
  const unavailableCount = sourceIds.length - allAvailable.length;
  const facts = (person.facts || []).map((fact) => ({
    label: fact.label,
    value: fact.value,
    place: fact.place || null,
    unresolved: identityScope === "hypothesis_group" || person.evidenceStatus === "unresolved" || fact.status === "unresolved",
    identityAmbiguous: identityScope === "hypothesis_group" || person.evidenceStatus === "unresolved",
    sourceId: fact.sourceId && dataIndex.sources.has(fact.sourceId) ? fact.sourceId : null,
  }));
  const unions = (context.tree.unions || []).filter((union) => union.partnerIds.includes(person.id));
  const ownedUnions = context.ownedUnions(person.id);
  const activeUnion = context.activeUnion(person.id);
  const singleUnionMode = context.collapsedUnions.has(person.id);
  const unionCards = unions.map((union) => {
    const partnerId = union.partnerIds.find((id) => id !== person.id);
    const partner = context.personById.get(partnerId);
    const unionSources = (union.sourceIds || []).map((id) => dataIndex.sources.get(id)).filter(Boolean);
    const canChoose = union.ownerId === person.id && ownedUnions.length > 1;
    const isActive = singleUnionMode && union.id === activeUnion?.id;
    return {
      id: union.id,
      partnerName: partner?.name || "Osoba nieustalona",
      status: context.unionStatusLabel(union.status),
      statusClass: union.status || "",
      statusKind: union.status === "documented" ? "is-good" : "is-warning",
      details: [union.label, union.date, union.place].filter(Boolean).join(" · "),
      canChoose,
      isActive,
      focus: () => context.focusPerson(partnerId),
      choose: () => context.showUnion(person.id, union.id),
      sources: unionSources.map((source) => ({ id: source.id, title: source.title })),
    };
  });
  const structureActions = [];
  if (context.ancestorScope) {
    if (context.ancestorScope.focusPersonId !== person.id) structureActions.push({
      label: "Ustaw jako punkt odniesienia przodków",
      run: () => context.ancestorScope.setFocus(person.id),
    });
    structureActions.push({
      label: "Pokaż w całym drzewie",
      run: () => context.ancestorScope.showInFullTree(person.id),
    });
  } else {
    if (context.additionalUnions(person.id).length) structureActions.push({
      label: context.collapsedUnions.has(person.id) ? "＋ Pokaż wszystkie małżeństwa" : "− Pokaż jedno małżeństwo",
      run: () => context.toggleUnions(person.id),
    });
    if (context.hasAncestorBranch(person.id)) structureActions.push({
      label: context.collapsedAncestors.has(person.id) ? "＋ Rozwiń dalszą gałąź" : "− Zwiń dalszą gałąź",
      run: () => context.toggleAncestors(person.id),
    });
    for (const family of context.expandableFamilies(person.id)) {
      const children = family.childIds.filter((id) => context.personById.has(id));
      const expanded = context.expandedFamilies.has(family.id);
      structureActions.push({
        label: expanded ? `− Zwiń pozostałe dzieci (${children.length})` : `＋ Pokaż wszystkie dzieci (${children.length})`,
        run: () => context.toggleFamilyChildren(family.id, person.id),
      });
    }
  }
  const occurrenceCount = (person.occurrenceIds || []).filter((id) => dataIndex.occurrences.has(id)).length;
  let materialNote = null;
  if (!available.length && !unavailableCount && person.privacy === "masked_living") {
    materialNote = "Dane osoby żyjącej są celowo zamaskowane przez regułę prywatności dla potomków oraz pokoleń 0–2.";
  } else if (!available.length && !unavailableCount && person.evidenceStatus === "unchecked") {
    materialNote = "To miejsce zachowuje pełną strukturę drzewa. Dane tej gałęzi nie zostały jeszcze zaimportowane ani sprawdzone.";
  }
  return projectTreeInspector({
    personId: person.id,
    identityScope,
    identityLabel,
    identityNotice,
    localIdentityReviews,
    identityComponentCount,
    generation: context.generationLabel(person),
    evidence: context.evidenceLabel(person.evidenceStatus),
    evidenceKind: person.evidenceStatus === "documented"
      ? "is-good"
      : person.evidenceStatus === "agent_approved"
        ? "is-agent"
        : "is-warning",
    name: person.name,
    summary: person.summary || "Brak skróconego opisu",
    lifeSpanLabel: dataIndex.personLifeSpan(person),
    review,
    structureActions,
    family,
    unions: unionCards,
    sourceRelations,
    candidates,
    appearances,
    appearanceSummary: `${polishCount(appearances.length, "akt", "akty", "aktów")} · ${polishCount(occurrenceCount, "wystąpienie", "wystąpienia", "wystąpień")}`,
    facts,
    materials: available,
    unavailableCount,
    materialTitle: appearances.length ? "Pozostałe materiały" : "Podstawa i materiały",
    materialNote,
  });
}
