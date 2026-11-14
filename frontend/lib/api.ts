export interface NovelSummary {
  id: number;
  title: string;
  genre?: string | null;
  tone?: string | null;
  synopsis?: string | null;
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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Thin fetch wrapper for the FastAPI backend. Attaches the Supabase session
 * access token as a Bearer header so the API can authenticate the caller.
 */
async function apiFetch<T>(path: string, accessToken: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });

  if (!res.ok) {
    throw new Error(`API request to ${path} failed: ${res.status} ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}

export function listNovels(accessToken: string): Promise<NovelSummary[]> {
  return apiFetch<NovelSummary[]>("/api/novels", accessToken);
}

export function getNovel(novelId: number, accessToken: string): Promise<NovelDetail> {
  return apiFetch<NovelDetail>(`/api/novels/${novelId}`, accessToken);
}
