export const RESEARCH_STAGE_LABELS = Object.freeze({
  detected: "Wykryte",
  triage: "Triage",
  ready: "Gotowe",
  in_progress: "W toku",
  blocked: "Zablokowane",
  correlation_review: "Korelacja",
  answered: "Odpowiedziane",
  dismissed: "Zamknięte",
});

export function researchStageLabel(stage) {
  return RESEARCH_STAGE_LABELS[stage] || stage;
}

export const RESEARCH_SCOPE_TYPE_LABELS = Object.freeze({
  program: "Program",
  lineage: "Linia",
  branch: "Gałąź",
  family_unit: "Rodzina",
  place_study: "Studium miejsca",
  theme: "Temat",
  legacy_case: "Sprawa legacy",
  other: "Inny zakres",
});

export function researchScopeTypeLabel(type) {
  return RESEARCH_SCOPE_TYPE_LABELS[type] || type || "Zakres";
}

function placeIds(item) {
  const targets = [
    ...(item.targets || []),
    ...(item.scopes || []).flatMap((scope) => scope.targets || []),
  ];
  return targets
    .filter((target) => target.type === "place" || target.type === "place_study")
    .map((target) => target.id);
}

export function filterBacklogItems(items, {
  query = "", caseId = "", scopeId = "", placeId = "", stage = "", origin = "",
} = {}) {
  const normalizedQuery = query.trim().toLocaleLowerCase("pl-PL");
  return items.filter((item) => {
    const searchText = [
      item.question, item.caseId, item.caseTitle, item.completionCriterion,
      ...(item.targets || []).map((target) => target.label),
      ...(item.scopes || []).flatMap((scope) => [
        scope.title,
        ...(scope.primaryPath || []).map((part) => part.title),
        ...(scope.targets || []).map((target) => target.label),
      ]),
    ].filter(Boolean).join(" ").toLocaleLowerCase("pl-PL");
    return (!normalizedQuery || searchText.includes(normalizedQuery))
      && (!caseId || item.caseId === caseId)
      && (!scopeId || (item.scopeIds || []).includes(scopeId))
      && (!placeId || placeIds(item).includes(placeId))
      && (!stage || item.workflowStage === stage)
      && (!origin || item.origin === origin);
  });
}

export function groupBacklogLanes(items, workflowStages) {
  const groups = [...new Map(items.map((item) => {
    const scopeId = item.primaryScopeId || `legacy:${item.caseId}`;
    return [scopeId, {
      scopeId,
      scopeTitle: item.primaryScopeTitle || item.caseTitle,
      scopeType: item.primaryScopeType || "legacy_case",
      caseId: item.caseId,
      caseTitle: item.caseTitle,
    }];
  })).values()];
  return groups.map((group) => {
    const caseItems = items.filter((item) => (
      item.primaryScopeId || `legacy:${item.caseId}`
    ) === group.scopeId);
    const stages = workflowStages
      .filter((stageId) => caseItems.some((item) => item.workflowStage === stageId))
      .map((stageId) => ({ stageId, items: caseItems.filter((item) => item.workflowStage === stageId) }));
    return { ...group, items: caseItems, stages };
  });
}

export function backlogSummary(items) {
  return {
    visible: items.length,
    curated: items.filter((item) => item.origin === "curated").length,
    deterministic: items.filter((item) => item.origin === "deterministic").length,
  };
}
