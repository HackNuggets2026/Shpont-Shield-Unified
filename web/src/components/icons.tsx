// A small inline icon set (24px grid, 1.75 stroke) so the bundle needs no icon library.
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function I({ size = 16, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconShield = (p: P) => (
  <I {...p}>
    <path d="M12 3 4.5 6v5.5c0 4.6 3.1 8.3 7.5 9.5 4.4-1.2 7.5-4.9 7.5-9.5V6L12 3z" />
  </I>
);
export const IconGauge = (p: P) => (
  <I {...p}>
    <path d="M4 15a8 8 0 1 1 16 0" />
    <path d="m12 15 4-5" />
    <circle cx="12" cy="15" r="1" />
  </I>
);
export const IconFlow = (p: P) => (
  <I {...p}>
    <rect x="3" y="4" width="7" height="6" rx="1.5" />
    <rect x="14" y="14" width="7" height="6" rx="1.5" />
    <path d="M6.5 10v4a3 3 0 0 0 3 3H14" />
  </I>
);
export const IconUsers = (p: P) => (
  <I {...p}>
    <circle cx="9" cy="8" r="3.5" />
    <path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5" />
    <path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.8.7 3 2.5 3.5 5.2" />
  </I>
);
export const IconAlert = (p: P) => (
  <I {...p}>
    <path d="M12 3.5 2.5 20h19L12 3.5z" />
    <path d="M12 10v4.5M12 17.5v.01" />
  </I>
);
export const IconBox = (p: P) => (
  <I {...p}>
    <path d="m3.5 7.5 8.5-4.5 8.5 4.5v9L12 21l-8.5-4.5v-9z" />
    <path d="m3.5 7.5 8.5 4.5 8.5-4.5M12 12v9" />
  </I>
);
export const IconInbox = (p: P) => (
  <I {...p}>
    <path d="M3 13.5 5.5 5h13l2.5 8.5V19H3v-5.5z" />
    <path d="M3 13.5h5l1.5 2.5h5l1.5-2.5h5" />
  </I>
);
export const IconHome = (p: P) => (
  <I {...p}>
    <path d="M3.5 10.5 12 4l8.5 6.5V20h-5.5v-6h-6v6H3.5v-9.5z" />
  </I>
);
export const IconMenuList = (p: P) => (
  <I {...p}>
    <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />
  </I>
);
export const IconKey = (p: P) => (
  <I {...p}>
    <circle cx="8" cy="15" r="4" />
    <path d="m11 12 9-9M17 6l2.5 2.5M14.5 8.5 16.5 10.5" />
  </I>
);
export const IconActivity = (p: P) => (
  <I {...p}>
    <path d="M3 12h4l3-7 4 14 3-7h4" />
  </I>
);
export const IconEye = (p: P) => (
  <I {...p}>
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </I>
);
export const IconLogout = (p: P) => (
  <I {...p}>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l4-4-4-4M14 12H4" />
  </I>
);
export const IconSun = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </I>
);
export const IconMoon = (p: P) => (
  <I {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />
  </I>
);
export const IconMonitor = (p: P) => (
  <I {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M8 20h8M12 16v4" />
  </I>
);
export const IconX = (p: P) => (
  <I {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </I>
);
export const IconBars = (p: P) => (
  <I {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </I>
);
export const IconDownload = (p: P) => (
  <I {...p}>
    <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
  </I>
);
export const IconStop = (p: P) => (
  <I {...p}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </I>
);
export const IconCheck = (p: P) => (
  <I {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </I>
);
export const IconLock = (p: P) => (
  <I {...p}>
    <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
    <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
  </I>
);
export const IconTerminal = (p: P) => (
  <I {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="m7 9 3 3-3 3M12.5 15H17" />
  </I>
);
export const IconGlobe = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z" />
  </I>
);
export const IconPlug = (p: P) => (
  <I {...p}>
    <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0V8zM12 17v4" />
  </I>
);
export const IconReceipt = (p: P) => (
  <I {...p}>
    <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z" />
    <path d="M9 8h6M9 12h6" />
  </I>
);
export const IconRadar = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <path d="M12 12 18 6" />
  </I>
);
export const IconClock = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </I>
);
export const IconArrowLeft = (p: P) => (
  <I {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </I>
);
export const IconPause = (p: P) => (
  <I {...p}>
    <path d="M9 5v14M15 5v14" />
  </I>
);
export const IconPlay = (p: P) => (
  <I {...p}>
    <path d="m7 5 12 7-12 7V5z" />
  </I>
);
export const IconSearch = (p: P) => (
  <I {...p}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </I>
);
export const IconBuilding = (p: P) => (
  <I {...p}>
    <path d="M4 21V5.5L12 3l8 2.5V21M4 21h16M9 21v-4h6v4M8 8h1M12 8h1M16 8h-1M8 12h1M12 12h1M16 12h-1" />
  </I>
);
export const IconChevronRight = (p: P) => (
  <I {...p}>
    <path d="m9 6 6 6-6 6" />
  </I>
);
export const IconTrendUp = (p: P) => (
  <I {...p}>
    <path d="M4 17l6-6 4 4 6-7M20 8h-5M20 8v5" />
  </I>
);
export const IconBug = (p: P) => (
  <I {...p}>
    <rect x="7" y="8" width="10" height="12" rx="5" />
    <path d="M9 8V6.5a3 3 0 0 1 6 0V8M12 12v8M3.5 13H7M17 13h3.5M4.5 7.5 7.5 9.5M19.5 7.5l-3 2M4.5 19l3-2M19.5 19l-3-2" />
  </I>
);
export const IconPullRequest = (p: P) => (
  <I {...p}>
    <circle cx="6" cy="5.5" r="2.25" />
    <circle cx="6" cy="18.5" r="2.25" />
    <circle cx="18" cy="18.5" r="2.25" />
    <path d="M6 7.75v8.5M18 16.25V9.5a3 3 0 0 0-3-3h-4M13 4.5l-2 2 2 2" />
  </I>
);
export const IconPhone = (p: P) => (
  <I {...p}>
    <rect x="6.5" y="2.5" width="11" height="19" rx="2.5" />
    <path d="M10.5 18.5h3" />
  </I>
);
export const IconChart = (p: P) => (
  <I {...p}>
    <path d="M4 4v16h16" />
    <path d="M8 16v-4M12 16V8M16 16v-6" />
  </I>
);
export const IconChat = (p: P) => (
  <I {...p}>
    <path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4.5 3.5v-3.5h-1a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z" />
    <path d="M8 10h8M8 13h5" />
  </I>
);
export const IconBolt = (p: P) => (
  <I {...p}>
    <path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12l1-8z" />
  </I>
);
export const IconQuestion = (p: P) => (
  <I {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7M12 17v.01" />
  </I>
);
export const IconGrid = (p: P) => (
  <I {...p}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
  </I>
);
export const IconServer = (p: P) => (
  <I {...p}>
    <rect x="3.5" y="4" width="17" height="7" rx="1.5" />
    <rect x="3.5" y="13" width="17" height="7" rx="1.5" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </I>
);
