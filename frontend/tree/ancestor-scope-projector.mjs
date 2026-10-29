import { familyRelationshipCanonical } from "./family-tree-model.mjs";

const DEFAULT_MAX_DEPTH = 4;

function compareIds(first, second) {
  if (first < second) return -1;
  if (first > second) return 1;
  return 0;
}

function identifiers(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .filter((value) => typeof value === "string" && value.length > 0))]
    .sort(compareIds);
}

function normalizedDepth(value) {
  const depth = Number(value);
  return Number.isFinite(depth) && depth >= 0 ? Math.floor(depth) : DEFAULT_MAX_DEPTH;
}

function slotPathId(focusPersonId, steps) {
  const encodedFocus = encodeURIComponent(focusPersonId);
  if (!steps.length) return `ancestor-slot:${encodedFocus}`;
  const encodedSteps = steps.map(({ familyId, personId }) =>
    `${encodeURIComponent(familyId)}:${encodeURIComponent(personId)}`
  );
  return `ancestor-slot:${encodedFocus}/${encodedSteps.join("/")}`;
}

/**
 * Projects the direct ancestor paths of one presentation-tree person.
 *
 * The only traversed relation is an explicit family childId -> parentId.
 * Repeated people intentionally remain repeated path slots; duplicateOfSlotId
 * is presentation metadata and never an identity or relationship decision.
 */
export function projectAncestorScope(tree, { focusPersonId, maxDepth } = {}) {
  const depthLimit = normalizedDepth(maxDepth);
  const people = Array.isArray(tree?.people) ? tree.people : [];
  const personById = new Map(people
    .filter((person) => typeof person?.id === "string" && person.id.length > 0)
    .map((person) => [person.id, person]));
  const warnings = [];
  const warningKeys = new Set();
  const warn = (warning) => {
    if (warningKeys.has(warning)) return;
    warningKeys.add(warning);
    warnings.push(warning);
  };

  const result = {
    focusTreePersonId: typeof focusPersonId === "string" ? focusPersonId : null,
    maxDepth: depthLimit,
    slots: [],
    edges: [],
    uniquePersonCount: 0,
    slotCount: 0,
    truncated: false,
    warnings,
  };

  if (!result.focusTreePersonId || !personById.has(result.focusTreePersonId)) {
    warn(`focus_person_not_found:${result.focusTreePersonId ?? "null"}`);
    return result;
  }

  const familiesByChild = new Map();
  const families = (Array.isArray(tree?.families) ? tree.families : [])
    .filter(familyRelationshipCanonical)
    .filter((family) => typeof family?.id === "string" && family.id.length > 0)
    .map((family, inputIndex) => ({ family, inputIndex }))
    .sort((first, second) => compareIds(first.family.id, second.family.id) || first.inputIndex - second.inputIndex);
  for (const { family } of families) {
    for (const childId of identifiers(family.childIds)) {
      const childFamilies = familiesByChild.get(childId) || [];
      childFamilies.push(family);
      familiesByChild.set(childId, childFamilies);
    }
  }

  const firstSlotByPersonId = new Map();
  const focusSlot = {
    slotId: slotPathId(result.focusTreePersonId, []),
    personId: result.focusTreePersonId,
    person: personById.get(result.focusTreePersonId),
    depth: 0,
    pathFamilyIds: [],
    duplicateOfSlotId: null,
  };
  result.slots.push(focusSlot);
  firstSlotByPersonId.set(focusSlot.personId, focusSlot.slotId);

  const queue = [{
    slot: focusSlot,
    pathSteps: [],
    pathPersonIds: new Set([focusSlot.personId]),
  }];
  while (queue.length) {
    const current = queue.shift();
    const parentFamilies = familiesByChild.get(current.slot.personId) || [];
    if (current.slot.depth >= depthLimit) {
      if (parentFamilies.some((family) => identifiers(family.parentIds).length > 0)) {
        result.truncated = true;
      }
      continue;
    }

    for (const family of parentFamilies) {
      for (const parentId of identifiers(family.parentIds)) {
        if (!personById.has(parentId)) {
          warn(`parent_person_not_found:${family.id}:${parentId}:${current.slot.slotId}`);
          continue;
        }

        const pathSteps = [...current.pathSteps, { familyId: family.id, personId: parentId }];
        const slotId = slotPathId(result.focusTreePersonId, pathSteps);
        if (current.pathPersonIds.has(parentId)) {
          warn(`ancestor_cycle_detected:${family.id}:${parentId}:${slotId}`);
          continue;
        }
        const duplicateOfSlotId = firstSlotByPersonId.get(parentId) || null;
        const parentSlot = {
          slotId,
          personId: parentId,
          person: personById.get(parentId),
          depth: current.slot.depth + 1,
          pathFamilyIds: [...current.slot.pathFamilyIds, family.id],
          duplicateOfSlotId,
        };
        result.slots.push(parentSlot);
        if (!duplicateOfSlotId) firstSlotByPersonId.set(parentId, slotId);

        result.edges.push({
          edgeId: `ancestor-edge:${slotId}->${current.slot.slotId}`,
          fromSlotId: slotId,
          toSlotId: current.slot.slotId,
          parentSlotId: slotId,
          childSlotId: current.slot.slotId,
          parentPersonId: parentId,
          childPersonId: current.slot.personId,
          familyId: family.id,
          status: family.status ?? null,
          sourceIds: identifiers(family.sourceIds),
        });

        queue.push({
          slot: parentSlot,
          pathSteps,
          pathPersonIds: new Set([...current.pathPersonIds, parentId]),
        });
      }
    }
  }

  result.uniquePersonCount = new Set(result.slots.map((slot) => slot.personId)).size;
  result.slotCount = result.slots.length;
  return result;
}
