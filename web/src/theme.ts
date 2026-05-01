/* Theme registry + persistence.
 *
 * Themes live as CSS-variable blocks in styles.css; this module is just the
 * metadata + the `applyTheme` helper that toggles ``data-theme`` on <html>
 * and persists the choice to localStorage.
 */
export type ThemeId = "midnight" | "arctic" | "sunset" | "terminal" | "rosepine";

export type ThemeMeta = {
  id: ThemeId;
  name: string;
  hint: string;          // one-line description shown in the picker
  swatch: [string, string, string]; // bg, accent, fg — for the swatch chip
  scheme: "dark" | "light";
};

export const THEMES: ThemeMeta[] = [
  { id: "midnight", name: "Midnight",  hint: "default · violet on near-black",      swatch: ["#0c0d11", "#7c5cff", "#f4f4f5"], scheme: "dark"  },
  { id: "arctic",   name: "Arctic",    hint: "light · sky-blue on slate",            swatch: ["#f1f5f9", "#0284c7", "#0f172a"], scheme: "light" },
  { id: "sunset",   name: "Sunset",    hint: "warm · orange on plum",                swatch: ["#241526", "#f97316", "#fef3c7"], scheme: "dark"  },
  { id: "terminal", name: "Terminal",  hint: "phosphor · green-on-black, monospaced", swatch: ["#000000", "#00ffd1", "#00ff88"], scheme: "dark"  },
  { id: "rosepine", name: "Rosé Pine", hint: "muted · iris on byzantium",            swatch: ["#1f1d2e", "#c4a7e7", "#e0def4"], scheme: "dark"  },
];

const STORAGE_KEY = "localagent.theme";

export function applyTheme(id: ThemeId): void {
  document.documentElement.setAttribute("data-theme", id);
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* private mode — ignore */
  }
}

export function loadInitialTheme(): ThemeId {
  try {
    const saved = localStorage.getItem(STORAGE_KEY) as ThemeId | null;
    if (saved && THEMES.some((t) => t.id === saved)) return saved;
  } catch {
    /* ignore */
  }
  return "midnight";
}
