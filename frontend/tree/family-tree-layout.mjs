const PERSON_GAP = 38;
const FAMILY_GAP = 56;
const SIDE_PADDING = 90;

export class FamilyTreeLayout {
  constructor({ cardWidth, cardHeight, rowGap, top }) {
    this.cardWidth = cardWidth;
    this.cardHeight = cardHeight;
    this.rowGap = rowGap;
    this.top = top;
  }

  generationStep() {
    return this.rowGap;
  }

  rowBlocks(row, families = [], personById = new Map(row.map((person) => [person.id, person]))) {
    const rowIds = new Set(row.map((person) => person.id));
    const used = new Set();
    const blocks = [];
    families.forEach((family, familyIndex) => {
      const people = (family.parentIds || [])
        .filter((id) => rowIds.has(id) && !used.has(id))
        .map((id) => personById.get(id))
        .filter(Boolean);
      if (!people.length) return;
      people.forEach((person) => used.add(person.id));
      const childOrders = (family.childIds || [])
        .map((id) => personById.get(id)?.order)
        .filter(Number.isFinite);
      blocks.push({
        people,
        family,
        priority: childOrders.length ? Math.min(...childOrders) : Math.min(...people.map((person) => person.order)),
        familyIndex,
      });
    });
    row.filter((person) => !used.has(person.id)).forEach((person, index) => {
      blocks.push({ people: [person], family: null, priority: person.order, familyIndex: families.length + index });
    });
    return blocks.sort((first, second) => first.priority - second.priority || first.familyIndex - second.familyIndex);
  }

  blockWidth(block) {
    return block.people.length * this.cardWidth + Math.max(0, block.people.length - 1) * PERSON_GAP;
  }

  gapBetween(first, second) {
    const orderGap = Math.max(0, Number(second.priority) - Number(first.priority) - 1);
    return FAMILY_GAP + Math.min(132, orderGap * 28);
  }

  compactMetrics(blocks) {
    const centers = [];
    let cursor = 0;
    blocks.forEach((block, index) => {
      if (index) cursor += this.gapBetween(blocks[index - 1], block);
      const width = this.blockWidth(block);
      centers.push(cursor + width / 2);
      cursor += width;
    });
    return { centers, width: cursor };
  }

  orderedRow(row, families = [], personById = new Map(row.map((person) => [person.id, person]))) {
    return this.rowBlocks(row, families, personById).flatMap((block) => block.people);
  }

  rowOffsets(row, families = [], personById) {
    const blocks = this.rowBlocks(row, families, personById);
    const offsets = [];
    let cursor = 0;
    blocks.forEach((block, blockIndex) => {
      if (blockIndex) cursor += this.gapBetween(blocks[blockIndex - 1], block);
      block.people.forEach((person, personIndex) => {
        if (personIndex) cursor += PERSON_GAP;
        offsets.push({ person, x: cursor });
        cursor += this.cardWidth;
      });
    });
    return { offsets, width: cursor };
  }

  requiredWidth(people, sidePadding = 180, families = []) {
    const grouped = new Map();
    const personById = new Map((people || []).map((person) => [person.id, person]));
    for (const person of people || []) {
      const row = grouped.get(person.generation) || [];
      row.push(person);
      grouped.set(person.generation, row);
    }
    return Math.max(sidePadding, ...[...grouped.values()].map((row) => this.rowOffsets(row, families, personById).width + sidePadding));
  }

  fitCenters(widths, desired, gaps) {
    if (!widths.length) return [];
    const cumulative = [0];
    for (let index = 1; index < widths.length; index += 1) {
      cumulative[index] = cumulative[index - 1]
        + widths[index - 1] / 2
        + gaps[index - 1]
        + widths[index] / 2;
    }
    const groups = [];
    desired.forEach((center, index) => {
      groups.push({ start: index, end: index, count: 1, sum: center - cumulative[index] });
      while (groups.length > 1) {
        const last = groups.at(-1);
        const previous = groups.at(-2);
        if (previous.sum / previous.count <= last.sum / last.count) break;
        groups.splice(-2, 2, {
          start: previous.start,
          end: last.end,
          count: previous.count + last.count,
          sum: previous.sum + last.sum,
        });
      }
    });
    const centers = new Array(widths.length);
    groups.forEach((group) => {
      const fitted = group.sum / group.count;
      for (let index = group.start; index <= group.end; index += 1) centers[index] = fitted + cumulative[index];
    });
    return centers;
  }

