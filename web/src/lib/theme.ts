import { useEffect, useState } from "react";

export type ThemePref = "system" | "light" | "dark";
const KEY = "shield.theme";

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: ThemePref) {
  const el = document.documentElement;
  if (t === "system") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", t);
}

applyTheme(read());

export function useTheme(): [ThemePref, () => void] {
  const [t, setT] = useState<ThemePref>(read);
  useEffect(() => {
    applyTheme(t);
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* per-viewer convenience only */
    }
  }, [t]);
  const cycle = () => setT((x) => (x === "system" ? "dark" : x === "dark" ? "light" : "system"));
  return [t, cycle];
}
