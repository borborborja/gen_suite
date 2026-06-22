// Inline SVG icons used across Estela views (ported from the design).
import type { ReactNode } from "react";

interface IconProps { size?: number; sw?: number; stroke?: string }

function svg(children: ReactNode, { size = 19, sw = 1.6, stroke = "currentColor" }: IconProps = {}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

export const HomeIcon = (p: IconProps) => svg(<><path d="M3 9.5 12 3l9 6.5" /><path d="M5 9.5V21h14V9.5" /></>, p);
export const TreeIcon = (p: IconProps) => svg(<><circle cx="12" cy="5" r="2.4" /><circle cx="6" cy="19" r="2.4" /><circle cx="18" cy="19" r="2.4" /><path d="M12 7.4v3.6M12 11h-6v5.6M12 11h6v5.6" /></>, p);
export const SparkIcon = (p: IconProps) => svg(<><path d="M12 3.5l1.7 4.6 4.8 1.7-4.8 1.7L12 16.1l-1.7-4.6L5.5 9.8l4.8-1.7z" /><path d="M18.5 16l.7 1.9 1.8.7-1.8.7-.7 1.9-.7-1.9-1.8-.7 1.8-.7z" /></>, p);
export const BookIcon = (p: IconProps) => svg(<><path d="M5 4.5h11a1.5 1.5 0 0 1 1.5 1.5v14H6.5A1.5 1.5 0 0 1 5 18.5z" /><path d="M5 18.5A1.5 1.5 0 0 1 6.5 17h11" /></>, p);
export const SearchIcon = (p: IconProps) => svg(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.6-3.6" /></>, p);
export const GearIcon = (p: IconProps) => svg(<><circle cx="12" cy="12" r="3" /><path d="M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M17.7 6.3l-1.4 1.4M7.7 16.3l-1.4 1.4" /></>, p);
export const SunIcon = (p: IconProps) => svg(<><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" /></>, p);
export const MoonIcon = (p: IconProps) => svg(<path d="M20 14.5A8 8 0 1 1 9.5 4 6.5 6.5 0 0 0 20 14.5z" />, p);
export const ArrowRight = (p: IconProps) => svg(<path d="M5 12h14M13 6l6 6-6 6" />, { sw: 2, ...p });
export const ArrowLeft = (p: IconProps) => svg(<path d="M19 12H5M11 6l-6 6 6 6" />, { sw: 2, ...p });
export const Check = (p: IconProps) => svg(<path d="M20 6 9 17l-5-5" />, { sw: 2.4, ...p });
export const Cross = (p: IconProps) => svg(<path d="M18 6 6 18M6 6l12 12" />, { sw: 2.2, ...p });
export const Plus = (p: IconProps) => svg(<path d="M12 5v14M5 12h14" />, { sw: 2, ...p });
export const ZoomIn = (p: IconProps) => svg(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3-3M11 8v6M8 11h6" /></>, { sw: 2, ...p });
export const SearchPlus = (p: IconProps) => svg(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>, { sw: 1.8, ...p });
