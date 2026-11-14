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
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="mb-6 text-2xl font-semibold">My Novels</h1>

      {novels.length === 0 ? (
        <p className="text-gray-600">You haven&apos;t generated any novels yet.</p>
      ) : (
        <ul className="space-y-3">
          {novels.map((novel) => (
            <li key={novel.id} className="rounded border border-gray-200 p-4 hover:bg-gray-100">
              <Link href={`/novels/${novel.id}`} className="font-medium">
                {novel.title}
              </Link>
              {novel.genre && <span className="ml-2 text-sm text-gray-500">({novel.genre})</span>}
              {novel.synopsis && <p className="mt-1 text-sm text-gray-600">{novel.synopsis}</p>}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
