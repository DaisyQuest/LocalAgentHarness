/* ThemeSwitcher — pill button with a popover swatch grid.
 *
 * Compact in the header (just shows the active swatch + name) but expands
 * into a hoverable list with theme previews. The applyTheme call updates
 * <html data-theme> and persists; everything else re-renders for free
 * because all our colors are CSS variables.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { applyTheme, loadInitialTheme, THEMES, type ThemeId, type ThemeMeta } from "./theme";

// Returns a tuple where the setter is strictly `(id: ThemeId) => void`
// (no React functional-updater form). Callers shouldn't need that here, and
// typing it strictly catches mistakes at the boundary.
export function useTheme(): [ThemeId, (id: ThemeId) => void] {
  const [theme, setTheme] = useState<ThemeId>(() => loadInitialTheme());
  useEffect(() => { applyTheme(theme); }, [theme]);
  const set = useCallback((id: ThemeId) => setTheme(id), []);
  return [theme, set];
}

export function ThemeSwitcher({ value, onChange }: { value: ThemeId; onChange: (id: ThemeId) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = THEMES.find((t) => t.id === value)!;

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-2.5 py-1 rounded-lg text-xs border border-ink-700 hover:border-accent transition"
        title="theme"
      >
        <Swatch theme={active} />
        <span className="text-fg-mute">{active.name}</span>
        <span className="text-fg-dim">▾</span>
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 bg-ink-900 border border-ink-700 rounded-xl p-2 z-40 shadow-glow animate-slide-up">
          <div className="text-[10px] uppercase tracking-wider text-fg-dim px-2 pt-1 pb-2">Theme</div>
          <div className="space-y-1">
            {THEMES.map((t) => (
              <button
                key={t.id}
                onClick={() => { onChange(t.id); setOpen(false); }}
                className={`w-full flex items-center gap-3 px-2 py-2 rounded-lg text-left transition ${
                  t.id === value ? "bg-ink-800 ring-1 ring-accent/40" : "hover:bg-ink-800"
                }`}
              >
                <Swatch theme={t} large />
                <div className="flex-1 min-w-0">
                  <div className="text-sm flex items-center gap-2">
                    {t.name}
                    {t.scheme === "light" && <span className="text-[9px] uppercase tracking-wide text-fg-dim">light</span>}
                  </div>
                  <div className="text-xs text-fg-dim truncate">{t.hint}</div>
                </div>
                {t.id === value && <span className="text-accent text-xs">●</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Swatch({ theme, large }: { theme: ThemeMeta; large?: boolean }) {
  const size = large ? "w-7 h-7" : "w-5 h-5";
  return (
    <div
      className={`${size} rounded-md overflow-hidden border border-ink-700 shrink-0 grid grid-cols-2`}
      style={{ background: theme.swatch[0] }}
    >
      <div style={{ background: theme.swatch[0] }} />
      <div style={{ background: theme.swatch[1] }} />
      <div style={{ background: theme.swatch[2] }} />
      <div style={{ background: theme.swatch[1] }} />
    </div>
  );
}
