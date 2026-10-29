import { aggregateSourceReviews, sourceReviewSummary } from "./review-status.mjs";

export class DataIndex {
  constructor(payload) {
    this.sources = new Map((payload.sources || []).map((item) => [item.id, item]));
    this.people = new Map((payload.people || []).map((item) => [item.id, item]));
    this.occurrences = new Map((payload.occurrences || []).map((item) => [item.id, item]));
    this.events = new Map((payload.events || []).map((item) => [item.id, item]));
    this.households = new Map((payload.households || []).map((item) => [item.id, item]));
    this.treePeople = new Map((payload.familyTree?.people || []).map((item) => [item.id, item]));
    this.personContextsByTreePerson = new Map();
    this.personContextsByOccurrence = new Map();
    this.relationshipsByOccurrence = new Map();
    this.eventsByOccurrence = new Map();
    this.eventsBySource = new Map();
    this.occurrencesByPerson = new Map();
    for (const occurrence of payload.occurrences || []) {
      if (occurrence.personId) this.#append(this.occurrencesByPerson, occurrence.personId, occurrence);
    }
    for (const event of payload.events || []) {
      if (event.sourceId) this.#append(this.eventsBySource, event.sourceId, event);
      for (const participant of event.participants || []) {
        this.#append(this.eventsByOccurrence, participant.occurrenceId, event);
      }
    }
    for (const context of payload.personContexts || []) {
      for (const treePersonId of context.treePersonIds || []) {
        this.personContextsByTreePerson.set(treePersonId, context);
      }
      for (const occurrenceId of context.occurrenceIds || []) {
        if (!this.personContextsByOccurrence.has(occurrenceId) || context.identityScope === "canonical") {
          this.personContextsByOccurrence.set(occurrenceId, context);
        }
      }
    }
    for (const relationship of payload.relationships || []) {
      this.#append(this.relationshipsByOccurrence, relationship.fromOccurrenceId, relationship);
      this.#append(this.relationshipsByOccurrence, relationship.toOccurrenceId, relationship);
    }
  }

  #append(index, key, value) {
    const items = index.get(key) || [];
    items.push(value);
    index.set(key, items);
  }

  occurrenceLabel(item) {
    if (!item) return "[brak osoby]";
    return this.people.get(item.personId)?.name || item.normalizedName || item.name || item.id;
  }

  sourceReview(sourceId) {
    return sourceReviewSummary(this.sources.get(sourceId));
  }

  occurrenceReview(item) {
    return item?.sourceId ? this.sourceReview(item.sourceId) : null;
  }

  personReview(person) {
    const sourceIds = new Set([
      ...(person?.sourceIds || []),
      ...(person?.occurrenceIds || []).map((id) => this.occurrences.get(id)?.sourceId).filter(Boolean),
    ]);
    return aggregateSourceReviews([...sourceIds].map((id) => this.sources.get(id)));
  }

  personContext(person) {
    return person?.id ? this.personContextsByTreePerson.get(person.id) || null : null;
  }

  occurrenceContext(occurrenceId) {
    return this.personContextsByOccurrence.get(occurrenceId) || null;
  }

  personLifeSpan(person) {
    return person?.lifeSpanLabel || "?–?";
  }
}
