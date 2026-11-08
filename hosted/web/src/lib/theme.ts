export const THEME_COOKIE = "theme";

export type Theme = "light" | "ocean";

/** T3's default is the light palette, without a data-theme-id. */
export function themeId(value: string | undefined): "ocean" | undefined {
  return value === "ocean" ? "ocean" : undefined;
}

export function applyTheme(theme: Theme) {
  document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=31536000; samesite=strict`;
  if (theme === "ocean") {
    document.documentElement.dataset.themeId = "ocean";
  } else {
    delete document.documentElement.dataset.themeId;
  }
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.themeId === "ocean" ? "ocean" : "light";
}
