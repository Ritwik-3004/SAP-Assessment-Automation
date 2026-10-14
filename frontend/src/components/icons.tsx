import type { ReactNode } from "react";

/** Small line icons (24x24, drawn in the current text colour) for the collapsible panels. */
function Svg({ children, width = 2 }: { children: ReactNode; width?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const ChevronLeft = () => (
  <Svg>
    <path d="M15 6l-6 6 6 6" />
  </Svg>
);

export const ChevronRight = () => (
  <Svg>
    <path d="M9 6l6 6-6 6" />
  </Svg>
);

export const CloseIcon = () => (
  <Svg>
    <path d="M6 6l12 12M18 6L6 18" />
  </Svg>
);

export const DockIcon = () => (
  <Svg>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M15 5v14" />
  </Svg>
);

export const ChatIcon = () => (
  <Svg>
    <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />
  </Svg>
);

/** One icon per sidebar task, by task id. */
export function TaskIcon({ id }: { id: string }) {
  switch (id) {
    case "TOP_TABLES":
      return (
        <Svg width={1.9}>
          <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />
        </Svg>
      );
    case "BATCH":
      return (
        <Svg width={1.9}>
          <circle cx="11" cy="11" r="6.5" />
          <path d="M16 16l4.5 4.5" />
        </Svg>
      );
    case "HEADER_TABLES":
      return (
        <Svg width={1.9}>
          <rect x="9" y="3" width="6" height="5" rx="1" />
          <rect x="3" y="16" width="6" height="5" rx="1" />
          <rect x="15" y="16" width="6" height="5" rx="1" />
          <path d="M12 8v4M6 16v-4h12v4" />
        </Svg>
      );
    case "TABLE_ANALYSIS":
      return (
        <Svg width={1.9}>
          <path d="M5 20V11M12 20V5M19 20v-7" />
        </Svg>
      );
    case "OBJECT_ANALYSIS":
      return (
        <Svg width={1.9}>
          <path d="M12 3l9 5-9 5-9-5 9-5z" />
          <path d="M3 13l9 5 9-5" />
        </Svg>
      );
    case "OPEN_ITEM_ANALYSIS":
      return (
        <Svg width={1.9}>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 7v5l3 2" />
        </Svg>
      );
    default:
      return (
        <Svg width={1.9}>
          <path d="M7 3h7l5 5v13H7z" />
          <path d="M14 3v5h5M10 13h6M10 17h6" />
        </Svg>
      );
  }
}

/** Icons of the three folded settings rows in the sidebar. */
export const PlugIcon = () => (
  <Svg>
    <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0V8zM12 17v4" />
  </Svg>
);

export const SparkIcon = () => (
  <Svg>
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM19 16l.7 1.8L21.5 18.5l-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7L19 16z" />
  </Svg>
);

export const MailIcon = () => (
  <Svg>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M3 7l9 6 9-6" />
  </Svg>
);
