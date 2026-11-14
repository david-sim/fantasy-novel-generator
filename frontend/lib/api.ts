export interface NovelSummary {
  id: number;
  title: string;
  genre?: string | null;
  tone?: string | null;
  synopsis?: string | null;
  chapter_count: number;
}

export interface ChapterOut {
  id: number;
  chapter_number: number;
  title?: string | null;
  content?: string | null;
  summary?: string | null;
}

export interface CharacterOut {
  id: number;
  name: string;
  role?: string | null;
  backstory?: string | null;
  arc_summary?: string | null;
}

export interface CreatureOut {
  id: number;
  name: string;
  species_type?: string | null;
  lore_description?: string | null;
}

export interface NovelDetail extends NovelSummary {
  chapters: ChapterOut[];
  characters: CharacterOut[];
  creatures: CreatureOut[];
}

export interface GenerationJobResponse {
  job_id: string;
  status: string;
}

export interface JobStatus {
  job_id: string;
  novel_id: number;
  status: "queued" | "running" | "succeeded" | "failed";
  error_message?: string | null;
  chapter_count?: number | null;
  feedback_score?: number | null;
  current_agent?: string | null;
  latest_log?: string | null;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Thin fetch wrapper for the FastAPI backend. Attaches the Supabase session
 * access token as a Bearer header so the API can authenticate the caller.
 * Safe to call from both server components (token from the server session)
 * and client components (token read client-side) — it only needs the token
 * string, not cookies.
 */
async function apiRequest<T>(
  path: string,
  accessToken: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!res.ok) {
    throw new Error(`API request to ${path} failed: ${res.status} ${res.statusText}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

function apiFetch<T>(path: string, accessToken: string): Promise<T> {
  return apiRequest<T>(path, accessToken);
}

function apiPost<T>(path: string, body: unknown, accessToken: string): Promise<T> {
  return apiRequest<T>(path, accessToken, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listNovels(accessToken: string): Promise<NovelSummary[]> {
  return apiFetch<NovelSummary[]>("/api/novels", accessToken);
}

export function getNovel(novelId: number, accessToken: string): Promise<NovelDetail> {
  return apiFetch<NovelDetail>(`/api/novels/${novelId}`, accessToken);
}

/**
 * Enqueue an async generation job (Phase 2: returns 202 + job id
 * immediately instead of blocking on the full LangGraph run).
 */
export function startGeneration(
  novelId: number,
  initialPrompt: string,
  accessToken: string
): Promise<GenerationJobResponse> {
  return apiPost<GenerationJobResponse>(
    `/api/novels/${novelId}/generate`,
    { initial_prompt: initialPrompt },
    accessToken
  );
}

/** Poll a generation job's current status/progress. */
export function getJobStatus(
  novelId: number,
  jobId: string,
  accessToken: string
): Promise<JobStatus> {
  return apiFetch<JobStatus>(`/api/novels/${novelId}/jobs/${jobId}`, accessToken);
}

export type ExportFormat = "markdown" | "pdf";

/**
 * Download a novel export (Markdown or PDF, Phase 3).
 *
 * Fetches the export endpoint with the bearer token attached (a plain
 * `<a href>` can't carry an Authorization header), then triggers a
 * client-side blob download using the filename the API suggested via
 * `Content-Disposition`.
 */
export async function downloadNovelExport(
  novelId: number,
  format: ExportFormat,
  accessToken: string
): Promise<void> {
  const res = await fetch(`${API_URL}/api/novels/${novelId}/export?format=${format}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // response body wasn't JSON — fall back to the status text above
    }
    throw new Error(detail);
  }

  const disposition = res.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] ?? `novel.${format === "markdown" ? "md" : "pdf"}`;

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
