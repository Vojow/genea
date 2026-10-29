export const API_ROOT = "/api/presentation/reading-verification";

export const REVIEW_SCALES = ["field", "context", "fullAct"] as const;
export type ReviewScale = (typeof REVIEW_SCALES)[number];

export interface ReviewField {
  id: string;
  kind: string;
  visibleScale: ReviewScale;
  frozenScales: ReviewScale[];
  submittedScales: ReviewScale[];
  availableScales: ReviewScale[];
}

export interface ReviewSession {
  runId: string;
  csrfToken: string;
  fields: ReviewField[];
}

export class ReviewApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ReviewApiError";
    this.status = status;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function scale(value: unknown, fieldName: string): ReviewScale {
  if (typeof value === "string" && REVIEW_SCALES.includes(value as ReviewScale)) {
    return value as ReviewScale;
  }
  throw new ReviewApiError(`Nieprawidłowe pole sesji: ${fieldName}.`, 502);
}

function scales(value: unknown, fieldName: string): ReviewScale[] {
  if (!Array.isArray(value)) {
    throw new ReviewApiError(`Nieprawidłowe pole sesji: ${fieldName}.`, 502);
  }
  return value.map((entry) => scale(entry, fieldName));
}

function requiredString(value: unknown, fieldName: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new ReviewApiError(`Nieprawidłowe pole sesji: ${fieldName}.`, 502);
  }
  return value;
}

export function parseReviewSession(value: unknown): ReviewSession {
  if (!isObject(value) || !Array.isArray(value.fields)) {
    throw new ReviewApiError("Serwer zwrócił nieprawidłową sesję odczytu.", 502);
  }
  const fields = value.fields.map((entry, index): ReviewField => {
    if (!isObject(entry)) {
      throw new ReviewApiError(`Nieprawidłowe pole sesji: fields[${index}].`, 502);
    }
    const availableScales = scales(entry.availableScales, `fields[${index}].availableScales`);
    const frozenScales = scales(entry.frozenScales, `fields[${index}].frozenScales`);
    const submittedScales = scales(entry.submittedScales, `fields[${index}].submittedScales`);
    const visibleScale = scale(entry.visibleScale, `fields[${index}].visibleScale`);
    if (!availableScales.includes(visibleScale) || !availableScales.includes("field")) {
      throw new ReviewApiError(`Niespójne skale pola fields[${index}].`, 502);
    }
    return {
      id: requiredString(entry.id, `fields[${index}].id`),
      kind: requiredString(entry.kind, `fields[${index}].kind`),
      visibleScale,
      frozenScales,
      submittedScales,
      availableScales,
    };
  });
  return {
    runId: requiredString(value.runId, "runId"),
    csrfToken: requiredString(value.csrfToken, "csrfToken"),
    fields,
  };
}

async function responseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ReviewApiError("Serwer zwrócił odpowiedź w nieobsługiwanym formacie.", 502);
  }
}

async function ensureOk(response: Response): Promise<void> {
  if (response.ok) return;
  const fallback = response.status === 423
    ? "Ta sesja odczytu jest zablokowana."
    : "Nie udało się zapisać etapu odczytu.";
  throw new ReviewApiError(fallback, response.status);
}

export async function loadReviewSession(): Promise<ReviewSession> {
  const response = await fetch(`${API_ROOT}/session`, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  await ensureOk(response);
  return parseReviewSession(await responseJson(response));
}

export function fieldAssetUrl(fieldId: string, scale: ReviewScale): string {
  return `${API_ROOT}/fields/${encodeURIComponent(fieldId)}/${scale}`;
}

async function postFieldCommand(
  fieldId: string,
  action: "freeze" | "submission" | "reveal",
  csrfToken: string,
  body: Record<string, string>,
): Promise<void> {
  const response = await fetch(`${API_ROOT}/fields/${encodeURIComponent(fieldId)}/${action}`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(body),
  });
  await ensureOk(response);
}

export async function freezeField(
  fieldId: string,
  scale: ReviewScale,
  submissionSha256: string,
  csrfToken: string,
  commandId: string,
): Promise<void> {
  await postFieldCommand(fieldId, "freeze", csrfToken, { commandId, scale, submissionSha256 });
}

export async function revealField(
  fieldId: string,
  scale: ReviewScale,
  csrfToken: string,
  commandId: string,
): Promise<void> {
  await postFieldCommand(fieldId, "reveal", csrfToken, { commandId, scale });
}

export async function submitField(
  fieldId: string,
  scale: ReviewScale,
  literalReading: string,
  csrfToken: string,
  commandId: string,
): Promise<void> {
  await postFieldCommand(fieldId, "submission", csrfToken, { commandId, scale, literalReading });
}

export async function sha256Literal(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function newCommandId(): string {
  return globalThis.crypto.randomUUID();
}
