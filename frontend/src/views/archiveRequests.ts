/** Ephemeral private API data; never add this model to the presentation payload. */
export const ARCHIVE_REQUEST_STATUSES = {
  draft: "Szkic",
  ready: "Gotowa do wysłania",
  sent: "Wysłana",
  awaiting_reply: "Oczekuje na odpowiedź",
  received: "Otrzymano odpowiedź",
  deferred: "Odłożona",
} as const;

export const ARCHIVE_REQUEST_READINESS = {
  ready_for_inquiry: "Gotowa do zapytania",
  needs_details: "Wymaga uzupełnienia danych",
  needs_eligibility: "Wymaga sprawdzenia uprawnienia do uzyskania dokumentu",
  online_first: "Najpierw sprawdź materiały online",
} as const;

export interface ArchiveRequest {
  id: string;
  caseId: string | null;
  title: string;
  repository: string;
  recipient: string;
  status: keyof typeof ARCHIVE_REQUEST_STATUSES;
  readiness: keyof typeof ARCHIVE_REQUEST_READINESS;
  priority: number;
  requestBody: string;
  instructions: string;
  targets: Array<{ signature: string; title: string; dates: string; url: string; sourceId?: string }>;
  provenance: string[];
  missingFields: string[];
  privacy: string;
  responseNote: string;
  createdAt: string;
  updatedAt: string;
}

function invalid(): never {
  throw new Error("Invalid archive request response");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : invalid();
}

function id(value: unknown): string {
  const result = text(value);
  return result.trim() ? result : invalid();
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text) : invalid();
}

export function parseArchiveRequests(value: unknown): ArchiveRequest[] {
  const requests = record(value).requests;
  if (!Array.isArray(requests)) return invalid();
  const result = requests.map((raw): ArchiveRequest => {
    const item = record(raw);
    const status = text(item.status);
    const readiness = text(item.readiness);
    if (!Object.hasOwn(ARCHIVE_REQUEST_STATUSES, status) || !Object.hasOwn(ARCHIVE_REQUEST_READINESS, readiness)
      || !Number.isSafeInteger(item.priority) || !Array.isArray(item.targets)) return invalid();
    return {
      id: id(item.id), caseId: item.caseId === null ? null : id(item.caseId), title: text(item.title), repository: text(item.repository),
      recipient: text(item.recipient), status: status as ArchiveRequest["status"],
      readiness: readiness as ArchiveRequest["readiness"], priority: item.priority as number,
      requestBody: text(item.requestBody), instructions: text(item.instructions),
      targets: item.targets.map((rawTarget) => {
        const target = record(rawTarget);
        return {
          signature: text(target.signature), title: text(target.title), dates: text(target.dates), url: text(target.url),
          ...(target.sourceId !== undefined && target.sourceId !== null ? { sourceId: id(target.sourceId) } : {}),
        };
      }),
      provenance: texts(item.provenance), missingFields: texts(item.missingFields), privacy: text(item.privacy),
      responseNote: text(item.responseNote), createdAt: text(item.createdAt), updatedAt: text(item.updatedAt),
    };
  });
  if (new Set(result.map((item) => item.id)).size !== result.length) return invalid();
  return result.sort((left, right) => left.priority - right.priority || left.id.localeCompare(right.id, "pl"));
}

export function safeArchiveUrl(value: string): string | null {
  if (!/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function safeSourceId(value?: string): value is string {
  return typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$/.test(value);
}

export function archiveRequestMatches(item: ArchiveRequest, query: string): boolean {
  const normalized = (value: string) => value.normalize("NFD").replace(/\p{M}/gu, "").replace(/ł/g, "l").replace(/Ł/g, "L").toLocaleLowerCase("pl");
  const haystack = normalized([
    item.id, item.caseId, item.title, item.repository, item.recipient, item.requestBody, item.instructions,
    ...item.targets.flatMap((target) => [target.signature, target.title, target.dates]),
  ].join(" "));
  return normalized(query).trim().split(/\s+/).every((term) => haystack.includes(term));
}
