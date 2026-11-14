import Link from "next/link";
import { redirect } from "next/navigation";
import { createServerSupabaseClient } from "@/lib/supabaseServer";
import { listNovels } from "@/lib/api";

export default async function NovelsPage() {
  const supabase = createServerSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect("/login");
  }

  const novels = await listNovels(session.access_token);

  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <h1 className="mb-6 text-xl font-semibold sm:text-2xl">My Novels</h1>

      {novels.length === 0 ? (
        <div className="rounded border border-dashed border-gray-300 p-8 text-center">
          <p className="text-gray-600">You haven&apos;t generated any novels yet.</p>
          <p className="mt-1 text-sm text-gray-500">
            Create a novel and start a generation job to see it here.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {novels.map((novel) => (
            <li
              key={novel.id}
              className="rounded border border-gray-200 p-4 transition hover:bg-gray-50"
            >
              <Link href={`/novels/${novel.id}`} className="font-medium">
                {novel.title}
              </Link>
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-gray-500">
                {novel.genre && (
                  <span className="rounded-full bg-gray-100 px-2 py-0.5">{novel.genre}</span>
                )}
                <span className="rounded-full bg-gray-100 px-2 py-0.5">
                  {novel.chapter_count === 0
                    ? "Not generated yet"
                    : `${novel.chapter_count} chapter${novel.chapter_count === 1 ? "" : "s"}`}
                </span>
              </div>
              {novel.synopsis && <p className="mt-2 text-sm text-gray-600">{novel.synopsis}</p>}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
