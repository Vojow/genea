import { describe, expect, it, vi } from "vitest";

import {
  ROUTE_NAMES,
  createTestRouter,
  entityLocation,
  entityPath,
  externalCandidateProfileLocation,
  externalNetworkDeepLinkFromSearch,
  externalNetworkLocation,
  externalNetworkReturnLocation,
  legacyPathFromHash,
  migrateLegacyHash,
  pathForView,
  treePersonDeepLinkFromSearch,
  viewFromRoute,
  type EntityKind,
} from "./router";


describe("typed Genea routes", () => {
  it("parses a single base-tree person without interpreting external network context", () => {
    expect(treePersonDeepLinkFromSearch("?person=father")).toBe("father");
    expect(treePersonDeepLinkFromSearch("?person=father&zoom=4")).toBe("father");
    for (const search of ["", "?person=", "?person=father&person=root", "?network=&person=father",
      "?network=network_public_01&person=profile_public_02", `?person=${"x".repeat(201)}`]) {
      expect(treePersonDeepLinkFromSearch(search)).toBeNull();
    }
  });

  it("keeps exactly five primary destinations and redirects research to its dashboard", async () => {
    const router = createTestRouter();
    const paths = ["/tree", "/map", "/graph", "/sources", "/research"];
    const expected = ["tree", "map", "graph", "sources", "research-dashboard"];

    for (const [index, path] of paths.entries()) {
      await router.push(path);
      expect(router.currentRoute.value.name).toBe(expected[index]);
    }
    expect(pathForView("research")).toBe("/research/dashboard");
  });

  it("provides a real catalog and a canonical people collection above person profiles", async () => {
    const router = createTestRouter();
    await router.push("/catalog");
    expect(router.currentRoute.value.name).toBe(ROUTE_NAMES.catalog);
    expect(router.currentRoute.value.meta.catalog).toBe(true);

    await router.push("/people");
    expect(router.currentRoute.value.name).toBe(ROUTE_NAMES.peopleCatalog);
    expect(router.currentRoute.value.meta.catalogKind).toBe("person");
    expect(router.currentRoute.value.meta.catalogCollection).toBe(true);

    const collections = [
      ["/lines", ROUTE_NAMES.linesCatalog, "tree"],
      ["/places", ROUTE_NAMES.placesCatalog, "map"],
      ["/events", ROUTE_NAMES.eventsCatalog, "sources"],
      ["/households", ROUTE_NAMES.householdsCatalog, "graph"],
      ["/clusters", ROUTE_NAMES.clustersCatalog, "graph"],
    ] as const;
    for (const [path, name, view] of collections) {
      await router.push(path);
      expect(router.currentRoute.value.name).toBe(name);
      expect(viewFromRoute(router.currentRoute.value)).toBe(view);
      expect(router.currentRoute.value.meta.catalogCollection).toBe(true);
    }
  });

  it("builds a stable opaque path for every linked entity kind", () => {
    const expected: Record<EntityKind, string> = {
      person: "/people/person_01",
      line: "/lines/line_01",
      place: "/places/place_01",
      event: "/events/event_01",
      source: "/sources/source_01",
      household: "/households/household_01",
      cluster: "/clusters/cluster_01",
      case: "/research/cases/case_01",
      question: "/research/questions/question_01",
      volume: "/research/volumes/volume_01",
      run: "/research/runs/run_01",
    };
    for (const [kind, path] of Object.entries(expected) as Array<[EntityKind, string]>) {
      expect(entityPath(kind, `${kind}_01`)).toBe(path);
      expect(entityLocation(kind, `${kind}_01`)).toMatchObject({ params: expect.any(Object) });
    }
  });

  it("opens detail, page, comparison and unknown URLs without prior application navigation", async () => {
    const router = createTestRouter();
    const cases = [
      ["/people/person_01", ROUTE_NAMES.person, "tree"],
      ["/research/volumes/volume_01/pages/12", ROUTE_NAMES.researchVolumePage, "research"],
      ["/research/runs/run_a/compare/run_b", ROUTE_NAMES.researchRunCompare, "research"],
      ["/missing/route", ROUTE_NAMES.notFound, "tree"],
    ] as const;
    for (const [path, name, view] of cases) {
      await router.replace(path);
      expect(router.currentRoute.value.name).toBe(name);
      expect(viewFromRoute(router.currentRoute.value)).toBe(view);
    }
  });

  it("migrates only the five legacy hashes without retaining the hash", () => {
    expect(legacyPathFromHash("#tree")).toBe("/tree");
    expect(legacyPathFromHash("#/map")).toBe("/map");
    expect(legacyPathFromHash("#research")).toBe("/research/dashboard");
    expect(legacyPathFromHash("#private-name")).toBeNull();

    const replaceState = vi.fn();
    const path = migrateLegacyHash(
      { hash: "#graph", pathname: "/", search: "?build=safe" } as Location,
      { replaceState, state: { retained: true } } as unknown as History,
    );
    expect(path).toBe("/graph");
    expect(replaceState).toHaveBeenCalledWith({ retained: true }, "", "/graph?build=safe");
  });

  it("accepts only privacy-safe external network deep-link fields on tree and graph routes", async () => {
    const parsed = externalNetworkDeepLinkFromSearch("?network=network_public_01&person=profile_public_02&candidateRole=direct_ancestor&zoom=4&sourceId=secret");
    expect(parsed).toEqual({
      networkId: "network_public_01",
      publicProfileId: "profile_public_02",
      candidateRole: "direct_ancestor",
    });
    expect(externalNetworkDeepLinkFromSearch("?network=network_public_01&candidateRole=unknown")).toBeNull();

    const router = createTestRouter();
    await router.push(externalNetworkLocation("graph", parsed!));
    expect(router.currentRoute.value.fullPath).toBe("/graph?network=network_public_01&person=profile_public_02&candidateRole=direct_ancestor");
    await router.push(externalNetworkLocation("tree", { ...parsed!, candidateRole: "all" }));
    expect(router.currentRoute.value.fullPath).toBe("/tree?network=network_public_01&person=profile_public_02");

    const publicState = JSON.stringify({
      route: router.currentRoute.value.fullPath,
      link: externalNetworkLocation("graph", parsed!),
    });
    for (const forbidden of ["providerPid", "sourceId", "assertionId", "backingId", "http://", "https://", "familysearch", "myheritage", "zoom", "camera"] ) {
      expect(publicState.toLocaleLowerCase()).not.toContain(forbidden.toLocaleLowerCase());
    }
  });

  it("round-trips an external candidate profile through public network and profile identifiers only", async () => {
    const router = createTestRouter();
    const origin = {
      view: "tree" as const,
      networkId: "network_public_01",
      candidateRole: "collateral" as const,
    };
    await router.push(externalNetworkLocation("tree", {
      networkId: origin.networkId,
      publicProfileId: "profile_public_02",
      candidateRole: origin.candidateRole,
    }));
    const originPath = router.currentRoute.value.fullPath;
    await router.push(externalCandidateProfileLocation("profile_public_02", origin));
    const profilePath = router.currentRoute.value.fullPath;
    expect(router.currentRoute.value.fullPath).toBe("/people/profile_public_02?returnView=tree&network=network_public_01&person=profile_public_02&candidateRole=collateral");
    const back = externalNetworkReturnLocation(router.currentRoute.value.query);
    expect(back).toEqual({
      name: ROUTE_NAMES.tree,
      query: {
        network: "network_public_01",
        person: "profile_public_02",
        candidateRole: "collateral",
      },
    });
    expect(externalNetworkReturnLocation({
      returnView: "tree",
      network: "network_public_01",
      person: "provider-pid-secret",
      candidateRole: "unknown",
    })).toBeNull();

    const reloaded = createTestRouter();
    await reloaded.push(profilePath);
    expect(externalNetworkReturnLocation(reloaded.currentRoute.value.query)).toEqual(back);

    const backCompleted = new Promise<void>((resolve) => {
      const remove = router.afterEach(() => {
        remove();
        resolve();
      });
      router.back();
    });
    await backCompleted;
    expect(router.currentRoute.value.fullPath).toBe(originPath);

    const serialized = JSON.stringify({ profile: router.currentRoute.value.fullPath, back });
    for (const forbidden of ["sourceId", "assertionId", "backingId", "http://", "https://", "familysearch", "myheritage", "camera", "zoom"]) {
      expect(serialized.toLocaleLowerCase()).not.toContain(forbidden.toLocaleLowerCase());
    }
  });
});
