import { redirect } from "next/navigation";
import { createServerSupabaseClient } from "@/lib/supabaseServer";
import { getNovel } from "@/lib/api";
import GenerationPanel from "./GenerationPanel";

export default async function NovelDetailPage({ params }: { params: { id: string } }) {
  const supabase = createServerSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect("/login");
  }

  const novel = await getNovel(Number(params.id), session.access_token);

  return (
    <main className="mx-auto max-w-3xl space-y-8 p-8">
      <div>
        <h1 className="text-2xl font-semibold">{novel.title}</h1>
        {novel.synopsis && <p className="mt-2 text-gray-600">{novel.synopsis}</p>}
      </div>

      <GenerationPanel novelId={novel.id} />

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
