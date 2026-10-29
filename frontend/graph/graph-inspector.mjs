import { eventTypeLabel, graphVisibilityLabel, polishCount, polishDate, roleLabel, statusLabel } from "../core/formatting.mjs";
import { occurrenceViewMetadata } from "./graph-view-reducer.mjs";
import { familyKnowledgePresentation } from "../core/family-knowledge.mjs";
import { projectApprovedActMaterials } from "../core/approved-act-materials.mjs";

const EXTERNAL_ROLE_LABELS = {
  anchor: "Punkt odniesienia",
  direct_ancestor: "Bezpośredni przodek wskazany w drzewie",
  ancestor_spouse: "Małżonek przodka wskazany w drzewie",
  collateral: "Linia poboczna wskazana w drzewie",
  descendant: "Potomek wskazany w drzewie",
  fan: "Relacja FAN wskazana w drzewie",
  unclassified: "Nieklasyfikowane",
  conflict: "Sprzeczne klasyfikacje",
};

const UNCLASSIFIED_REASON_LABELS = {
  missing_anchor: "brak ustalonego punktu odniesienia",
  masked_anchor: "punkt odniesienia ukryty przez reguły prywatności",
  masked_path: "ścieżka klasyfikacji została przerwana przez reguły prywatności",
  disconnected: "brak jawnej ścieżki do punktu odniesienia",
  unsupported_relation: "relacja nie ma obsługiwanej klasyfikacji genealogicznej",
  incomplete_materialization: "kontrolowany fragment ma niepełną materializację i wymaga kuracji brakujących profili",
};

const EXTERNAL_PROVENANCE_LABELS = {
  source_assertion: "jawne wskazanie w źródle zewnętrznym",
  source_reported_network: "kontrolowana projekcja sieci źródła zewnętrznego",
  controlled_external_tree_export: "kontrolowany eksport drzewa zewnętrznego",
};

function externalProfileHref(graphNode, candidateRole) {
  const publicProfileId = graphNode.publicProfileId;
  const networkId = graphNode.sourceReportedActiveNetworkId;
  if (!publicProfileId || !networkId) return null;
  const query = new URLSearchParams({
    returnView: "graph",
    network: networkId,
    person: publicProfileId,
  });
  if (candidateRole && candidateRole !== "all") query.set("candidateRole", candidateRole);
  return `/people/${encodeURIComponent(publicProfileId)}?${query.toString()}`;
}

function sourceReportedNetworkInspector(graphNode) {
  const item = graphNode.item || {};
  const anchor = item.anchorState === "visible"
    ? item.anchor?.label || "jawny punkt odniesienia"
    : item.anchorState === "masked"
      ? "ukryty przez reguły prywatności"
      : "nieustalony";
  const incomplete = item.materializationState === "incomplete";
  const materializationLabel = incomplete
    ? "niepełna — brakujące profile wymagają kuracji"
    : item.materializationState === "complete"
      ? "kompletna w kontrolowanym eksporcie"
      : "stan nie został podany w eksporcie";
  const gapLabel = item.materializationGap
    ? `${item.materializationGap.status || "stan niepodany"} · brakujące profile wymagają kuracji`
    : incomplete
      ? "niepełna materializacja bez publicznego opisu luki"
      : "brak otwartej luki w kontrolowanym eksporcie";
  return {
    kindLabel: "Stały zakres źródła zewnętrznego",
    status: item.status || "brak statusu w eksporcie",
    statusKind: "is-warning",
    title: item.label || "Sieć kandydacka",
    subtitle: incomplete
      ? "Kontrolowany fragment źródła zewnętrznego ma niepełną materializację. Brakujące profile wymagają kuracji; UI nie tworzy osób automatycznie, nie rozszerza FAN i nie scala osób."
      : "Widok jest kontrolowanym fragmentem źródła zewnętrznego. Niepełna materializacja, jeśli zostanie wskazana w eksporcie, wymaga kuracji brakujących profili; UI nie tworzy osób automatycznie, nie rozszerza FAN i nie scala osób.",
    sections: [{
      title: "Kontrakt zakresu",
      rows: [
        ["Widoczne profile", item.visibleProfileCount ?? 0],
        ["Widoczne relacje", item.visibleRelationshipCount ?? 0],
        ["Stan identity relacji", item.identityStatus || "brak w eksporcie"],
        ["Relacje kanoniczne", item.canonicalRelationship === true ? "tak" : item.canonicalRelationship === false ? "nie" : "brak w eksporcie"],
        ["Automatyczne scalanie", item.automaticMerge === true ? "tak" : item.automaticMerge === false ? "nie" : "brak w eksporcie"],
        ["Materializacja", materializationLabel],
        ["Luka materializacji", gapLabel],
        ["Automatyczne tworzenie osób", item.materializationGap?.automaticPersonCreation === false ? "nie" : "niedostępne w tym widoku"],
        ["Punkt odniesienia", anchor],
      ],
    }],
    candidates: [],
    review: null,
  };
}

