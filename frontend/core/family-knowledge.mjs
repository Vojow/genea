const FAMILY_KNOWLEDGE_FIELDS = Object.freeze({
  status: "family_known",
  provenanceKind: "family_testimony",
  basis: "user_reported_family_conversations",
  confidenceDeclared: "100%",
  confidenceSemantics: "user_declared",
  recognitionScope: "family_person",
});

// The backend validates provenance and privacy; UI accepts only this typed shape.
export function sanitizeFamilyKnowledge(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (!Object.entries(FAMILY_KNOWLEDGE_FIELDS).every(([key, expected]) => value[key] === expected)) return null;
  if (typeof value.provenanceRef !== "string" || !value.provenanceRef.trim()) return null;
  return { ...FAMILY_KNOWLEDGE_FIELDS, provenanceRef: value.provenanceRef };
}

export function familyKnowledgePresentation(value) {
  if (!sanitizeFamilyKnowledge(value)) return null;
  return {
    label: "Znany rodzinnie",
    badgeLabel: "Znany rodzinnie · 100% użytkownika",
    confidenceLabel: "100% — pewność zadeklarowana przez użytkownika",
    basisLabel: "Rozmowy rodzinne — relacja użytkownika",
    notice: "Pewność rozpoznania osoby: 100% — deklaracja użytkownika na podstawie rozmów rodzinnych. Dopasowanie do konkretnych aktów wymaga osobnej oceny.",
  };
}
