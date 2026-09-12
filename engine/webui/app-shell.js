(() => {
  const STORAGE_KEY = "aurexvideo-theme";

  function storedTheme() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
    } catch (_) {
      return "light";
    }
  }

  function applyTheme(theme) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;
    document.body?.classList.toggle("theme-light", nextTheme === "light");
    try {
      window.localStorage.setItem(STORAGE_KEY, nextTheme);
    } catch (_) {
      // Theme still applies for this page when browser storage is unavailable.
    }
    document.querySelectorAll("[data-app-theme-toggle]").forEach((button) => {
      const symbol = button.querySelector(".aurex-app-theme-symbol");
      const label = button.querySelector(".aurex-app-theme-label");
      if (symbol) symbol.textContent = nextTheme === "dark" ? "☾" : "☼";
      if (label) label.textContent = nextTheme === "dark" ? "Dark" : "Light";
      button.setAttribute("aria-label", nextTheme === "dark" ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối");
      button.setAttribute("aria-pressed", String(nextTheme === "dark"));
    });
  }

  function bindThemeControls() {
    applyTheme(storedTheme());
    document.querySelectorAll("[data-app-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindThemeControls, { once: true });
  } else {
    bindThemeControls();
  }
})();
