export const TREE_CARD_GEOMETRY = Object.freeze({ width: 224, height: 86 });

const ENTER_COMPACT = .52;
const LEAVE_COMPACT = .60;
const ENTER_COMFORTABLE = 1.14;
const LEAVE_COMFORTABLE = 1.06;

export function semanticTreeCardVariant(scale, previous = "standard") {
  if (previous === "compact") return scale >= LEAVE_COMPACT ? "standard" : "compact";
  if (previous === "comfortable") return scale <= LEAVE_COMFORTABLE ? "standard" : "comfortable";
  if (scale <= ENTER_COMPACT) return "compact";
  if (scale >= ENTER_COMFORTABLE) return "comfortable";
  return "standard";
}

function attentionMarker(person, review, candidateCount, identityScope, identityComponentCount) {
  if (identityScope === "hypothesis_group") {
    return {
      kind: "candidate",
      symbol: `?${Math.max(2, identityComponentCount || 0)}`,
      label: `${Math.max(2, identityComponentCount || 0)} osoby kanoniczne do rozstrzygnięcia`,
    };
  }
  if (person.evidenceStatus === "unresolved") {
    return { kind: "conflict", symbol: "!", label: "Nierozstrzygnięta informacja" };
  }
  if (review?.needsHumanReview) {
    return { kind: "review", symbol: "!", label: review.label || "Wymaga kontroli człowieka" };
  }
  if (review) {
    return {
      kind: "review",
      symbol: review.state === "human_reviewed" ? "✓" : "A",
      label: review.label,
    };
  }
  if (candidateCount) {
    return {
      kind: "candidate",
      symbol: `?${candidateCount}`,
      label: `${candidateCount} kandydatur do sprawdzenia`,
    };
  }
  return null;
}

export function presentTreePersonCard(person, {
  variant = "standard",
  generationLabel = "",
  evidenceLabel = "Status nieustalony",
  review = null,
  candidateCount = 0,
  identityScope = "presentation_only",
  identityComponentCount = 0,
} = {}) {
  const fullName = String(person?.name || "Osoba bez nazwy").trim();
  const lifeSpanLabel = String(person?.lifeSpanLabel || "?–?").trim();
  const initials = person?.privacy === "masked_living"
    ? "•"
    : fullName.split(/\s+/).slice(0, 2).map((part) => part[0]).join("");
  return {
    variant,
    width: TREE_CARD_GEOMETRY.width,
    height: TREE_CARD_GEOMETRY.height,
    displayName: fullName,
    fullName,
    accessibleName: `Wybierz osobę: ${fullName}, lata życia ${lifeSpanLabel}`,
    initials,
    subtitle: variant === "compact" ? lifeSpanLabel : lifeSpanLabel,
    detail: person?.summary || generationLabel.toLocaleLowerCase("pl"),
    lifeSpanLabel,
    evidence: evidenceLabel,
    identityScope,
    identityComponentCount,
    attention: attentionMarker(person || {}, review, candidateCount, identityScope, identityComponentCount),
  };
}
