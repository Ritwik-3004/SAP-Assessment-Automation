interface DeterminateProps {
  mode: "determinate";
  completed: number;
  total: number;
  label?: string;
}

interface IndeterminateProps {
  mode: "indeterminate";
  label?: string;
}

type Props = DeterminateProps | IndeterminateProps;

export default function ProgressBar(props: Props) {
  if (props.mode === "indeterminate") {
    return (
      <div className="progress-bar">
        <div className="progress-bar-track">
          <div className="progress-bar-fill progress-bar-indeterminate" />
        </div>
        {props.label && <p className="progress-bar-label">{props.label}</p>}
      </div>
    );
  }

  const { completed, total, label } = props;
  const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;

  return (
    <div className="progress-bar">
      <div className="progress-bar-track">
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="progress-bar-label">
        {completed} of {total}
        {label ? ` — ${label}` : ""} ({pct}%)
      </p>
    </div>
  );
}
