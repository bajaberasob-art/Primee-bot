/* GENERATED FROM tokens.json -- DO NOT EDIT. Run scripts/build-tokens.mjs. */
// Portable design tokens (colors as hex). Web consumes the theme via
// src/index.css; mobile (Expo) and any other platform import this object so the
// whole product shares one source of truth.
export const tokens = {
  "color": {
    "light": {
      "primary": "#1d4ed8",
      "secondary": "#e6ebfa",
      "accent": "#ddf4ff",
      "background": "#f7fafc",
      "foreground": "#101827",
      "border": "#d6e0ed",
      "card": "#ffffff",
      "cardForeground": "#101827",
      "popover": "#ffffff",
      "popoverForeground": "#101827",
      "primaryForeground": "#ffffff",
      "secondaryForeground": "#172554",
      "muted": "#f1f5f9",
      "mutedForeground": "#475569",
      "accentForeground": "#075985",
      "destructive": "#b91c1c",
      "destructiveForeground": "#ffffff",
      "input": "#cbd5e1",
      "ring": "#2563eb",
      "chart1": "#0277bd",
      "chart2": "#0f766e",
      "chart3": "#b45309",
      "chart4": "#d92d20",
      "chart5": "#6d28d9",
      "sidebar": "#edf3fa",
      "sidebarForeground": "#1e2b3c",
      "sidebarBorder": "#d6e0ed",
      "sidebarPrimary": "#1d4ed8",
      "sidebarPrimaryForeground": "#ffffff",
      "sidebarAccent": "#ddf4ff",
      "sidebarAccentForeground": "#075985",
      "sidebarRing": "#2563eb"
    },
    "dark": {
      "primary": "#3b82f6",
      "secondary": "#5865f2",
      "accent": "#42b9ff",
      "background": "#05070b",
      "foreground": "#f1f5f9",
      "border": "#1e293b",
      "card": "#0a0e17",
      "cardForeground": "#f1f5f9",
      "popover": "#111827",
      "popoverForeground": "#f1f5f9",
      "primaryForeground": "#05070b",
      "secondaryForeground": "#ffffff",
      "muted": "#111827",
      "mutedForeground": "#a3b0c2",
      "accentForeground": "#062030",
      "destructive": "#ef4444",
      "destructiveForeground": "#05070b",
      "input": "#1e293b",
      "ring": "#42b9ff",
      "chart1": "#42b9ff",
      "chart2": "#10b981",
      "chart3": "#f59e0b",
      "chart4": "#ff647c",
      "chart5": "#a78bfa",
      "sidebar": "#080b12",
      "sidebarForeground": "#f1f5f9",
      "sidebarBorder": "#1e293b",
      "sidebarPrimary": "#3b82f6",
      "sidebarPrimaryForeground": "#05070b",
      "sidebarAccent": "#111827",
      "sidebarAccentForeground": "#e2e8f0",
      "sidebarRing": "#42b9ff"
    }
  },
  "fontFamily": {
    "sans": [
      "DejaVu Sans",
      "PRIME Noto Emoji",
      "Arial",
      "sans-serif"
    ],
    "serif": [
      "Georgia",
      "serif"
    ],
    "mono": [
      "ui-monospace",
      "SFMono-Regular",
      "Consolas",
      "monospace"
    ]
  },
  "radius": "1rem",
  "spacing": "0.25rem"
} as const;

export type Tokens = typeof tokens;
export default tokens;
