import { useState, useEffect } from "react";

interface Props {
  rows: Record<string, string>[];
  caption?: string;
  pageSize?: number;
}

export default function ResultsTable({ rows, caption, pageSize = 20 }: Props) {
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [rows]);

  if (!rows || rows.length === 0) {
    return <p className="empty-msg">No results returned.</p>;
  }

  const columns = Object.keys(rows[0]);
  const totalPages = Math.ceil(rows.length / pageSize);
  const pageRows = rows.slice(page * pageSize, (page + 1) * pageSize);
  const start = page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, rows.length);

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
            {pageRows.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td key={col}>{row[col] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        <p className="row-count">
          Showing {start}–{end} of {rows.length} row{rows.length !== 1 ? "s" : ""}
        </p>
        {totalPages > 1 && (
          <div className="pagination">
            <button
              className="page-btn"
              onClick={() => setPage((p) => p - 1)}
              disabled={page === 0}
              aria-label="Previous page"
            >
              ←
            </button>
            <span className="page-info">Page {page + 1} of {totalPages}</span>
            <button
              className="page-btn"
              onClick={() => setPage((p) => p + 1)}
              disabled={page === totalPages - 1}
              aria-label="Next page"
            >
              →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