function externalRelationshipInspector(graphEdge) {
  const item = graphEdge.item || {};
  const fromLabel = graphEdge.fromNode?.label || "profil źródłowy bez etykiety";
  const toLabel = graphEdge.toNode?.label || "profil źródłowy bez etykiety";
  const provenance = EXTERNAL_PROVENANCE_LABELS[item.provenance]
    || "relacja wskazana w kontrolowanej projekcji źródła zewnętrznego";
  return {
    kindLabel: "Relacja ze źródła zewnętrznego",
    status: item.relationshipIdentityStatus || item.identityStatus || "unresolved",
    statusKind: "is-warning",
    title: `${fromLabel} — ${item.type || "relationship"} — ${toLabel}`,
    subtitle: "Literalna relacja zewnętrzna. Nie jest relacją kanoniczną ani automatyczną decyzją identity.",
    sections: [{
      title: "Literalna relacja",
      rows: [
        ["Od", fromLabel],
        ["Typ", item.type || "relationship"],
        ["Do", toLabel],
        ["Stan identity relacji", item.relationshipIdentityStatus || item.identityStatus || "unresolved"],
        ["Relacja kanoniczna", item.canonicalRelationship === true ? "tak" : "nie"],
        ["Pochodzenie", provenance],
        ["Publiczne ID relacji", graphEdge.externalRelationshipId || item.id || "brak w eksporcie"],
      ],
    }],
    candidates: [],
    review: null,
  };
}

