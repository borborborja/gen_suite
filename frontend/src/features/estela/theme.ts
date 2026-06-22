// Estela theme tokens — ported from the Estela.dc.html design (light/dark warm palette).
import type { CSSProperties } from "react";

export type ThemeMode = "light" | "dark";

export type ThemeVars = Record<string, string>;

export function themeVars(mode: ThemeMode, accent?: string | null): ThemeVars {
  if (mode === "dark") {
    return {
      "--bg": "#0a0a0f",
      "--surface": "#13131c",
      "--elevated": "#1b1b26",
      "--fg": "#f0ede8",
      "--muted": "#8f897f",
      "--line": "rgba(255,255,255,0.12)",
      "--line2": "rgba(255,255,255,0.06)",
      "--accent": accent || "#ff6b35",
      "--gold": "#ffd23f",
      "--ok": "#5cc08a",
      "--warn": "#f0b341",
      "--terra": "#d8743f",
      "--danger": "#e15a4d",
      "--ok-faint": "rgba(92,192,138,0.1)",
      "--warn-faint": "rgba(240,179,65,0.12)",
      "--accent-faint": "rgba(255,107,53,0.14)",
      "--shadow": "0 8px 32px rgba(0,0,0,0.5)",
    };
  }
  return {
    "--bg": "#f4f0e8",
    "--surface": "#fbf8f2",
    "--elevated": "#ffffff",
    "--fg": "#26211b",
    "--muted": "#7d756a",
    "--line": "rgba(38,33,27,0.13)",
    "--line2": "rgba(38,33,27,0.06)",
    "--accent": accent || "#d9531e",
    "--gold": "#bb7d1a",
    "--ok": "#2f7d54",
    "--warn": "#bb7d1a",
    "--terra": "#b5532a",
    "--danger": "#c0392b",
    "--ok-faint": "rgba(47,125,84,0.09)",
    "--warn-faint": "rgba(187,125,26,0.1)",
    "--accent-faint": "rgba(217,83,30,0.08)",
    "--shadow": "0 3px 16px rgba(50,35,15,0.1)",
  };
}

export function rootStyle(mode: ThemeMode, accent?: string | null): CSSProperties {
  return {
    ...(themeVars(mode, accent) as CSSProperties),
    display: "flex",
    minHeight: "100vh",
    width: "100%",
    background: "var(--bg)",
    color: "var(--fg)",
    fontFamily: "'DM Sans',sans-serif",
  };
}

export const fonts = {
  serif: "'Playfair Display',serif",
  sans: "'DM Sans',sans-serif",
  mono: "'DM Mono',monospace",
};