  orderFamilyBlocks(rows, families, blockByPerson) {
    if (rows.length < 2) return;
    const rowIndexByBlock = new Map();
    const neighbours = new Map();
    const familyGroupsByRow = new Map();
    rows.forEach(({ blocks }, rowIndex) => blocks.forEach((block, stableIndex) => {
      block.stableIndex = stableIndex;
      rowIndexByBlock.set(block, rowIndex);
      neighbours.set(block, new Map());
    }));
    const connect = (first, second, weight) => {
      if (first === second) return;
      neighbours.get(first).set(second, (neighbours.get(first).get(second) || 0) + weight);
      neighbours.get(second).set(first, (neighbours.get(second).get(first) || 0) + weight);
    };
    families.forEach((family) => {
      const parentBlocks = [...new Set((family.parentIds || []).map((id) => blockByPerson.get(id)).filter(Boolean))];
      const childBlocks = [...new Set((family.childIds || []).map((id) => blockByPerson.get(id)).filter(Boolean))];
      parentBlocks.forEach((parentBlock) => childBlocks.forEach((childBlock) => {
        connect(parentBlock, childBlock, 1);
      }));
      [parentBlocks, childBlocks].forEach((familyBlocks) => familyBlocks.forEach((block, index) => {
        familyBlocks.slice(index + 1).forEach((other) => {
          if (rowIndexByBlock.get(block) === rowIndexByBlock.get(other)) connect(block, other, 4);
        });
      }));
      [family.parentIds || [], family.childIds || []].forEach((familyPersonIds) => {
        const groupedByRow = new Map();
        familyPersonIds.forEach((personId) => {
          const block = blockByPerson.get(personId);
          if (!block) return;
          const rowIndex = rowIndexByBlock.get(block);
          const group = groupedByRow.get(rowIndex) || { blocks: new Set() };
          group.blocks.add(block);
          groupedByRow.set(rowIndex, group);
        });
        groupedByRow.forEach((group, rowIndex) => {
          if (group.blocks.size < 2) return;
          const rowGroups = familyGroupsByRow.get(rowIndex) || [];
          rowGroups.push(group);
          familyGroupsByRow.set(rowIndex, rowGroups);
        });
      });
    });

    const rankByBlock = () => {
      const ranks = new Map();
      rows.forEach(({ blocks }) => blocks.forEach((block, index) => {
        ranks.set(block, (index + .5) / blocks.length);
      }));
      return ranks;
    };
    const sweep = (start, end, step) => {
      for (let rowIndex = start; rowIndex !== end; rowIndex += step) {
        const blocks = rows[rowIndex].blocks;
        const ranks = rankByBlock();
        const scored = blocks.map((block, currentIndex) => {
          const relevant = [...neighbours.get(block)].filter(([other]) => {
            const otherRowIndex = rowIndexByBlock.get(other);
            return otherRowIndex === rowIndex || (step > 0 ? otherRowIndex < rowIndex : otherRowIndex > rowIndex);
          });
          const totalWeight = relevant.reduce((sum, [, weight]) => sum + weight, 0);
          return {
            block,
            score: relevant.length
              ? relevant.reduce((sum, [other, weight]) => sum + ranks.get(other) * weight, 0) / totalWeight
              : (currentIndex + .5) / blocks.length,
          };
        });
        scored.sort((first, second) => (
          first.score - second.score
          || first.block.stableIndex - second.block.stableIndex
          || first.block.familyIndex - second.block.familyIndex
        ));
        rows[rowIndex].blocks = scored.map(({ block }) => block);
      }
    };

    // Alternating barycentric sweeps let both ancestry and descendant rows
    // influence branch order.  The final forward sweep makes every parent
    // row follow the already ordered children while stable indices resolve
    // ties deterministically and keep spouse blocks intact.
    for (let iteration = 0; iteration < 6; iteration += 1) {
      sweep(1, rows.length, 1);
      sweep(rows.length - 2, -1, -1);
    }
    sweep(1, rows.length, 1);

    const familyRelations = families.map((family) => ({
      parents: (family.parentIds || []).filter((id) => blockByPerson.has(id)),
      children: (family.childIds || []).filter((id) => blockByPerson.has(id)),
    })).filter((relation) => relation.parents.length && relation.children.length);
    const compactPersonCenters = () => {
      const centers = new Map();
      rows.forEach(({ blocks }) => {
        const centerByBlock = new Map();
        let cursor = 0;
        blocks.forEach((block, index) => {
          if (index) cursor += this.gapBetween(blocks[index - 1], block);
          centerByBlock.set(block, cursor + this.blockWidth(block) / 2);
          cursor += this.blockWidth(block);
        });
        blocks.forEach((block) => block.people.forEach((person, personIndex) => {
          const memberOffset = -this.blockWidth(block) / 2
            + this.cardWidth / 2
            + personIndex * (this.cardWidth + PERSON_GAP);
          centers.set(person.id, centerByBlock.get(block) + memberOffset - cursor / 2);
        }));
      });
      return centers;
    };
    // Move whole contiguous family ranges as well as single blocks. This
    // removes unrelated blocks from sibling buses without splitting spouse
    // pairs, then minimizes the worst parent-to-child horizontal bridge.
    for (let optimizationRound = 0; optimizationRound < 4; optimizationRound += 1) {
      rows.forEach(({ blocks }, rowIndex) => {
      const familyGroups = familyGroupsByRow.get(rowIndex) || [];
      if (!familyGroups.length) return;
      const penalty = () => {
        const indexByBlock = new Map(blocks.map((block, index) => [block, index]));
        const personCenters = compactPersonCenters();
        const score = familyGroups.reduce((result, group) => {
          const indices = [...group.blocks].map((block) => indexByBlock.get(block));
          const intruders = Math.max(...indices) - Math.min(...indices) + 1 - group.blocks.size;
          return {
            intruders: result.intruders + intruders * intruders,
          };
        }, { intruders: 0 });
        const geometryScores = familyRelations.map((relation) => {
          const parentCenters = relation.parents.map((id) => personCenters.get(id));
          const childCenters = relation.children.map((id) => personCenters.get(id));
          const parentCenter = parentCenters.reduce((total, value) => total + value, 0) / parentCenters.length;
          const childCenter = childCenters.reduce((total, value) => total + value, 0) / childCenters.length;
          const bridge = Math.abs(parentCenter - childCenter);
          return {
            bridge,
            horizontal: Math.max(
              Math.max(...parentCenters) - Math.min(...parentCenters),
              Math.max(...childCenters) - Math.min(...childCenters),
              bridge,
            ),
          };
        });
        const horizontalSpans = geometryScores.map(({ horizontal }) => horizontal);
        score.maxBridge = Math.max(0, ...geometryScores.map(({ bridge }) => bridge));
        score.maxHorizontal = Math.max(0, ...horizontalSpans);
        score.totalHorizontal = horizontalSpans.reduce((sum, value) => sum + value, 0);
        return score;
      };
      const improves = (candidate, current) => candidate.intruders < current.intruders
        || (candidate.intruders === current.intruders
          && candidate.maxBridge < current.maxBridge)
        || (candidate.intruders === current.intruders
          && candidate.maxBridge === current.maxBridge
          && candidate.maxHorizontal < current.maxHorizontal)
        || (candidate.intruders === current.intruders
          && candidate.maxBridge === current.maxBridge
          && candidate.maxHorizontal === current.maxHorizontal
          && candidate.totalHorizontal < current.totalHorizontal);
      let currentPenalty = penalty();
      for (let pass = 0; pass < blocks.length * 2; pass += 1) {
        let bestOrder = null;
        let bestPenalty = currentPenalty;
        const original = [...blocks];
        const indexByBlock = new Map(original.map((block, index) => [block, index]));
        const ranges = original.map((block, index) => ({ start: index, length: 1, key: block.stableIndex }));
        familyGroups.forEach((group) => {
          const indices = [...group.blocks].map((block) => indexByBlock.get(block)).sort((first, second) => first - second);
          if (indices.at(-1) - indices[0] + 1 !== indices.length) return;
          ranges.push({ start: indices[0], length: indices.length, key: indices[0] });
        });
        ranges.sort((first, second) => first.start - second.start || first.length - second.length || first.key - second.key);
        ranges.forEach(({ start, length }) => {
          for (let to = 0; to <= blocks.length - length; to += 1) {
            if (start === to) continue;
            const candidate = [...original];
            const moved = candidate.splice(start, length);
            candidate.splice(to, 0, ...moved);
            blocks.splice(0, blocks.length, ...candidate);
            const candidatePenalty = penalty();
            if (!improves(candidatePenalty, bestPenalty)) continue;
            bestOrder = candidate;
            bestPenalty = candidatePenalty;
          }
        });
        if (!bestOrder) {
          blocks.splice(0, blocks.length, ...original);
          break;
        }
        blocks.splice(0, blocks.length, ...bestOrder);
        currentPenalty = bestPenalty;
      }
      });
    }
  }

