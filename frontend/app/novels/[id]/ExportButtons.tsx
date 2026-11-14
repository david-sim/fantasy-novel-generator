"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabaseClient";
import { downloadNovelExport, type ExportFormat } from "@/lib/api";

/**
 * "Download Markdown" / "Download PDF" buttons for a completed novel
 * (Phase 3 export). Only rendered when the novel has at least one chapter
 * with generated content — the backend returns a 409 with a clear message
 * otherwise, which we surface here if it happens anyway (e.g. a stale page).
 */
export default function ExportButtons({
  novelId,
  hasContent,
}: {
  novelId: number;
  hasContent: boolean;
}) {
  const [pending, setPending] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(format: ExportFormat) {
    setError(null);
    setPending(format);
    try {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session) throw new Error("Not signed in.");
      await downloadNovelExport(novelId, format, session.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setPending(null);
    }
  }

  if (!hasContent) {
    return (
      <p className="text-sm text-gray-500">
        Export will be available once this novel has at least one generated chapter.
      </p>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded border border-gray-300 px-4 py-1.5 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
          onClick={() => handleDownload("markdown")}
          disabled={pending !== null}
        >
          {pending === "markdown" ? "Preparing…" : "Download Markdown"}
        </button>
        <button
          className="rounded border border-gray-300 px-4 py-1.5 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
          onClick={() => handleDownload("pdf")}
          disabled={pending !== null}
        >
          {pending === "pdf" ? "Preparing…" : "Download PDF"}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
