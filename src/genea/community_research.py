"""Pure, read-only community search plans and curator-ready log previews.

No search, database access, promotion, contact or persistence occurs here.
Community statements remain attributed leads, including statements about identity.
"""
from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import ipaddress
import json
import re
import unicodedata
from urllib.parse import urlsplit


DEFAULT_DOMAINS = (
    "forum.polishorigins.com", "forum.genealodzy.pl", "wtg-gniazdo.org/forum",
    "community.familysearch.org", "myheritage.com", "ancestry.com",
)
TRIGGERS = {"new_branch", "insufficient_evidence", "primary_dead_end", "conflict", "new_context"}
# Matches research.VALID_QUESTION_TARGET_TYPES without importing its database facade.
TARGET_TYPES = {
    "person", "person_occurrence", "named_person", "household", "place",
    "place_study", "family_cluster", "event", "source_item", "other",
}
OUTCOMES = {"positive", "negative", "inconclusive", "not-searched"}
INTAKE_STATES = {"possible", "unresolved", "contradicted"}
IDENTITY_FIELDS = {"chronology", "geography", "relationships", "originalSource", "alternatives", "contradictions"}
MENTION_FIELDS = {"people", "surnames", "relationships", "dates", "places"}
BASE_FIELDS = {"schemaVersion", "caseId", "questionId", "trigger", "targets"}
LIMIT_NOTE = (
    "Community search is an attributed lead only (Source != proof). "
    "Coverage is incomplete and nonsystematic; no-hit, blocked or unsearched "
    "scope does not establish negative evidence. Independent primary-source "
    "verification and curator review are required."
)


def _object(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"{label}: unsupported fields: {', '.join(sorted(unknown))}")
    return value


def _text(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 16000:
        raise ValueError(f"{label} must be a nonempty string (maximum 16000 characters)")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError(f"{label} must not contain control characters")
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value) or re.search(r"\+\d[\d ()-]{7,}\d", value):
        raise ValueError(f"{label}: contact details are not allowed")
    return value


def _list(value: object, label: str, *, maximum: int = 64) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} items")
    return value


def _texts(value: object, label: str, *, required: bool = False) -> list[str]:
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(_list(value, label))]
    if required and not result:
        raise ValueError(f"{label} must not be empty")
    return result


def _choice(value: object, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(sorted(allowed))}")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _key(prefix: str, value: object) -> str:
    return prefix + "_" + sha256(_json(value).encode("utf-8")).hexdigest()[:24]


def _base(payload: dict, extra_fields: set[str]) -> dict:
    payload = _object(payload, BASE_FIELDS | extra_fields, "payload")
    if type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        raise ValueError("schemaVersion must be 1")
    targets = []
    for index, value in enumerate(_list(payload.get("targets"), "targets")):
        target = _object(value, {"targetType", "targetId", "targetLabel"}, f"targets[{index}]")
        targets.append({
            "targetType": _choice(target.get("targetType"), TARGET_TYPES, "targetType"),
            "targetId": _text(target.get("targetId"), "targetId"),
            "targetLabel": _text(target.get("targetLabel"), "targetLabel"),
        })
    if not targets:
        raise ValueError("targets must not be empty")
    return {
        "schemaVersion": 1, "caseId": _text(payload.get("caseId"), "caseId"),
        "questionId": _text(payload.get("questionId"), "questionId", nullable=True),
        "trigger": _choice(payload.get("trigger"), TRIGGERS, "trigger"), "targets": targets,
    }


