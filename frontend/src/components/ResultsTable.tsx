interface Props {
  rows: Record<string, string>[];
  caption?: string;
}

export default function ResultsTable({ rows, caption }: Props) {
  if (!rows || rows.length === 0) {
    return <p className="empty-msg">No results returned.</p>;
  }

  const columns = Object.keys(rows[0]);

  return (
    <div className="table-wrapper">
      {caption && <p className="table-caption">{caption}</p>}
      <div className="table-scroll">
        <table className="results-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td key={col}>{row[col] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="row-count">{rows.length} row{rows.length !== 1 ? "s" : ""}</p>
    </div>
  );
}
