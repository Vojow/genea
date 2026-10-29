import { describe, expect, it } from "vitest";
import { DataIndex } from "../../core/data-index.mjs";
import { projectApprovedActMaterials } from "../../core/approved-act-materials.mjs";
import { projectPersonProfile } from "./person-profile-projector";
import type { PresentedPersonContext, PresentationPayload } from "../types/presentation";

function fixture() {
  const chain = { candidateId: "candidate-exact", commandId: "command-exact", decisionId: "decision-exact" };
  const guarded = { personId: "person", identityStatus: "resolved_same", personIdentityResolved: true,
    identityDecisionTier: "agent_approved_human_pending", sourceProfileIdentityGuarded: true,
    sourceProfileIdentityDirectChains: [chain] };
  const wrapper = { ...guarded, id: "wrapper", externalTreeCandidate: true, sourceRedacted: true, status: "imported_unverified", name: "Osoba testowa" };
  const approved = { ...guarded, id: "approved", sourceId: "act-approved", name: "Osoba testowa", status: "agent_approved", role: "groom" };
  const unapproved = { ...approved, id: "unapproved-inside", sourceId: "act-unapproved", sourceProfileIdentityDirectChains: [] };
  const stale = { ...approved, id: "stale", sourceId: "act-stale", sourceProfileIdentityDirectChains: [{ ...chain, decisionId: "decision-stale" }] };
  const mismatched = { ...approved, id: "mismatched", personId: "other-person", sourceId: "act-mismatched" };
  const context: PresentedPersonContext = {
    personKey: "person", publicProfileId: "profile", identityScope: "canonical", label: "Osoba testowa",
    canonicalPersonIds: ["person"], treePersonIds: ["profile"], mapPersonIds: [], graphIdentityIds: [], aliases: ["person"],
    occurrenceIds: ["wrapper", "approved", "unapproved-inside", "stale", "mismatched"],
    sourceIds: ["act-approved", "act-unapproved", "act-stale", "act-mismatched"],
    sourceReportedMemberships: [{ networkId: "network", treeNodeId: "profile", occurrenceId: "profile", classificationState: "classified", role: "anchor", unclassifiedReason: null }],
  };
  const payload = {
    people: [{ id: "person", name: context.label }], personContexts: [context], sources: context.sourceIds.map((id) => ({ id, type: "act", title: id, readings: [] })),
    occurrences: [wrapper, approved, unapproved, { ...unapproved, id: "unapproved-outside" }, stale, mismatched],
    events: [{ id: "event-approved", type: "marriage", date: "1913", sourceId: "act-approved", participants: [{ occurrenceId: "approved", role: "groom" }] },
      { id: "event-unapproved", type: "birth", date: "1800", sourceId: "act-unapproved", participants: [{ occurrenceId: "unapproved-inside", role: "child" }] }],
    sourceReportedNetworks: [{ id: "network", kind: "external_tree_candidate", label: "Sieć", status: "imported_unverified", nodes: [{
      treeNodeId: "profile", publicProfileId: "profile", personId: "profile", occurrenceId: "profile", label: context.label,
      status: "candidate", identityStatus: "resolved_same", profileIdentityStatus: "resolved_same", personIdentityResolved: true,
      identityDecisionTier: "agent_approved_human_pending", generation: 0, order: 0,
      classification: { state: "classified", primaryRole: "anchor", alternatives: [], unclassifiedReason: null },
    }], edges: [] }],
    relationships: [], identityCandidates: [{ id: "candidate-lead", status: "unresolved", fromOccurrenceId: "wrapper", toOccurrenceId: "unapproved-inside" }],
    households: [], claims: [], conflicts: [], familyTree: { people: [], unions: [], families: [] },
    researchBacklog: { items: [] }, map: { places: [] },
  } as unknown as PresentationPayload;
  return { context, payload, approved, wrapper };
}

describe("source-reported profile approved primary acts", () => {
  it("matches shared admissible cards/counts and rejects shared-person grafts inside and outside context", () => {
    const { context, payload } = fixture();
    const shared = projectApprovedActMaterials({ publicProfileId: "profile", dataIndex: new DataIndex(payload) });
    const model = projectPersonProfile(payload, context, null, {});
    expect(model.approvedActMaterials).toEqual(shared.materials);
    expect(model.approvedActMaterialSummary).toBe("1 akt · 1 wystąpienie");
    expect(model.occurrenceEntries.map((entry) => entry.id)).toEqual(["approved"]);
    expect(model.metrics.occurrences).toBe(shared.occurrenceCount);
    expect(model.timeline.map((entry) => entry.eventId)).toEqual(["event-approved"]);
    expect(model.sourceBoundaryLabel).not.toContain("1800");
    expect(model.sourceReportedLeadEntries).toEqual([expect.objectContaining({ id: "wrapper", entryKind: "source_reported_lead", sourceId: null, eventId: null })]);
    expect(model.identityCandidates.map((candidate) => candidate.id)).toEqual(["candidate-lead"]);
    expect(model.identityNotice).toContain("oczekuje potwierdzenia człowieka");
    expect(model.family.externalTreeRelations).toEqual([]);
    payload.events.unshift({ id: "wrong-source-event", type: "birth", date: "1777", sourceId: "act-unapproved",
      participants: [{ occurrenceId: "approved", role: "child" }] } as unknown as PresentationPayload["events"][number]);
    const mismatchedEvent = projectPersonProfile(payload, context, null, {});
    expect(mismatchedEvent.occurrenceEntries[0].eventId).toBe("event-approved");
    expect(mismatchedEvent.sourceBoundaryLabel).not.toContain("1777");
  });

  it("does not turn a wrapper into an act even if it carries a visible act source ID", () => {
    const { context, payload, wrapper } = fixture();
    Object.assign(wrapper, { sourceId: "act-approved", sourceRedacted: false });
    const model = projectPersonProfile(payload, context, null, {});
    expect(model.occurrenceEntries.map((entry) => entry.id)).toEqual(["approved"]);
    expect(model.sourceReportedLeadEntries[0].sourceId).toBeNull();
    expect(model.metrics.occurrences).toBe(1);
  });

  it("keeps established regular canonical profile occurrence behavior", () => {
    const { context, payload } = fixture();
    payload.sourceReportedNetworks = [];
    context.sourceReportedMemberships = [];
    const model = projectPersonProfile(payload, context, null, {});
    expect(model.occurrenceEntries.map((entry) => entry.id)).toEqual(expect.arrayContaining(["approved", "unapproved-inside", "unapproved-outside", "stale", "mismatched", "wrapper"]));
    expect(model.metrics.occurrences).toBe(6);
    expect(model.sourceReportedLeadEntries).toEqual([]);
    expect(model.approvedActMaterialSummary).toBeNull();
  });

  it("keeps explicit source-reported membership guarded when the network node is absent", () => {
    const { context, payload } = fixture();
    payload.sourceReportedNetworks = [];
    const model = projectPersonProfile(payload, context, null, {});
    expect(model.occurrenceEntries.map((entry) => entry.id)).toEqual(["approved"]);
    expect(model.metrics.occurrences).toBe(1);
    expect(model.sourceReportedLeadEntries[0].sourceId).toBeNull();
  });
});