def _public_host(host: str | None, label: str) -> None:
    if not host or not re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", host):
        raise ValueError(f"{label} must use a public DNS hostname")
    if host.lower().endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise ValueError(f"{label} must use a public DNS hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    raise ValueError(f"{label} must not use an IP address")


def _domain(value: object) -> str:
    value = _text(value, "domain")
    if len(value) > 255 or not re.fullmatch(r"[A-Za-z0-9.-]+(?:/[A-Za-z0-9_-]+)*/?", value):
        raise ValueError("domain must be a site root without scheme, query or search syntax")
    _public_host(value.split("/", 1)[0], "domain")
    return value.lower().rstrip("/")


def _url(value: object, label: str, *, nullable: bool = False) -> str | None:
    value = _text(value, label, nullable=nullable)
    if value is None:
        return None
    if any(char.isspace() for char in value) or "\\" in value:
        raise ValueError(f"{label} must be a direct public HTTP(S) URL")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise ValueError(f"{label}: invalid URL") from error
    if parts.scheme not in {"http", "https"} or parts.username is not None or parts.password is not None or port not in {None, 80, 443}:
        raise ValueError(f"{label} must be a direct public HTTP(S) URL")
    _public_host(parts.hostname, label)
    # Intentionally preserve every query parameter and fragment: post IDs are provenance.
    return value


def _date(value: object, label: str) -> str | None:
    value = _text(value, label, nullable=True)
    if value is not None:
        try:
            if re.fullmatch(r"\d{4}", value):
                date(int(value), 1, 1)
            else:
                date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{label} must be a year or ISO date") from error
    return value


def validate_search_timestamp(value: object) -> str:
    """Validate a performed-search timestamp without replacing its literal value."""
    value = _text(value, "searchedAt")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("searchedAt must be an ISO timestamp with an explicit timezone") from error
    if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("searchedAt must be an ISO timestamp with an explicit timezone")
    return value


def _variants(values: list[str], ascii_variants: bool) -> list[str]:
    result = []
    for value in values:
        forms = [value]
        if ascii_variants:
            mapped = value.translate(str.maketrans({"ł": "l", "Ł": "L"}))
            forms.append("".join(char for char in unicodedata.normalize("NFD", mapped) if not unicodedata.combining(char)))
        for form in forms:
            if form not in result:
                result.append(form)
    return result


def _quote(value: str) -> str:
    return '"' + value.replace('"', " ").replace("\\", " ") + '"'


def _relaxed(value: str) -> str:
    # Search words only; user-supplied operators must never change site scope.
    return " ".join(re.findall(r"[^\W_]+", value, re.UNICODE))


def _lexical_key(value: str) -> str:
    return _relaxed(_variants([value], True)[-1]).casefold()


def _cluster_terms(surnames: list[str], associates: list[str], index: int) -> list[str]:
    # Prefer the supplied primary anchor plus an explicit FAN/name context.
    # Only whole-term ASCII equivalence is lexical duplication. An explicitly
    # supplied full name remains useful even when its surname matches the
    # anchor; that does not establish identity, kinship or independence.
    anchor = surnames[0]
    anchor_key = _lexical_key(anchor)
    options = associates[index % len(associates):] + associates[:index % len(associates)] if associates else []
    options += surnames[1:]
    for candidate in options:
        candidate_key = _lexical_key(candidate)
        if candidate_key and candidate_key != anchor_key:
            return [anchor, candidate]
    return [anchor]


def plan_community_research(payload: dict) -> dict:
    """Return a deterministic, bounded search proposal; no queries are executed.

    names/surnames/places/associates are literal string arrays. domains adds site
    roots to the six defaults. includeAsciiVariants defaults to true. maxQueries
    is an integer from 1 through 120. Dates describe scope, not a search engine's
    publication-date filter.
    """
    base = _base(payload, {"names", "surnames", "places", "associates", "dateFrom", "dateTo", "domains", "maxQueries", "includeAsciiVariants"})
    originals = {key: _texts(payload.get(key, []), key) for key in ("names", "surnames", "places", "associates")}
    if not originals["names"] and not originals["surnames"]:
        raise ValueError("at least one name or surname is required")
    ascii_variants = _boolean(payload.get("includeAsciiVariants", True), "includeAsciiVariants")
    expanded = {key: _variants(values, ascii_variants) for key, values in originals.items()}
    maximum = payload.get("maxQueries", 24)
    if type(maximum) is not int or not 1 <= maximum <= 120:
        raise ValueError("maxQueries must be an integer from 1 through 120")
    domains = list(DEFAULT_DOMAINS)
    for domain in _list(payload.get("domains", []), "domains", maximum=16):
        validated = _domain(domain)
        if validated not in domains:
            domains.append(validated)
    date_from, date_to = _date(payload.get("dateFrom"), "dateFrom"), _date(payload.get("dateTo"), "dateTo")
    if date_from and date_to and date_from > date_to:
        raise ValueError("dateFrom must not follow dateTo")
    exacts = expanded["names"] + expanded["surnames"]
    surnames = expanded["surnames"] or expanded["names"]
    places, associates = expanded["places"], expanded["associates"]
    candidates, seen = [], set()
    limitations = []

    def add(strategy: str, domain: str | None, terms: list[str], query: str) -> None:
        query = (f"site:{domain} " if domain else "") + query
        if query not in seen:
            seen.add(query)
            candidates.append({"queryId": _key("community_query", query), "queryText": query,
                               "strategy": strategy, "domain": domain, "variants": terms,
                               "dateFrom": date_from, "dateTo": date_to})

    for round_index in range(max(3, len(exacts), len(surnames), len(places), len(associates))):
        for index, domain in enumerate(domains):
            choice = round_index + index
            variant_index = round_index * len(domains) + index
            strategy = ("exact", "relaxed", "cluster")[choice % 3]
            surname = surnames[variant_index % len(surnames)]
            place = places[variant_index % len(places)] if places else None
            if strategy == "exact":
                terms = [exacts[variant_index % len(exacts)]] + ([place] if place else [])
                query = " ".join(_quote(term) for term in terms)
            elif strategy == "relaxed":
                terms = [surname] + ([surnames[(variant_index + 1) % len(surnames)]] if len(surnames) > 1 else [])
                query = "(" + " OR ".join(dict.fromkeys(_relaxed(term) for term in terms)) + ")"
                if place:
                    terms.append(place)
                    query += " " + _relaxed(place)
            else:
                terms = _cluster_terms(surnames, associates, variant_index)
                if len(terms) < 2:
                    strategy = "contextual"
                    limitation = "No second lexically distinct surname or associate was supplied; contextual queries do not establish a family/FAN cluster."
                    if limitation not in limitations:
                        limitations.append(limitation)
                if place and _lexical_key(place) not in {_lexical_key(term) for term in terms}:
                    terms.append(place)
                suffix = " (family OR rodzina OR Familie)" if strategy == "cluster" else " (genealogia OR genealogy OR Familienforschung)"
                query = " ".join(_quote(term) for term in terms) + suffix
            add(strategy, domain, terms, query)
        if round_index == 0:
            if date_from or date_to:
                first_year = date_from[:4] if date_from else None
                last_year = date_to[:4] if date_to else None
                year_hint = f"{first_year}..{last_year}" if first_year and last_year else first_year or last_year
                terms = [surnames[0]] + ([places[0]] if places else [])
                add("dated", domains[0], terms, " ".join(_quote(term) for term in terms) + " " + year_hint)
                limitations.append("Dated query is a historical-content year hint. Numeric-range syntax requires adaptation to the selected search engine; it does not filter record chronology or publication dates reliably. Undated queries remain in scope.")
            for strategy, suffix in (
                ("regional_society", '("towarzystwo genealogiczne" OR "genealogical society" OR Heimatverein)'),
                ("blog", "(genealogia OR genealogy OR Familienforschung) (blog OR historia)"),
                ("published_research", '("historia rodziny" OR "family history" OR Familiengeschichte) (publication OR monografia OR opracowanie)'),
            ):
                terms = [surnames[0]] + ([places[0]] if places else [])
                add(strategy, None, terms, " ".join(_quote(term) for term in terms) + " " + suffix)
    # Make every literal/ASCII exact variant inspectable even when the balanced
    # first rounds and the query budget defer it. This is not a Cartesian search.
    for index, exact in enumerate(exacts):
        for domain in domains:
            terms = [exact] + ([places[index % len(places)]] if places else [])
            add("exact", domain, terms, " ".join(_quote(term) for term in terms))
    return {**base, "status": "planned", "applied": False, "originalTerms": originals,
            "expandedTerms": expanded, "domains": domains, "maxQueries": maximum,
            "queries": candidates[:maximum], "omittedQueries": candidates[maximum:],
            "unsearchedScope": {"allQueriesUnsearched": True, "candidateCount": len(candidates),
                                "omittedCount": max(0, len(candidates) - maximum),
                                "exhaustive": False, "note": LIMIT_NOTE, "limitations": limitations}}


def _finding(value: object) -> dict:
    fields = {"url", "locator", "title", "publishedAt", "attribution", "statement", "mentioned", "recordRefs",
              "hypotheses", "contradictions", "originalEvidence", "verificationStatus", "nextAction",
              "backingIds", "identityProposed", "identityEvaluation", "originRefs", "dependencyNote"}
    value = _object(value, fields, "finding")
    mentioned = _object(value.get("mentioned"), MENTION_FIELDS, "mentioned")
    result = {key: _text(value.get(key), key) for key in ("locator", "title", "statement", "nextAction")}
    for key in ("publishedAt", "attribution"):
        if key not in value:
            raise ValueError(f"{key} is required; use null for unknown")
        result[key] = _text(value[key], key, nullable=True)
    result.update({"url": _url(value.get("url"), "url"),
                   "mentioned": {key: _texts(mentioned.get(key), key) for key in sorted(MENTION_FIELDS)},
                   "hypotheses": _texts(value.get("hypotheses"), "hypotheses"),
                   "contradictions": _texts(value.get("contradictions"), "contradictions"),
                   "originalEvidence": _choice(value.get("originalEvidence"), {"available", "unavailable", "unknown"}, "originalEvidence"),
                   "verificationStatus": _choice(value.get("verificationStatus"), INTAKE_STATES, "verificationStatus")})
    refs = []
    for ref in _list(value.get("recordRefs"), "recordRefs"):
        ref = _object(ref, {"url", "signature"}, "recordRef")
        if "url" not in ref or "signature" not in ref:
            raise ValueError("recordRef requires url and signature; use null for unknown")
        refs.append({"url": _url(ref["url"], "recordRef.url", nullable=True),
                     "signature": _text(ref["signature"], "recordRef.signature", nullable=True)})
    result["recordRefs"] = refs
    result["originRefs"] = [_url(url, "originRefs URL") for url in _list(value.get("originRefs", []), "originRefs")]
    result["dependencyNote"] = _text(value.get("dependencyNote", "unknown"), "dependencyNote")
    backing = value.get("backingIds", {})
    if not isinstance(backing, dict) or len(backing) > 32:
        raise ValueError("backingIds must be an object with at most 32 keys")
    result["backingIds"] = {_text(key, "backingIds key"): _texts(ids, "backingIds") for key, ids in backing.items()}
    result["identityProposed"] = _boolean(value.get("identityProposed", False), "identityProposed")
    evaluation = value.get("identityEvaluation")
    if evaluation is not None or result["identityProposed"]:
        evaluation = _object(evaluation, IDENTITY_FIELDS, "identityEvaluation")
        result["identityEvaluation"] = {key: _text(evaluation.get(key), key) for key in sorted(IDENTITY_FIELDS)}
    else:
        result["identityEvaluation"] = None
    return result


def prepare_community_research_log(payload: dict) -> dict:
    """Validate receipts and return existing Research Log API kwargs, unapplied.

    Unknown fields are rejected, never silently skipped. Identity intake allows
    only possible/unresolved/contradicted. A finding is deduplicated solely by
    exact URL + locator + statement; each search retains its full context and
    finding metadata. Same website/post is never evidence of independence.
    """
    base = _base(payload, {"searches"})
    entries, registry, duplicates, contexts, identities = [], {}, [], [], {}
    searches = _list(payload.get("searches"), "searches", maximum=120)
    if not searches:
        raise ValueError("searches must not be empty")
    for index, value in enumerate(searches):
        value = _object(value, {"repository", "queryText", "scopeText", "searchedAt", "outcome", "limitations", "findings"}, "search")
        search = {key: _text(value.get(key), key) for key in ("repository", "queryText", "scopeText")}
        search.update({"searchedAt": validate_search_timestamp(value.get("searchedAt")),
                       "outcome": _choice(value.get("outcome"), OUTCOMES, "outcome"),
                       "limitations": _texts(value.get("limitations"), "limitations", required=True)})
        findings = [_finding(item) for item in _list(value.get("findings"), "findings")]
        if search["outcome"] in {"negative", "not-searched"} and findings:
            raise ValueError("negative/not-searched receipts cannot contain findings")
        if search["outcome"] == "positive" and not findings:
            raise ValueError("positive receipts require findings")
        refs, captured = [], []
        for finding_index, finding in enumerate(findings):
            key = _key("community_finding", [finding["url"], finding["locator"], finding["statement"]])
            context = {"searchIndex": index, "findingIndex": finding_index}
            if key in registry:
                duplicates.append({"findingKey": key, **context, "firstContext": registry[key]["searchContexts"][0]})
            else:
                registry[key] = {"findingKey": key, "finding": finding, "searchContexts": [], "independence": "unassessed"}
            registry[key]["searchContexts"].append(context)
            if key not in refs:
                refs.append(key)
            # Preserve differing metadata for the same exact statement as well.
            if finding not in captured:
                captured.append(finding)
        receipt = {**base, "receiptType": "community_research", "search": {**search, "findings": captured},
                   "findingKeys": refs, "provenance": {"sourceKind": "community_statement", "proof": False,
                   "independence": "unassessed", "reviewStatus": "curator_review_required"}}
        kwargs = {"case_id": base["caseId"], "question_id": base["questionId"], "repository": search["repository"],
                  "query_text": search["queryText"], "scope_text": search["scopeText"], "result_type": search["outcome"],
                  "result_summary": _json(receipt), "searched_at": search["searchedAt"]}
        # Existing Research Log identity excludes scope/result; reject collisions
        # rather than let persistence silently overwrite a distinct search context.
        identity = (base["caseId"], base["questionId"], search["repository"], search["queryText"], search["searchedAt"])
        if identity in identities and identities[identity] != kwargs:
            raise ValueError("distinct receipts collide with existing Research Log identity; use a distinct searchedAt or queryText")
        identities[identity] = kwargs
        key = _key("community_receipt", kwargs)
        entries.append({"receiptKey": key, "logEntryKwargs": kwargs,
                        "coverageKwargs": {"appropriate": False, "complete": False, "systematic": False, "assessment_note": LIMIT_NOTE}})
        contexts.append({"searchIndex": index, "receiptKey": key, "findingKeys": refs,
                         "repository": search["repository"], "queryText": search["queryText"], "scopeText": search["scopeText"]})
    return {**base, "status": "prepared", "applied": False, "note": "Preview only; no Research Log or coverage was applied.",
            "entries": entries, "findings": list(registry.values()), "duplicates": duplicates,
            "searchContexts": contexts, "independence": "unassessed", "assessmentNote": LIMIT_NOTE}
