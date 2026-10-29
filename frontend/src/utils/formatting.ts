export function statusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    imported_unverified: "do weryfikacji",
    unresolved: "nierozstrzygnięte",
    Confirmed: "potwierdzone",
    Probable: "prawdopodobne",
    Possible: "możliwe",
    Speculation: "hipoteza",
    Rejected: "odrzucone",
    open: "otwarte",
    active: "aktywna",
    detected: "wykryte",
    triage: "do triage",
    ready: "gotowe do pracy",
    needs_stable_locator: "brak stabilnego lokalizatora",
    nice_to_have: "opcjonalne",
    planned: "zaplanowane",
    in_review: "w recenzji",
    waiting: "oczekuje",
    inconclusive: "niekonkluzywne",
    superseded: "zastąpione nowszym",
    in_progress: "w toku",
    correlation_review: "kontrola korelacji",
    resolved: "rozwiązane",
    candidate: "kandydatura",
    answered: "odpowiedziane",
    blocked: "zablokowane",
    closed: "zamknięte",
    dismissed: "odrzucone",
    complete: "ukończone",
    completed: "ukończone",
    accepted: "przyjęte",
    documented: "udokumentowane",
    source_reported: "zapisane w źródle",
    "source-reported": "zapisane w źródle",
    agent_approved: "odczyt sprawdzony agentowo",
    Certain: "pewny",
    Probable_reading: "prawdopodobny",
    partial_agent_review: "częściowo sprawdzone agentowo",
    human_review_required: "wymaga Twojej kontroli",
    human_reviewed: "sprawdzone przez Ciebie",
    deceased: "osoba zmarła",
    living: "osoba żyjąca",
    unknown: "nieustalone",
    deterministic: "wykryte regułą",
    curated: "kuratorowane",
    agent: "agentowe",
    needs_residence_confirmation: "wspólny adres do potwierdzenia",
  };
  return labels[status || ""] || status || "brak statusu";
}

export function polishCount(count: number, one: string, few: string, many: string): string {
  if (count === 1) return `1 ${one}`;
  const mod10 = count % 10;
  const mod100 = count % 100;
  const noun = mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14) ? few : many;
  return `${count} ${noun}`;
}

export function shortDate(value?: string | null): string {
  if (!value) return "data nieustalona";
  return value.length > 28 ? `${value.slice(0, 28)}…` : value;
}

export function polishDate(value?: string | null): string {
  if (!value) return "data nieustalona";
  if (/^\d{4}$/.test(value)) return value;
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return shortDate(value);
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return new Intl.DateTimeFormat("pl-PL", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(date);
}

export function eventTypeLabel(value = ""): string {
  const labels: Record<string, string> = {
    "birth-baptism": "urodzenie i chrzest",
    birth: "urodzenie",
    "death-burial": "zgon i pogrzeb",
    death: "zgon",
    marriage: "małżeństwo",
    census: "księga ludności",
  };
  const original = String(value || "");
  return labels[original.toLocaleLowerCase("pl-PL")] || original || "zdarzenie";
}

export function roleLabel(value = ""): string {
  const labels: Record<string, string> = {
    bride: "panna młoda",
    groom: "pan młody",
    witness: "świadek",
    father: "ojciec",
    mother: "matka",
    child: "dziecko",
    baptized: "osoba chrzczona",
    declarant: "zgłaszający",
    husband: "mąż",
    wife: "żona",
    widower: "wdowiec",
    widow: "wdowa",
    "father-of-bride": "ojciec panny młodej",
    "father-of-groom": "ojciec pana młodego",
    "mother-of-bride": "matka panny młodej",
    "mother-of-groom": "matka pana młodego",
    living: "żyje według aktu",
    deceased: "zmarły według aktu",
    "related-person": "osoba z gospodarstwa",
    participant: "uczestnik",
  };
  return String(value || "").split(";").map((part) => {
    const trimmed = part.trim();
    return labels[trimmed.toLocaleLowerCase("pl-PL")] || trimmed;
  }).filter(Boolean).join("; ");
}