function externalOccurrenceInspector(graphNode, dataIndex) {
  const item = graphNode.item || {};
  const familyKnown = familyKnowledgePresentation(item.familyKnowledge);
  const approvedActs = projectApprovedActMaterials({ publicProfileId: graphNode.publicProfileId || item.publicProfileId, dataIndex });
  const personIdentityResolved = item.personIdentityResolved === true
    || item.profileIdentityStatus === "resolved_same"
    || item.identityStatus === "resolved_same";
  const identityDecisionTier = personIdentityResolved
    && ["agent_approved_human_pending", "human_confirmed"].includes(item.identityDecisionTier)
    ? item.identityDecisionTier : null;
  const identityLabel = identityDecisionTier === "agent_approved_human_pending"
    ? "tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka"
    : identityDecisionTier === "human_confirmed" ? "tożsamość potwierdzona przez człowieka" : "tożsamość rozstrzygnięta";
  const classification = graphNode.sourceReportedClassification || item.classification || {};
  const candidateRole = graphNode.sourceReportedCandidateRole
    || (classification.state === "conflict" ? "conflict" : classification.state === "unclassified" ? "unclassified" : classification.primaryRole)
    || "unclassified";
  const alternatives = (classification.alternatives || []).map((alternative) =>
    EXTERNAL_ROLE_LABELS[alternative.role] || alternative.role);
  const anchorState = graphNode.sourceReportedAnchorState || "missing";
  const anchorDescription = anchorState === "visible"
    ? graphNode.sourceReportedAnchorLabel || "jawny punkt odniesienia"
    : anchorState === "masked"
      ? "ukryty przez reguły prywatności"
      : "nieustalony";
  return {
    kindLabel: personIdentityResolved
      ? `Profil zewnętrzny · ${identityLabel}`
      : familyKnown ? "Znany rodzinnie · profil z drzewa zewnętrznego" : "Kandydat z drzewa zewnętrznego",
    status: personIdentityResolved ? identityLabel : "imported_unverified",
    statusKind: personIdentityResolved && identityDecisionTier === "human_confirmed" ? "is-good" : "is-warning",
    title: graphNode.label || item.name || "Kandydat bez nazwy",
    subtitle: [familyKnown?.notice, personIdentityResolved
      ? identityDecisionTier === "agent_approved_human_pending"
        ? "Tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka. Relacje pozostają wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
        : identityDecisionTier === "human_confirmed"
          ? "Tożsamość profilu potwierdzona przez człowieka; relacje pozostają wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
          : "Tożsamość profilu jest rozstrzygnięta; relacje pozostają wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
      : familyKnown
        ? "Dopasowanie do konkretnych aktów i relacje pozostają nierozstrzygnięte. Relacje wskazane w drzewie zewnętrznym są niekanoniczne."
        : "Relacja zgłoszona w drzewie zewnętrznym; nie jest potwierdzonym pokrewieństwem ani decyzją tożsamości."].filter(Boolean).join(" "),
    sections: [...(familyKnown ? [{
      title: "Rozpoznanie rodzinne",
      rows: [
        ["Stan", familyKnown.label],
        ["Pewność rozpoznania osoby", familyKnown.confidenceLabel],
        ["Podstawa", familyKnown.basisLabel],
      ],
    }] : []), {
      title: "Klasyfikacja względem punktu odniesienia",
      rows: [
        ...(personIdentityResolved ? [
          ["Tożsamość profilu", identityDecisionTier ? identityLabel : "rozstrzygnięta (resolved_same)"],
          ["Relacje", "wskazane w źródle · niekanoniczne · niepotwierdzone"],
        ] : []),
        ["Rola", EXTERNAL_ROLE_LABELS[candidateRole] || candidateRole],
        ["Stan", classification.state || "unclassified"],
        ["Literalny profil", item.name || graphNode.label || "brak etykiety w eksporcie"],
        ["Stan identity profilu", item.profileIdentityStatus || item.identityStatus || "unresolved"],
        ["Pochodzenie", "profil wskazany w kontrolowanej projekcji źródła zewnętrznego"],
        ...(graphNode.publicProfileId ? [["Publiczne ID profilu", graphNode.publicProfileId]] : []),
        ["Punkt odniesienia", anchorDescription],
        ...(classification.unclassifiedReason
          ? [["Powód", UNCLASSIFIED_REASON_LABELS[classification.unclassifiedReason] || classification.unclassifiedReason]]
          : []),
        ...(alternatives.length ? [["Alternatywy", alternatives.join(" · ")]] : []),
      ],
    }],
    candidates: [],
    review: null,
    externalProfileHref: externalProfileHref(graphNode, candidateRole),
    approvedActMaterials: approvedActs.materials,
    approvedActMaterialSummary: approvedActs.appearanceSummary,
  };
}