  positions(people, baseWidth, scale, families = []) {
    const positions = new Map();
    const grouped = new Map();
    const personById = new Map(people.map((person) => [person.id, person]));
    const generationStep = this.generationStep();
    const minimumGeneration = people.length ? Math.min(...people.map((person) => person.generation)) : 0;
    const maximumGeneration = people.length ? Math.max(...people.map((person) => person.generation)) : 0;
    for (const person of people) {
      const generation = person.generation - minimumGeneration;
      const row = grouped.get(generation) || [];
      row.push(person);
      grouped.set(generation, row);
    }
    const rows = [...grouped.entries()].sort(([first], [second]) => first - second).map(([generation, row]) => ({
      generation,
      blocks: this.rowBlocks(row, families, personById),
    }));
    const blockByPerson = new Map();
    rows.forEach(({ blocks }) => blocks.forEach((block) => block.people.forEach((person) => blockByPerson.set(person.id, block))));
    this.orderFamilyBlocks(rows, families, blockByPerson);
    rows.forEach(({ blocks }) => {
      const compact = this.compactMetrics(blocks);
      const start = (baseWidth - compact.width) / 2;
      blocks.forEach((block, blockIndex) => {
        block.width = this.blockWidth(block);
        block.center = start + compact.centers[blockIndex];
        block.anchor = block.center;
        block.offsetByPerson = new Map();
        let offset = -block.width / 2 + this.cardWidth / 2;
        block.people.forEach((person, personIndex) => {
          if (personIndex) offset += PERSON_GAP;
          block.offsetByPerson.set(person.id, offset);
          offset += this.cardWidth;
        });
      });
    });

    const relations = families.map((family) => ({
      parents: (family.parentIds || []).filter((id) => blockByPerson.has(id)),
      children: (family.childIds || []).filter((id) => blockByPerson.has(id)),
    })).filter((relation) => relation.parents.length && relation.children.length);
    const rowIndexByBlock = new Map();
    rows.forEach(({ blocks }, rowIndex) => blocks.forEach((block) => rowIndexByBlock.set(block, rowIndex)));
    const cohesionGroups = [];
    relations.forEach((relation) => [relation.parents, relation.children].forEach((personIds) => {
      const groupsByRow = new Map();
      personIds.forEach((personId) => {
        const block = blockByPerson.get(personId);
        const rowIndex = rowIndexByBlock.get(block);
        const group = groupsByRow.get(rowIndex) || new Set();
        group.add(block);
        groupsByRow.set(rowIndex, group);
      });
      groupsByRow.forEach((group) => {
        if (group.size > 1) cohesionGroups.push([...group]);
      });
    }));
    const centerOf = (ids) => ids.reduce((sum, id) => {
      const block = blockByPerson.get(id);
      return sum + block.center + block.offsetByPerson.get(id);
    }, 0) / ids.length;

    // Solve the layered family constraints globally.  Every relation pulls
    // its parent pair towards its child (and the child back towards the pair),
    // while row projection keeps cards apart in their deterministic order.
    for (let iteration = 0; iteration < 120; iteration += 1) {
      const suggestions = new Map();
      rows.forEach(({ blocks }) => blocks.forEach((block) => suggestions.set(block, { sum: block.anchor * .01, weight: .01 })));
      relations.forEach((relation) => {
        const error = centerOf(relation.children) - centerOf(relation.parents);
        const parentBlocks = [...new Set(relation.parents.map((id) => blockByPerson.get(id)))];
        const childBlocks = [...new Set(relation.children.map((id) => blockByPerson.get(id)))];
        parentBlocks.forEach((block) => {
          const item = suggestions.get(block);
          item.sum += (block.center + error / 2) * 2;
          item.weight += 2;
        });
        childBlocks.forEach((block) => {
          const item = suggestions.get(block);
          item.sum += (block.center - error / 2) * 2;
          item.weight += 2;
        });
      });
      // A relation-center pull alone can leave siblings spread across a row.
      // Cohesion suggestions retain the compact family interval selected
      // above while the row projection continues to prevent card overlap.
      cohesionGroups.forEach((group) => {
        const ordered = [...group].sort((first, second) => {
          const blocks = rows[rowIndexByBlock.get(first)].blocks;
          return blocks.indexOf(first) - blocks.indexOf(second);
        });
        const compactCenters = [ordered[0].width / 2];
        for (let index = 1; index < ordered.length; index += 1) {
          compactCenters[index] = compactCenters[index - 1]
            + ordered[index - 1].width / 2
            + this.gapBetween(ordered[index - 1], ordered[index])
            + ordered[index].width / 2;
        }
        const currentMean = ordered.reduce((sum, block) => sum + block.center, 0) / ordered.length;
        const compactMean = compactCenters.reduce((sum, value) => sum + value, 0) / compactCenters.length;
        ordered.forEach((block, index) => {
          const item = suggestions.get(block);
          item.sum += currentMean + compactCenters[index] - compactMean;
          item.weight += 1;
        });
      });
      rows.forEach(({ blocks }) => {
        const desired = blocks.map((block) => {
          const item = suggestions.get(block);
          return item.sum / item.weight;
        });
        const centers = this.fitCenters(
          blocks.map((block) => block.width),
          desired,
          blocks.slice(1).map((block, index) => this.gapBetween(blocks[index], block)),
        );
        blocks.forEach((block, index) => { block.center = centers[index]; });
      });
    }

    rows.forEach(({ generation, blocks }) => blocks.forEach((block) => {
      block.people.forEach((person) => {
        positions.set(person.id, {
          x: block.center + block.offsetByPerson.get(person.id) - this.cardWidth / 2,
          y: this.top + generation * generationStep,
        });
      });
    }));
    const left = positions.size ? Math.min(...[...positions.values()].map((position) => position.x)) : 0;
    const right = positions.size ? Math.max(...[...positions.values()].map((position) => position.x + this.cardWidth)) : 0;
    const contentWidth = right - left + SIDE_PADDING * 2;
    let shift = 0;
    if (contentWidth > baseWidth || left < SIDE_PADDING) shift = SIDE_PADDING - left;
    else if (right > baseWidth - SIDE_PADDING) shift = baseWidth - SIDE_PADDING - right;
    positions.forEach((position) => { position.x += shift; });
    return {
      positions,
      generationSpan: maximumGeneration - minimumGeneration,
      width: Math.max(baseWidth, contentWidth),
    };
  }
}
