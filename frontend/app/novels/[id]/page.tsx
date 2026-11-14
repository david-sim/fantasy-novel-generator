import { redirect } from "next/navigation";
import { createServerSupabaseClient } from "@/lib/supabaseServer";
import { getNovel } from "@/lib/api";
import GenerationPanel from "./GenerationPanel";
import ExportButtons from "./ExportButtons";

export default async function NovelDetailPage({ params }: { params: { id: string } }) {
  const supabase = createServerSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect("/login");
  }

  const novel = await getNovel(Number(params.id), session.access_token);
  const hasContent = novel.chapters.some((chapter) => !!chapter.content?.trim());

  return (
    <main className="mx-auto max-w-3xl space-y-6 p-4 sm:space-y-8 sm:p-8">
      <div>
        <h1 className="text-xl font-semibold sm:text-2xl">{novel.title}</h1>
        <div className="mt-1 flex flex-wrap gap-2 text-sm text-gray-500">
          {novel.genre && (
            <span className="rounded-full bg-gray-100 px-2 py-0.5">{novel.genre}</span>
          )}
          {novel.tone && (
            <span className="rounded-full bg-gray-100 px-2 py-0.5">{novel.tone}</span>
          )}
          <span className="rounded-full bg-gray-100 px-2 py-0.5">
            {novel.chapters.length} chapter{novel.chapters.length === 1 ? "" : "s"}
          </span>
        </div>
        {novel.synopsis && <p className="mt-3 text-gray-600">{novel.synopsis}</p>}
      </div>

      <section className="rounded border border-gray-200 p-4">
        <h2 className="mb-2 text-lg font-semibold">Generate</h2>
        <GenerationPanel novelId={novel.id} />
      </section>

      <section className="rounded border border-gray-200 p-4">
        <h2 className="mb-2 text-lg font-semibold">Export</h2>
        <ExportButtons novelId={novel.id} hasContent={hasContent} />
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Chapters</h2>
        {novel.chapters.length === 0 ? (
          <p className="text-sm text-gray-500">No chapters generated yet.</p>
        ) : (
          <ul className="space-y-2">
            {novel.chapters.map((chapter) => (
              <li key={chapter.id} className="rounded border border-gray-200 p-3">
                <p className="font-medium">
                  Chapter {chapter.chapter_number}
                  {chapter.title ? `: ${chapter.title}` : ""}
                </p>
                {chapter.summary && <p className="text-sm text-gray-600">{chapter.summary}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Characters</h2>
        {novel.characters.length === 0 ? (
          <p className="text-sm text-gray-500">No characters generated yet.</p>
        ) : (
          <ul className="space-y-2">
            {novel.characters.map((character) => (
              <li key={character.id} className="rounded border border-gray-200 p-3">
                <p className="font-medium">
                  {character.name}
                  {character.role ? ` — ${character.role}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Creatures</h2>
        {novel.creatures.length === 0 ? (
          <p className="text-sm text-gray-500">No creatures generated yet.</p>
        ) : (
          <ul className="space-y-2">
            {novel.creatures.map((creature) => (
              <li key={creature.id} className="rounded border border-gray-200 p-3">
                <p className="font-medium">
                  {creature.name}
                  {creature.species_type ? ` — ${creature.species_type}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

