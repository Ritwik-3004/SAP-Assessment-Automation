/** Tells the user where a result was saved automatically (the exact path), or why saving failed. */
export default function SavedNotice({ path, error }: { path?: string | null; error?: string | null }) {
  if (error) return <p className="tx-error">{error}</p>;
  if (!path) return null;
  return (
    <div className="saved-notice">
      Results saved automatically to <code>{path}</code>
    </div>
  );
}
