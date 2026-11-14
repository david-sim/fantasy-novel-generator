"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabaseClient";
import { getJobStatus, startGeneration, type JobStatus } from "@/lib/api";

const POLL_INTERVAL_MS = 2500;

/**
 * Client-side trigger + live progress monitor for novel generation.
 *
 * Phase 2: generation runs as an async Redis/RQ job. This replaces the old
 * Streamlit `@st.fragment(run_every=2)` live monitor with simple HTTP
 * polling (no websockets/SSE) against `GET /api/novels/{id}/jobs/{job_id}`.
 */
export default function GenerationPanel({ novelId }: { novelId: number }) {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  async function getAccessToken(): Promise<string> {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session) throw new Error("Not signed in.");
    return session.access_token;
  }

  async function pollOnce(jobId: string) {
    try {
      const token = await getAccessToken();
      const status = await getJobStatus(novelId, jobId, token);
      setJob(status);
      if (status.status === "succeeded" || status.status === "failed") {
        stopPolling();
        if (status.status === "succeeded") {
          router.refresh();
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch job status.");
      stopPolling();
    }
  }

  async function handleGenerate() {
    setError(null);
    setSubmitting(true);
    try {
      const token = await getAccessToken();
      const { job_id, status: initialStatus } = await startGeneration(novelId, prompt, token);
      setJob({ job_id, novel_id: novelId, status: initialStatus as JobStatus["status"] });
      pollRef.current = setInterval(() => {
        void pollOnce(job_id);
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start generation.");
    } finally {
      setSubmitting(false);
    }
  }

  const isActive = job?.status === "queued" || job?.status === "running";

  return (
    <section className="rounded border border-gray-200 p-4">
      <h2 className="mb-2 text-lg font-semibold">Generation</h2>

      {!isActive && (
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border border-gray-300 px-3 py-1.5 text-sm"
            placeholder="Optional creative brief…"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            disabled={submitting}
          />
          <button
            className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            onClick={handleGenerate}
            disabled={submitting}
          >
            {submitting ? "Starting…" : "Generate"}
          </button>
        </div>
      )}

      {job && (
        <div className="mt-3 text-sm">
          <p>
            Status: <span className="font-medium">{job.status}</span>
            {job.current_agent && ` — ${job.current_agent}`}
          </p>
          {job.latest_log && <p className="mt-1 text-gray-600">{job.latest_log}</p>}
          {job.status === "succeeded" && (
            <p className="mt-1 text-green-700">
              Done — {job.chapter_count ?? 0} chapter(s), feedback score{" "}
              {job.feedback_score ?? "n/a"}.
            </p>
          )}
          {job.status === "failed" && (
            <p className="mt-1 text-red-700">Failed: {job.error_message}</p>
          )}
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </section>
  );
}
