"use client";

import { createClient } from "@/lib/supabaseClient";

export default function LoginPage() {
  const supabase = createClient();

  async function signInWithGoogle() {
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-2xl font-semibold">NovelEngine</h1>
      <p className="text-gray-600">Sign in to view and generate your fantasy novels.</p>
      <button
        onClick={signInWithGoogle}
        className="rounded bg-gray-900 px-4 py-2 text-white hover:bg-gray-700"
      >
        Sign in with Google
      </button>
    </main>
  );
}
