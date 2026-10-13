/** Tells the user where a result was saved automatically (the exact path), or why saving failed. */
export default function SavedNotice({
  path,
  paths,
  error,
}: {
  path?: string | null;
  /** several files saved at once: each full path is listed */
  paths?: string[];
  error?: string | null;
}) {
  if (error) return <p className="tx-error">{error}</p>;
  const all = paths ?? (path ? [path] : []);
  if (all.length === 0) return null;
  return (
    <div className="saved-notice">
      {all.length === 1 ? "Results saved automatically to " : "Results saved automatically to:"}
      {all.map((p) => (
        <div key={p}>
          <code>{p}</code>
        </div>
      ))}
    </div>
  );
}
