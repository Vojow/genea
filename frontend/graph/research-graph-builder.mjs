import { eventTypeLabel, roleLabel } from "../core/formatting.mjs";
import { sanitizeFamilyKnowledge } from "../core/family-knowledge.mjs";

function evidenceDetail(event, source) {
  const date = event?.dateFrom || event?.date || event?.originalDates?.[0] || "";
  const year = String(date).match(/\b(1[5-9]\d{2}|20\d{2})\b/)?.[1] || "rok nieustalony";
  const locator = source?.locator || source?.title || "akt bez lokatora";
  return `${year} · ${locator}`;
}

function graphHouseholds(payload) {
  const candidates = (payload.graphScopes?.scopes || [])
    .flatMap((scope) => scope.familyHouseholds || []);
  const byId = new Map();
  for (const household of [...(payload.households || []), ...candidates]) {
    if (household?.id && !byId.has(household.id)) byId.set(household.id, household);
  }
  return [...byId.values()];
}

function sourceReportedNetworks(payload) {
  if (Array.isArray(payload.sourceReportedNetworks)) return payload.sourceReportedNetworks;
  return Array.isArray(payload.sourceReportedNetworks?.networks)
    ? payload.sourceReportedNetworks.networks
    : [];
}

function isExternalTreeRelation(relation) {
  return relation?.restrictedBacking === true
    || relation?.relationshipScope === "source_reported_external_tree";
}

function externalRelationItem(relation) {
  const safeProvenance = [
    "source_assertion",
    "source_reported_network",
    "controlled_external_tree_export",
  ].includes(relation?.provenance) ? relation.provenance : null;
  return {
    id: relation.id,
    type: relation.type || "relationship",
    status: relation.status || "source-reported",
    identityStatus: relation.identityStatus || "unresolved",
    relationshipIdentityStatus: relation.relationshipIdentityStatus
      || relation.identityStatus
      || "unresolved",
    canonicalRelationship: false,
    ...(safeProvenance ? { provenance: safeProvenance } : {}),
  };
}

function sameEndpoints(edge, from, to) {
  return (edge.from === from && edge.to === to) || (edge.from === to && edge.to === from);
}

function externalProfileIdentityResolved(item) {
  return item?.personIdentityResolved === true
    || item?.profileIdentityStatus === "resolved_same"
    || item?.externalTreeIdentityProjection === "resolved_same"
    || item?.identityStatus === "resolved_same";
}

export class ResearchGraphBuilder {
  constructor({ payload, dataIndex }) {
    this.payload = payload;
    this.dataIndex = dataIndex;
  }

