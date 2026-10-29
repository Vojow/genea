export const POLAND_BOUNDS = [[14.07, 49.0], [24.15, 54.84]];
export const WORLD_BOUNDS = [[-170, -55], [170, 75]];
export const NEUTRAL_PLACE_COLOR = "#66756e";

export function routeFeatureCollection(model, places) {
  return {
    type: "FeatureCollection",
    features: model.routes.flatMap((route, index) => {
      const from = places.get(route.from.placeId);
      const to = places.get(route.to.placeId);
      if (!from || !to) return [];
      return [{
        type: "Feature",
        id: index,
        properties: {
          routeIndex: index,
          personLabel: route.personLabel,
          color: route.color || model.line?.color || "#285c53",
          verified: Boolean(route.verified),
        },
        geometry: {
          type: "LineString",
          coordinates: [
            [from.longitude, from.latitude],
            [to.longitude, to.latitude],
          ],
        },
      }];
    }),
  };
}

export function linesForItems(model, items) {
  return (model.lines || (model.line ? [model.line] : [])).filter((line) =>
    items.some((item) => item.lineIds?.includes(line.id))
  );
}

export function markerGroupsForZoom(model, places, zoom, regionZoomThreshold = 8) {
  const exact = [...places].flatMap(([placeId, place]) => {
    if (!Number.isFinite(place?.longitude) || !Number.isFinite(place?.latitude)) return [];
    const personItems = [...(model.groups.get(placeId) || [])];
    const sourceItems = [...(model.sourceGroups?.get(placeId) || [])];
    return [{
      key: placeId,
      place,
      items: [...personItems, ...sourceItems],
      personItems,
      sourceItems,
      placeIds: [placeId],
      isRegionCluster: false,
      isSourceOnly: personItems.length === 0 && sourceItems.length > 0,
    }];
  });
  if (zoom > regionZoomThreshold) return exact;

  const grouped = new Map();
  const neutral = exact.filter((entry) => entry.personItems.length === 0);
  for (const entry of exact.filter((candidate) => candidate.personItems.length > 0)) {
    const regionId = entry.place.regionId || entry.place.id;
    const region = grouped.get(regionId) || [];
    region.push(entry);
    grouped.set(regionId, region);
  }
  const observed = [...grouped].map(([regionId, entries]) => {
    if (entries.length === 1) return entries[0];
    const items = entries.flatMap((entry) => entry.items);
    const personItems = entries.flatMap((entry) => entry.personItems);
    const sourceItems = entries.flatMap((entry) => entry.sourceItems);
    const weight = entries.reduce((sum, entry) => sum + Math.max(1, entry.personItems.length), 0);
    const longitude = entries.reduce((sum, entry) => sum + entry.place.longitude * Math.max(1, entry.personItems.length), 0) / weight;
    const latitude = entries.reduce((sum, entry) => sum + entry.place.latitude * Math.max(1, entry.personItems.length), 0) / weight;
    const label = entries.find((entry) => entry.place.regionLabel)?.place.regionLabel || `Rejon (${entries.length} miejscowości)`;
    return {
      key: regionId,
      place: {
        label,
        shortLabel: label,
        longitude,
        latitude,
        isRegionCluster: true,
        regionId,
        placeIds: entries.map((entry) => entry.place.id),
        localityCount: entries.length,
      },
      items,
      personItems,
      sourceItems,
      placeIds: entries.map((entry) => entry.place.id),
      isRegionCluster: true,
      isSourceOnly: false,
    };
  });
  return [...observed, ...neutral];
}

export function markerBackground(lines, fallback = "#285c53") {
  const colors = [...new Set((lines || []).map((line) => line?.color).filter(Boolean))];
  if (!colors.length) return fallback;
  if (colors.length === 1) return colors[0];
  const step = 100 / colors.length;
  const stops = colors.flatMap((color, index) => [
    `${color} ${(index * step).toFixed(2)}%`,
    `${color} ${((index + 1) * step).toFixed(2)}%`,
  ]);
  return `conic-gradient(${stops.join(", ")})`;
}

export function boundsForModel(model, places, fallbackBounds) {
  const visible = [...places.values()].filter((place) =>
    Number.isFinite(place?.longitude) && Number.isFinite(place?.latitude)
  );
  if (!visible.length && fallbackBounds) {
    return [
      [fallbackBounds.west, fallbackBounds.south],
      [fallbackBounds.east, fallbackBounds.north],
    ];
  }
  if (!visible.length) return POLAND_BOUNDS;
  const longitudes = visible.map((place) => place.longitude);
  const latitudes = visible.map((place) => place.latitude);
  const west = Math.min(...longitudes);
  const east = Math.max(...longitudes);
  const south = Math.min(...latitudes);
  const north = Math.max(...latitudes);
  const longitudePadding = Math.max((east - west) * 0.15, 0.18);
  const latitudePadding = Math.max((north - south) * 0.15, 0.12);
  return [
    [west - longitudePadding, south - latitudePadding],
    [east + longitudePadding, north + latitudePadding],
  ];
}
