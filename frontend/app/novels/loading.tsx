export default function NovelsLoading() {
  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <div className="mb-6 h-7 w-40 animate-pulse rounded bg-gray-200" />
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded border border-gray-200 bg-gray-100" />
        ))}
      </div>
    </main>
  );
}