  build(mode, { includeDocuments = false } = {}) {
    const viewMode = mode === "family" ? "people" : mode;
    const includeDocumentLayer = includeDocuments && (viewMode === "fan" || viewMode === "clusters");
    const nodeDefs = new Map();
    const edges = [];
    const sourceReportedNodeByOccurrenceId = new Map(
      sourceReportedNetworks(this.payload).flatMap((network) => (network.nodes || [])
        .filter((item) => item?.occurrenceId)
        .map((item) => [item.occurrenceId, item])),
    );
    const externalRelationLabel = (fromOccurrenceId, toOccurrenceId) => {
      const endpointResolved = [fromOccurrenceId, toOccurrenceId].some((occurrenceId) => (
        externalProfileIdentityResolved(sourceReportedNodeByOccurrenceId.get(occurrenceId))
        || externalProfileIdentityResolved(this.dataIndex.occurrences.get(occurrenceId))
      ));
      return endpointResolved
        ? "źródło zewnętrzne · relacja niekanoniczna i niepotwierdzona"
        : "źródło zewnętrzne · relacja nierozstrzygnięta";
    };
    const addOccurrence = (id) => {
      const item = this.dataIndex.occurrences.get(id);
      if (!item || nodeDefs.has(`occ:${id}`)) return;
      nodeDefs.set(`occ:${id}`, {
        id: `occ:${id}`, ref: id, kind: "occurrence",
        label: this.dataIndex.occurrenceLabel(item), item,
        review: this.dataIndex.occurrenceReview(item),
      });
    };
    const addExternalOccurrence = (item, networkId) => {
      const occurrenceId = item?.occurrenceId;
      if (!occurrenceId) return;
      const source = this.dataIndex.occurrences.get(occurrenceId) || {};
      const profileIdentityStatus = item.profileIdentityStatus
        || item.identityStatus
        || source.externalTreeIdentityProjection
        || source.identityStatus
        || "unresolved";
      const personIdentityResolved = externalProfileIdentityResolved(item)
        || externalProfileIdentityResolved(source);
      const familyKnowledge = sanitizeFamilyKnowledge(item.familyKnowledge
        ?? (source.id === occurrenceId ? source.familyKnowledge : null));
      const sourceTier = source.id === occurrenceId ? source.identityDecisionTier : null;
      const reportedTier = item.identityDecisionTier ?? sourceTier;
      const tierConflict = item.identityDecisionTier != null && sourceTier != null
        && item.identityDecisionTier !== sourceTier;
      const identityDecisionTier = personIdentityResolved && !tierConflict
        && ["agent_approved_human_pending", "human_confirmed"].includes(reportedTier)
        ? reportedTier : null;
      const externalItem = {
        id: occurrenceId,
        personId: item.personId || source.personId || null,
        publicProfileId: item.publicProfileId || item.treeNodeId || null,
        name: item.label || source.name || source.normalizedName || occurrenceId,
        status: item.status || source.status || "imported_unverified",
        identityStatus: profileIdentityStatus,
        profileIdentityStatus,
        relationshipIdentityStatus: item.relationshipIdentityStatus || "unresolved",
        personIdentityResolved,
        externalTreeIdentityProjection: item.externalTreeIdentityProjection
          || source.externalTreeIdentityProjection
          || null,
        externalTreeCandidate: true,
        classification: item.classification || null,
        ...(identityDecisionTier ? { identityDecisionTier } : {}),
        ...(familyKnowledge ? { familyKnowledge } : {}),
      };
      const id = `occ:${occurrenceId}`;
      const existing = nodeDefs.get(id);
      nodeDefs.set(id, {
        ...(existing || {}),
        id,
        ref: occurrenceId,
        kind: "occurrence",
        label: item.label || existing?.label || this.dataIndex.occurrenceLabel(externalItem),
        item: externalItem,
        review: null,
        publicProfileId: item.publicProfileId || item.treeNodeId || existing?.publicProfileId || null,
        sourceReportedClassification: item.classification || existing?.sourceReportedClassification || null,
        sourceReportedCandidateRole: item.classification?.state === "conflict"
          ? "conflict"
          : item.classification?.state === "unclassified"
            ? "unclassified"
            : item.classification?.primaryRole || "unclassified",
        sourceReportedNetworkIds: [...new Set([
          ...(existing?.sourceReportedNetworkIds || []),
          networkId,
        ].filter(Boolean))].sort(),
      });
    };
    const addEvent = (item) => {
      const id = `event:${item.id}`;
      if (!nodeDefs.has(id)) nodeDefs.set(id, { id, ref: item.id, kind: "event", label: eventTypeLabel(item.type), item, review: this.dataIndex.sourceReview(item.sourceId) });
    };
    const addSource = (item) => {
      if (!item) return;
      const id = `source:${item.id}`;
      if (!nodeDefs.has(id)) nodeDefs.set(id, {
        id, ref: item.id, kind: "source",
        label: item.title || item.locator || "Źródło bez tytułu", item,
        review: this.dataIndex.sourceReview(item.id),
      });
    };
    const addHousehold = (item) => {
      const id = `house:${item.id}`;
      if (!nodeDefs.has(id)) nodeDefs.set(id, { id, ref: item.id, kind: "household", label: item.label, item });
    };
    const addIdentity = (item) => {
      const id = `identity:${item.personId}`;
      if (!nodeDefs.has(id)) nodeDefs.set(id, {
        id, ref: item.personId, kind: "identity",
        label: `${item.label} · ${item.lifeSpanLabel || "?–?"} · ${item.occurrenceCount} aktów`, item,
        review: this.dataIndex.personReview(item),
      });
    };
    const isFamilyRole = (role) => /(ojciec|matka|syn|cór|dziec|małż|panna młoda|pan młody|wdow|żona|mąż|ochrzcz)/i.test(roleLabel(role));

    if (viewMode !== "clusters" || includeDocumentLayer) {
      for (const event of this.payload.events) {
        const included = event.participants
          .filter((part) => viewMode === "fan" || viewMode === "evidence" || includeDocumentLayer || isFamilyRole(part.role))
          .filter((part) => this.dataIndex.occurrences.has(part.occurrenceId));
        if (!included.length) continue;
        if (viewMode === "evidence" || includeDocumentLayer) {
          addEvent(event);
          for (const part of included) {
            addOccurrence(part.occurrenceId);
            edges.push({
              id: `participation:${event.id}:${part.occurrenceId}`,
              from: `event:${event.id}`, to: `occ:${part.occurrenceId}`,
              kind: "participation", label: roleLabel(part.role), item: event,
            });
          }
          const source = this.dataIndex.sources.get(event.sourceId);
          if (source) {
            addSource(source);
            edges.push({
              id: `source:${event.id}:${source.id}`,
              from: `event:${event.id}`, to: `source:${source.id}`,
              kind: "source", label: "dokument źródłowy", item: source,
            });
          }
        } else {
          const occurrenceIds = [...new Set(included.map((part) => part.occurrenceId))].sort();
          for (const occurrenceId of occurrenceIds) addOccurrence(occurrenceId);
          const anchorId = occurrenceIds[0];
          for (const occurrenceId of occurrenceIds.slice(1)) {
            edges.push({
              id: `event-link:${event.id}:${anchorId}:${occurrenceId}`,
              from: `occ:${anchorId}`, to: `occ:${occurrenceId}`,
              kind: "event-link", label: eventTypeLabel(event.type), item: event,
              backingEdgeIds: [
                `participation:${event.id}:${anchorId}`,
                `participation:${event.id}:${occurrenceId}`,
              ],
              backingNodeIds: [`event:${event.id}`, `occ:${anchorId}`, `occ:${occurrenceId}`],
            });
          }
        }
      }
      for (const relation of this.payload.relationships || []) {
        addOccurrence(relation.fromOccurrenceId);
        addOccurrence(relation.toOccurrenceId);
        const external = isExternalTreeRelation(relation);
        edges.push({
          id: `${external ? "external-relation" : "relation"}:${relation.id}`,
          from: `occ:${relation.fromOccurrenceId}`, to: `occ:${relation.toOccurrenceId}`,
          kind: external ? "external-relation" : "relation",
          label: external
            ? externalRelationLabel(relation.fromOccurrenceId, relation.toOccurrenceId)
            : relation.type,
          item: external ? externalRelationItem(relation) : relation,
          externalRelationshipId: external ? relation.id : null,
        });
      }

      const externalEdgesByRelationshipId = new Map();
      for (const edge of edges.filter((item) => item.kind === "external-relation")) {
        const list = externalEdgesByRelationshipId.get(edge.externalRelationshipId) || [];
        list.push(edge);
        externalEdgesByRelationshipId.set(edge.externalRelationshipId, list);
      }
      for (const network of sourceReportedNetworks(this.payload)) {
        const occurrenceByTreeNodeId = new Map();
        for (const item of network.nodes || []) {
          if (item?.treeNodeId && item?.occurrenceId) {
            occurrenceByTreeNodeId.set(item.treeNodeId, item.occurrenceId);
          }
          addExternalOccurrence(item, network.id);
        }
        for (const edge of network.edges || []) {
          const relationshipId = edge?.relationshipId;
          if (!relationshipId) continue;
          const fromOccurrenceId = occurrenceByTreeNodeId.get(edge.fromTreeNodeId);
          const toOccurrenceId = occurrenceByTreeNodeId.get(edge.toTreeNodeId);
          if (!fromOccurrenceId || !toOccurrenceId || fromOccurrenceId === toOccurrenceId) continue;
          addOccurrence(fromOccurrenceId);
          addOccurrence(toOccurrenceId);
          const from = `occ:${fromOccurrenceId}`;
          const to = `occ:${toOccurrenceId}`;
          const existing = externalEdgesByRelationshipId.get(relationshipId) || [];
          for (const existingEdge of existing) {
            if (existingEdge.sourceReportedProjectionNetworkId && !sameEndpoints(existingEdge, from, to)) continue;
            existingEdge.sourceReportedNetworkIds = [...new Set([
              ...(existingEdge.sourceReportedNetworkIds || []),
              network.id,
            ].filter(Boolean))].sort();
          }
          const matchingProjection = existing.find((item) => sameEndpoints(item, from, to));
          if (matchingProjection) continue;
          const projectedEdge = {
            id: `external-relation:${relationshipId}:network:${network.id}`,
            from,
            to,
            kind: "external-relation",
            label: externalRelationLabel(fromOccurrenceId, toOccurrenceId),
            item: externalRelationItem({
              id: relationshipId,
              type: edge.type,
              status: network.status,
              identityStatus: edge.identityStatus || edge.relationshipIdentityStatus || "unresolved",
              relationshipIdentityStatus: edge.relationshipIdentityStatus
                || network.relationshipIdentityStatus
                || "unresolved",
              provenance: edge.provenance || "source_reported_network",
            }),
            externalRelationshipId: relationshipId,
            sourceReportedNetworkIds: [network.id].filter(Boolean),
            sourceReportedProjectionNetworkId: network.id,
            backingEdgeIds: existing
              .filter((item) => !item.sourceReportedProjectionNetworkId)
              .map((item) => item.id),
          };
          edges.push(projectedEdge);
          externalEdgesByRelationshipId.set(relationshipId, [...existing, projectedEdge]);
        }
      }
    }

    for (const candidate of this.payload.identityCandidates.filter((item) => item.status === "unresolved")) {
      addOccurrence(candidate.fromOccurrenceId);
      addOccurrence(candidate.toOccurrenceId);
      edges.push({
        id: `candidate:${candidate.id}`,
        from: `occ:${candidate.fromOccurrenceId}`, to: `occ:${candidate.toOccurrenceId}`,
        kind: "candidate", label: "kandydat tożsamości", item: candidate,
      });
    }

    for (const identity of this.payload.identityGroups || []) {
      addIdentity(identity);
      for (const occurrenceId of identity.occurrenceIds || []) {
        addOccurrence(occurrenceId);
        edges.push({
          id: `same-person:${identity.personId}:${occurrenceId}`,
          from: `identity:${identity.personId}`, to: `occ:${occurrenceId}`,
          kind: "same-person", label: "ta sama osoba — tożsamość rozstrzygnięta", item: identity,
        });
      }
    }

    if (viewMode === "clusters" || viewMode === "fan") {
      for (const household of graphHouseholds(this.payload)) {
        const members = household.members.flatMap((member) => {
          if (Array.isArray(member.occurrenceIds)) {
            return member.occurrenceIds
              .filter((occurrenceId) => this.dataIndex.occurrences.has(occurrenceId))
              .map((occurrenceId) => ({ ...member, resolvedOccurrenceId: occurrenceId }));
          }
          if (member.occurrenceId && this.dataIndex.occurrences.has(member.occurrenceId)) {
            return [{ ...member, resolvedOccurrenceId: member.occurrenceId }];
          }
          return (this.dataIndex.occurrencesByPerson.get(member.personId) || [])
            .map((occurrence) => ({ ...member, resolvedOccurrenceId: occurrence.id }));
        }).filter((member, index, list) =>
          list.findIndex((candidate) => candidate.resolvedOccurrenceId === member.resolvedOccurrenceId) === index);
        if (!members.length) continue;
        addHousehold(household);
        for (const member of members) {
          addOccurrence(member.resolvedOccurrenceId);
          edges.push({
            id: `household:${household.id}:${member.resolvedOccurrenceId}`,
            from: `house:${household.id}`, to: `occ:${member.resolvedOccurrenceId}`,
            kind: "household", label: member.role, item: household,
          });
        }
      }
    }

    return {
      nodes: [...nodeDefs.values()],
      edges: edges.filter((edge) => nodeDefs.has(edge.from) && nodeDefs.has(edge.to)),
    };
  }