export function buildGraphInspectorModel(graphNode, { payload, dataIndex }) {
  if (!graphNode) return null;
  if (graphNode.kind === "source-reported-network-context") {
    return sourceReportedNetworkInspector(graphNode);
  }
  if (graphNode.kind === "external-relation") {
    return externalRelationshipInspector(graphNode);
  }
  if (graphNode.kind === "hidden-selection") {
    return {
      empty: true,
      glyph: "↗",
      title: "Element ukryty przez filtr",
      subtitle: "Wybierz element z aktualnego zakresu albo wyczyść filtry.",
    };
  }
  const item = graphNode.item;
  if (graphNode.kind === "occurrence") {
    if (item?.externalTreeCandidate || graphNode.sourceReportedNetworkIds?.length) {
      return externalOccurrenceInspector(graphNode, dataIndex);
    }
    const source = dataIndex.sources.get(item.sourceId);
    const candidates = payload.identityCandidates
      .filter((candidate) => candidate.status === "unresolved")
      .filter((candidate) => candidate.fromOccurrenceId === item.id || candidate.toOccurrenceId === item.id)
      .map((candidate) => {
        const otherId = candidate.fromOccurrenceId === item.id ? candidate.toOccurrenceId : candidate.fromOccurrenceId;
        const other = dataIndex.occurrences.get(otherId);
        const score = candidate.score == null ? "bez oceny siły tropu" : `${Math.round(candidate.score * 100)}/100 siły tropu`;
        const semantics = candidate.scoreSemantics === "research_priority_not_probability" ? " — to nie jest prawdopodobieństwo" : "";
        return {
          id: candidate.id,
          status: candidate.status,
          fromOccurrenceId: candidate.fromOccurrenceId,
          toOccurrenceId: candidate.toOccurrenceId,
          label: dataIndex.occurrenceLabel(other),
          fromLabel: candidate.fromLabel,
          toLabel: candidate.toLabel,
          scoreValue: candidate.score,
          highPriority: candidate.highPriority === true,
          score: `${score}${semantics}`,
          basis: candidate.basis || "Brak zapisanego uzasadnienia.",
        };
      });
    const displayName = dataIndex.occurrenceLabel(item);
    const originalDiffers = item.name && item.name.toLocaleLowerCase("pl-PL") !== displayName.toLocaleLowerCase("pl-PL");
    return {
      kindLabel: "Wystąpienie osoby",
      status: statusLabel(item.status),
      statusKind: "is-warning",
      title: displayName,
      subtitle: originalDiffers ? `Oryginalny zapis: ${item.name}` : "Osoba nazwana w jednym dokumencie",
      sections: [{
        title: "W tym zapisie",
        rows: [
          ["Zakres", graphVisibilityLabel(graphNode.visibilityRole)],
          ["Rola", roleLabel(item.role) || "nieokreślona"],
          ["Urodzenie", item.birth || "nie podano"],
          ["Pewność nazwy", item.confidence == null ? "nieoceniona w tym odczycie" : `${Math.round(item.confidence * 100)}% odczytu`],
          ["Źródło", source?.title || "brak źródła"],
        ],
      }],
      review: graphNode.review || null,
      candidates,
      sourceId: source?.id || null,
      actionLabel: source ? "Otwórz akt i transkrypcję" : null,
    };
  }
  if (graphNode.kind === "identity") {
    const appearances = (item.occurrenceIds || []).map((occurrenceId) => {
      const occurrence = dataIndex.occurrences.get(occurrenceId);
      const source = dataIndex.sources.get(occurrence?.sourceId);
      const meta = occurrenceViewMetadata(occurrence, dataIndex);
      return {
        id: occurrenceId,
        label: meta.label,
        literalName: occurrence?.name || occurrenceId,
        source: source?.title || "brak źródła",
        sourceId: source?.id || null,
        review: dataIndex.sourceReview(source?.id),
        sortKey: meta.event?.dateFrom || meta.event?.date || "9999",
      };
    }).sort((left, right) => left.sortKey.localeCompare(right.sortKey) || left.id.localeCompare(right.id));
    const occurrenceIds = new Set(item.occurrenceIds || []);
    const candidates = payload.identityCandidates
      .filter((candidate) => candidate.status === "unresolved")
      .filter((candidate) => occurrenceIds.has(candidate.fromOccurrenceId) || occurrenceIds.has(candidate.toOccurrenceId))
      .map((candidate) => ({
        id: candidate.id,
        status: candidate.status,
        fromOccurrenceId: candidate.fromOccurrenceId,
        toOccurrenceId: candidate.toOccurrenceId,
        label: `${candidate.fromLabel} ⇄ ${candidate.toLabel}`,
        fromLabel: candidate.fromLabel,
        toLabel: candidate.toLabel,
        scoreValue: candidate.score,
        highPriority: candidate.highPriority === true,
        score: candidate.score == null ? "bez oceny siły tropu" : `${Math.round(candidate.score * 100)}/100 siły tropu — to nie jest prawdopodobieństwo`,
        basis: candidate.basis || "Brak zapisanego uzasadnienia.",
      }));
    return {
      kindLabel: "Osoba kanoniczna",
      status: "tożsamość rozstrzygnięta",
      statusKind: "is-good",
      title: item.label,
      subtitle: `${item.lifeSpanLabel || "?–?"} · ${polishCount(appearances.length, "wystąpienie źródłowe", "wystąpienia źródłowe", "wystąpień źródłowych")} połączonych jawną decyzją identity`,
      sections: [{
        title: "Klaster tożsamości",
        rows: [
          ["Osoba", item.personId],
          ["Lata życia", item.lifeSpanLabel || "?–? · brak ustalonych granic"],
          ["Wystąpienia", appearances.length],
          ["Status osoby", statusLabel(item.status)],
        ],
      }],
      appearances,
      candidates,
      identityPersonId: item.personId,
      identityExpanded: graphNode.expandedIdentity === true,
      identityActionLabel: graphNode.identityExpansionLocked
        ? null
        : graphNode.expandedIdentity
        ? "Zwiń wystąpienia na grafie"
        : `Pokaż ${polishCount(appearances.length, "wystąpienie", "wystąpienia", "wystąpień")} na grafie`,
      evidencePersonId: item.personId,
      evidenceActionLabel: "Otwórz kartę badawczą",
      evidenceGraphVisible: graphNode.evidenceGraphVisible === true,
      searchMatch: graphNode.searchMatch ? {
        title: graphNode.searchMatch.kind === "occurrence" ? "Dopasowano wystąpienie" : "Dopasowano osobę",
        label: graphNode.searchMatch.label,
        detail: graphNode.searchMatch.detail || null,
        occurrenceId: graphNode.searchMatch.occurrenceId || null,
      } : null,
      review: graphNode.review || null,
    };
  }
  if (graphNode.kind === "event") {
    const source = dataIndex.sources.get(item.sourceId);
    const originalDates = (item.originalDates || [item.date]).filter(Boolean);
    const normalizedDate = polishDate(item.dateFrom || item.date);
    const participants = item.participants.slice(0, 16).map((part) => {
      const occurrence = dataIndex.occurrences.get(part.occurrenceId);
      const displayName = dataIndex.occurrenceLabel(occurrence);
      const details = [part.birth ? `ur. ${part.birth}` : null, part.age ? `wiek w akcie: ${part.age}` : null].filter(Boolean).join(" · ");
      const originalDiffers = occurrence?.name && occurrence.name.toLocaleLowerCase("pl-PL") !== displayName.toLocaleLowerCase("pl-PL");
      return {
        role: roleLabel(part.role),
        name: displayName,
        details,
        original: originalDiffers ? occurrence.name : null,
      };
    });
    return {
      kindLabel: "Zdarzenie",
      status: statusLabel(item.status),
      statusKind: "is-warning",
      title: eventTypeLabel(item.type),
      subtitle: `${normalizedDate} · ${item.place || "miejsce nieustalone"}`,
      sections: [{
        title: "Metryka",
        rows: [
          ["Zakres", graphVisibilityLabel(graphNode.visibilityRole)],
          ["Data", normalizedDate],
          ["Zapis w akcie", originalDates.join(" · ") || "nieustalony"],
          ["Miejsce", item.place || "nieustalone"],
          ["Uczestnicy", item.participants.length],
        ],
      }],
      participants,
      review: graphNode.review || null,
      sourceId: source?.id || null,
      actionLabel: source ? "Otwórz dokument źródłowy" : null,
      evidenceEventId: graphNode.contextualEvidenceFor ? item.id : null,
      evidenceEventExpanded: graphNode.evidenceDetailsExpanded === true,
      evidenceEventActionLabel: graphNode.contextualEvidenceFor
        ? graphNode.evidenceDetailsExpanded
          ? "Zwiń wystąpienie i źródło"
          : "Pokaż wystąpienie i źródło na grafie"
        : null,
    };
  }
  if (graphNode.kind === "source") {
    return {
      kindLabel: "Źródło i akt",
      status: statusLabel(item.status),
      statusKind: item.status === "Confirmed" ? "is-good" : "is-warning",
      title: item.title || item.locator || "Źródło bez tytułu",
      subtitle: item.locator || "Brak zapisanego lokatora",
      sections: [{
        title: "Pochodzenie",
        rows: [
          ["Zakres", graphVisibilityLabel(graphNode.visibilityRole)],
          ["Typ", item.type || "nieustalony"],
          ["Data", polishDate(item.date)],
          ["Lokalnie", item.availableLocally ? "tak" : "nie"],
          ["Materiały", item.artifactCount ?? item.artifacts?.length ?? 0],
          ["Odczyty", item.readings?.length ?? 0],
        ],
      }],
      review: graphNode.review || null,
      sourceId: item.id,
      actionLabel: "Otwórz skan i transkrypcję",
    };
  }
  if (graphNode.kind === "bridge") {
    return {
      kindLabel: "Łącznik rodzinny",
      status: "warstwa prezentacyjna",
      statusKind: "",
      title: item.label,
      subtitle: item.summary || "Łącznik potrzebny do pokazania ścieżki wybranej linii.",
      sections: [{
        title: "Podstawa",
        rows: [
          ["Rola", graphVisibilityLabel(graphNode.visibilityRole)],
          ["Warstwa", "prywatne drzewo prezentacyjne"],
          ["Zakresy", (graphNode.scopeIds || []).join(", ") || "wybrana linia"],
          ["Dowód z aktu", "nie — łącznik nie jest relacją źródłową"],
        ],
      }],
    };
  }
  if (graphNode.kind === "cluster") {
    const reconstructedHousehold = item.type === "household"
      && item.residenceEvidenceStatus === "unconfirmed";
    return {
      kindLabel: reconstructedHousehold
        ? "Gospodarstwo rekonstruowane"
        : item.type === "household"
          ? "Klaster gospodarstwa"
        : item.type === "unassigned"
          ? "Wystąpienia poza klastrami"
          : "Klaster sieci badawczej",
      status: "agregat prezentacyjny",
      statusKind: "",
      title: item.label,
      subtitle: reconstructedHousehold
        ? "Rodzina z drzewa jest kandydatem wspólnego gospodarstwa; adres i okres zamieszkania czekają na dokument."
        : item.type === "unassigned"
        ? "Kontrolowany koszyk jednoosobowych kontekstów. Elementy nie są ze sobą scalone ani przedstawione jako grupa."
        : "Podsumowanie kontekstu. Nie oznacza automatycznie rodziny biologicznej ani tej samej osoby.",
      sections: [
        {
          title: "Zawartość klastra",
          rows: [
            ["Osoby", item.memberCount],
            ["Wystąpienia", item.occurrenceCount],
            ["Akty", item.actCount],
            ["Kandydatury", item.candidateCount],
            ...(reconstructedHousehold ? [["Wspólny adres", "do potwierdzenia dokumentem"]] : []),
            ...(item.sourceLabel && item.sourceLabel !== item.label ? [["Identyfikator roboczy", item.sourceLabel]] : []),
          ],
        },
        {
          title: "Widoczne nazwiska",
          rows: (item.memberLabels || []).slice(0, 12).map((label, index) => [index ? "" : "Osoby", label]),
        },
      ].filter((section) => section.rows.length),
      clusterId: graphNode.id,
      clusterExpanded: graphNode.clusterExpanded === true,
      clusterActionLabel: graphNode.clusterExpanded ? "Zwiń elementy klastra" : "Pokaż osoby klastra na grafie",
      searchMatch: graphNode.searchMatch ? {
        title: graphNode.searchMatch.kind === "occurrence" ? "Dopasowano zawartość klastra" : "Dopasowano klaster",
        label: graphNode.searchMatch.label,
        detail: graphNode.searchMatch.detail || null,
        occurrenceId: graphNode.searchMatch.occurrenceId || null,
      } : null,
    };
  }
  return {
    kindLabel: "Klaster gospodarstwa",
    status: statusLabel(item.status),
    statusKind: "is-warning",
    title: item.label,
    subtitle: item.kind === "family_reconstruction"
      ? "Rodzina z drzewa potraktowana jako kandydat gospodarstwa; wspólny adres nie jest jeszcze potwierdzony."
      : "Grupa ograniczona miejscem i czasem — nie automatyczna rodzina biologiczna",
    sections: [{
      title: "Zakres",
      rows: [
        ["Rola filtra", graphVisibilityLabel(graphNode.visibilityRole)],
        ...(item.kind === "family_reconstruction" ? [["Wspólny adres", "do potwierdzenia dokumentem"]] : []),
        ["Od", item.dateFrom || "nieustalone"],
        ["Do", item.dateTo || "nieustalone"],
        ["Członkowie", item.members.length],
      ],
    }],
  };
}
