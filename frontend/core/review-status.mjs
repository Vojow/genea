const AGENT_APPROVED = new Set(["agent_consensus", "agent_adjudicated"]);
const FIELD_STATUSES = new Set([...AGENT_APPROVED, "agent_single_pass", "human_review_required", "human_reviewed"]);
const count = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
const COUNT_KEYS = ["agentApprovedFieldCount", "singlePassFieldCount", "humanReviewRequiredFieldCount", "humanReviewedFieldCount"];

export function sourceReviewFieldStatus(field) {
  if (!field || typeof field !== "object" || Array.isArray(field)) return null;
  const status = Object.hasOwn(field, "status") ? field.status : field.reviewStatus;
  if (!FIELD_STATUSES.has(status)) return null;
  if (Object.hasOwn(field, "status") && Object.hasOwn(field, "reviewStatus") && field.reviewStatus !== status) return null;
  return status;
}

function fieldCounts(summary) {
  const counts = {
    agentApprovedFieldCount: count(summary.agentApprovedFieldCount),
    singlePassFieldCount: count(summary.singlePassFieldCount),
    humanReviewRequiredFieldCount: count(summary.humanReviewRequiredFieldCount),
    humanReviewedFieldCount: count(summary.humanReviewedFieldCount),
  };
  const fieldCount = count(summary.fieldCount);
  const unmappedFieldCount = count(summary.unmappedFieldCount);
  const state = summary.fieldReviewState;
  if (state == null) return { ...counts, fieldCount, unmappedFieldCount, fieldReviewState: "unknown" };
  const mapped = COUNT_KEYS.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  const completeCounts = COUNT_KEYS.every((key) => counts[key] !== null);
  if (["known", "partial"].includes(state) && completeCounts && mapped > 0 && fieldCount !== null
    && unmappedFieldCount !== null && mapped + unmappedFieldCount === fieldCount
    && (state === "known" ? unmappedFieldCount === 0 : unmappedFieldCount > 0)) {
    return { ...counts, fieldCount, unmappedFieldCount, fieldReviewState: state };
  }
  return { agentApprovedFieldCount: null, singlePassFieldCount: null, humanReviewRequiredFieldCount: null, humanReviewedFieldCount: null, fieldCount,
    unmappedFieldCount: state === "unknown" && fieldCount !== null && unmappedFieldCount === fieldCount ? unmappedFieldCount : null, fieldReviewState: "unknown" };
}

function reviewLabel(state, counts, explicitUnknown = false) {
  if (explicitUnknown) return "stan kontroli pól nieustalony";
  const agent = counts.agentApprovedFieldCount;
  const human = counts.humanReviewRequiredFieldCount;
  const base = state === "human_reviewed" ? "sprawdzone przez Ciebie"
    : state === "partial_agent_review" ? `agentowo ${agent ?? "część"} · dla Ciebie ${human ?? "nieustalone"}`
    : state === "human_review_required" ? `wymaga Twojej kontroli${human !== null ? ` · ${human} pól` : ""}`
    : state === "agent_approved" ? `sprawdzone przez agentów${agent !== null ? ` · ${agent} pól` : ""}`
    : state === "partial_field_review" ? "znany podzbiór kontroli pól"
    : `jeden odczyt agentowy${counts.singlePassFieldCount !== null ? ` · ${counts.singlePassFieldCount} pól` : ""}`;
  const partial = counts.fieldReviewState === "partial" ? ` · ${counts.unmappedFieldCount ?? "nieustalone"} pól o nieustalonym stanie` : "";
  return base + partial;
}

export function sourceReviewSummary(source) {
  const workflow = source?.reviewWorkflow;
  if (!workflow) return null;
  const summary = workflow.summary || {};
  const counts = fieldCounts(summary);
  const needsHuman = summary.needsHumanReview === true || workflow.status === "human_review_required";
  const hasAgent = summary.hasAgentApproval === true || AGENT_APPROVED.has(workflow.status);
  let state = "agent_single_pass";
  if (workflow.status === "human_reviewed") {
    state = "human_reviewed";
  } else if (needsHuman && hasAgent) {
    state = "partial_agent_review";
  } else if (needsHuman) {
    state = "human_review_required";
  } else if (hasAgent) {
    state = "agent_approved";
  }
  const unknownMapping = (summary.fieldReviewState != null && counts.fieldReviewState === "unknown")
    || COUNT_KEYS.every((key) => counts[key] === null);
  if (unknownMapping) state = "review_unknown";
  else if (counts.fieldReviewState === "partial" && state === "human_reviewed") state = "partial_field_review";
  return {
    state,
    label: reviewLabel(state, counts, unknownMapping),
    ...counts,
    needsHumanReview: needsHuman,
    hasAgentApproval: hasAgent,
  };
}

export function aggregateSourceReviews(sources) {
  const summaries = [...new Map((sources || []).filter(Boolean).map((source) => [source.id, source])).values()]
    .map(sourceReviewSummary)
    .filter(Boolean);
  if (!summaries.length) return null;
  const sumCount = (key) => summaries.every((item) => item[key] !== null)
    ? summaries.reduce((sum, item) => sum + item[key], 0) : null;
  const counts = Object.fromEntries([...COUNT_KEYS, "fieldCount", "unmappedFieldCount"].map((key) => [key, sumCount(key)]));
  counts.fieldReviewState = summaries.every((item) => item.fieldReviewState === "known") ? "known"
    : summaries.some((item) => item.fieldReviewState !== "unknown") ? "partial" : "unknown";
  const needsHumanReview = summaries.some((item) => item.needsHumanReview);
  const hasAgentApproval = summaries.some((item) => item.hasAgentApproval);
  const allHumanReviewed = summaries.every((item) => item.state === "human_reviewed");
  const state = counts.agentApprovedFieldCount === null && counts.humanReviewRequiredFieldCount === null
    ? "review_unknown"
    : allHumanReviewed
    ? "human_reviewed"
    : needsHumanReview && hasAgentApproval
      ? "partial_agent_review"
      : needsHumanReview
        ? "human_review_required"
        : hasAgentApproval
          ? "agent_approved"
          : "agent_single_pass";
  const label = reviewLabel(state, counts, counts.agentApprovedFieldCount === null && counts.humanReviewRequiredFieldCount === null);
  return { state, label, ...counts, needsHumanReview, hasAgentApproval };
}

export function reviewBadgeKind(state) {
  if (state === "human_reviewed") return "is-good";
  if (state === "human_review_required" || state === "partial_agent_review") return "is-warning";
  if (state === "agent_approved") return "is-agent";
  return "";
}