  buildResearchGraph(evidencePersonId = null, expandedEventIds = new Set()) {
    const fan = this.build("fan");
    const evidence = this.build("evidence");
    const contextual = evidencePersonId
      ? this.buildContextual("people", evidencePersonId, expandedEventIds)
      : { nodes: [], edges: [] };
    const nodes = new Map();
    const edges = new Map();
    for (const graph of [fan, evidence, contextual]) {
      for (const item of graph.nodes) nodes.set(item.id, { ...(nodes.get(item.id) || {}), ...item });
      for (const item of graph.edges) edges.set(item.id, { ...(edges.get(item.id) || {}), ...item });
    }
    return {
      nodes: [...nodes.values()],
      edges: [...edges.values()].filter((edge) => nodes.has(edge.from) && nodes.has(edge.to)),
    };
  }

  buildContextual(mode, evidencePersonId = null, expandedEventIds = new Set(), includeDocuments = false) {
    const base = this.build(mode, { includeDocuments });
    if (!evidencePersonId || mode === "evidence") return base;

    const evidence = this.build("evidence");
    const identityId = `identity:${evidencePersonId}`;
    const identity = evidence.nodes.find((item) => item.id === identityId);
    if (!identity) return base;

    const occurrenceIds = new Set(identity.item?.occurrenceIds || []);
    const eventParticipations = new Map();
    for (const edge of evidence.edges) {
      if (edge.kind !== "participation") continue;
      const occurrenceNodeId = edge.from.startsWith("occ:") ? edge.from : edge.to.startsWith("occ:") ? edge.to : null;
      const occurrenceId = occurrenceNodeId?.slice(4);
      if (!occurrenceId || !occurrenceIds.has(occurrenceId)) continue;
      const eventNodeId = edge.from === occurrenceNodeId ? edge.to : edge.from;
      const participations = eventParticipations.get(eventNodeId) || [];
      participations.push({ edge, occurrenceId, occurrenceNodeId });
      eventParticipations.set(eventNodeId, participations);
    }

    const nodeById = new Map(base.nodes.map((item) => [item.id, item]));
    const edgeById = new Map(base.edges.map((item) => [item.id, item]));
    const evidenceNodeById = new Map(evidence.nodes.map((item) => [item.id, item]));
    const evidenceEdgeById = new Map(evidence.edges.map((item) => [item.id, item]));

    for (const [eventNodeId, participations] of [...eventParticipations.entries()].sort()) {
      const eventNode = evidenceNodeById.get(eventNodeId);
      if (!eventNode) continue;
      const eventId = eventNode.ref;
      const source = this.dataIndex.sources.get(eventNode.item?.sourceId);
      const detailsExpanded = expandedEventIds.has(eventId);
      nodeById.set(eventNodeId, {
        ...eventNode,
        labelDetail: evidenceDetail(eventNode.item, source),
        contextualEvidenceFor: evidencePersonId,
        evidenceDetailsExpanded: detailsExpanded,
      });

      for (const { edge, occurrenceId, occurrenceNodeId } of participations.sort((left, right) => left.edge.id.localeCompare(right.edge.id))) {
        edgeById.set(edge.id, {
          ...edge,
          contextualEvidenceFor: evidencePersonId,
          backingEdgeIds: [...new Set([...(edge.backingEdgeIds || []), edge.id])].sort(),
          backingNodeIds: [...new Set([...(edge.backingNodeIds || []), eventNodeId, occurrenceNodeId])].sort(),
        });
        if (!detailsExpanded) continue;
        const occurrence = this.dataIndex.occurrences.get(occurrenceId);
        if (!occurrence) continue;
        const detailNodeId = `evidence-occ:${occurrenceId}`;
        nodeById.set(detailNodeId, {
          id: detailNodeId,
          ref: occurrenceId,
          kind: "occurrence",
          label: this.dataIndex.occurrenceLabel(occurrence),
          item: occurrence,
          contextualEvidenceFor: evidencePersonId,
          contextualEvidenceDetail: true,
          evidenceEventId: eventId,
          backingNodeIds: [occurrenceNodeId],
        });
        edgeById.set(`evidence-detail:${eventId}:${occurrenceId}`, {
          id: `evidence-detail:${eventId}:${occurrenceId}`,
          from: eventNodeId,
          to: detailNodeId,
          kind: "participation",
          label: edge.label,
          item: eventNode.item,
          contextualEvidenceFor: evidencePersonId,
          contextualEvidenceDetail: true,
          backingEdgeIds: [edge.id],
          backingNodeIds: [eventNodeId, occurrenceNodeId],
        });
      }

      if (!detailsExpanded || !source) continue;
      const sourceNodeId = `source:${source.id}`;
      const sourceNode = evidenceNodeById.get(sourceNodeId);
      if (!sourceNode) continue;
      nodeById.set(sourceNodeId, {
        ...sourceNode,
        contextualEvidenceFor: evidencePersonId,
        contextualEvidenceDetail: true,
        evidenceEventId: eventId,
      });
      const sourceEdge = evidenceEdgeById.get(`source:${eventId}:${source.id}`);
      if (sourceEdge) edgeById.set(sourceEdge.id, {
        ...sourceEdge,
        contextualEvidenceFor: evidencePersonId,
        contextualEvidenceDetail: true,
        backingEdgeIds: [...new Set([...(sourceEdge.backingEdgeIds || []), sourceEdge.id])].sort(),
        backingNodeIds: [...new Set([...(sourceEdge.backingNodeIds || []), eventNodeId, sourceNodeId])].sort(),
      });
    }

    return {
      nodes: [...nodeById.values()],
      edges: [...edgeById.values()].filter((edge) => nodeById.has(edge.from) && nodeById.has(edge.to)),
    };
  }
}
