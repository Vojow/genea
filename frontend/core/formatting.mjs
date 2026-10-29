export function shortDate(value) {
  if (!value) return "data nieustalona";
  return value.length > 28 ? `${value.slice(0, 28)}…` : value;
}

export function polishDate(value) {
  if (!value) return "data nieustalona";
  if (/^\d{4}$/.test(value)) return value;
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return shortDate(value);
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return new Intl.DateTimeFormat("pl-PL", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(date);
}

export function eventTypeLabel(value = "") {
  const labels = {
    "birth-baptism": "urodzenie i chrzest", birth: "urodzenie",
    "death-burial": "zgon i pogrzeb", death: "zgon", marriage: "małżeństwo",
    census: "księga ludności",
  };
  const original = String(value || "");
  return labels[original.toLocaleLowerCase("pl-PL")] || original || "zdarzenie";
}

export function roleLabel(value = "") {
  const labels = {
    bride: "panna młoda", groom: "pan młody", witness: "świadek",
    father: "ojciec", mother: "matka", child: "dziecko",
    declarant: "zgłaszający", baptized: "ochrzczony", godparent: "chrzestny",
    "buried person": "osoba pochowana",
    "father-of-bride": "ojciec panny młodej", "father-of-groom": "ojciec pana młodego",
    "mother-of-bride": "matka panny młodej", "mother-of-groom": "matka pana młodego",
    living: "żyje według aktu", deceased: "zmarły według aktu",
    "related-person": "osoba z gospodarstwa", participant: "uczestnik",
  };
  return String(value || "").split(";").map((part) => {
    const trimmed = part.trim();
    return labels[trimmed.toLocaleLowerCase("pl-PL")] || trimmed;
  }).filter(Boolean).join("; ");
}

export function graphVisibilityLabel(value) {
  return {
    core: "Rdzeń wybranej linii", "fan-direct": "FAN bezpośredni",
    "fan-extended": "FAN rozszerzony", bridge: "Łącznik rodzinny",
    candidate: "Kandydatura nierozstrzygnięta", unassigned: "Poza filtrem linii",
  }[value] || "Poza filtrem linii";
}

export function statusLabel(status) {
  const labels = {
    imported_unverified: "do weryfikacji", unresolved: "nierozstrzygnięte",
    Confirmed: "potwierdzone", Probable: "prawdopodobne", Possible: "możliwe",
    Speculation: "hipoteza", Rejected: "odrzucone", open: "otwarte",
    resolved: "rozwiązane", candidate: "kandydatura", answered: "odpowiedziane",
    blocked: "zablokowane", closed: "zamknięte", documented: "udokumentowane",
    source_reported: "zapisane w źródle", "source-reported": "zapisane w źródle",
    agent_approved: "odczyt sprawdzony agentowo", Certain: "pewny",
    needs_residence_confirmation: "wspólny adres do potwierdzenia",
  };
  return labels[status] || status || "brak statusu";
}

export function polishCount(count, one, few, many) {
  if (count === 1) return `1 ${one}`;
  const mod10 = count % 10;
  const mod100 = count % 100;
  return `${count} ${mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14) ? few : many}`;
}
