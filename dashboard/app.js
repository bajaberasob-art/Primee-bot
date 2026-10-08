(() => {
  "use strict";
  // DOM helpers
  const $ = (s, p = document) => p.querySelector(s);
  const urlMountPrefix = location.pathname === "/api" || location.pathname.startsWith("/api/") ? "/api" : "";
  const PUBLIC_SLUG_RE = /^(?=.{3,40}$)[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const el = (tag, props = {}, ...children) => {
    const n = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
    });
    children
      .flat()
      .forEach((c) => {
        if (c == null || c === false) return;
        n.append(c instanceof Node ? c : document.createTextNode(c));
      });
    return n;
  };
  // State
  const state = {
    session: null,
    guild: null,
    meta: null,
    incidents: [],
    actions: [],
    stats: null,
    whitelist: [],
    lockdown: false,
    protectedChannels: [],
    incidentTimer: null,
    baseline: null,
    draft: null,
    revision: null,
    updated: null,
    onboarding: null,
    commandStudio: { commands: [], roles: [], channels: [] },
    commandRegistry: { categories: [], commands: [], policies: {}, byKey: {} },
    autoResponses: [],
    autoResponderMeta: { roles: [], emojis: [], members: [] },
    commandSearch: "",
    commandCogFilter: "all",
    commandStatusFilter: "all",
    commandRoleFilter: "all",
    commandTab: "commands",
    commandCollapsedGroups: {},
    selectedCommandIds: [],
    commandSimulatorText: "",
    commandDetail: null,
    tickets: {
      active: [],
      archive: [],
      kpis: [],
      canned: [],
    },
    ticketTab: sessionStorage.getItem("ticket-tab") || "overview",
    ticketPanels: [],
    ticketRatings: [],
    ticketAnalytics: { overview: {}, priorities: [], ratings: [], staff: [], activity: [] },
    ticketBlacklist: [],
    ticketSettings: null,
    ticketPermissions: {},
    gaming: [],
    clanOps: {
      applications: [],
      roster: [],
      scrims: [],
      dropdown: { config: {}, categories: [] },
    },
    broadcast: {
      history: [],
      draft: {
        mode: "embed",
        channel_id: "",
        mention_type: "none",
        content: "",
        title: "",
        description: "",
        color: "#6366F1",
        thumbnail_url: "",
        image_url: "",
        footer: "PR1ME TEAM Official",
      },
    },
    economy: { wealth: [], settings: null, multipliers: {} },
    subscriptionDashboard: {
      data: null,
      loading: false,
      error: "",
      guildId: null,
      tab: sessionStorage.getItem("subscription-tab") || "overview",
      query: "",
      status: "all",
    },
    logRouting: { channels: {} },
    ticketSearch: "",
    ticketStatusFilter: "all",
    ticketConfig: {
      embed_title: "مركز الدعم والتذاكر",
      embed_description: "اختر التصنيف الأقرب لطلبك لفتح قناة خاصة مع فريق الدعم.",
       embed_color: 0x6366F1,
      footer_text: "Help Desk • اختر تصنيفاً لبدء المحادثة",
      channel_id: null,
      message_id: null,
    },
    ticketDropdown: {
      config: {
        embed_title: "مركز الدعم والتذاكر",
        embed_description: "اختر التصنيف الأقرب لطلبك لفتح قناة خاصة مع فريق الدعم.",
         embed_color: "#6366F1",
        footer_text: "Help Desk • اختر تصنيفاً لبدء المحادثة",
        channel_id: null,
        message_id: null,
      },
      categories: [],
    },
    ticketCategories: [
      { key: "general", label: "الدعم العام", description: "للاستفسارات العامة، الاقتراحات، أو المشاكل التقنية", emoji: "D", support_role_ids: [], senior_role_ids: [] },
      { key: "girls-verification", label: "توثيق البنات", description: "يتم توثيقك وتمييزك عن باقي الأعضاء", emoji: "V", support_role_ids: [], senior_role_ids: [] },
      { key: "rewards", label: "المكافآت والجوائز", description: "لاستلام جوائز المسابقات الخاصة بPR1ME", emoji: "R", support_role_ids: [], senior_role_ids: [] },
      { key: "content-creators", label: "برنامج صناع المحتوى", description: "للحصول على رتبة صانع محتوى ومزايا خاصة", emoji: "C", support_role_ids: [], senior_role_ids: [] },
      { key: "clan-application", label: "التقديم للكلان", description: "طلبات الانضمام إلى الكلان", emoji: "P", support_role_ids: [], senior_role_ids: [] },
    ],
    selfRoleBuilder: null,
    onboardingPreviewTimer: null,
    fields: {},
    saving: false,
    online: navigator.onLine,
    failures: 0,
    source: null,
    newer: false,
    activeView: sessionStorage.getItem("dashboard-view") || "overview",
    aiControlCleanup: null,
    themeTokens: null,
    themeSaved: null,
    themeDraft: null,
    themeDirty: false,
    themeSaving: false,
    themeError: "",
    drawerOpen: false,
    overviewRange: sessionStorage.getItem("overview-range") || "7d",
    overviewHeatMode: "written",
    analytics: null,
    analyticsRange: sessionStorage.getItem("analytics-range") || "7d",
    analyticsHeatMode: "written",
    analyticsLoading: false,
  };
  // Public lookup maps keep Discord snowflakes out of labels while preserving
  // the string IDs used by every existing API form and handler.
  window.guildChannels = Object.create(null);
  window.guildRoles = Object.create(null);
  let deferredInstallPrompt = null;
  let installBanner = null;

  function mobileInstallContext() {
    const standalone = window.matchMedia?.("(display-mode: standalone)")?.matches
      || window.navigator.standalone === true;
    const mobile = window.matchMedia?.("(max-width: 768px)")?.matches
      || /Android|iPhone|iPad|iPod/i.test(window.navigator.userAgent || "");
    return mobile && !standalone;
  }

  function showInstallBanner() {
    if (!deferredInstallPrompt || !mobileInstallContext() || installBanner) return;
    installBanner = el(
      "aside",
      { class: "pwa-install-pill", id: "pwa-install-banner", role: "status" },
      el("span", { text: "تثبيت التطبيق على هاتفك" }),
      el("button", {
        class: "pwa-install-action",
        type: "button",
        text: "تثبيت",
        onClick: async () => {
          const prompt = deferredInstallPrompt;
          deferredInstallPrompt = null;
          installBanner?.remove();
          installBanner = null;
          if (!prompt) return;
          await prompt.prompt();
          await prompt.userChoice.catch(() => null);
        },
      }),
      el("button", {
        class: "pwa-install-dismiss",
        type: "button",
        "aria-label": "إخفاء رسالة التثبيت",
        text: "×",
        onClick: () => {
          installBanner?.remove();
          installBanner = null;
        },
      }),
    );
    document.body.append(installBanner);
  }

  function setupPwa() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {
        // The dashboard remains fully functional when a browser blocks SWs.
      });
    }
    addEventListener("beforeinstallprompt", (event) => {
      event.preventDefault();
      deferredInstallPrompt = event;
      showInstallBanner();
    });
    addEventListener("appinstalled", () => {
      deferredInstallPrompt = null;
      installBanner?.remove();
      installBanner = null;
    });
  }
  const keys = [
    "prefix",
    "anti_nuke",
    "anti_alt_days",
    "captcha_enabled",
    "captcha_role_id",
    "management_role_ids",
    "auto_role_id",
    "leave_channel_id",
    "member_auto_role_id",
    "bot_auto_role_id",
    "verified_role_id",
    "unverified_role_id",
    "rules_channel_id",
    "welcome_dm_enabled",
    "welcome_channel_id",
    "leave_channel_id",
    "welcome_message",
    "leave_message",
    "welcome_embed_enabled",
    "welcome_embed_color",
    "welcome_embed_title",
    "welcome_embed_description",
    "welcome_embed_image_url",
    "welcome_embed_sticker_id",
    "welcome_embed_footer",
    "welcome_embed_show_avatar",
    "log_channel_id",
    "anti_spam_enabled",
    "anti_link_enabled",
    "anti_invites",
    "anti_links",
    "anti_spam",
    "anti_mass_mention",
    "anti_spam_max_messages",
    "anti_spam_time_window_seconds",
    "anti_spam_action",
    "anti_spam_timeout_duration_minutes",
    "anti_spam_ignored_role_ids",
    "anti_spam_ignored_channel_ids",
    "anti_mention_max_per_message",
    "anti_mention_target_enabled",
    "anti_mention_target_max_repeats",
    "anti_mention_target_time_window_seconds",
    "anti_mention_action",
    "anti_mention_timeout_duration_minutes",
    "anti_mention_ignored_role_ids",
    "anti_mention_ignored_channel_ids",
    "banned_words_list",
    "economy_tax",
    "daily_amount",
  ];
  const onboardingKeys = [
    "welcome_channel_id",
    "welcome_message",
    "leave_message",
    "welcome_dm_enabled",
    "welcome_embed_enabled",
    "welcome_embed_color",
    "welcome_embed_title",
    "welcome_embed_description",
    "welcome_embed_image_url",
    "welcome_embed_sticker_id",
    "welcome_embed_footer",
    "welcome_embed_show_avatar",
    "auto_role_id",
    "member_auto_role_id",
    "bot_auto_role_id",
    "verified_role_id",
    "unverified_role_id",
    "rules_channel_id",
  ];
  const settingsKeys = keys.filter((key) => !onboardingKeys.includes(key));
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const sameValue = (a, b) =>
    a === b ||
    (a != null &&
      b != null &&
      typeof a === "object" &&
      typeof b === "object" &&
      JSON.stringify(a) === JSON.stringify(b));
  const changes = () =>
    !state.baseline || !state.draft
      ? {}
      : Object.fromEntries(
          settingsKeys
            .filter((k) => !sameValue(state.baseline[k], state.draft[k]))
            .map((k) => [k, state.draft[k]]),
        );
  const onboardingChanges = () =>
    !state.baseline || !state.draft
      ? {}
      : Object.fromEntries(
          onboardingKeys
            .filter((k) => !sameValue(state.baseline[k], state.draft[k]))
            .map((k) => [k, state.draft[k]]),
        );
  const dirty = () => Object.keys(changes()).length > 0;
  const onboardingDirty = () => Object.keys(onboardingChanges()).length > 0;
  // اعتماد نسخة أحدث من الخادم مع الإبقاء على تعديلات المستخدم فقط (لا على القيم القديمة غير المعدّلة)
  function adopt(snapshot, keepLocal = true) {
    const local = keepLocal ? { ...changes(), ...onboardingChanges() } : {};
    state.baseline = clone(snapshot.settings);
    state.revision = snapshot.revision;
    state.updated = snapshot.updated_at;
    state.draft = { ...clone(snapshot.settings), ...local };
    state.newer = keepLocal && Object.keys(local).length > 0;
  }
  const redirect = () => location.assign("login");
  const app = $("#app");
  // API and feedback
  function toast(message, type = "error", life = 4000) {
    const old = $(".toast");
    if (old) old.remove();
    const t = el("div", {
      class: `toast ${type}`,
      role: "status",
      text: message,
    });
    document.body.append(t);
    if (life) setTimeout(() => t.remove(), life);
  }
  async function api(url, opts = {}) {
    const r = await fetch(url, opts);
    if (r.status === 401) {
      redirect();
      throw Error("unauth");
    }
    return r;
  }
  async function readJson(response, fallback = {}) {
    if (!response?.ok) return fallback;
    const contentType = response.headers?.get("content-type") || "";
    if (!contentType.toLowerCase().includes("application/json")) return fallback;
    try {
      return await response.json();
    } catch (_) {
      return fallback;
    }
  }
  async function optionalJson(url, fallback = {}) {
    try {
      return await readJson(await api(url), fallback);
    } catch (error) {
      if (error.message === "unauth") throw error;
      return fallback;
    }
  }
  async function refreshSession() {
    const r = await api("api/me", { cache: "no-store" });
    const data = await readJson(r, {});
    if (!data.auth || !data.session) return false;
    state.session = data.session;
    return true;
  }
  async function writeApi(url, body, retry = true) {
    try {
      const options = {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": state.session.csrf,
        },
        body: JSON.stringify(body),
      };
      let response = await api(url, options);
      if (response.status === 403 && retry) {
        const data = await readJson(response, {});
        if (data.error === "csrf" && await refreshSession()) {
          options.headers["X-CSRF-Token"] = state.session.csrf;
          response = await api(url, options);
        }
      }
      return response;
    } catch (error) {
      if (error.message !== "unauth") {
        toast("تعذر الاتصال بالخادم. تحقق من الاتصال وحاول مجدداً", "warn");
      }
      throw error;
    }
  }
  function primeAIRequest(method, url, body) {
    if (method === "GET") return api(url, { cache: "no-store" });
    if (method === "POST") return writeApi(url, body || {});
    return Promise.reject(new Error("unsupported_method"));
  }
  const THEME_PRESET_SOURCES = [
    ["prime-blue", "أزرق PRIME", "primary", "secondary"],
    ["indigo", "نيلي", "secondary", "accent"],
    ["sky", "سماوي", "accent", "chart2"],
    ["emerald", "زمردي", "chart2", "chart3"],
    ["amber", "عنبر", "chart3", "chart4"],
    ["rose", "وردي", "chart4", "chart5"],
    ["violet", "بنفسجي", "chart5", "destructive"],
    ["crimson", "قرمزي", "destructive", "primary"],
    ["ice-blue", "أزرق جليدي", "foreground", "accent"],
    ["muted-silver", "فضي", "mutedForeground", "foreground"],
    ["blue-amber", "أزرق وعنبر", "primary", "chart3"],
    ["indigo-emerald", "نيلي وزمردي", "secondary", "chart2"],
    ["sky-violet", "سماوي وبنفسجي", "accent", "chart5"],
    ["emerald-rose", "زمردي ووردي", "chart2", "chart4"],
    ["amber-indigo", "عنبر ونيلي", "chart3", "secondary"],
    ["rose-sky", "وردي وسماوي", "chart4", "accent"],
    ["violet-blue", "بنفسجي وأزرق", "chart5", "primary"],
    ["crimson-amber", "قرمزي وعنبر", "destructive", "chart3"],
    ["ice-indigo", "جليدي ونيلي", "foreground", "secondary"],
    ["silver-violet", "فضي وبنفسجي", "mutedForeground", "chart5"],
  ];
  const THEME_COLOR_FIELDS = [
    ["primary", "لون التمييز والأزرار"],
    ["secondary", "اللون الثانوي"],
    ["background", "خلفية اللوحة"],
    ["surface", "البطاقات والأسطح"],
    ["surfaceAlt", "السطح الثانوي"],
    ["text", "النص"],
    ["muted", "النص الثانوي"],
    ["border", "الحدود"],
  ];
  function themeTokenValue(mode, role) {
    return state.themeTokens?.color?.[mode]?.[role]?.$value || "";
  }
  function buildDashboardTheme(mode, primary, secondary, preset = "custom", buttonStyle = "solid") {
    return {
      preset,
      primary,
      secondary,
      background: themeTokenValue(mode, "background"),
      surface: themeTokenValue(mode, "card"),
      surfaceAlt: themeTokenValue(mode, "popover"),
      text: themeTokenValue(mode, "foreground"),
      muted: themeTokenValue(mode, "mutedForeground"),
      border: themeTokenValue(mode, "border"),
      buttonStyle,
    };
  }
  function systemDashboardTheme() {
    return buildDashboardTheme(
      "dark",
      themeTokenValue("dark", "primary"),
      themeTokenValue("dark", "secondary"),
      "prime-default",
    );
  }
  function tokenFontStack(name) {
    return (state.themeTokens?.typography?.fontFamily?.[name]?.$value || [])
      .map((family) => /^[a-z-]+$/i.test(family) ? family : `"${String(family).replaceAll('"', "")}"`)
      .join(", ");
  }
  function applyDashboardFoundationTokens() {
    const root = document.documentElement;
    const sans = tokenFontStack("sans");
    const mono = tokenFontStack("mono");
    const radius = state.themeTokens?.radius?.base?.$value;
    const spacing = state.themeTokens?.spacing?.base?.$value;
    if (!sans || !mono || !radius || !spacing) throw Error("theme_tokens_invalid");
    root.style.setProperty("--prime-font-sans", sans);
    root.style.setProperty("--prime-font-mono", mono);
    root.style.setProperty("--prime-radius-base", radius);
    root.style.setProperty("--prime-spacing-base", spacing);
  }
  function dashboardThemePresets() {
    return THEME_PRESET_SOURCES.map(([id, label, primaryRole, secondaryRole]) => {
      const theme = buildDashboardTheme(
        "dark",
        themeTokenValue("dark", primaryRole),
        themeTokenValue("dark", secondaryRole),
        id,
      );
      return { id, label, theme };
    });
  }
  function applyDashboardTheme(theme) {
    const root = document.documentElement;
    if (!theme) {
      root.classList.remove("dashboard-theme-enabled");
      delete root.dataset.dashboardButtonStyle;
      [
        "--bg", "--surface", "--surface-2", "--line", "--text", "--muted",
        "--blue", "--blurple", "--overview-ink", "--overview-muted",
        "--overview-panel", "--overview-panel-raised", "--overview-border",
        "--overview-blue", "--overview-cyan", "--prime-bg-base",
        "--prime-bg-surface", "--prime-bg-card", "--prime-indigo",
        "--prime-purple", "--prime-border", "--theme-action-foreground",
      ].forEach((name) => root.style.removeProperty(name));
      return;
    }
    const mapped = {
      "--bg": theme.background,
      "--surface": theme.surface,
      "--surface-2": theme.surfaceAlt,
      "--line": theme.border,
      "--text": theme.text,
      "--muted": theme.muted,
      "--blue": theme.primary,
      "--blurple": theme.secondary,
      "--overview-ink": theme.text,
      "--overview-muted": theme.muted,
      "--overview-panel": theme.surface,
      "--overview-panel-raised": theme.surfaceAlt,
      "--overview-border": theme.border,
      "--overview-blue": theme.primary,
      "--overview-cyan": theme.secondary,
      "--prime-bg-base": theme.background,
      "--prime-bg-surface": theme.surface,
      "--prime-bg-card": theme.surface,
      "--prime-indigo": theme.primary,
      "--prime-purple": theme.secondary,
      "--prime-border": theme.border,
      "--theme-action-foreground": themeActionForeground(theme.primary),
    };
    root.classList.add("dashboard-theme-enabled");
    root.dataset.dashboardButtonStyle = theme.buttonStyle;
    Object.entries(mapped).forEach(([name, value]) => root.style.setProperty(name, value));
  }
  function themeActionForeground(background) {
    const dark = themeTokenValue("dark", "background");
    const light = themeTokenValue("light", "primaryForeground");
    const toLuminance = (hex) => {
      const channels = hex.match(/[0-9a-f]{2}/gi)?.map((part) => parseInt(part, 16) / 255) || [];
      if (channels.length !== 3) return 0;
      const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    };
    const bg = toLuminance(background);
    const darkL = toLuminance(dark), lightL = toLuminance(light);
    const contrast = (luminance) => (Math.max(bg, luminance) + 0.05) / (Math.min(bg, luminance) + 0.05);
    return contrast(darkL) >= contrast(lightL) ? dark : light;
  }
  async function loadDashboardTheme() {
    const [tokensResponse, themeResponse] = await Promise.all([
      api("api/design-system/tokens", { cache: "no-store" }),
      api("api/user/theme", { cache: "no-store" }),
    ]);
    const [tokens, saved] = await Promise.all([
      readJson(tokensResponse, {}),
      readJson(themeResponse, {}),
    ]);
    const sans = tokens.typography?.fontFamily?.sans?.$value;
    const mono = tokens.typography?.fontFamily?.mono?.$value;
    if (
      !tokensResponse.ok || !tokens.color?.dark || !tokens.color?.light || !themeResponse.ok
      || !Array.isArray(sans) || !sans.length
      || !Array.isArray(mono) || !mono.length
      || typeof tokens.radius?.base?.$value !== "string"
      || typeof tokens.spacing?.base?.$value !== "string"
    ) {
      throw Error("theme_load_failed");
    }
    state.themeTokens = tokens;
    applyDashboardFoundationTokens();
    state.themeSaved = saved.theme && typeof saved.theme === "object" ? saved.theme : null;
    state.themeDraft = state.themeSaved ? clone(state.themeSaved) : null;
    state.themeDirty = false;
    applyDashboardTheme(state.themeSaved);
  }
  // Shared components
  function avatar(src, name) {
    const a = el("div", { class: "avatar" });
    if (src) {
      const im = el("img", { src, alt: "" });
      im.onerror = () => {
        im.remove();
        a.textContent = (name || "?").slice(0, 1);
      };
      a.append(im);
    } else a.textContent = (name || "?").slice(0, 1);
    return a;
  }
  function guildIcon(g) {
    const i = el("span", { class: "guild-icon" });
    if (g.icon) {
      const im = el("img", { src: g.icon, alt: "" });
      im.onerror = () => {
        im.remove();
        i.textContent = g.name.slice(0, 1);
      };
      i.append(im);
    } else i.textContent = g.name.slice(0, 1);
    return i;
  }
  function header() {
    const h = el("header", { class: "topbar" }),
      inr = el("div", { class: "topbar-inner" });
    inr.append(
      el(
        "button",
        {
          class: "menu-toggle",
          type: "button",
          "aria-label": "فتح قائمة الأقسام",
          "aria-expanded": String(state.drawerOpen),
          onClick: () => toggleDrawer(),
        },
        el("span", { text: "☰" }),
      ),
      el(
        "div",
        { class: "brand" },
        el("strong", { text: "PRIME | TEAM" }),
        el("i", { text: "تطوير abood2026" }),
      ),
      el("div", { class: "head-grow" }),
    );
    const picker = el("div", { class: "server-picker" }),
      btn = el("button", {
        class: "picker-button",
        "aria-expanded": "false",
        type: "button",
      });
    const setBtn = () => {
      btn.replaceChildren(
        el(
          "span",
          { class: "guild-value" },
          guildIcon(state.guild),
          el("span", { class: "ell", text: state.guild.name }),
        ),
        el("span", { text: "⌄" }),
      );
    };
    setBtn();
    const menu = el("div", { class: "popover", hidden: true });
    state.session.guilds.forEach((g) => {
      const o = el(
        "button",
        { class: "option", type: "button", onClick: () => chooseGuild(g.id) },
        guildIcon(g),
        el("span", { class: "ell", text: g.name }),
      );
      menu.append(o);
    });
    btn.onclick = () => {
      const open = menu.hidden;
      menu.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    };
    picker.append(btn, menu);
    const ping = el("div", { class: "ping", id: "ping" });
    inr.append(
      picker,
      ping,
      el(
        "div",
        { class: "user" },
        avatar(state.session.avatar, state.session.username),
        el("span", { text: state.session.username }),
        el("a", { class: "logout", href: "logout", text: "خروج" }),
      ),
    );
    h.append(inr);
    return h;
  }
  const viewLabels = {
    overview: { label: "نظرة عامة", icon: "⌂", hint: "مركز القيادة" },
    tickets: { label: "التذاكر", icon: "▣", hint: "Help Desk" },
    gaming: { label: "السكريمات", icon: "◉", hint: "Gaming Ops" },
    subscriptions: { label: "الاشتراكات", icon: "◈", hint: "Subscription Control" },
    clan: { label: "الكلان والتنافس", icon: "♛", hint: "Clan Ops" },
    broadcast: { label: "استوديو البث", icon: "✦", hint: "Broadcast Studio" },
    commands: { label: "الأوامر والأتمتة", icon: "⌘", hint: "Commands" },
    onboarding: { label: "الترحيب والأدوار", icon: "✦", hint: "Onboarding" },
    security: { label: "الحماية", icon: "◈", hint: "Security" },
    moderation: { label: "المراقبة", icon: "⚔", hint: "Moderation" },
    analytics: { label: "السجلات", icon: "◉", hint: "Analytics" },
    leveling: { label: "المستويات", icon: "✦", hint: "Levels" },
    economy: { label: "الاقتصاد", icon: "◌", hint: "Economy" },
    community: { label: "المجتمع", icon: "◎", hint: "Community" },
    ai: { label: "الذكاء الاصطناعي", icon: "✧", hint: "AI Tools" },
    talk: { label: "Talk", icon: "◉", hint: "محادثة PRIME" },
    appearance: { label: "المظهر الشخصي", icon: "◐", hint: "Personal Theme" },
    settings: { label: "الإعدادات", icon: "⚙", hint: "Configuration" },
    system: { label: "النظام", icon: "⌁", hint: "Runtime" },
  };
  function closeMobileMoreMenus() {
    document.querySelectorAll(".mobile-more-menu").forEach((menu) => {
      menu.hidden = true;
      menu.parentElement?.querySelector('[aria-expanded="true"]')?.setAttribute("aria-expanded", "false");
    });
  }
  function closeNavigationOverlays() {
    state.drawerOpen = false;
    closeMobileMoreMenus();
    document.querySelectorAll(".workspace-nav").forEach((nav) => {
      nav.classList.remove("drawer-open");
      nav.setAttribute("aria-hidden", "true");
    });
    document.querySelectorAll(".drawer-scrim").forEach((scrim) => {
      scrim.classList.remove("show");
    });
    document.body.classList.remove("drawer-visible");
    document.querySelectorAll(".menu-toggle").forEach((button) => {
      button.setAttribute("aria-expanded", "false");
    });
  }
  function navigateView(view) {
    if (!viewLabels[view]) return;
    // Tear down every live overlay before changing the page. This must happen
    // before renderPage so a stale scrim cannot capture the next tap/back gesture.
    closeNavigationOverlays();
    state.activeView = view;
    sessionStorage.setItem("dashboard-view", view);
    document.querySelectorAll("[data-nav-view]").forEach((item) => {
      item.classList.toggle("active", item.dataset.navView === view);
      item.setAttribute("aria-current", item.dataset.navView === view ? "page" : "false");
    });
    renderPage();
    window.scrollTo({ top: 0, behavior: "auto" });
  }
  function toggleDrawer(force = null) {
    state.drawerOpen = force == null ? !state.drawerOpen : Boolean(force);
    if (state.drawerOpen) closeMobileMoreMenus();
    $(".workspace-nav")?.classList.toggle("drawer-open", state.drawerOpen);
    $(".drawer-scrim")?.classList.toggle("show", state.drawerOpen);
    document.body.classList.toggle("drawer-visible", state.drawerOpen);
    $(".menu-toggle")?.setAttribute("aria-expanded", String(state.drawerOpen));
    $(".workspace-nav")?.setAttribute("aria-hidden", String(!state.drawerOpen));
    navigator.vibrate?.(12);
  }
  function openCommandPalette() {
    $(".command-palette-back")?.remove();
    const back = el("div", { class: "command-palette-back", role: "dialog", "aria-modal": "true" });
    const input = el("input", {
      class: "command-palette-input",
      type: "search",
      placeholder: "ابحث في أقسام مركز القيادة…",
      "aria-label": "بحث الأقسام",
    });
    const list = el("div", { class: "command-palette-list" });
    const renderMatches = () => {
      const query = input.value.trim().toLocaleLowerCase();
      list.replaceChildren(
        ...Object.entries(viewLabels)
          .filter(([, meta]) => !query || `${meta.label} ${meta.hint}`.toLocaleLowerCase().includes(query))
          .map(([view, meta]) => el(
            "button",
            {
              class: "command-palette-item",
              type: "button",
              onClick: () => {
                back.remove();
                navigateView(view);
              },
            },
            el("span", { class: "nav-icon", text: meta.icon }),
            el("span", {}, el("strong", { text: meta.label }), el("small", { text: meta.hint })),
          )),
      );
    };
    input.addEventListener("input", renderMatches);
    back.append(
      el(
        "div",
        { class: "command-palette", onClick: (event) => event.stopPropagation() },
        el("div", { class: "command-palette-head" }, el("strong", { text: "التنقل السريع" }), el("kbd", { text: "ESC" })),
        input,
        list,
      ),
    );
    back.addEventListener("click", () => back.remove());
    document.body.append(back);
    renderMatches();
    input.focus();
  }
  function navButton(view) {
    const meta = viewLabels[view];
    return el(
      "button",
      {
        class: `nav-item sidebar-item nav-link ${state.activeView === view ? "active" : ""}`,
        type: "button",
        "data-nav-view": view,
        "aria-current": state.activeView === view ? "page" : "false",
        onClick: () => navigateView(view),
      },
      el("span", { class: "nav-icon", text: meta.icon, "aria-hidden": "true" }),
      el("span", { class: "nav-copy" }, el("b", { text: meta.label }), el("small", { text: meta.hint })),
    );
  }
  function workspaceNav() {
    return el(
      "aside",
      {
        class: `workspace-nav ${state.drawerOpen ? "drawer-open" : ""}`,
        "aria-label": "التنقل الرئيسي",
        "aria-hidden": String(!state.drawerOpen),
      },
      el(
        "div",
        { class: "workspace-nav-head" },
        el("span", { class: "workspace-kicker", text: "PRIME / CONTROL" }),
        el("strong", { text: "مركز القيادة" }),
        el("small", { text: "إدارة البوت من مكان واحد" }),
      ),
      el(
        "nav",
        { class: "nav-list" },
        ...Object.keys(viewLabels).map((view) => navButton(view)),
      ),
      el(
        "div",
        { class: "workspace-nav-foot" },
        el("span", { class: "nav-status-dot" }),
        el("span", { text: state.online ? "الخدمات تعمل بشكل طبيعي" : "الاتصال يحتاج مراجعة" }),
      ),
    );
  }
  function mobileNav() {
    const primary = ["overview", "tickets", "commands"];
    const moreMenu = el(
      "div",
      { class: "mobile-more-menu", hidden: true },
      navButton("onboarding"),
      navButton("gaming"),
      navButton("subscriptions"),
      navButton("clan"),
      navButton("broadcast"),
      navButton("security"),
      navButton("moderation"),
      navButton("analytics"),
      navButton("leveling"),
      navButton("economy"),
      navButton("community"),
      navButton("ai"),
      navButton("talk"),
      navButton("appearance"),
      navButton("settings"),
      navButton("system"),
    );
    const moreButton = el(
      "button",
      {
         class: `nav-item ${["onboarding", "gaming", "subscriptions", "clan", "security", "moderation", "analytics", "leveling", "economy", "community", "ai", "talk", "appearance", "settings", "system"].includes(state.activeView) ? "active" : ""}`,
        type: "button",
        "aria-expanded": "false",
        onClick: () => {
          const open = moreMenu.hidden;
          moreMenu.hidden = !open;
          moreButton.setAttribute("aria-expanded", String(open));
        },
      },
      el("span", { class: "nav-icon", text: "•••", "aria-hidden": "true" }),
      el("span", { class: "nav-copy" }, el("b", { text: "المزيد" }), el("small", { text: "إدارة" })),
    );
    return el(
      "div",
      { class: "mobile-nav-wrap" },
      moreMenu,
      el("nav", { class: "mobile-nav", "aria-label": "التنقل السريع" }, ...primary.map((view) => navButton(view)), moreButton),
    );
  }
  function updatePing(kind = "online", latency = null) {
    const p = $("#ping");
    if (!p) return;
    p.replaceChildren(
      el("span", {
        class: `dot ${kind === "online" ? "" : kind === "wait" ? "wait" : "off"}`,
      }),
      document.createTextNode(
        kind === "online"
          ? `متصل ${latency == null ? "—" : latency + "ms"}`
          : kind === "wait"
            ? "إعادة الاتصال..."
            : "غير متصل",
      ),
    );
  }
  function renderShell() {
    const main = el(
      "main",
      { class: "page", id: "main" },
      el(
        "div",
        { class: "loading" },
        el("div", { class: "skeleton" }),
        el("p", { text: "جارٍ تحميل إعدادات السيرفر…" }),
      ),
    );
    app.replaceChildren(
      header(),
      el("div", { class: "workspace-layout" }, workspaceNav(), main),
      el("button", {
        class: "drawer-scrim",
        type: "button",
        "aria-label": "إغلاق قائمة الأقسام",
        onClick: () => toggleDrawer(false),
      }),
      mobileNav(),
    );
    updatePing(state.online ? "online" : "offline");
  }
  // Form components
  function field(label, node, key, hint = "") {
    const f = el("div", { class: "field" }),
      target = node.id || node.querySelector?.("[id]")?.id;
    f.append(el("label", { for: target, text: label }), node);
    if (hint) f.append(el("div", { class: "hint", text: hint }));
    f.append(el("div", { class: "field-error", id: `err-${key}` }));
    return f;
  }
  function input(key, label, type, attrs = {}) {
    const n = el("input", { id: `in-${key}`, type, ...attrs });
    n.value = state.draft[key] ?? "";
    n.addEventListener("input", () => {
      state.draft[key] =
        type === "number" ? (n.value === "" ? "" : Number(n.value)) : n.value;
      state.fields[key] = "";
      renderDynamic();
    });
    return field(label, n, key);
  }
  function settingSelect(key, label, options, hint = "") {
    const n = el("select", { id: `in-${key}` });
    options.forEach(([value, text]) =>
      n.append(el("option", { value, text })),
    );
    n.value = state.draft[key] ?? options[0]?.[0] ?? "";
    n.addEventListener("change", () => {
      state.draft[key] = n.value;
      state.fields[key] = "";
      if (key.endsWith("_action")) renderPage();
      else renderDynamic();
    });
    return field(label, n, key, hint);
  }
  function managementRoleSelect(tier, label) {
    const key = "management_role_ids";
    const select = el("select", { id: `in-management-role-${tier}` });
    select.append(el("option", { value: "", text: "غير محدد" }));
    Object.values(window.guildRoles)
      .sort((a, b) => Number(b.position || 0) - Number(a.position || 0))
      .forEach((role) => {
        select.append(el("option", { value: String(role.id), text: role.name }));
      });
    const current = state.draft.management_role_ids || {};
    select.value = String(current[tier] || "");
    select.addEventListener("change", () => {
      state.draft.management_role_ids = {
        admin: "",
        moderator: "",
        staff: "",
        ...(state.draft.management_role_ids || {}),
        [tier]: select.value,
      };
      state.fields[key] = "";
      renderDynamic();
    });
    return field(label, select, key);
  }
  function multiSettingSelect(key, label, type, hint = "") {
    const choices =
      type === "channel" ? Object.values(window.guildChannels) : Object.values(window.guildRoles);
    const selected = new Set(
      (Array.isArray(state.draft[key]) ? state.draft[key] : []).map(String),
    );
    const n = el("select", {
      id: `in-${key}`,
      class: "security-multi-select",
      multiple: true,
      size: Math.min(5, Math.max(3, choices.length || 3)),
    });
    choices.forEach((choice) => {
      const option = el("option", {
        value: choice.id,
        text: type === "channel" ? `#${choice.name}` : choice.name,
      });
      option.selected = selected.has(String(choice.id));
      n.append(option);
    });
    n.addEventListener("change", () => {
      state.draft[key] = [...n.selectedOptions].map((option) => option.value);
      state.fields[key] = "";
      renderDynamic();
    });
    return field(label, n, key, hint);
  }
  function securityAccordion(key, title, summary, content, open = true) {
    const panel = el(
      "div",
      { class: `security-accordion-panel${open ? " is-open" : ""}`, "aria-hidden": String(!open) },
      el("div", { class: "security-accordion-inner" }, content),
    );
    const button = el(
      "button",
      {
        class: "security-accordion-trigger",
        type: "button",
        "aria-expanded": String(open),
        "aria-controls": `security-panel-${key}`,
      },
      el("span", { class: "security-accordion-copy" },
        el("strong", { text: title }),
        el("small", { text: summary }),
      ),
      el("span", { class: "security-accordion-chevron", text: "⌄", "aria-hidden": "true" }),
    );
    panel.id = `security-panel-${key}`;
    button.onclick = () => {
      const next = !panel.classList.contains("is-open");
      panel.classList.toggle("is-open", next);
      panel.setAttribute("aria-hidden", String(!next));
      button.setAttribute("aria-expanded", String(next));
    };
    return el("section", { class: "security-accordion" }, button, panel);
  }
  function toggle(key, label) {
    const b = el("button", {
      class: "switch",
      type: "button",
      role: "switch",
      "aria-label": label,
      "aria-checked": String(!!state.draft[key]),
    });
    b.append(el("b"));
    const go = () => {
      state.draft[key] = !state.draft[key];
      b.setAttribute("aria-checked", String(state.draft[key]));
       if (
         key === "welcome_embed_enabled"
         || ["anti_spam", "anti_mass_mention", "anti_mention_target_enabled"].includes(key)
       ) renderPage();
      else {
        renderDynamic();
        refreshEmbedPreview();
      }
    };
    b.onclick = go;
    b.onkeydown = (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        go();
      }
    };
    return el("div", { class: "switch-row" }, el("label", { text: label }), b);
  }
  function selector(key, label, type) {
    const wrap = el("div", { class: "select-wrap" }),
      b = el("button", {
        class: "select-button",
        id: `in-${key}`,
        type: "button",
        "aria-expanded": "false",
      }),
      pop = el("div", { class: "popover", hidden: true }),
      search = el("input", {
        class: "search",
        type: "text",
        placeholder: "ابحث…",
        "aria-label": "بحث",
      }),
      list = el("div", { class: "options" });
    pop.append(search, list);
    wrap.append(b, pop);
    let choices =
        type === "channel"
          ? Object.values(window.guildChannels)
          : type === "sticker"
            ? state.meta.stickers || []
            : Object.values(window.guildRoles),
      active = 0;
    const value = () => state.draft[key];
    const nameFor = (id) =>
      choices.find((x) => String(x.id) === String(id));
    function display() {
      const found = nameFor(value());
      let current;
      if (found)
        current = el(
          "span",
          { class: "choice" },
          type === "channel"
            ? el("span", { text: "#" })
            : type === "sticker"
              ? el("span", { text: "✦" })
              : el("span", {
                class: "role-dot",
                style: `background:${found.color || "#64748b"}`,
              }),
          el("span", { class: "ell", text: found.name }),
        );
      else if (value()) current = el("span", { text: `غير موجود: ${value()}` });
      else current = el("span", { class: "choice none", text: "بدون" });
      b.classList.toggle("invalid", !!value() && !found);
      b.replaceChildren(current, el("span", { text: "⌄" }));
      const err = $(`#err-${key}`);
      if (err)
        err.textContent =
          value() && !found
            ? "العنصر المحفوظ لم يعد موجوداً. اختر قيمة أخرى."
            : state.fields[key] || "";
    }
    function select(id) {
      state.draft[key] = id;
      state.fields[key] = "";
      pop.hidden = true;
      b.setAttribute("aria-expanded", "false");
      display();
      renderDynamic();
    }
    function build() {
      choices =
        type === "channel"
          ? Object.values(window.guildChannels)
          : type === "sticker"
            ? state.meta.stickers || []
            : Object.values(window.guildRoles);
      const q = search.value.trim().toLowerCase();
      list.replaceChildren();
      const add = (x, txt, group) => {
        if (q && !x.name.toLowerCase().includes(q)) return;
        if (group) list.append(el("div", { class: "group", text: group }));
        const blocked = type === "role" && x.assignable === false;
        const o = el(
          "button",
          {
            class: blocked ? "option blocked" : "option",
            type: "button",
            disabled: blocked,
            title: blocked ? "أعلى من رتبة البوت أو رتبة مُدارة" : null,
            onClick: () => select(x.id),
          },
          type === "channel"
            ? el("span", { text: "#" })
            : type === "sticker"
              ? el("span", { text: "✦" })
              : el("span", {
                class: "role-dot",
                style: `background:${x.color || "#64748b"}`,
              }),
          el("span", { text: txt || x.name }),
          blocked ? el("small", { class: "hint", text: "غير متاحة للبوت" }) : [],
        );
        list.append(o);
      };
      const none = el("button", {
        class: "option none",
        type: "button",
        onClick: () => select(null),
        text: "بدون",
      });
      list.append(none);
      if (type === "channel") {
        let last;
        choices.forEach((x) => {
          const group = x.category || "قنوات أخرى";
          add(x, x.name, group !== last ? group : null);
          last = group;
        });
      } else choices.forEach((x) => add(x));
      if (list.children.length === 1)
        list.append(
          el("div", { class: "empty-row", text: "لا توجد نتائج مطابقة" }),
        );
      active = 0;
    }
    function open() {
      build();
      pop.hidden = false;
      b.setAttribute("aria-expanded", "true");
      search.focus();
    }
    function close() {
      pop.hidden = true;
      b.setAttribute("aria-expanded", "false");
    }
    b.onclick = () => (pop.hidden ? open() : close());
    search.oninput = build;
    pop.onkeydown = (e) => {
      const opts = [...list.querySelectorAll(".option")];
      if (e.key === "Escape") {
        close();
        b.focus();
      }
      if (["ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        active =
          (active + (e.key === "ArrowDown" ? 1 : -1) + opts.length) %
          opts.length;
        opts.forEach((x, i) => x.classList.toggle("active", i === active));
        opts[active]?.focus();
      }
      if (e.key === "Enter" && document.activeElement === search)
        opts[active]?.click();
    };
    display();
    return field(label, wrap, key);
  }
  function appendSafeMarkdown(parent, source) {
    const lines = String(source || "").split("\n");
    const tokenPattern = /(\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|`[^`\n]+`)/g;
    lines.forEach((line, lineIndex) => {
      let cursor = 0;
      for (const match of line.matchAll(tokenPattern)) {
        const start = match.index || 0;
        if (start > cursor) parent.append(document.createTextNode(line.slice(cursor, start)));
        const token = match[0];
        const inner = token.slice(token.startsWith("`") ? 1 : 2, token.startsWith("`") ? -1 : -2);
        const tag = token.startsWith("`")
          ? "code"
          : token.startsWith("**") || token.startsWith("__")
            ? "strong"
            : "em";
        parent.append(el(tag, { text: inner }));
        cursor = start + token.length;
      }
      if (cursor < line.length) parent.append(document.createTextNode(line.slice(cursor)));
      if (lineIndex < lines.length - 1) parent.append(el("br"));
    });
  }
  function onboardingTemplate() {
    const guild = state.guild || {};
    return String(state.draft?.welcome_message || "")
      .replace(/\{user\}/g, "@عضو_جديد")
      .replace(/\{username\}/g, "عضو جديد")
      .replace(/\{server\}/g, guild.name || "السيرفر")
      .replace(/\{count\}/g, guild.members == null ? "1,284th" : `${Number(guild.members).toLocaleString("en-US")}th`)
      .replace(/\{inviter\}/g, "دعوة تجريبية");
  }
  function embedTemplate(template) {
    return String(template || "")
      .replace(/\{user\}/g, "@عضو_جديد")
      .replace(/\{username\}/g, "عضو جديد")
      .replace(/\{server\}/g, state.guild?.name || "السيرفر")
      .replace(/\{count\}/g, state.guild?.members == null ? "1,284" : Number(state.guild.members).toLocaleString("en-US"))
      .replace(/\{inviter\}/g, "دعوة تجريبية");
  }
  function normalizeEmbedColor(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || "")) ? String(value) : "#7c3aed";
  }
  function welcomeEmbedPreview() {
    const draft = state.draft || {};
    const sticker = (state.meta?.stickers || []).find(
      (item) => String(item.id) === String(draft.welcome_embed_sticker_id),
    );
    const image = draft.welcome_embed_image_url || sticker?.url;
    const color = normalizeEmbedColor(draft.welcome_embed_color);
    const title = embedTemplate(draft.welcome_embed_title || "أهلاً بك في {server} ✨");
    const description = embedTemplate(
      draft.welcome_embed_description ||
        draft.welcome_message ||
        "يا هلا {user} في {server}! أنت العضو رقم {count}.",
    );
    const card = el(
      "div",
      { id: "welcome-embed-preview", class: "welcome-embed-preview", style: `--embed-accent:${color}` },
      el("div", { class: "embed-preview-author" },
        el("span", { class: "embed-avatar", text: "✦" }),
        el("strong", { text: "PRIME | TEAM" }),
        el("small", { text: "BOT" }),
      ),
      el("h3", { text: title }),
      el("p", { text: description }),
      el("div", { class: "embed-preview-stats" },
        el("span", { text: `العضو رقم  #${state.guild?.members || "1,284"}` }),
        el("span", { text: `${state.guild?.members || "1,284"} عضو` }),
      ),
    );
    if (draft.welcome_embed_show_avatar !== false) {
      card.append(el("div", { class: "embed-preview-member", text: "@عضو_جديد  •  عضو جديد" }));
    }
    if (image) {
      const imageNode = el("img", { class: "embed-preview-image", src: image, alt: "صورة المعاينة" });
      imageNode.onerror = () => imageNode.remove();
      card.append(imageNode);
    }
    if (draft.welcome_embed_footer || draft.welcome_embed_enabled) {
      card.append(el("small", { class: "embed-preview-footer", text: draft.welcome_embed_footer || "PRIME | TEAM" }));
    }
    return card;
  }
  function refreshEmbedPreview() {
    const current = $("#welcome-embed-preview");
    if (current) current.replaceWith(welcomeEmbedPreview());
  }
  function preview() {
    if (state.draft?.welcome_embed_enabled) {
      return el(
        "div",
        { id: "preview" },
        el("div", { class: "preview-title" }, el("span", { text: "معاينة Embed احترافية" }), el("small", { text: "تتحدث بعد كل تعديل" })),
        welcomeEmbedPreview(),
      );
    }
    const body = el("div", { class: "embed" });
    appendSafeMarkdown(body, onboardingTemplate() || "اكتب رسالة الترحيب لرؤية المعاينة.");
    return el(
      "div",
      { id: "preview" },
      el("div", { class: "preview-title" }, el("span", { text: "معاينة Discord مباشرة" }), el("small", { text: "تتحدث بعد كل تعديل" })),
      el(
        "div",
        { class: "discord" },
        el(
          "div",
          { class: "msg-head" },
          el("span", { class: "bot-face", text: "ب" }),
          el("b", { text: "البوت" }),
          el("span", { class: "bot-tag", text: "BOT" }),
          el("span", { class: "msg-time", text: "الآن" }),
        ),
        body,
      ),
    );
  }
  // Page rendering
  function card(title, ...content) {
    return el(
      "section",
      { class: "card" },
      el(
        "div",
        { class: "card-head" },
        el("h2", { text: title }),
        el("small", { text: "إعدادات مباشرة" }),
      ),
      ...content,
    );
  }
  function incidentBody() {
    const body = el("div", { class: "incident-list security-incidents" });
    if (!state.incidents.length) {
      body.append(
        el("div", {
          class: "empty-row",
          text: "لا توجد حوادث أمنية مسجلة مؤخراً",
        }),
      );
    } else {
      state.incidents
        .slice()
        .reverse()
        .forEach((incident) => {
          const date = new Date(incident.timestamp);
          const when = Number.isNaN(date.getTime())
            ? incident.timestamp
            : date.toLocaleString("ar", {
                dateStyle: "short",
                timeStyle: "short",
              });
          body.append(
            el(
              "article",
              { class: "incident-row" },
              el(
                "div",
                { class: "incident-row-head" },
                el("strong", { text: incident.action_type }),
                el("time", { dateTime: incident.timestamp, text: when }),
              ),
              el(
                "div",
                { class: "incident-row-meta" },
                el("span", {
                  text: `${incident.culprit_name} (${incident.culprit_id})`,
                }),
                el("span", { text: incident.mitigation_taken }),
              ),
            ),
          );
        });
    }
    return body;
  }
  function incidentsCard() {
    return el(
      "section",
      { class: "security-feed card" },
      el(
        "div",
        { class: "card-head" },
        el("h2", { text: "بث التهديدات الحي" }),
        el("small", { class: "feed-status", text: "LIVE / 50" }),
      ),
      el(
        "div",
        { class: "terminal-label" },
        el("span", { class: "terminal-dot" }),
        el("span", { text: "SECURITY EVENT STREAM" }),
      ),
      incidentBody(),
    );
  }
  function refreshIncidentBody() {
    const current = $(".security-incidents");
    if (current) current.replaceWith(incidentBody());
  }
  function antiAltTone(value) {
    if (value >= 14) return "critical";
    if (value >= 7) return "warning";
    return "safe";
  }
  function securityView() {
    const section = el("section", { id: "view-security", class: "security-view" });
    const lockButton = el("button", {
      class: "lockdown-trigger",
      type: "button",
      text: state.lockdown
        ? "إلغاء الإغلاق الطارئ"
        : "إغلاق السيرفر الفوري (Emergency Lockdown)",
      onClick: () => confirmLockdown(!state.lockdown),
    });
    const lockCard = el(
      "article",
      { class: `lockdown-card ${state.lockdown ? "is-locked" : ""}` },
      el(
        "div",
        { class: "tactical-heading" },
        el("span", { class: "tactical-kicker", text: "CRISIS CONTROL / 01" }),
        el("h2", { text: "بروتوكول العزل الطارئ" }),
        el("p", {
          text: state.lockdown
            ? "الإغلاق قيد التنفيذ عبر طابور القنوات العامة."
            : "أوقف الكتابة العامة فوراً عند الاشتباه بغارة أو تخريب منسق.",
        }),
        el("div", { class: "lockdown-protected-note" },
          el("strong", { text: "القنوات المحمية تلقائياً" }),
          el("span", {
            text: state.protectedChannels.length
              ? state.protectedChannels.map((channel) => `#${channel.name}`).join("، ")
              : "لا توجد قنوات إدارية معروفة بالأسماء الحالية",
          }),
        ),
      ),
      lockButton,
    );
    const current = Math.max(
      0,
      Math.min(30, Number(state.draft?.anti_alt_days) || 0),
    );
    const output = el("output", {
      class: `anti-alt-value ${antiAltTone(current)}`,
      text: `${current} يوم`,
      for: "anti-alt-range",
    });
    const range = el("input", {
      id: "anti-alt-range",
      class: `anti-alt-range ${antiAltTone(current)}`,
      type: "range",
      min: "0",
      max: "30",
      step: "1",
      value: String(current),
      "aria-label": "الحد الأدنى لعمر الحساب",
    });
    range.addEventListener("input", () => {
      const value = Number(range.value);
      state.draft.anti_alt_days = value;
      state.fields.anti_alt_days = "";
      output.className = `anti-alt-value ${antiAltTone(value)}`;
      output.textContent = `${value} يوم`;
      range.className = `anti-alt-range ${antiAltTone(value)}`;
      renderDynamic();
    });
    const sliderCard = el(
      "article",
      { class: "tactical-card anti-alt-card" },
      el(
        "div",
        { class: "tactical-heading compact" },
        el("span", { class: "tactical-kicker", text: "ACCOUNT AGE GATE / 02" }),
        el("h2", { text: "حد عمر الحساب" }),
        el("p", { text: "الحسابات الأحدث من الحد المحدد ستخضع للحماية." }),
      ),
      el("div", { class: "anti-alt-readout" }, output),
      range,
      el(
        "div",
        { class: "range-scale" },
        el("span", { text: "0" }),
        el("span", { text: "7" }),
        el("span", { text: "14" }),
        el("span", { text: "30 يوم" }),
      ),
    );
    const whitelistInput = el("input", {
      class: "whitelist-input",
      type: "text",
      inputmode: "numeric",
      maxlength: "22",
      placeholder: "أدخل Discord User ID",
      "aria-label": "معرف عضو موثوق",
    });
    const whitelistBody = el("div", { class: "whitelist-chips" });
    const drawWhitelist = () => {
      whitelistBody.replaceChildren();
      if (!state.whitelist.length) {
        whitelistBody.append(
          el("span", { class: "whitelist-empty", text: "لا توجد معرفات موثوقة مضافة" }),
        );
        return;
      }
      state.whitelist.forEach((id) => {
        const chip = el(
          "span",
          { class: "whitelist-chip" },
          el("span", { text: id }),
          el("button", {
            type: "button",
            "aria-label": `إزالة ${id} من القائمة البيضاء`,
            text: "×",
            onClick: () => updateWhitelist("remove", id),
          }),
        );
        whitelistBody.append(chip);
      });
    };
    const addWhitelist = () => {
      const id = whitelistInput.value.trim();
      if (!/^\d{15,22}$/.test(id)) {
        toast("أدخل Discord User ID صالحاً", "warn");
        return;
      }
      updateWhitelist("add", id);
    };
    whitelistInput.onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addWhitelist();
      }
    };
    const addButton = el("button", {
      class: "btn whitelist-add",
      type: "button",
      text: "إضافة موثوق",
      onClick: addWhitelist,
    });
    drawWhitelist();
    const whitelistCard = el(
      "article",
      { class: "tactical-card whitelist-card" },
      el(
        "div",
        { class: "tactical-heading compact" },
        el("span", { class: "tactical-kicker", text: "TRUSTED OPERATORS / 03" }),
        el("h2", { text: "القائمة البيضاء للمشرفين" }),
        el("p", { text: "أضف معرفات المشرفين الموثوقين لمنع تدخل Anti-Nuke ضدهم." }),
      ),
      el("div", { class: "whitelist-entry" }, whitelistInput, addButton),
      whitelistBody,
    );
    section.append(
      el(
        "div",
        { class: "security-section-head" },
        el("div", { class: "eyebrow", text: "TACTICAL SECURITY / LIVE" }),
        el("h2", { text: "مركز الدفاع والأزمات" }),
        el("p", { text: "تحكم مباشر في عزل السيرفر ومراقبة النشاط الإداري عالي الخطورة." }),
      ),
      el("div", { class: "security-grid" }, lockCard, sliderCard, whitelistCard),
      incidentsCard(),
    );
    return section;
  }
  function confirmLockdown(locked) {
    navigator.vibrate?.([30, 50, 30]);
    const back = el("div", {
        class: "modal-back crisis-modal-back",
        role: "dialog",
        "aria-modal": "true",
      }),
      modal = el(
        "div",
        { class: "modal crisis-modal" },
        el("div", { class: "crisis-modal-icon", text: locked ? "⚠" : "✓" }),
        el("h2", { text: locked ? "تأكيد الإغلاق الطارئ" : "إلغاء الإغلاق الطارئ" }),
        el("p", {
          text: locked
            ? "سيتم منع الإرسال في جميع القنوات النصية العامة. هل تريد المتابعة؟"
            : "سيتم إعادة السماح بالإرسال في القنوات التي تم عزلها.",
        }),
      ),
      actions = el("div", { class: "modal-actions" });
    actions.append(
      el("button", {
        class: locked ? "crisis-confirm" : "save",
        type: "button",
        text: locked ? "نعم، فعّل الإغلاق" : "نعم، ألغِ الإغلاق",
        onClick: () => {
          navigator.vibrate?.([30, 50, 30]);
          back.remove();
          setLockdown(locked);
        },
      }),
      el("button", {
        type: "button",
        text: "إلغاء",
        onClick: () => back.remove(),
      }),
    );
    modal.append(actions);
    back.append(modal);
    document.body.append(back);
  }
  async function setLockdown(locked) {
    if (!state.online) {
      toast("الحفظ معطّل أثناء انقطاع الاتصال", "warn");
      return;
    }
    try {
      const r = await api(`api/guild/${state.guild.id}/security/lockdown`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": state.session.csrf,
        },
        body: JSON.stringify({ locked }),
      });
      const data = await readJson(r, {});
      if (r.ok && data.ok) {
        state.lockdown = locked;
        toast(
          locked ? "🚨 بدأ عزل القنوات العامة" : "✅ تم إلغاء الإغلاق الطارئ",
          locked ? "warn" : "success",
          4000,
        );
        await refreshIncidents(state.guild.id, true);
        renderPage();
      } else if (r.status === 429) {
        toast(`تم تجاوز الحد، حاول بعد ${data.retry_after || 5} ثانية`, "warn");
      } else {
        toast("تعذر تنفيذ بروتوكول الإغلاق");
      }
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بمحرك الأمان");
    }
  }
  async function updateWhitelist(action, userId) {
    try {
      const r = await api(`api/guild/${state.guild.id}/security/whitelist`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": state.session.csrf,
        },
        body: JSON.stringify({ action, user_id: userId }),
      });
      const data = await readJson(r, {});
      if (r.ok && data.ok) {
        state.whitelist = data.whitelist || [];
        toast(action === "add" ? "تمت إضافة المشرف إلى القائمة البيضاء" : "تمت الإزالة", "success", 2600);
        renderPage();
        return;
      }
      toast(data.fields?.user_id || "تعذر تحديث القائمة البيضاء");
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بمحرك الأمان");
    }
  }
  function scheduleOnboardingPreview() {
    clearTimeout(state.onboardingPreviewTimer);
    state.onboardingPreviewTimer = setTimeout(() => {
      const current = $("#preview");
      if (current) current.replaceWith(preview());
    }, 120);
  }
  function insertTemplateVariable(textarea, token) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    textarea.value = `${textarea.value.slice(0, start)}${token}${textarea.value.slice(end)}`;
    textarea.selectionStart = textarea.selectionEnd = start + token.length;
    state.draft.welcome_message = textarea.value;
    navigator.vibrate?.(10);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
  }
  function variableChips(textarea) {
    const wrap = el("div", { class: "variable-chips", "aria-label": "متغيرات الرسالة" });
    [
      ["{user}", "منشن العضو"],
      ["{server}", "اسم السيرفر"],
      ["{count}", "ترتيب العضو"],
      ["{inviter}", "صاحب الدعوة"],
    ].forEach(([token, label]) => {
      wrap.append(
        el("button", {
          class: "variable-chip",
          type: "button",
          title: label,
          text: token,
          onClick: () => insertTemplateVariable(textarea, token),
        }),
      );
    });
    return wrap;
  }
  function roleName(id) {
    return window.guildRoles[String(id)]?.name || "بدون رتبة";
  }
  function channelName(id) {
    return window.guildChannels[String(id)]?.name || "بدون قناة";
  }
  function applyGuildMeta(meta) {
    const normalized = {
      ...(meta || {}),
      channels: Array.isArray(meta?.channels) ? meta.channels : [],
      roles: Array.isArray(meta?.roles) ? meta.roles : [],
    };
    window.guildChannels = Object.fromEntries(
      normalized.channels.map((channel) => [String(channel.id), channel]),
    );
    window.guildRoles = Object.fromEntries(
      normalized.roles.map((role) => [String(role.id), role]),
    );
    state.meta = normalized;
    return normalized;
  }
  function normalizePanelColor(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || "")) ? String(value) : "#5865f2";
  }
  function ensureSelfRoleBuilder() {
    if (state.selfRoleBuilder) return state.selfRoleBuilder;
    const saved = state.onboarding?.self_roles?.[0];
    state.selfRoleBuilder = {
      target_channel_id: String(
        saved?.channel_id || state.draft?.welcome_channel_id || state.meta?.channels?.[0]?.id || "",
      ),
      title: saved?.title || "اختر رتبتك",
      description: saved?.description || "اختر الرتب التي تناسبك من القائمة التالية:",
      color: normalizePanelColor(saved?.color_hex || saved?.color),
      emoji: saved?.emoji || "🏷️",
      roles: (saved?.buttons || saved?.role_specs || []).map((role) => ({
        id: String(role.role_id || role.id),
        label: String(role.label || role.name || role.role_id || role.id),
        emoji: String(role.emoji || ""),
      })),
    };
    return state.selfRoleBuilder;
  }
  function nativeSelect(label, value, options, onChange) {
    const select = el("select", { class: "studio-select", "aria-label": label });
    options.forEach((option) =>
      select.append(el("option", { value: String(option.value), text: option.label })),
    );
    select.value = String(value ?? "");
    select.onchange = () => onChange(select.value);
    return select;
  }
  function selfRolePreview(builder) {
    const panel = el(
      "div",
      { class: "self-role-preview-panel", style: `--panel-accent:${normalizePanelColor(builder.color)}` },
      el("div", { class: "self-role-preview-kicker", text: "DISCORD / ROLE SELECTOR" }),
      el("h3", { text: `${builder.emoji || "🏷️"} ${builder.title || "اختر رتبتك"}` }),
      el("p", { text: builder.description || "اختر الرتب التي تناسبك:" }),
    );
    const grid = el("div", { class: "role-button-grid" });
    (builder.roles.length ? builder.roles : [{ id: "preview", label: "رتبة تجريبية", emoji: "✨" }]).forEach(
      (role) => {
        grid.append(
          el("button", {
            class: "role-preview-button",
            type: "button",
            text: `${role.emoji || "🏷️"} ${role.label || roleName(role.id)}`,
          }),
        );
      },
    );
    panel.append(grid);
    return panel;
  }
  function selfRolesBuilder() {
    const builder = ensureSelfRoleBuilder();
    const roles = state.meta?.roles || [];
    const availableRoles = roles.filter(
      (role) =>
        role.assignable !== false &&
        !role.managed &&
        !role.default &&
        !builder.roles.some((selected) => String(selected.id) === String(role.id)),
    );
    const roleRows = el("div", { class: "builder-role-list" });
    if (!builder.roles.length)
      roleRows.append(el("div", { class: "builder-empty", text: "أضف رتبة واحدة على الأقل لتفعيل النشر." }));
    builder.roles.forEach((role, index) => {
      const label = el("input", {
        class: "builder-role-input",
        type: "text",
        maxlength: "100",
        value: role.label || roleName(role.id),
        "aria-label": `اسم الرتبة ${index + 1}`,
      });
      const emoji = el("input", {
        class: "builder-emoji-input",
        type: "text",
        maxlength: "8",
        value: role.emoji || "",
        "aria-label": `إيموجي الرتبة ${index + 1}`,
      });
      label.oninput = () => (role.label = label.value);
      emoji.oninput = () => (role.emoji = emoji.value);
      roleRows.append(
        el(
          "div",
          { class: "builder-role-row" },
          el("span", { class: "role-dot", style: `background:${roles.find((r) => String(r.id) === String(role.id))?.color || "#64748b"}` }),
          emoji,
          label,
          el("small", { class: "builder-role-source", text: roleName(role.id) }),
          el("button", {
            class: "icon-button",
            type: "button",
            "aria-label": "إزالة الرتبة",
            text: "×",
            onClick: () => {
              builder.roles.splice(index, 1);
              renderPage();
            },
          }),
        ),
      );
    });
    const channelOptions = (state.meta?.channels || []).map((channel) => ({
      value: channel.id,
      label: `# ${channel.name}`,
    }));
    const rolePicker = nativeSelect(
      "إضافة رتبة للوحة",
      "",
      [{ value: "", label: "إضافة رتبة…" }, ...availableRoles.map((role) => ({ value: role.id, label: role.name }))],
      (value) => {
        if (!value) return;
        const role = roles.find((item) => String(item.id) === String(value));
        if (!role) return;
        builder.roles.push({ id: String(role.id), label: role.name, emoji: "🏷️" });
        navigator.vibrate?.(10);
        renderPage();
      },
    );
    const title = el("input", {
      class: "builder-input",
      type: "text",
      maxlength: "256",
      value: builder.title,
      "aria-label": "عنوان لوحة الرتب",
    });
    const description = el("textarea", {
      class: "builder-textarea",
      maxlength: "4000",
      "aria-label": "وصف لوحة الرتب",
    });
    description.value = builder.description;
    const color = el("input", {
      class: "builder-color",
      type: "color",
      value: normalizePanelColor(builder.color),
      "aria-label": "لون لوحة الرتب",
    });
    const emoji = el("input", {
      class: "builder-emoji-input panel-emoji",
      type: "text",
      maxlength: "8",
      value: builder.emoji,
      "aria-label": "إيموجي لوحة الرتب",
    });
    title.oninput = () => {
      builder.title = title.value;
      const current = $(".self-role-preview-panel");
      if (current) current.replaceWith(selfRolePreview(builder));
    };
    description.oninput = () => {
      builder.description = description.value;
      const current = $(".self-role-preview-panel");
      if (current) current.replaceWith(selfRolePreview(builder));
    };
    color.oninput = () => {
      builder.color = normalizePanelColor(color.value);
      const current = $(".self-role-preview-panel");
      if (current) current.replaceWith(selfRolePreview(builder));
    };
    emoji.oninput = () => {
      builder.emoji = emoji.value;
      const current = $(".self-role-preview-panel");
      if (current) current.replaceWith(selfRolePreview(builder));
    };
    const targetChannel = nativeSelect(
      "قناة لوحة الرتب",
      builder.target_channel_id,
      channelOptions.length ? channelOptions : [{ value: "", label: "لا توجد قنوات" }],
      (value) => (builder.target_channel_id = value),
    );
    const deploy = el("button", {
      class: "btn builder-deploy",
      type: "button",
      text: "نشر لوحة الرتب في Discord",
      onClick: deploySelfRoles,
    });
    const form = el(
      "div",
      { class: "self-role-builder-form" },
      el("div", { class: "builder-form-row" }, el("label", { text: "القناة" }), targetChannel),
      el("div", { class: "builder-form-row" }, el("label", { text: "العنوان" }), title),
      el("div", { class: "builder-form-row" }, el("label", { text: "الوصف" }), description),
      el("div", { class: "builder-inline-fields" }, el("div", { class: "builder-form-row" }, el("label", { text: "الإيموجي" }), emoji), el("div", { class: "builder-form-row" }, el("label", { text: "اللون" }), color)),
      el("div", { class: "builder-form-row" }, el("label", { text: "الرتب" }), rolePicker),
      roleRows,
      deploy,
    );
    const previewPane = el("div", { class: "self-role-builder-preview" }, el("div", { class: "preview-title", text: "المعاينة" }), selfRolePreview(builder));
    const existing = el("div", { class: "deployed-panels" });
    const panels = state.onboarding?.self_roles || [];
    if (panels.length) {
      existing.append(el("div", { class: "deployed-heading", text: "لوحات منشورة" }));
      panels.slice(0, 4).forEach((panel) => {
        existing.append(
          el("div", { class: "deployed-panel" },
            el("span", { class: "deployed-panel-icon", text: panel.emoji || "🏷️" }),
            el("span", { class: "ell", text: panel.title || "لوحة رتب" }),
             el("small", { text: `${panel.buttons?.length || panel.role_specs?.length || 0} رتب · رسالة ${panel.message_id}` }),
          ),
        );
      });
    }
    return el("div", { class: "self-role-builder" }, form, previewPane, existing);
  }
  function onboardingView() {
    const section = el("section", { id: "view-onboarding", class: "onboarding-view" });
    const message = el("textarea", {
      id: "in-onboarding-message",
      maxlength: "1000",
      placeholder: "مرحباً {user} في {server} — أنت العضو {count}.",
    });
    message.value = state.draft.welcome_message || "";
    const messageCount = el("div", { class: "counter", text: `${message.value.length} / 1000` });
    message.oninput = () => {
      state.draft.welcome_message = message.value;
      messageCount.textContent = `${message.value.length} / 1000`;
      renderDynamic();
      scheduleOnboardingPreview();
    };
    const onboardingFields = el("div", { class: "fields onboarding-fields" });
    onboardingFields.append(
      selector("welcome_channel_id", "قناة الترحيب", "channel"),
      selector("leave_channel_id", "قناة المغادرة", "channel"),
      toggle("welcome_dm_enabled", "إرسال ترحيب خاص للعضو"),
      selector("rules_channel_id", "قناة القوانين", "channel"),
      selector("verified_role_id", "رتبة العضو الموثق", "role"),
      selector("unverified_role_id", "رتبة العضو غير الموثق", "role"),
      el("div", { class: "field wide role-matrix-field" },
        el("label", { text: "مصفوفة الأدوار التلقائية" }),
        el("div", { class: "role-matrix" },
          el("div", { class: "role-matrix-card human" }, el("span", { class: "matrix-icon", text: "◉" }), el("div", { class: "matrix-copy" }, el("strong", { text: "الأعضاء البشر" }), el("small", { "data-role-matrix-key": "member_auto_role_id", text: roleName(state.draft.member_auto_role_id) })), selector("member_auto_role_id", "رتبة الأعضاء", "role")),
          el("div", { class: "role-matrix-card bot" }, el("span", { class: "matrix-icon", text: "⌘" }), el("div", { class: "matrix-copy" }, el("strong", { text: "البوتات" }), el("small", { "data-role-matrix-key": "bot_auto_role_id", text: roleName(state.draft.bot_auto_role_id) })), selector("bot_auto_role_id", "رتبة البوتات", "role")),
          el("div", { class: "role-matrix-card all" }, el("span", { class: "matrix-icon", text: "✦" }), el("div", { class: "matrix-copy" }, el("strong", { text: "رتبة افتراضية للجميع" }), el("small", { "data-role-matrix-key": "auto_role_id", text: roleName(state.draft.auto_role_id) })), selector("auto_role_id", "رتبة عامة", "role")),
        ),
      ),
    );
    const leave = el("textarea", {
      id: "in-onboarding-leave",
      maxlength: "1000",
      placeholder: "{username} غادر {server}.",
    });
    leave.value = state.draft.leave_message || "";
    const leaveCount = el("div", { class: "counter", text: `${leave.value.length} / 1000` });
    leave.oninput = () => {
      state.draft.leave_message = leave.value;
      leaveCount.textContent = `${leave.value.length} / 1000`;
      renderDynamic();
    };
    const messageField = field("قالب رسالة الترحيب", message, "welcome_message", "يدعم Markdown محدوداً، وسيتم تجاهل أي HTML.");
    messageField.classList.add("wide", "message-template-field");
    messageField.append(variableChips(message), messageCount, preview());
    const leaveField = field("رسالة المغادرة", leave, "leave_message");
    leaveField.classList.add("wide");
    leaveField.append(leaveCount);
    const embedDescription = el("textarea", {
      id: "in-welcome-embed-description",
      maxlength: "1000",
      placeholder: "يا هلا {user} في {server}! أنت العضو رقم {count}.",
    });
    embedDescription.value = state.draft.welcome_embed_description || "";
    embedDescription.oninput = () => {
      state.draft.welcome_embed_description = embedDescription.value;
      renderDynamic();
    };
    const embedDescriptionField = field(
      "وصف الـ Embed",
      embedDescription,
      "welcome_embed_description",
      "استخدم {user} و {server} و {count} و {inviter} لإظهار بيانات العضو تلقائياً.",
    );
    embedDescriptionField.classList.add("wide");
    const embedControls = el(
      "div",
      { class: "embed-builder-grid" },
      toggle("welcome_embed_enabled", "تفعيل ترحيب Embed احترافي"),
      toggle("welcome_embed_show_avatar", "إظهار صورة العضو"),
      input("welcome_embed_title", "عنوان الترحيب", "text", {
        maxlength: "256",
        placeholder: "أهلاً بك في {server} ✨",
      }),
      input("welcome_embed_color", "لون الـ Embed", "color"),
      input("welcome_embed_image_url", "صورة رئيسية اختيارية", "url", {
        maxlength: "2048",
        placeholder: "https://example.com/welcome.png",
      }),
      selector("welcome_embed_sticker_id", "ملصق من السيرفر", "sticker"),
      input("welcome_embed_footer", "التذييل", "text", {
        maxlength: "2048",
        placeholder: "PRIME | TEAM • تطوير abood2026",
      }),
      embedDescriptionField,
    );
    const actions = el(
      "div",
      { class: "onboarding-actions" },
      el("button", { class: "btn onboarding-save", type: "button", text: "حفظ إعدادات onboarding", onClick: saveOnboarding }),
      el("button", { class: "btn onboarding-test", type: "button", text: "إرسال تجربة إلى Discord", onClick: sendTestWelcome }),
    );
    section.append(
      el("div", { class: "studio-hero" },
        el("div", { class: "eyebrow", text: "ONBOARDING STUDIO / LIVE" }),
        el("h2", { text: "استوديو الدخول والهوية" }),
        el("p", { text: "صمّم لحظة دخول العضو، راجعها بصرياً، ثم انشر تجربة حقيقية في قناة Discord." }),
        actions,
      ),
       card("قواعد الدخول والترحيب", el("div", { class: "onboarding-card-body" }, onboardingFields, messageField, leaveField)),
       card("مصمم الترحيب الاحترافي", el("div", { class: "onboarding-card-body embed-builder-card" },
         el("p", { class: "hint", text: "أنشئ رسالة Embed تظهر فيها صورة العضو، عدد الأعضاء، الترتيب، صورة رئيسية أو ملصق من السيرفر." }),
         embedControls,
       )),
      card("منشئ لوحة الرتب الذاتية", selfRolesBuilder()),
    );
    return section;
  }
  function pulse() {
    navigator.vibrate?.(10);
  }
  function commandRoles(command) {
    return new Set((command.allowed_roles || []).map(String));
  }
  function commandShortcuts(command) {
    const name = String(command.command_name || "").toLowerCase();
    const policyAliases = (Array.isArray(command.custom_aliases)
      ? command.custom_aliases
      : (Array.isArray(command.aliases) ? command.aliases : [])
    ).map((trigger) => ({
      trigger: String(trigger),
      target_type: "command",
      target: `/${name}`,
      policyAlias: true,
    }));
    return policyAliases;
  }
  function commandPermissionWarnings(command) {
    const raw = [
      ...(Array.isArray(command.permission_warnings) ? command.permission_warnings : []),
      ...(Array.isArray(command.missing_permissions) ? command.missing_permissions : []),
      ...(command.permission_warning ? [command.permission_warning] : []),
    ];
    return raw.filter(Boolean).map((item) => typeof item === "string" ? item : item.name || item.permission || JSON.stringify(item));
  }
  function commandMatchesFilters(command) {
    const query = state.commandSearch.trim().toLowerCase();
    const roleId = String(state.commandRoleFilter || "");
    return (
      (!query || `${command.command_name || ""} ${command.cog || ""}`.toLowerCase().includes(query)) &&
      (state.commandCogFilter === "all" || String(command.cog || "Commands") === state.commandCogFilter) &&
      (state.commandStatusFilter === "all" ||
        (state.commandStatusFilter === "enabled" && command.enabled !== false) ||
        (state.commandStatusFilter === "disabled" && command.enabled === false) ||
        (state.commandStatusFilter === "warning" && commandPermissionWarnings(command).length > 0)) &&
      (state.commandRoleFilter === "all" || commandRoles(command).has(roleId))
    );
  }
  const COMMAND_UI_LABELS = {
    warn: ["تحذير", "إرسال تحذير لعضو من السيرفر", "⚠️", "violet"],
    ban: ["حظر", "حظر عضو ومنعه من دخول السيرفر", "🔨", "red"],
    kick: ["طرد", "طرد عضو من السيرفر", "👤", "red"],
    timeout: ["تايم أوت", "إسكات عضو لمدة محددة", "⏱", "red"],
    untimeout: ["فك التايم أوت", "السماح للعضو بالكلام من جديد", "🔓", "red"],
    clear: ["مسح الرسائل", "حذف رسائل القناة بسرعة", "🗑", "blue"],
    lockdown: ["قفل القناة", "منع الأعضاء من الكتابة في القناة", "🔒", "blue"],
    unlock: ["فتح القناة", "السماح للأعضاء بالكتابة", "🔓", "blue"],
    slowmode: ["الوضع البطيء", "تحديد وقت بين رسائل الأعضاء", "🐌", "blue"],
    mute: ["إسكات", "منع العضو من التحدث", "🔇", "gold"],
    unmute: ["فك الإسكات", "إعادة صلاحية التحدث للعضو", "🔊", "gold"],
    poll: ["استطلاع", "إنشاء استطلاع داخل القناة", "📊", "gold"],
    ticket: ["التذاكر", "إدارة تذاكر الدعم والمساعدة", "🎫", "purple"],
  };
  function commandVisual(command) {
    const name = String(command.command_name || "").toLowerCase().split(/\s+/).pop();
    const metadata = state.commandRegistry?.byKey?.[name];
    const item = COMMAND_UI_LABELS[name];
    return {
      name: metadata?.display_name || item?.[0] || `/${name}`,
      description: metadata?.description || item?.[1] || command.description || "إدارة هذا الأمر من إعدادات السيرفر.",
      icon: item?.[2] || "✦",
      tone: item?.[3] || "slate",
      premium: Boolean(command.premium || command.is_premium || command.pro),
    };
  }
  function commandSectionVisual(cog) {
    const value = String(cog || "").toLowerCase();
    if (value.includes("moder")) return { label: "الطرد والحظر", icon: "📌", tone: "red" };
    if (value.includes("econom")) return { label: "مزایا", icon: "✦", tone: "pink" };
    if (value.includes("engage") || value.includes("community")) return { label: "الإسكات والصوت", icon: "🔇", tone: "gold" };
    if (value.includes("ticket") || value.includes("channel")) return { label: "إدارة القنوات", icon: "🖌", tone: "blue" };
    if (value.includes("security")) return { label: "Blacklist", icon: "⛔", tone: "red" };
    if (value.includes("utility") || value.includes("command")) return { label: "التحذيرات والإدارة", icon: "★", tone: "purple" };
    return { label: cog || "الأوامر", icon: "✦", tone: "slate" };
  }
  function commandRows() {
    const commands = (state.commandStudio.commands || []).filter(commandMatchesFilters);
    const groups = new Map();
    commands.forEach((command) => {
      const key = command.cog || "Commands";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(command);
    });
    const root = el("div", { id: "command-rows", class: "command-groups" });
    if (!commands.length) {
      root.append(el("div", { class: "empty studio-empty", text: "لا توجد أوامر مطابقة للبحث" }));
      return root;
    }
    groups.forEach((items, cog) => {
      const section = commandSectionVisual(cog);
      const collapsed = Boolean(state.commandCollapsedGroups[cog]);
      const group = el("section", { class: `command-group${collapsed ? " is-collapsed" : ""}` });
      const groupButton = el("button", {
        class: "command-section-head",
        type: "button",
        "aria-expanded": String(!collapsed),
        onClick: () => {
          state.commandCollapsedGroups[cog] = !state.commandCollapsedGroups[cog];
          renderPage();
        },
      },
        el("span", { class: `command-section-icon tone-${section.tone}`, text: section.icon }),
        el("span", { class: "command-section-copy" },
          el("strong", { text: section.label }),
          el("small", { text: `${items.length} أمر` }),
        ),
        el("span", { class: "command-section-chevron", text: collapsed ? "⌄" : "⌃" }),
      );
      group.append(groupButton);
      if (collapsed) {
        root.append(group);
        return;
      }
      const list = el("div", { class: "command-section-list" });
      items.forEach((command) => {
        const roles = commandRoles(command);
        const commandId = String(command.command_name);
        const warnings = commandPermissionWarnings(command);
        const shortcuts = commandShortcuts(command);
        const visual = commandVisual(command);
        const toggleButton = el("button", {
          class: `studio-switch command-list-switch${command.enabled ? " on" : ""}`,
          type: "button",
          role: "switch",
          "aria-checked": String(!!command.enabled),
          "aria-label": `تفعيل ${command.command_name}`,
        }, el("i"));
        toggleButton.onclick = () => {
          pulse();
          updateCommand(command, !command.enabled, [...roles]);
        };
        const select = el("input", {
          class: "command-select command-list-check",
          type: "checkbox",
          checked: state.selectedCommandIds.includes(commandId),
          "aria-label": `تحديد ${command.command_name}`,
        });
        select.onchange = () => {
          state.selectedCommandIds = select.checked
            ? [...new Set([...state.selectedCommandIds, commandId])]
            : state.selectedCommandIds.filter((id) => id !== commandId);
          renderPage();
        };
        const row = el("article", { class: `command-list-row${warnings.length ? " has-warning" : ""}` },
          el("div", { class: `command-list-icon tone-${visual.tone}`, text: visual.icon }),
          el("div", { class: "command-list-copy" },
            el("div", { class: "command-list-title" },
              el("strong", { text: visual.name }),
              visual.premium ? el("span", { class: "command-pro-badge", text: "Pro ✨" }) : [],
            ),
            el("small", { text: visual.description }),
            shortcuts.length ? el("div", { class: "command-list-aliases" }, shortcuts.slice(0, 3).map((item) => el("code", { text: item.trigger }))) : [],
          ),
          el("div", { class: "command-list-actions" },
            select,
            el("button", { class: "command-list-settings", type: "button", text: "⚙", title: "إعدادات الأمر", onClick: () => openCommandDetail(command) }),
            toggleButton,
          ),
        );
        list.append(row);
      });
      group.append(list);
      root.append(group);
    });
    return root;
  }
  async function updateCommand(command, enabled, allowedRoles, allowedChannels = command.allowed_channels || []) {
    try {
      const r = await api(`api/guild/${state.guild.id}/commands/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify({
          command_name: command.command_name,
          enabled,
          allowed_roles: allowedRoles,
          allowed_channels: allowedChannels,
        }),
      });
      const data = await readJson(r, {});
      if (!r.ok) {
        toast(data.fields ? Object.values(data.fields)[0] : "تعذر تحديث صلاحية الأمر");
        return;
      }
      Object.assign(command, data.command);
      toast(`✅ تم ${enabled ? "تفعيل" : "تعطيل"} /${command.command_name}`, "success", 2200);
      renderPage();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
    }
  }
  async function saveCommandPolicy(
    command,
    enabled,
    allowedRoles,
    allowedChannels,
    aliases = command.custom_aliases || command.aliases || [],
    policyExtras = {},
  ) {
    try {
      const r = await api(`api/guild/${state.guild.id}/commands/${encodeURIComponent(command.command_name)}/policy`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify({
          command_name: command.command_name,
          is_enabled: Boolean(enabled),
          aliases,
          allowed_roles: allowedRoles,
          allowed_channels: allowedChannels,
          auto_delete_seconds: policyExtras.auto_delete_seconds,
          response_mode: policyExtras.response_mode,
          custom_template: policyExtras.custom_template,
        }),
      });
      const data = await readJson(r, {});
      if (!r.ok || !data.command) {
        toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ إعدادات الأمر", "warn");
        return false;
      }
      const saved = data.command;
      const savedAliases = Array.isArray(saved.aliases)
        ? [...saved.aliases]
        : [...(aliases || [])];
      Object.assign(command, saved, {
        aliases: savedAliases,
        custom_aliases: savedAliases,
      });
      const policyStore = state.commandRegistry?.policies || {};
      const policyKeys = [
        String(command.command_name || "").toLowerCase(),
        String(command.command_name || "").toLowerCase().split(/\s+/).pop(),
      ];
      policyKeys.forEach((key) => {
        const policy = policyStore[key];
        if (policy) {
          Object.assign(policy, {
            enabled: command.enabled,
            aliases: [...savedAliases],
            allowed_roles: [...(command.allowed_roles || [])],
            allowed_channels: [...(command.allowed_channels || [])],
            auto_delete_seconds: Number(command.auto_delete_seconds || 0),
            response_mode: command.response_mode || command.response_style || "default",
            custom_template: command.custom_template || command.response_template || "",
          });
        }
      });
      return true;
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم", "warn");
      return false;
    }
  }
  function parseCommandAliases(input) {
    const aliases = [...new Set(
      (Array.isArray(input) ? input : String(input?.value || "").split(/[,،\n]+/))
        .map((value) => String(value))
        .map((value) => value.trim().replace(/^[/!]/, ""))
        .filter(Boolean),
    )];
    if (aliases.length > 20) {
      toast("يمكن إضافة 20 اختصاراً كحد أقصى للأمر", "warn");
      return null;
    }
    if (aliases.some((value) => /\s/.test(value) || value.length > 80)) {
      toast("كل اختصار يجب أن يكون كلمة واحدة وبحد أقصى 80 حرفاً", "warn");
      return null;
    }
    return aliases;
  }
  async function saveCommandPrefix(input, feedback) {
    const value = input.value.trim();
    if (!value || value.length > 5 || /\s/.test(value)) {
      feedback.textContent = "استخدم prefix من 1 إلى 5 أحرف بدون مسافات";
      feedback.className = "prefix-feedback bad";
      return;
    }
    try {
      const r = await api(`api/guild/${state.guild.id}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify({ revision: state.revision, changes: { prefix: value } }),
      });
      const data = await readJson(r, {});
      if (r.ok && data.revision != null) {
        state.baseline = { ...state.baseline, prefix: data.settings.prefix };
        state.draft = { ...state.draft, prefix: data.settings.prefix };
        state.revision = data.revision;
        feedback.textContent = "تم تطبيق prefix فورياً";
        feedback.className = "prefix-feedback good";
        pulse();
        toast("✅ تم تحديث prefix", "success", 2200);
        setTimeout(() => renderPage(), 700);
      } else if (r.status === 409) {
        feedback.textContent = "توجد نسخة أحدث من الإعدادات، أعد المحاولة";
        feedback.className = "prefix-feedback bad";
      } else {
        feedback.textContent = data.fields?.prefix || "تعذر حفظ prefix";
        feedback.className = "prefix-feedback bad";
      }
    } catch (error) {
      if (error.message !== "unauth") {
        feedback.textContent = "تعذر الاتصال بالخادم";
        feedback.className = "prefix-feedback bad";
      }
    }
  }
  function commandListForWorkspace() {
    return (state.commandStudio.commands || []).filter(commandMatchesFilters);
  }
  function closeCommandDetail() {
    document.querySelector(".command-detail-back")?.remove();
    state.commandDetail = null;
  }
  function commandChoiceEditor(title, choices, selected, placeholder, icon, formatter) {
    const picked = new Set((selected || []).map(String));
    const search = el("input", {
      class: "studio-input command-policy-search",
      type: "search",
      placeholder,
      "aria-label": title,
    });
    const chips = el("div", { class: "command-policy-chips" });
    const list = el("div", { class: "command-policy-options" });
    const root = el("section", { class: "command-policy-section" },
      el("div", { class: "command-policy-heading" },
        el("span", { class: "command-policy-icon", text: icon }),
        el("div", {}, el("strong", { text: title }), el("small", { text: "اتركه فارغاً للسماح للجميع" })),
      ),
      chips,
      search,
      list,
    );
    const render = () => {
      const query = search.value.trim().toLowerCase();
      const chipNodes = picked.size
        ? [...picked].map((id) => {
            const item = choices.find((choice) => String(choice.id) === id);
            const chip = el("button", { class: "command-policy-chip", type: "button", text: `× ${formatter(item || { id })}` });
            chip.onclick = () => { picked.delete(id); render(); };
            return chip;
          })
        : [el("span", { class: "command-policy-empty", text: "لم يتم تحديد أي عنصر" })];
      chips.replaceChildren(...chipNodes);

      const optionNodes = choices
          .filter((item) => !query || formatter(item).toLowerCase().includes(query))
          .slice(0, 80)
          .map((item) => {
            const id = String(item.id);
            const button = el("button", {
              class: `command-policy-option${picked.has(id) ? " selected" : ""}`,
              type: "button",
              text: `${picked.has(id) ? "✓ " : ""}${formatter(item)}`,
            });
            button.onclick = () => {
              if (picked.has(id)) picked.delete(id);
              else if (picked.size < 25) picked.add(id);
              render();
            };
            return button;
          });
      list.replaceChildren(...optionNodes);
    };
    search.oninput = render;
    render();
    return { root, values: () => [...picked] };
  }
  function openCommandDetail(command) {
    closeCommandDetail();
    state.commandDetail = command;
    const commandKey = String(command.command_name || "").toLowerCase().split(/\s+/).pop();
    const registryMeta = state.commandRegistry?.byKey?.[commandKey] || {};
    const registryPolicies = state.commandRegistry?.policies || {};
    const registryPolicy = registryPolicies[command.command_name]
      || registryPolicies[String(command.command_name || "").toLowerCase()]
      || registryPolicies[commandKey]
      || {};
    const effectivePolicy = {
      ...registryPolicy,
      ...command,
      custom_aliases: command.custom_aliases || registryPolicy.aliases || command.aliases || [],
      allowed_roles: command.allowed_roles || registryPolicy.allowed_roles || [],
      allowed_channels: command.allowed_channels || registryPolicy.allowed_channels || [],
    };
    const warnings = commandPermissionWarnings(command);
    const visual = commandVisual(command);
    let detailEnabled = effectivePolicy.enabled !== false;
    let activeTab = "general";
    const back = el("div", { class: "modal-back command-detail-back", role: "dialog", "aria-modal": "true" });
    const permissionBox = warnings.length
      ? el("div", { class: "permission-warning" },
          el("strong", { text: "تنبيه صلاحيات" }),
          el("p", { text: warnings.join("، ") }),
        )
      : el("div", { class: "permission-ok", text: "لا توجد تحذيرات صلاحيات من البيانات الحالية" });
    let aliasValues = [...new Set(effectivePolicy.custom_aliases.map((value) => String(value).trim().replace(/^[/!]/, "")).filter(Boolean))].slice(0, 20);
    const aliasInput = el("input", {
      class: "studio-input command-alias-input",
      type: "text",
      placeholder: "اكتب اسماً بديلاً ثم اضغط Enter أو Space",
      "aria-label": "إضافة اسم بديل",
      autocomplete: "off",
    });
    const aliasChips = el("div", { class: "command-alias-chips" });
    const renderAliases = () => {
      const nodes = aliasValues.length
        ? aliasValues.map((alias) => {
            const chip = el("span", { class: "command-alias-chip" },
              el("span", { text: alias }),
              el("button", {
                type: "button",
                class: "command-alias-remove",
                text: "×",
                title: `إزالة ${alias}`,
                "aria-label": `إزالة ${alias}`,
                onClick: () => {
                  aliasValues = aliasValues.filter((value) => value !== alias);
                  renderAliases();
                },
              }),
            );
            return chip;
          })
        : [el("span", { class: "hint", text: "لا توجد اختصارات مخصصة لهذا الأمر بعد" })];
      aliasChips.replaceChildren(...nodes);
      aliasCount.textContent = `${aliasValues.length}/20`;
    };
    const aliasCount = el("span", { class: "alias-count", text: "0/20" });
    const addAlias = () => {
      const value = aliasInput.value.trim().replace(/^[/!]/, "");
      if (!value) return;
      if (/\s/.test(value) || value.length > 80) {
        toast("كل اسم بديل يجب أن يكون كلمة واحدة وبحد أقصى 80 حرفاً", "warn");
        return;
      }
      if (!aliasValues.some((item) => item.toLocaleLowerCase() === value.toLocaleLowerCase())) {
        if (aliasValues.length >= 20) {
          toast("يمكن إضافة 20 اسماً بديلاً كحد أقصى للأمر", "warn");
          return;
        }
        aliasValues = [...aliasValues, value];
      }
      aliasInput.value = "";
      renderAliases();
      aliasInput.focus();
    };
    aliasInput.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        addAlias();
      } else if (event.key === "Backspace" && !aliasInput.value && aliasValues.length) {
        aliasValues = aliasValues.slice(0, -1);
        renderAliases();
      }
    };
    renderAliases();
    const aliasesSection = el("section", { class: "command-aliases" },
      el("div", { class: "section-heading compact" },
        el("div", {}, el("div", { class: "eyebrow", text: "CUSTOM ALIASES" }), el("h3", { text: "الأسماء البديلة" })),
        aliasCount,
      ),
      el("p", { class: "hint", text: "اضغط Enter أو Space بعد كل اسم. اضغط × للحذف أو Backspace لإزالة آخر اسم." }),
      aliasInput,
      aliasChips,
      el("button", {
        class: "btn primary alias-save-button",
        type: "button",
        text: "حفظ الاختصارات والإعدادات",
        onClick: saveDetails,
      }),
    );
    const rolesEditor = commandChoiceEditor(
      "الرتب المسموحة",
      state.commandStudio.roles || [],
      effectivePolicy.allowed_roles,
      "ابحث عن رتبة...",
      "♟",
      (item) => `@${item.name || item.id}`,
    );
    const channelsEditor = commandChoiceEditor(
      "القنوات المسموحة",
      state.commandStudio.channels || [],
      effectivePolicy.allowed_channels,
      "ابحث عن قناة...",
      "#",
      (item) => `#${item.name || item.id}`,
    );
    const autoDeleteValue = Number(effectivePolicy.auto_delete_seconds || 0);
    const autoDeleteButtons = el("div", { class: "command-choice-buttons" });
    let selectedAutoDelete = [0, 5, 10, 30, 60, 300].includes(autoDeleteValue) ? autoDeleteValue : 0;
    const renderAutoDelete = () => {
      const nodes = [0, 5, 10, 30, 60, 300].map((seconds) => {
        const label = seconds === 0 ? "لا تحذف" : `${seconds} ث`;
        return el("button", {
          class: `command-choice-button${selectedAutoDelete === seconds ? " selected" : ""}`,
          type: "button",
          text: label,
          onClick: () => {
            selectedAutoDelete = seconds;
            renderAutoDelete();
          },
        });
      });
      autoDeleteButtons.replaceChildren(...nodes);
    };
    renderAutoDelete();
    const autoDeleteSection = el("section", { class: "command-detail-policy command-detail-subsection" },
      el("div", { class: "command-detail-policy-heading" },
        el("span", { class: "command-policy-icon", text: "⌫" }),
        el("div", {}, el("strong", { text: "الحذف التلقائي" }), el("small", { text: "حذف رد البوت بعد المدة المحددة" })),
      ),
      autoDeleteButtons,
    );
    const responseStyleSelect = el("select", { class: "studio-input command-response-select", "aria-label": "نمط الرد" },
      [
        ["default", "افتراضي"],
        ["embed", "Embed منسق"],
        ["compact", "مختصر"],
        ["silent", "صامت"],
      ].map(([value, label]) => el("option", { value, text: label })),
    );
    responseStyleSelect.value = effectivePolicy.response_mode || effectivePolicy.response_style || "default";
    const responseTemplate = el("textarea", {
      class: "studio-textarea command-response-template",
      rows: "5",
      placeholder: "اختياري: اكتب قالب الرد المخصص (حتى 2000 حرف)…",
      "aria-label": "قالب الرد المخصص",
    });
    responseTemplate.value = effectivePolicy.custom_template || effectivePolicy.response_template || "";
    const responsePanel = el("section", { class: "command-detail-policy command-response-panel" },
      el("div", { class: "command-detail-policy-heading" },
        el("span", { class: "command-policy-icon", text: "↗" }),
        el("div", {}, el("strong", { text: "شكل رد البوت" }), el("small", { text: "تخصيص طريقة عرض رد هذا الأمر" })),
      ),
      responseStyleSelect,
      el("label", { class: "command-field-label", text: "القالب المخصص" }),
      responseTemplate,
      el("small", { class: "hint", text: "يمكن استخدام القالب مع الأوامر التي تدعم تخصيص الردود." }),
    );
    const syntaxCode = el("code", { text: registryMeta.syntax || `!${commandKey}` });
    const exampleCode = el("code", { text: registryMeta.example || `!${commandKey}` });
    const registryAliases = Array.isArray(registryMeta.default_aliases) ? registryMeta.default_aliases : [];
    const usagePanel = el("section", { class: "command-detail-policy command-usage-panel" },
      el("div", { class: "command-detail-policy-heading" },
        el("span", { class: "command-policy-icon", text: "?" }),
        el("div", {}, el("strong", { text: "طريقة الاستخدام" }), el("small", { text: "المعلومات الرسمية لهذا الأمر" })),
      ),
      el("dl", { class: "command-detail-list command-usage-list" },
        el("div", {}, el("dt", { text: "الصياغة" }), el("dd", {}, syntaxCode)),
        el("div", {}, el("dt", { text: "مثال" }), el("dd", {}, exampleCode)),
        el("div", {}, el("dt", { text: "الصلاحية المطلوبة" }), el("dd", { text: registryMeta.required_permission || "send_messages" })),
        el("div", {}, el("dt", { text: "الأسماء الرسمية" }), el("dd", { text: registryAliases.length ? registryAliases.join("، ") : "لا توجد" })),
      ),
    );
    const statusSwitch = el("button", {
      class: `studio-switch command-detail-switch${detailEnabled ? " on" : ""}`,
      type: "button",
      role: "switch",
      "aria-checked": String(detailEnabled),
      "aria-label": "حالة الأمر",
    }, el("i"));
    const statusCopy = el("div", { class: "command-detail-status-copy" },
      el("strong", { text: "تفعيل الأمر" }),
      el("small", { text: "عند التعطيل، المستخدمون لن يقدروا على استخدام هذا الأمر." }),
    );
    statusSwitch.onclick = () => {
      detailEnabled = !detailEnabled;
      statusSwitch.classList.toggle("on", detailEnabled);
      statusSwitch.setAttribute("aria-checked", String(detailEnabled));
    };
    const generalPanel = el("div", { class: "command-detail-tab-panel", "data-detail-panel": "general" },
      permissionBox,
      el("section", { class: "command-detail-policy" },
        el("div", { class: "command-detail-policy-heading" },
          el("span", { class: "command-policy-icon", text: "◉" }),
          el("div", {}, el("strong", { text: "حالة الأمر" }), el("small", { text: "هذه القيمة تُحفظ وتُطبق على Discord" })),
        ),
        el("div", { class: "command-detail-status-editor" }, statusCopy, statusSwitch),
      ),
      aliasesSection,
      rolesEditor.root,
      channelsEditor.root,
      autoDeleteSection,
    );
    const tabPanels = el("div", { class: "command-detail-tab-panels" }, generalPanel, responsePanel, usagePanel);
    const tabs = el("nav", { class: "command-detail-tabs", "aria-label": "إعدادات الأمر" });
    const tabDefinitions = [
      ["general", "عام", generalPanel],
      ["response", "رد البوت", responsePanel],
      ["usage", "طريقة الاستخدام", usagePanel],
    ];
    const setActiveTab = (key) => {
      activeTab = key;
      tabs.querySelectorAll(".command-detail-tab").forEach((tab) => {
        const selected = tab.dataset.tab === key;
        tab.classList.toggle("active", selected);
        tab.setAttribute("aria-selected", String(selected));
      });
      tabDefinitions.forEach(([panelKey, , panel]) => {
        const isActive = panelKey === key;
        panel.hidden = !isActive;
        panel.style.display = isActive ? "grid" : "none";
      });
    };
    tabDefinitions.forEach(([key, label]) => tabs.append(el("button", {
      class: `command-detail-tab${key === activeTab ? " active" : ""}`,
      type: "button",
      role: "tab",
      "aria-selected": String(key === activeTab),
      "data-tab": key,
      text: label,
      onClick: () => setActiveTab(key),
    })));
    setActiveTab(activeTab);
    let savingDetails = false;
    async function saveDetails() {
      if (savingDetails) return;
      const pendingAlias = aliasInput.value.trim().replace(/^[/!]/, "");
      if (pendingAlias) {
        if (/\s/.test(pendingAlias) || pendingAlias.length > 80) {
          toast("كل اسم بديل يجب أن يكون كلمة واحدة وبحد أقصى 80 حرفاً", "warn");
          aliasInput.focus();
          return;
        }
        if (!aliasValues.some((item) => item.toLocaleLowerCase() === pendingAlias.toLocaleLowerCase())) {
          if (aliasValues.length >= 20) {
            toast("يمكن إضافة 20 اسماً بديلاً كحد أقصى للأمر", "warn");
            aliasInput.focus();
            return;
          }
          aliasValues = [...aliasValues, pendingAlias];
        }
        aliasInput.value = "";
        renderAliases();
      }
      const aliases = parseCommandAliases(aliasValues);
      if (aliases === null) return;
      savingDetails = true;
      try {
        const policySaved = await saveCommandPolicy(
          command,
          detailEnabled,
          rolesEditor.values(),
          channelsEditor.values(),
          aliases,
          {
            auto_delete_seconds: selectedAutoDelete,
            response_mode: responseStyleSelect.value,
            custom_template: responseTemplate.value,
          },
        );
        if (!policySaved) return;
        pulse();
        toast("تم حفظ إعدادات الأمر وتطبيقها على السيرفر", "success", 2600);
      } finally {
        savingDetails = false;
      }
    }
    const modal = el("aside", { class: "modal command-detail-drawer" },
      el("div", { class: "drawer-head" },
        el("div", {}, el("span", { class: "eyebrow", text: `${command.cog || "COMMANDS"} / POLICY` }), el("h2", {}, el("span", { text: visual.name }), el("small", { text: ` (${command.command_name})` }))),
        el("button", { class: "icon-action", type: "button", text: "×", title: "إغلاق", onClick: closeCommandDetail }),
      ),
      el("div", { class: "detail-status-line" },
        el("span", { class: `status-pill ${detailEnabled ? "on" : "off"}`, text: detailEnabled ? "مفعّل" : "معطّل" }),
        el("span", { class: "detail-meta", text: command.configured ? "سياسة مخصصة" : "إعداد افتراضي" }),
      ),
      el("p", { class: "command-detail-description", text: visual.description }),
      tabs,
      tabPanels,
      el("div", { class: "modal-actions" },
        el("button", { class: "btn ghost", type: "button", text: "إلغاء", onClick: closeCommandDetail }),
        el("button", { class: "btn primary command-detail-save", type: "button", text: "حفظ التعديلات ✓", onClick: saveDetails }),
      ),
    );
    back.onclick = (event) => { if (event.target === back) closeCommandDetail(); };
    back.append(modal);
    document.body.append(back);
    aliasInput.focus();
  }
  async function bulkUpdateCommands(enabled) {
    const selected = (state.commandStudio.commands || []).filter((command) => state.selectedCommandIds.includes(String(command.command_name)));
    if (!selected.length) return toast("حدد أمراً واحداً على الأقل", "info", 2200);
    if (!confirm(`${enabled ? "تفعيل" : "تعطيل"} ${selected.length} أوامر؟`)) return;
    let changed = 0;
    for (const command of selected) {
      try {
        const r = await api(`api/guild/${state.guild.id}/commands/toggle`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
          body: JSON.stringify({ command_name: command.command_name, enabled, allowed_roles: [...commandRoles(command)] }),
        });
        const data = await readJson(r, {});
        if (r.ok && data.command) {
          Object.assign(command, data.command);
          changed++;
        }
      } catch (error) {
        if (error.message === "unauth") return;
      }
    }
    state.selectedCommandIds = [];
    pulse();
    toast(`تم تحديث ${changed} من ${selected.length} أوامر`, changed === selected.length ? "success" : "warn", 2500);
    renderPage();
  }
  function commandPolicyMatrix() {
    const roles = (state.commandStudio.roles || []).slice(0, 6);
    const commands = commandListForWorkspace().slice(0, 30);
    const table = el("div", { class: "policy-matrix", role: "table" });
    const head = el("div", { class: "policy-matrix-row matrix-head", role: "row" },
      el("span", { role: "columnheader", text: "الأمر" }),
      ...roles.map((role) => el("span", { role: "columnheader", text: `@${role.name}` })),
    );
    table.append(head);
    if (!commands.length) {
      table.append(el("div", { class: "empty studio-empty", text: "لا توجد بيانات كافية لبناء مصفوفة السياسات" }));
      return table;
    }
    commands.forEach((command) => {
      const allowed = commandRoles(command);
      table.append(el("div", { class: "policy-matrix-row", role: "row" },
        el("button", { class: "matrix-command", type: "button", text: `/${command.command_name}`, onClick: () => openCommandDetail(command) }),
        ...roles.map((role) => el("button", {
          class: `matrix-cell ${allowed.has(String(role.id)) ? "allowed" : ""}`,
          type: "button",
          title: allowed.has(String(role.id)) ? "مسموح" : "غير مسموح",
          text: allowed.has(String(role.id)) ? "●" : "—",
          onClick: () => openCommandDetail(command),
        })),
      ));
    });
    return table;
  }
  function insertVariable(textarea, value) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    textarea.setRangeText(value, start, end, "end");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
    pulse();
  }
  function setRuleForm(rule = null) {
    const form = $("#auto-responder-form");
    if (!form) return;
    form.dataset.ruleId = rule?.id || "";
    form.elements.trigger.value = rule?.trigger || "";
    form.elements.response.value = rule?.response || "";
    form.elements.cooldown_seconds.value = rule?.cooldown_seconds ?? 5;
    form.elements.channel_id.value = rule?.channel_id || "";
    form.elements.target_type.value = rule?.target_type || "everyone";
    form.elements.target_role_id.value = rule?.target_type === "role" ? String(rule?.target_id || "") : "";
    if (form._setAutoMember) {
      form._setAutoMember(rule?.target_type === "user" ? String(rule?.target_id || "") : "");
    } else {
      form.elements.target_user_id.value = rule?.target_type === "user" ? String(rule?.target_id || "") : "";
    }
    if (form._setAutoReaction) {
      form._setAutoReaction(rule?.reaction_emoji || "");
    } else {
      form.elements.reaction_emoji.value = rule?.reaction_emoji || "";
    }
    updateAutoTargetFields(form);
    form.elements.trigger.dispatchEvent(new Event("input", { bubbles: true }));
    form.elements.response.dispatchEvent(new Event("input", { bubbles: true }));
    form.querySelectorAll("[data-match-type]").forEach((button) => {
      button.classList.toggle("active", button.dataset.matchType === (rule?.match_type || "exact"));
    });
    const title = $("#auto-form-title");
    if (title) title.textContent = rule ? "تعديل قاعدة الرد" : "إنشاء رد تلقائي";
    form.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function updateAutoTargetFields(form) {
    const type = form.elements.target_type.value;
    form.querySelector(".auto-role-target")?.toggleAttribute("hidden", type !== "role");
    form.querySelector(".auto-user-target")?.toggleAttribute("hidden", type !== "user");
  }
  async function saveAutoResponder(form) {
    const selected = form.querySelector(".match-badge.active")?.dataset.matchType || "exact";
    const body = {
      trigger: form.elements.trigger.value.trim(),
      match_type: selected,
      response: form.elements.response.value,
      cooldown_seconds: Number(form.elements.cooldown_seconds.value),
      channel_id: form.elements.channel_id.value || null,
      target_type: form.elements.target_type.value,
      target_id: form.elements.target_type.value === "role"
        ? form.elements.target_role_id.value
        : form.elements.target_type.value === "user"
          ? form.elements.target_user_id.value.trim()
          : 0,
      reaction_emoji: form.elements.reaction_emoji.value.trim(),
    };
    if (!body.trigger || (!body.response.trim() && !body.reaction_emoji)) {
      toast("أدخل المشغل ونص الرد أو اختر إيموجي التفاعل");
      return;
    }
    try {
      const r = await api(`api/guild/${state.guild.id}/auto-responses`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify(body),
      });
      const data = await readJson(r, {});
      if (!r.ok) {
        toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ قاعدة الرد");
        return;
      }
      const index = state.autoResponses.findIndex((item) => String(item.id) === String(data.rule.id));
      if (index >= 0) state.autoResponses[index] = data.rule;
      else state.autoResponses.push(data.rule);
      pulse();
      toast("✅ تم حفظ قاعدة الرد وتفعيلها", "success", 2500);
      renderPage();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
    }
  }
  async function deleteAutoResponder(rule) {
    if (!confirm(`حذف قاعدة «${rule.trigger}»؟`)) return;
    try {
      const r = await api(`api/guild/${state.guild.id}/auto-responses/${rule.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": state.session.csrf },
      });
      const data = await readJson(r, {});
      if (!r.ok) {
        toast(data.error === "auto_responder_not_found" ? "القاعدة غير موجودة" : "تعذر حذف القاعدة");
        return;
      }
      state.autoResponses = state.autoResponses.filter((item) => String(item.id) !== String(rule.id));
      pulse();
      toast("تم حذف قاعدة الرد", "success", 2200);
      renderPage();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
    }
  }
  function formatDuration(seconds) {
    if (seconds == null || Number.isNaN(Number(seconds))) return "—";
    const total = Math.max(0, Math.round(Number(seconds)));
    if (total < 60) return `${total}ث`;
    if (total < 3600) return `${Math.round(total / 60)}د`;
    return `${(total / 3600).toFixed(1)}س`;
  }
  async function refreshTickets() {
    const id = state.guild.id;
    try {
      const [active, archive, kpis, canned, configResponse, panels, analytics, settings, permissions, categories, blacklist, ratingsResponse] = await Promise.all([
        api(`api/guild/${id}/tickets/active`),
        api(`api/guild/${id}/tickets/archive?q=${encodeURIComponent(state.ticketSearch)}`),
        api(`api/guild/${id}/tickets/kpis`),
        api(`api/guild/${id}/tickets/canned`),
        api(`api/guild/${id}/tickets/config`),
        api(`api/guild/${id}/tickets/panels`),
        api(`api/guild/${id}/tickets/analytics`),
        api(`api/guilds/${id}/tickets/settings`),
        api(`api/guilds/${id}/tickets/permissions`),
        api(`api/guilds/${id}/tickets/categories`),
        api(`api/guilds/${id}/tickets/blacklist`),
        api(`api/guilds/${id}/tickets/ratings`),
      ]);
      state.tickets = {
        active: (await readJson(active, { tickets: state.tickets.active })).tickets || [],
        archive: (await readJson(archive, { tickets: state.tickets.archive })).tickets || [],
        kpis: (await readJson(kpis, { kpis: state.tickets.kpis })).kpis || [],
        canned: (await readJson(canned, { responses: state.tickets.canned })).responses || [],
      };
      if (configResponse.ok) {
        const configData = await readJson(configResponse, {});
        state.ticketConfig = { ...state.ticketConfig, ...(configData.config || {}) };
        if (Array.isArray(configData.categories) && configData.categories.length) {
          state.ticketCategories = configData.categories;
        }
      }
      if (panels.ok) state.ticketPanels = (await readJson(panels, { panels: [] })).panels || [];
      if (analytics.ok) state.ticketAnalytics = {
        ...state.ticketAnalytics,
        ...(await readJson(analytics, state.ticketAnalytics)),
      };
      if (settings.ok) {
        const settingsData = await readJson(settings, {});
        state.ticketSettings = settingsData.config || null;
        state.ticketConfig = { ...state.ticketConfig, ...(settingsData.config || {}) };
        state.ticketPermissions = settingsData.permissions || state.ticketPermissions;
      }
      if (permissions.ok) {
        const permissionsData = await readJson(permissions, {});
        state.ticketPermissions = permissionsData.permissions || state.ticketPermissions;
      }
      if (categories.ok) {
        const categoriesData = await readJson(categories, {});
        if (Array.isArray(categoriesData.categories) && categoriesData.categories.length) {
          state.ticketCategories = categoriesData.categories.map((item) => ({
            ...item,
            key: item.key || item.name,
            welcome_msg: item.welcome_msg || item.welcome_message || "",
            support_role_ids: item.support_role_ids || item.staff_role_ids || [],
          }));
        }
      }
      if (blacklist.ok) state.ticketBlacklist = (await readJson(blacklist, { entries: [] })).entries || [];
      if (ratingsResponse.ok) state.ticketRatings = (await readJson(ratingsResponse, { ratings: [] })).ratings || [];
      renderPage();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحديث مركز التذاكر");
    }
  }
  async function loadAndHydratePermissions(guildId) {
    const response = await api(`api/guilds/${guildId}/tickets/permissions`);
    const data = await readJson(response, {});
    if (!response.ok) return {};
    const permissions = data.permissions && typeof data.permissions === "object"
      ? data.permissions
      : {};
    if (state.guild?.id !== guildId) return permissions;
    state.ticketPermissions = permissions;
    document.querySelectorAll(".perm-checkbox").forEach((input) => {
      const action = input.dataset.permissionAction;
      const roleId = String(input.dataset.roleId || input.value);
      const checked = Array.isArray(permissions[action])
        && permissions[action].map(String).includes(roleId);
      input.checked = checked;
      input.classList.toggle("checked", checked);
      input.classList.toggle("is-checked", checked);
      input.closest("label")?.classList.toggle("is-checked", checked);
    });
    return permissions;
  }
  async function deployTicketPanel(form) {
    const channelId = form.elements.target_channel_id.value;
    if (!channelId) return toast("اختر قناة نشر اللوحة");
    const color = form.elements.embed_color.value || "#5865f2";
    try {
      const r = await api(`api/guild/${state.guild.id}/tickets/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify({
          target_channel_id: channelId,
          categories: state.ticketCategories,
          embed_title: form.elements.embed_title.value.trim(),
          embed_description: form.elements.embed_description.value.trim(),
          embed_color: color,
          footer_text: form.elements.footer_text.value.trim(),
        }),
      });
      const data = await readJson(r, {});
      if (!r.ok) {
        toast(data.fields ? Object.values(data.fields)[0] : "تعذر نشر لوحة التذاكر");
        return;
      }
      pulse();
      toast("نُشرت لوحة التذاكر للسيرفر", "success", 3000);
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال لنشر لوحة التذاكر");
    }
  }
  const ticketStatusLabels = {
    active: "قيد المعالجة",
    waiting_user: "بانتظار العميل",
    waiting_staff: "بانتظار فريق الدعم",
    closed: "مغلقة",
  };
  const ticketPriorityLabels = {
    normal: "عادية",
    high: "عالية",
    management: "تصعيد إداري",
  };
  async function ticketAction(ticket, action, payload = {}) {
    let staffId = null;
    if (action === "reassign") {
      staffId = prompt("أدخل Discord ID للموظف الجديد:", ticket.claimed_by || "");
      if (!staffId) return;
    }
    if (action === "close" && !confirm(`إغلاق التذكرة #${ticket.id} وأرشفتها؟`)) return null;
    try {
      const r = await writeApi(`api/guild/${state.guild.id}/tickets/action`, {
          ticket_id: ticket.id,
          action,
          staff_id: staffId,
          reason: "أُغلقت من لوحة الإدارة",
          ...payload,
      });
      const data = await readJson(r, {});
      if (!r.ok) {
        toast(data.fields ? Object.values(data.fields)[0] : "تعذر تنفيذ الإجراء");
        return null;
      }
      pulse();
      const messages = {
        close: "تم إغلاق التذكرة وأرشفتها",
        reassign: "تمت إعادة إسناد التذكرة",
        reopen: "تمت إعادة فتح التذكرة",
        note: "تم حفظ الملاحظة الداخلية",
        status: "تم تحديث حالة التذكرة",
        priority: "تم تحديث أولوية التذكرة",
      };
      toast(messages[action] || "تم تحديث التذكرة", "success", 2400);
      await refreshTickets();
      return data.ticket || data.result || null;
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      return null;
    }
  }
  async function openTicketDetail(ticket) {
    try {
      const r = await api(`api/guild/${state.guild.id}/tickets/detail/${ticket.id}`);
      const data = await readJson(r, {});
      if (!r.ok || !data.ticket) return toast("تعذر تحميل تفاصيل التذكرة");
      const current = data.ticket;
      const notes = data.notes || [];
      const closeDrawer = () => $(".ticket-drawer")?.remove();
      const statusSelect = el(
        "select",
        { class: "ticket-detail-select", "aria-label": "حالة التذكرة" },
        Object.entries(ticketStatusLabels).map(([value, label]) =>
          el("option", { value, text: label }),
        ),
      );
      statusSelect.value = current.status || "active";
      statusSelect.disabled = current.status === "closed";
      statusSelect.onchange = async () => {
        await ticketAction(current, "status", { status: statusSelect.value });
        closeDrawer();
      };
      const prioritySelect = el(
        "select",
        { class: "ticket-detail-select", "aria-label": "أولوية التذكرة" },
        Object.entries(ticketPriorityLabels).map(([value, label]) =>
          el("option", { value, text: label }),
        ),
      );
      prioritySelect.value = current.priority || "normal";
      prioritySelect.disabled = current.status === "closed";
      prioritySelect.onchange = async () => {
        await ticketAction(current, "priority", { priority: prioritySelect.value });
        closeDrawer();
      };
      const noteForm = el("form", { class: "ticket-note-form" },
        el("textarea", {
          name: "content",
          class: "studio-textarea",
          maxlength: "2000",
          placeholder: "ملاحظة لا تظهر لصاحب التذكرة…",
          required: true,
        }),
        el("button", { class: "btn primary", type: "submit", text: "حفظ الملاحظة" }),
      );
      noteForm.onsubmit = async (event) => {
        event.preventDefault();
        const content = noteForm.elements.content.value.trim();
        if (!content) return;
        await ticketAction(current, "note", { content });
        closeDrawer();
        await openTicketDetail(current);
      };
      const notesList = el("div", { class: "ticket-notes-list" });
      if (!notes.length) {
        notesList.append(el("div", { class: "empty studio-empty", text: "لا توجد ملاحظات داخلية" }));
      } else {
        notes.forEach((note) => notesList.append(el("article", { class: "ticket-note" },
          el("div", { class: "ticket-note-meta", text: `موظف #${note.staff_id} · ${note.created_at || ""}` }),
          el("p", { text: note.content }),
        )));
      }
      const intake = el("div", { class: "ticket-intake-grid" });
      Object.entries(current.intake_data || {}).forEach(([key, value]) => intake.append(
        el("div", { class: "ticket-intake-item" },
          el("small", { text: key }),
          el("strong", { text: String(value || "—") }),
        ),
      ));
      if (!intake.children.length) intake.append(el("small", { class: "muted", text: "لا توجد بيانات إضافية" }));
      const actions = el("div", { class: "ticket-detail-actions" },
        el("a", {
          class: "btn ghost",
          href: `https://discord.com/channels/${state.guild.id}/${current.channel_id}`,
          target: "_blank",
          text: "فتح القناة ↗",
        }),
      );
      if (current.status === "closed") {
        actions.append(el("button", {
          class: "btn primary",
          type: "button",
          text: "إعادة فتح التذكرة",
          onClick: async () => { await ticketAction(current, "reopen"); closeDrawer(); },
        }));
      } else {
        actions.append(el("button", {
          class: "btn danger",
          type: "button",
          text: "إغلاق وأرشفة",
          onClick: async () => { await ticketAction(current, "close"); closeDrawer(); },
        }));
      }
      const drawer = el("aside", { class: "ticket-drawer ticket-detail-drawer", role: "dialog", "aria-modal": "true" },
        el("div", { class: "ticket-drawer-head" },
          el("div", {},
            el("span", { class: "eyebrow", text: `${current.category_label} / TICKET #${current.id}` }),
            el("h3", { text: current.subject }),
          ),
          el("button", { class: "icon-action", type: "button", text: "×", "aria-label": "إغلاق", onClick: closeDrawer }),
        ),
        el("div", { class: "ticket-detail-body" },
          el("div", { class: "ticket-detail-summary" },
            el("span", { class: `priority-tag ${current.priority || "normal"}`, text: ticketPriorityLabels[current.priority] || current.priority || "عادية" }),
            el("span", { class: "status-tag", text: ticketStatusLabels[current.status] || current.status }),
            el("span", { class: "ticket-detail-id", text: `العضو #${current.user_id}` }),
          ),
          el("p", { class: "ticket-detail-description", text: current.details || "بدون تفاصيل" }),
          el("div", { class: "ticket-detail-controls" },
            el("label", {}, "الحالة", statusSelect),
            el("label", {}, "الأولوية", prioritySelect),
          ),
          el("div", { class: "ticket-detail-section" }, el("h4", { text: "بيانات نموذج الفتح" }), intake),
          el("div", { class: "ticket-detail-section" }, el("h4", { text: "ملاحظة داخلية" }), noteForm),
          el("div", { class: "ticket-detail-section" }, el("h4", { text: "سجل الملاحظات" }), notesList),
          actions,
        ),
      );
      document.body.append(drawer);
      pulse();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحميل تفاصيل التذكرة");
    }
  }
  async function openTicketTranscript(ticket) {
    try {
      const r = await api(`api/guild/${state.guild.id}/tickets/transcript/${ticket.id}`);
      if (!r.ok) return toast("السجل غير متاح");
      const source = await r.text();
       const frame = el("iframe", {
         class: "ticket-transcript-frame",
         title: `Transcript #${ticket.id}`,
         sandbox: "",
       });
      frame.srcdoc = source;
      const drawer = el("aside", { class: "ticket-drawer", role: "dialog", "aria-modal": "true" },
        el("div", { class: "ticket-drawer-head" },
          el("div", {}, el("span", { class: "eyebrow", text: "TRANSCRIPT VAULT" }), el("h3", { text: `سجل التذكرة #${ticket.id}` })),
          el("button", { class: "icon-action", type: "button", text: "×", "aria-label": "إغلاق", onClick: () => drawer.remove() }),
        ),
        frame,
      );
      document.body.append(drawer);
      pulse();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحميل السجل");
    }
  }
  async function saveCannedResponse(form) {
    const body = {
      id: form.dataset.id || null,
      title: form.elements.title.value.trim(),
      content: form.elements.content.value.trim(),
      category: form.elements.category.value.trim() || "عام",
      shortcut: form.elements.shortcut.value.trim() || null,
      sticker_id: form.elements.sticker_id.value || null,
    };
    if (!body.title || !body.content) return toast("أدخل عنوان ونص الرد الجاهز");
    try {
      const r = await api(`api/guild/${state.guild.id}/tickets/canned`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify(body),
      });
      const data = await readJson(r, {});
      if (!r.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ الرد الجاهز");
      pulse();
      toast("تم حفظ الرد الجاهز", "success", 2000);
      await refreshTickets();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
    }
  }
  async function deleteCannedResponse(item) {
    try {
      const r = await api(`api/guild/${state.guild.id}/tickets/canned`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.session.csrf },
        body: JSON.stringify({ action: "delete", id: item.id }),
      });
      if (!r.ok) return toast("تعذر حذف الرد الجاهز");
      await refreshTickets();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
    }
  }
  function ticketEmojiNode(value, className = "") {
    const raw = String(value || "🎫");
    const guildEmojis = [
      ...(Array.isArray(state.meta?.emojis) ? state.meta.emojis : []),
      ...(Array.isArray(state.meta?.guild_emojis) ? state.meta.guild_emojis : []),
    ];
    const custom = guildEmojis.find((emoji) =>
      (emoji.token || `<${emoji.animated ? "a" : ""}:${emoji.name}:${emoji.id}>`) === raw,
    );
    return custom?.url
      ? el("img", { class: `ticket-emoji-image ${className}`.trim(), src: custom.url, alt: custom.name || "" })
      : el("span", { class: `ticket-emoji-glyph ${className}`.trim(), text: raw });
  }
  function ticketCategoryEditor() {
    const wrap = el("div", { class: "ticket-category-list" });
    const defaultTicketEmojis = [
      ["🎫", "تذكرة"], ["🛠️", "دعم تقني"], ["❓", "استفسار"], ["🎁", "جوائز"],
      ["📹", "صناع محتوى"], ["🎮", "ألعاب"], ["📝", "تقديم"], ["🔒", "خاص"],
      ["⚠️", "بلاغ"], ["💎", "مميز"], ["📣", "اقتراح"], ["🛒", "شراء"],
      ["✅", "مكتمل"], ["🚨", "عاجل"], ["⭐", "نجمة"], ["🤝", "مساعدة"],
    ];
    const emojiCatalog = () => {
      const guildEmojis = [
        ...(Array.isArray(state.meta?.emojis) ? state.meta.emojis : []),
        ...(Array.isArray(state.meta?.guild_emojis) ? state.meta.guild_emojis : []),
      ];
      const seen = new Set(defaultTicketEmojis.map(([value]) => value));
      return [
        ...defaultTicketEmojis.map(([value, name]) => ({ value, name, custom: false })),
        ...guildEmojis
          .map((emoji) => ({
            value: emoji.token || `<${emoji.animated ? "a" : ""}:${emoji.name}:${emoji.id}>`,
            name: emoji.name || "server emoji",
            url: emoji.url || emoji.image || emoji.icon_url,
            custom: true,
          }))
          .filter((emoji) => emoji.value && !seen.has(emoji.value) && (seen.add(emoji.value), true)),
      ];
    };
    const emojiPicker = (category) => {
      const root = el("div", { class: "ticket-emoji-picker" });
      const input = el("input", {
        class: "studio-input ticket-emoji-input",
        value: category.emoji || "🎫",
        maxlength: "100",
        placeholder: "🎫 أو إيموجي السيرفر",
        "aria-label": "إيموجي القسم",
      });
      const preview = el("span", { class: "ticket-emoji-preview", "aria-hidden": "true" });
      const search = el("input", {
        class: "studio-input ticket-emoji-search",
        type: "search",
        placeholder: "ابحث في الإيموجيات…",
        "aria-label": "البحث عن إيموجي",
      });
      const grid = el("div", { class: "ticket-emoji-grid" });
      const popover = el("div", { class: "ticket-emoji-popover", hidden: true }, search, grid);
      const renderPreview = (value) => {
        preview.replaceChildren(ticketEmojiNode(value));
      };
      const setEmoji = (value) => {
        category.emoji = String(value || "🎫").trim() || "🎫";
        input.value = category.emoji;
        renderPreview(category.emoji);
        renderGrid(search.value);
      };
      const renderGrid = (query = "") => {
        const normalized = query.trim().toLocaleLowerCase();
        const items = emojiCatalog().filter((item) =>
          !normalized || `${item.name} ${item.value}`.toLocaleLowerCase().includes(normalized),
        );
        grid.replaceChildren(
          ...items.map((item) => el("button", {
            class: `ticket-emoji-chip${item.value === category.emoji ? " active" : ""}`,
            type: "button",
            title: item.name,
            onClick: () => {
              setEmoji(item.value);
              popover.hidden = true;
              toggle.setAttribute("aria-expanded", "false");
            },
          }, item.url
            ? el("img", { src: item.url, alt: item.name })
            : el("span", { class: "ticket-emoji-glyph", text: item.value }))),
        );
        if (!grid.children.length) grid.append(el("small", { class: "ticket-emoji-empty", text: "لا توجد نتائج" }));
      };
      const toggle = el("button", {
        class: "ticket-emoji-toggle",
        type: "button",
        "aria-expanded": "false",
        title: "اختيار إيموجي القسم",
        text: "اختيار إيموجي",
        onClick: () => {
          const open = popover.hidden;
          popover.hidden = !open;
          toggle.setAttribute("aria-expanded", String(open));
          if (open) {
            renderGrid(search.value);
            search.focus();
          }
        },
      });
      input.addEventListener("input", () => {
        category.emoji = input.value.trim() || "🎫";
        renderPreview(category.emoji);
      });
      search.addEventListener("input", () => renderGrid(search.value));
      renderPreview(input.value);
      renderGrid();
      root.append(el("div", { class: "ticket-emoji-control" }, preview, input, toggle), popover);
      return root;
    };
    state.ticketCategories.forEach((category, index) => {
      const label = el("input", { class: "studio-input", value: category.label, maxlength: "80" });
      label.oninput = () => { state.ticketCategories[index].label = label.value; };
      const description = el("input", {
        class: "studio-input",
        value: category.description || "",
        maxlength: "100",
        placeholder: "وصف مختصر يظهر في القائمة",
      });
      description.oninput = () => { state.ticketCategories[index].description = description.value; };
      const emoji = emojiPicker(category);
      const parent = el("select", { class: "studio-input", "aria-label": `فئة قنوات ${category.label}` },
        el("option", { value: "" }, "بدون فئة أب"),
        (state.meta?.categories || []).map((item) => el("option", { value: item.id }, item.name)),
      );
      parent.value = category.category_id || "";
      parent.onchange = () => { state.ticketCategories[index].category_id = parent.value || null; };
      const roles = el("select", { class: "ticket-role-select", multiple: "multiple", "aria-label": `رتب دعم ${category.label}` });
      (state.commandStudio.roles || []).forEach((role) => {
        const option = el("option", { value: role.id }, role.name);
        option.selected = (category.support_role_ids || []).map(String).includes(String(role.id));
        roles.append(option);
      });
      roles.onchange = () => {
        state.ticketCategories[index].support_role_ids = [...roles.selectedOptions].map((option) => option.value);
        state.ticketCategories[index].role_id = state.ticketCategories[index].support_role_ids[0] || null;
      };
      const seniorRoles = el("select", { class: "ticket-role-select ticket-senior-role-select", multiple: "multiple", "aria-label": `رتب التصعيد ${category.label}` });
      (state.commandStudio.roles || []).forEach((role) => {
        const option = el("option", { value: role.id }, role.name);
        option.selected = (category.senior_role_ids || []).map(String).includes(String(role.id));
        seniorRoles.append(option);
      });
      seniorRoles.onchange = () => {
        state.ticketCategories[index].senior_role_ids = [...seniorRoles.selectedOptions].map((option) => option.value);
      };
      const fields = el("div", { class: "ticket-intake-editor" });
      const welcome = el("textarea", {
        class: "studio-textarea",
        maxlength: "2000",
        rows: "2",
        placeholder: "رسالة ترحيبية اختيارية داخل التذكرة",
      });
      welcome.value = category.welcome_msg || "";
      welcome.oninput = () => { state.ticketCategories[index].welcome_msg = welcome.value; };
      const renderFields = () => {
        fields.replaceChildren();
        const intakeFields = category.intake_fields || [];
        intakeFields.forEach((field, fieldIndex) => {
          const key = el("input", { class: "studio-input", value: field.key || `field_${fieldIndex + 1}`, maxlength: "40", placeholder: "المفتاح" });
          const fieldLabel = el("input", { class: "studio-input", value: field.label || "", maxlength: "45", placeholder: "اسم الحقل" });
          const placeholder = el("input", { class: "studio-input", value: field.placeholder || "", maxlength: "100", placeholder: "النص الإرشادي" });
          const required = el("input", { type: "checkbox", checked: field.required === true });
          key.oninput = () => { field.key = key.value; };
          fieldLabel.oninput = () => { field.label = fieldLabel.value; };
          placeholder.oninput = () => { field.placeholder = placeholder.value; };
          required.onchange = () => { field.required = required.checked; };
          fields.append(el("div", { class: "ticket-intake-row" },
            key, fieldLabel, placeholder,
            el("label", { class: "ticket-required-toggle" }, required, "إلزامي"),
            el("button", { class: "icon-action danger", type: "button", text: "×", title: "حذف الحقل", onClick: () => {
              category.intake_fields.splice(fieldIndex, 1);
              renderFields();
            } }),
          ));
        });
        if (intakeFields.length < 3) {
          fields.append(el("button", { class: "btn ghost ticket-add-field", type: "button", text: "＋ إضافة حقل في نموذج الفتح", onClick: () => {
            category.intake_fields = category.intake_fields || [];
            category.intake_fields.push({ key: `field_${category.intake_fields.length + 1}`, label: "", placeholder: "", required: false });
            renderFields();
          } }));
        }
      };
      category.intake_fields = Array.isArray(category.intake_fields) ? category.intake_fields : [];
      category.support_role_ids = Array.isArray(category.support_role_ids) ? category.support_role_ids : [];
      category.senior_role_ids = Array.isArray(category.senior_role_ids) ? category.senior_role_ids : [];
      renderFields();
      wrap.append(el("div", { class: "ticket-category-row" },
        el("div", { class: "ticket-category-head" },
          el("span", { class: "ticket-category-index", text: String(index + 1).padStart(2, "0") }),
          emoji,
          el("button", {
            class: "icon-action danger ticket-remove-category",
            type: "button",
            text: "×",
            title: state.ticketCategories.length > 1 ? "حذف القسم" : "يجب إبقاء قسم واحد على الأقل",
            disabled: state.ticketCategories.length <= 1,
            onClick: () => {
              if (state.ticketCategories.length <= 1) return toast("يجب إبقاء قسم دعم واحد على الأقل");
              if (!confirm(`حذف قسم «${category.label || "بدون اسم"}»؟`)) return;
              state.ticketCategories.splice(index, 1);
              renderPage();
            },
          }),
        ),
        el("label", { class: "ticket-editor-label" }, "اسم القسم", label),
         el("label", { class: "ticket-editor-label" }, "وصف القائمة", description),
         el("label", { class: "ticket-editor-label" }, "الفئة الأب للقنوات", parent),
        el("label", { class: "ticket-editor-label" }, "فريق الدعم", roles),
        el("label", { class: "ticket-editor-label" }, "رتب التصعيد", seniorRoles),
         el("label", { class: "ticket-editor-label" }, "رسالة الترحيب", welcome),
        el("small", { class: "ticket-field-caption", text: "الرتب المحددة تمنح صلاحية متابعة هذا القسم والتصعيد الإداري." }),
        el("small", { class: "ticket-field-caption", text: "حقول نموذج الفتح (اختيارية، حتى 3)" }),
        fields,
      ));
    });
    return wrap;
  }
  function ticketsView() {
    const studio = state.commandStudio || { channels: [] };
    const ticketConfig = state.ticketConfig || {};
    const colorValue = Number(ticketConfig.embed_color);
    const embedColor = `#${(Number.isFinite(colorValue) ? colorValue : 0x5865F2)
      .toString(16).padStart(6, "0").slice(-6)}`;
    const launchForm = el("form", { class: "ticket-launcher-form" },
      el("div", { class: "ticket-preview-card" },
        el("div", { class: "ticket-preview-glow" }),
        el("span", { class: "eyebrow", text: "LIVE LAUNCHER PREVIEW" }),
        el("h3", { text: ticketConfig.embed_title || "مركز الدعم والتذاكر" }),
        el("p", { text: "اختر التصنيف المناسب وسيتولى فريق الدعم متابعة طلبك في قناة خاصة." }),
        el("div", { class: "ticket-preview-buttons" },
            state.ticketCategories.map((category) => el("span", { class: "ticket-preview-button" }, category.emoji || "T", category.label)),
        ),
      ),
      el("label", { class: "ticket-form-label" }, "قناة نشر لوحة التذاكر",
        el("select", { name: "target_channel_id", class: "studio-input" },
          el("option", { value: "" }, "اختر قناة نصية"),
          (studio.channels || []).map((channel) => el("option", { value: channel.id }, `#${channel.name}`)),
        ),
      ),
      el("div", { class: "ticket-config-grid" },
        el("label", { class: "ticket-editor-label" }, "عنوان الـ Embed",
          el("input", {
            name: "embed_title",
            class: "studio-input",
            maxlength: "256",
            value: ticketConfig.embed_title || "مركز الدعم والتذاكر",
            oninput: (event) => { state.ticketConfig.embed_title = event.target.value; },
          }),
        ),
        el("label", { class: "ticket-editor-label" }, "لون اللوحة",
          el("input", {
            name: "embed_color",
            class: "studio-input",
            type: "color",
            value: embedColor,
            oninput: (event) => { state.ticketConfig.embed_color = parseInt(event.target.value.slice(1), 16); },
          }),
        ),
        el("label", { class: "ticket-editor-label" }, "تذييل اللوحة",
          el("input", {
            name: "footer_text",
            class: "studio-input",
            maxlength: "2048",
            value: ticketConfig.footer_text || "",
            oninput: (event) => { state.ticketConfig.footer_text = event.target.value; },
          }),
        ),
        el("label", { class: "ticket-editor-label ticket-config-wide" }, "وصف اللوحة",
          el("textarea", {
            name: "embed_description",
            class: "studio-textarea",
            maxlength: "4096",
            rows: "3",
            placeholder: "النص الظاهر أعلى قائمة الأقسام",
          }),
        ),
      ),
      el("div", { class: "ticket-category-heading" },
        el("div", {}, el("h4", { text: "تصنيفات التذاكر" }), el("small", { text: "أضف أقسام الدعم وحدد فريق المتابعة ورتب التصعيد لكل قسم." })),
        el("div", { class: "ticket-category-heading-actions" },
          el("span", { class: "live-dot", text: `${state.ticketCategories.length} تصنيفات` }),
          el("button", {
            class: "btn ghost ticket-add-category",
            type: "button",
            text: "＋ قسم جديد",
            onClick: () => {
              if (state.ticketCategories.length >= 25) return toast("لا يمكن إضافة أكثر من 25 قسماً");
              const next = state.ticketCategories.length + 1;
              state.ticketCategories.push({
                key: `support_${Date.now()}_${next}`,
                label: `قسم دعم ${next}`,
                emoji: "T",
                support_role_ids: [],
                senior_role_ids: [],
                intake_fields: [],
              });
              renderPage();
            },
          }),
        ),
      ),
      ticketCategoryEditor(),
      el("button", { class: "btn primary ticket-deploy", type: "submit", text: "نشر لوحة التذاكر للسيرفر" }),
    );
    launchForm.elements.embed_description.value = ticketConfig.embed_description || "";
    launchForm.elements.embed_description.oninput = (event) => {
      state.ticketConfig.embed_description = event.target.value;
    };
    launchForm.onsubmit = (event) => { event.preventDefault(); deployTicketPanel(launchForm); };

    const kpis = state.tickets.kpis || [];
    const total = kpis.reduce((sum, item) => sum + Number(item.tickets_handled || 0), 0);
    const responseValues = kpis.filter((item) => item.avg_response_seconds != null).map((item) => Number(item.avg_response_seconds));
    const ratingValues = kpis.filter((item) => item.avg_rating != null).map((item) => Number(item.avg_rating));
    const avgResponse = responseValues.length ? responseValues.reduce((a, b) => a + b, 0) / responseValues.length : null;
    const avgRating = ratingValues.length ? ratingValues.reduce((a, b) => a + b, 0) / ratingValues.length : null;
    const kpiCards = el("div", { class: "ticket-kpi-grid" },
      [["↯", "متوسط أول رد", formatDuration(avgResponse)], ["✓", "التذاكر المحلولة", total], ["★", "رضا الأعضاء", avgRating == null ? "—" : `${avgRating.toFixed(1)}/5`]].map(([icon, label, value]) =>
        el("article", { class: "ticket-kpi-card" }, el("span", { class: "kpi-icon", text: icon }), el("small", { text: label }), el("strong", { text: String(value) })),
      ),
    );
    const active = el("div", { class: "ticket-radar-grid" });
    const queue = state.tickets.active.reduce((result, ticket) => {
      const status = ticket.status || "active";
      result[status] = (result[status] || 0) + 1;
      return result;
    }, {});
    const queueStats = el("div", { class: "ticket-queue-stats" },
      [["active", "قيد المعالجة"], ["waiting_staff", "بانتظار الدعم"], ["waiting_user", "بانتظار العميل"]].map(([key, label]) =>
        el("button", { class: `queue-stat ${state.ticketStatusFilter === key ? "selected" : ""}`, type: "button", onClick: () => {
          state.ticketStatusFilter = state.ticketStatusFilter === key ? "all" : key;
          renderPage();
        } }, el("strong", { text: String(queue[key] || 0) }), el("small", { text: label })),
      ),
    );
    active.append(queueStats);
    const filteredTickets = state.tickets.active.filter((ticket) =>
      state.ticketStatusFilter === "all" || (ticket.status || "active") === state.ticketStatusFilter
    );
    if (!filteredTickets.length) active.append(el("div", { class: "empty studio-empty", text: "لا توجد تذاكر مطابقة للفترة الحالية" }));
    filteredTickets.forEach((ticket) => {
      const priority = ticket.priority || "normal";
      const label = ticketPriorityLabels[priority] || priority;
      active.append(el("article", { class: `ticket-radar-card ${priority}` },
        el("div", { class: "ticket-radar-top" }, el("span", { class: `priority-tag ${priority}`, text: label }), el("small", { text: `#${ticket.id}` })),
        el("h4", { text: ticket.subject }),
        el("p", { text: `${ticket.category_label} · ${ticketStatusLabels[ticket.status] || "قيد المعالجة"} · ${ticket.claimed_by ? `مستلمة بواسطة ${ticket.claimed_by}` : "بانتظار الاستلام"}` }),
        el("div", { class: "ticket-radar-actions" },
          el("a", { class: "icon-action", href: `https://discord.com/channels/${state.guild.id}/${ticket.channel_id}`, target: "_blank", text: "↗", title: "فتح القناة" }),
          el("button", { class: "icon-action", type: "button", text: "⇄", title: "إعادة إسناد", onClick: () => ticketAction(ticket, "reassign") }),
          el("button", { class: "icon-action", type: "button", text: "◉", title: "التفاصيل والملاحظات", onClick: () => openTicketDetail(ticket) }),
          el("button", { class: "icon-action danger", type: "button", text: "⌫", title: "إغلاق قسري", onClick: () => ticketAction(ticket, "close") }),
        ),
      ));
    });
    const archiveSearch = el("input", { class: "studio-search", type: "search", placeholder: "ابحث في الأرشيف…", value: state.ticketSearch });
    let searchTimer;
    archiveSearch.oninput = () => {
      state.ticketSearch = archiveSearch.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(refreshTickets, 280);
    };
    const archiveRows = el("div", { class: "ticket-archive-list" });
    if (!state.tickets.archive.length) archiveRows.append(el("div", { class: "empty studio-empty", text: "لا توجد سجلات مغلقة" }));
    state.tickets.archive.forEach((ticket) => archiveRows.append(el("div", { class: "ticket-archive-row" },
      el("div", {}, el("strong", { text: `#${ticket.id} · ${ticket.subject}` }), el("small", { text: `${ticket.category_label} · ${ticket.close_reason || "بدون سبب"}` })),
      el("div", { class: "ticket-archive-actions" },
        el("button", { class: "btn ghost", type: "button", text: "التفاصيل", onClick: () => openTicketDetail(ticket) }),
        el("button", { class: "btn ghost", type: "button", text: "السجل", onClick: () => openTicketTranscript(ticket) }),
      ),
    )));
     const serverEmojis = [
       ...(Array.isArray(state.meta?.emojis) ? state.meta.emojis : []),
       ...(Array.isArray(state.meta?.guild_emojis) ? state.meta.guild_emojis : []),
       ...(Array.isArray(state.guild?.emojis) ? state.guild.emojis : []),
     ];
     const cannedForm = el("form", { class: "canned-form" },
       el("div", { class: "canned-form-heading" },
         el("div", {}, el("span", { class: "eyebrow", text: "REPLY KIT" }), el("h4", { text: "رد سريع للموظفين" })),
         el("small", { text: "يستخدمه الموظف داخل التذكرة عبر /ticket_reply" }),
       ),
       el("div", { class: "canned-form-fields" },
         el("label", { class: "canned-field" }, "العنوان",
           el("input", { name: "title", class: "studio-input", maxlength: "120", placeholder: "سياسة الاسترداد" }),
         ),
         el("label", { class: "canned-field" }, "التصنيف",
           el("input", { name: "category", class: "studio-input", maxlength: "80", placeholder: "عام", value: "عام" }),
         ),
         el("label", { class: "canned-field" }, "الاختصار",
           el("input", { name: "shortcut", class: "studio-input", maxlength: "80", placeholder: "refund أو /refund" }),
         ),
         el("label", { class: "canned-field" }, "ملصق اختياري",
           el("select", { name: "sticker_id", class: "studio-input canned-sticker-select" },
             el("option", { value: "" }, "بدون ملصق"),
             (state.meta?.stickers || []).map((sticker) => el("option", { value: sticker.id }, `◇ ${sticker.name}`)),
           ),
         ),
       ),
       el("div", { class: "canned-token-bar" },
         el("small", { text: "متغيرات قابلة للإدراج" }),
         ["{user}", "{staff}", "{channel}", "{server}", "{count}", "{members}", "{ticket}", "{subject}", "{category}", "{random:أهلاً|مرحباً}"].map((token) =>
           el("button", {
             class: "token-chip",
             type: "button",
             text: token,
             title: `إدراج ${token}`,
             onClick: () => {
               const textarea = cannedForm.elements.content;
               const start = textarea.selectionStart ?? textarea.value.length;
               const end = textarea.selectionEnd ?? start;
               textarea.value = `${textarea.value.slice(0, start)}${token}${textarea.value.slice(end)}`;
               textarea.focus();
               textarea.setSelectionRange(start + token.length, start + token.length);
             },
           }),
         ),
       ),
       el("div", { class: "server-emoji-picker" },
         el("div", { class: "server-emoji-heading" },
           el("span", { class: "eyebrow", text: "SERVER EMOJI" }),
           el("small", { text: serverEmojis.length ? "اختر رمزاً لإدراجه في الرد" : "لا توجد رموز سيرفر مقدمة من الخادم" }),
         ),
         el("div", { class: "server-emoji-list" },
           serverEmojis.length
             ? serverEmojis.slice(0, 40).map((emoji) => {
                 const token = emoji.token || `<:${emoji.name || "emoji"}:${emoji.id || ""}>`;
                 const image = emoji.url || emoji.image || emoji.icon_url;
                 const button = el("button", { class: "server-emoji-token", type: "button", title: `إدراج ${token}`, "aria-label": `إدراج ${token}` });
                 if (image) {
                   const imageNode = el("img", { src: image, alt: emoji.name || "emoji" });
                   imageNode.onerror = () => { imageNode.remove(); button.append(el("span", { text: emoji.name || token })); };
                   button.append(imageNode);
                 } else button.append(el("span", { text: emoji.name || token }));
                 button.onclick = () => insertVariable(cannedForm.elements.content, token);
                 return button;
               })
             : [el("span", { class: "empty-row", text: "سيظهر هنا ما يرسله Discord من رموز السيرفر." })],
         ),
       ),
       el("label", { class: "canned-content-field" }, "نص الرد",
         el("textarea", { name: "content", class: "studio-textarea", maxlength: "2000", rows: "5", placeholder: "أهلاً {user}، سيتابع {staff} طلبك في {channel}…" }),
       ),
       el("div", { class: "canned-form-actions" },
         el("button", { class: "btn ghost canned-cancel", type: "button", text: "تفريغ الحقول", onClick: () => {
           delete cannedForm.dataset.id;
           cannedForm.reset();
           cannedForm.elements.category.value = "عام";
           cannedForm.querySelector(".canned-submit").textContent = "حفظ الرد الجاهز";
         } }),
         el("button", { class: "btn primary canned-submit", type: "submit", text: "حفظ الرد الجاهز" }),
       ),
     );
    cannedForm.onsubmit = (event) => { event.preventDefault(); saveCannedResponse(cannedForm); };
    const cannedList = el("div", { class: "canned-list" });
     state.tickets.canned.forEach((item) => cannedList.append(el("div", { class: "canned-row" },
        el("div", { class: "canned-row-copy" },
          el("div", { class: "canned-row-title" },
            el("strong", { text: item.title }),
            ...(item.shortcut ? [el("code", { class: "canned-shortcut", text: item.shortcut })] : []),
          ),
          el("div", { class: "canned-row-meta" },
            el("span", { text: item.category || "عام" }),
            ...(item.sticker_id ? [el("span", { class: "canned-sticker-badge", text: "◇ ملصق مرفق" })] : []),
          ),
          el("p", { text: item.content }),
        ),
       el("div", { class: "canned-actions" },
         el("button", {
           class: "icon-action",
           type: "button",
           text: "✎",
           title: "تحرير الرد",
           onClick: () => {
             cannedForm.dataset.id = item.id;
             cannedForm.elements.title.value = item.title || "";
             cannedForm.elements.category.value = item.category || "عام";
              cannedForm.elements.shortcut.value = item.shortcut || "";
              cannedForm.elements.sticker_id.value = item.sticker_id || "";
             cannedForm.elements.content.value = item.content || "";
             cannedForm.querySelector(".canned-submit").textContent = "تحديث الرد";
             cannedForm.scrollIntoView({ behavior: "smooth", block: "center" });
             cannedForm.elements.title.focus();
           },
         }),
         el("button", { class: "icon-action danger", type: "button", text: "⌫", title: "حذف الرد", onClick: () => deleteCannedResponse(item) }),
       ),
     )));
    return el("section", { id: "view-tickets", class: "tickets-view" },
      el("div", { class: "studio-hero tickets-hero" },
        el("div", { class: "ticket-hero-copy" },
          el("div", { class: "eyebrow", text: `${state.guild.name} / TICKET CONTROL` }),
          el("h2", { text: "مركز عمليات التذاكر" }),
          el("p", { text: "طابور الدعم، الأرشيف، وأدوات النشر في مساحة واحدة سريعة لفريق PRIME." }),
          el("div", { class: "ticket-hero-actions" },
            el("button", { class: "btn ghost ticket-refresh", type: "button", text: "تحديث البيانات", onClick: refreshTickets }),
            el("span", { class: "live-dot", text: `${state.tickets.active.length} تذاكر مباشرة` }),
          ),
        ),
        el("div", { class: "ticket-hero-signal" },
          el("span", { class: "live-dot", text: "LIVE" }),
          el("strong", { text: `${state.tickets.active.length} مفتوحة` }),
          el("small", { text: "آخر مزامنة من مركز الدعم" }),
        ),
      ),
      card("استوديو لوحة الدعم", launchForm),
       ticketDropdownBuilder(),
      el("section", { class: "ticket-kpi-section" }, el("div", { class: "section-heading" }, el("div", {}, el("div", { class: "eyebrow", text: "STAFF VELOCITY" }), el("h3", { text: "مؤشرات فريق الدعم" })), el("span", { class: "live-dot", text: `${kpis.length} موظفين` })), kpiCards),
       el("section", { class: "ticket-radar-section" }, el("div", { class: "section-heading" }, el("div", {}, el("div", { class: "eyebrow", text: "ACTIVE RADAR" }), el("h3", { text: "التذاكر النشطة" })), el("span", { class: "live-dot", text: `${state.tickets.active.length} مفتوحة` })), active),
       card("خزينة السجلات", el("div", { class: "ticket-vault" }, archiveSearch, archiveRows)),
       card("مكتبة الردود السريعة", el("div", { class: "canned-drawer" }, cannedForm, cannedList)),
    );
  }
  function ticketsViewNextGen() {
    const config = state.ticketConfig || {};
    const analytics = state.ticketAnalytics || {};
    const overview = analytics.overview || {};
    const channels = state.commandStudio?.channels || [];
    const roles = state.commandStudio?.roles || [];
    const categories = state.ticketCategories || [];
    const activeCount = Number(overview.active ?? state.tickets.active.length);
    const closedCount = Number(overview.closed ?? state.tickets.archive.length);
    const ratings = analytics.ratings || [];
    const ratingCount = ratings.reduce((sum, item) => sum + Number(item.count || 0), 0);
    const ratingAverage = ratingCount
      ? ratings.reduce((sum, item) => sum + Number(item.rating || 0) * Number(item.count || 0), 0) / ratingCount
      : null;
    const priorities = { normal: 0, high: 0, management: 0 };
    (analytics.priorities || []).forEach((item) => { priorities[item.priority || "normal"] = Number(item.count || 0); });
    const priorityTotal = Object.values(priorities).reduce((sum, value) => sum + value, 0) || 1;
    const saveControlSettings = async (form) => {
      const permissions = {};
      form.querySelectorAll("[data-permission-key]").forEach((input) => {
        const key = input.dataset.permissionKey;
        permissions[key] = permissions[key] || [];
        if (input.checked) permissions[key].push(input.value);
      });
      const body = {
        closed_category_id: form.elements.closed_category_id.value || null,
        log_channel_id: form.elements.log_channel_id.value || null,
        evaluation_channel_id: form.elements.evaluation_channel_id.value || null,
        allow_user_close: form.elements.allow_user_close.checked,
        send_transcript_dm: form.elements.send_transcript_dm.checked,
        auto_close_minutes: Number(form.elements.auto_close_minutes.value || 0),
        open_limit: Number(form.elements.open_limit.value || 1),
        panel_mode: form.elements.panel_mode.value,
        select_placeholder: form.elements.select_placeholder.value.trim(),
        close_config: {
          move_to_category: form.elements.closed_category_id.value || null,
          archive_transcript: form.elements.send_transcript_dm.checked,
        },
      };
      try {
        const response = await writeApi(`api/guilds/${state.guild.id}/tickets/settings`, body);
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ إعدادات التذاكر");
        state.ticketConfig = { ...state.ticketConfig, ...(data.config || {}) };
        state.ticketSettings = data.config || state.ticketSettings;
        if (data.permissions) state.ticketPermissions = data.permissions;
        pulse();
        toast("تم حفظ إعدادات التذاكر", "success", 2400);
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      }
    };
    const deployPanel = async (form) => {
      const channelId = form.elements.target_channel_id.value;
      if (!channelId) return toast("اختر قناة نشر اللوحة");
      const submitButton = form.querySelector("button[type=submit]");
      const originalLabel = submitButton?.textContent || "🚀 نشر في ديسكورد";
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.replaceChildren(el("span", { class: "spinner" }), document.createTextNode(" جارٍ النشر…"));
      }
      try {
        const response = await writeApi(`api/guild/${state.guild.id}/tickets/deploy`, {
          target_channel_id: channelId,
          categories,
          embed_title: form.elements.embed_title.value.trim(),
          embed_description: form.elements.embed_description.value.trim(),
          embed_color: form.elements.embed_color.value,
          footer_text: form.elements.footer_text.value.trim(),
        });
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر نشر اللوحة");
        toast("✅ تم نشر وتحديث البانل في ديسكورد بنجاح!", "success", 2600);
        await refreshTickets();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال لنشر اللوحة");
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalLabel;
        }
      }
    };
    const saveSections = async () => {
      try {
        const response = await writeApi(`api/guilds/${state.guild.id}/tickets/categories`, {
          categories,
          replace: true,
        });
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ الأقسام");
        const savedCategories = data.categories || categories;
        // Keep the legacy panel options synchronized for the running cog.
        const legacy = await writeApi(`api/guild/${state.guild.id}/tickets/config`, {
          categories: savedCategories,
          embed_title: config.embed_title,
          embed_description: config.embed_description,
          embed_color: config.embed_color,
          footer_text: config.footer_text,
        });
        const legacyData = await readJson(legacy, {});
        if (!legacy.ok) return toast(legacyData.fields ? Object.values(legacyData.fields)[0] : "تعذر مزامنة الأقسام");
        state.ticketCategories = legacyData.categories || savedCategories;
        toast("تم حفظ باني الأقسام", "success", 2200);
        renderPage();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      }
    };
    const removePanel = async (panel) => {
      if (!confirm("حذف هذه اللوحة من Discord؟")) return;
      try {
        const response = await api(`api/guild/${state.guild.id}/tickets/panels/${panel.message_id}`, {
          method: "DELETE",
          headers: { "X-CSRF-Token": state.session.csrf },
        });
        if (!response.ok) return toast("تعذر حذف اللوحة");
        state.ticketPanels = state.ticketPanels.filter((item) => String(item.message_id) !== String(panel.message_id));
        toast("تم حذف اللوحة", "success", 2000);
        renderPage();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      }
    };
    const publishPanel = async (panel, button = null) => {
      const originalLabel = button?.textContent || "🚀 نشر في ديسكورد";
      if (button) {
        button.disabled = true;
        button.replaceChildren(el("span", { class: "spinner" }), document.createTextNode(" جارٍ النشر…"));
      }
      try {
        const response = await writeApi(`api/guilds/${state.guild.id}/tickets/panels/${panel.id}/publish`, {});
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر نشر اللوحة");
        toast("✅ تم نشر وتحديث البانل في ديسكورد بنجاح!", "success", 2400);
        await refreshTickets();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = originalLabel;
        }
      }
    };
    const duplicatePanel = async (panel) => {
      try {
        const response = await writeApi(`api/guilds/${state.guild.id}/tickets/panels`, {
          channel_id: panel.channel_id,
          title: `${panel.title || "مركز الدعم"} · نسخة`,
          description: panel.description || "",
          color: panel.color,
          mode: panel.mode || "dropdown",
          categories: panel.categories || [],
        });
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر نسخ اللوحة");
        state.ticketPanels.unshift(data.panel);
        toast("تم إنشاء نسخة مسودة من اللوحة", "success", 2200);
        renderPage();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      }
    };
    const exportPanel = (panel) => {
      const blob = new Blob([JSON.stringify(panel, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `prime-ticket-panel-${panel.id || panel.message_id}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    };
    const addBlacklist = async (form) => {
      try {
        const response = await writeApi(`api/guilds/${state.guild.id}/tickets/blacklist`, {
          user_id: form.elements.user_id.value.trim(),
          duration_days: form.elements.duration_days.value || null,
          reason: form.elements.reason.value.trim(),
        });
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حظر العضو");
        state.ticketBlacklist = [data.entry, ...state.ticketBlacklist.filter((item) => String(item.user_id) !== String(data.entry.user_id))];
        toast("تمت إضافة العضو إلى القائمة السوداء", "success", 2400);
        form.reset();
        renderPage();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      }
    };
    const removeBlacklist = async (entry) => {
      try {
        const response = await api(`api/guilds/${state.guild.id}/tickets/blacklist/${entry.user_id}`, {
          method: "DELETE",
          headers: { "X-CSRF-Token": state.session.csrf },
        });
        if (!response.ok) return toast("تعذر إزالة الحظر");
        state.ticketBlacklist = state.ticketBlacklist.filter((item) => String(item.user_id) !== String(entry.user_id));
        renderPage();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
      }
    };
    const tab = (key, label, icon) => el("button", {
       class: `ticket-next-tab nav-link category-chip ${state.ticketTab === key ? "active" : ""}`,
      type: "button",
      "aria-selected": String(state.ticketTab === key),
      onClick: () => {
        state.ticketTab = key;
        sessionStorage.setItem("ticket-tab", key);
        renderPage();
           if (key === "permissions" && state.guild?.id) {
             loadAndHydratePermissions(state.guild.id).catch((error) => {
               if (error.message !== "unauth") toast("تعذر تحميل حالة الصلاحيات");
             });
           }
      },
    }, el("span", { class: "ticket-tab-icon", text: icon }), label);
    const categoryChannels = Array.isArray(state.meta?.categories)
      ? state.meta.categories
      : channels.filter((item) => item.type === "category");
    const channelOptions = (placeholder, onlyCategories = false) => [
      el("option", { value: "" }, placeholder),
      ...(onlyCategories ? categoryChannels : channels.filter((item) => item.type !== "category"))
        .map((item) => el("option", { value: item.id }, `${onlyCategories ? "▦" : "#"} ${item.name}`)),
    ];
    const metric = (label, value, detail, tone) => el("article", { class: `ticket-next-metric ${tone || ""}` },
      el("span", { class: "ticket-metric-label", text: label }),
      el("strong", { text: String(value) }),
      el("small", { text: detail }),
    );
    const overviewView = () => {
      const activity = analytics.activity || [];
      const live = el("div", { class: "ticket-live-feed" });
      if (!activity.length) live.append(el("div", { class: "ticket-next-empty", text: "لا يوجد نشاط مسجل بعد" }));
      activity.slice(0, 8).forEach((item) => {
        const closed = item.status === "closed";
        live.append(el("article", { class: "ticket-live-item" },
          el("span", { class: `ticket-live-mark ${closed ? "rose" : "emerald"}`, text: closed ? "✓" : "•" }),
          el("div", {},
            el("strong", { text: `${closed ? "أُغلقت" : "تذكرة نشطة"} · #${item.id}` }),
            el("small", { text: `${item.category_label || "دعم"} · ${item.subject || "بدون عنوان"}` }),
          ),
          el("time", { text: item.closed_at || item.opened_at || "الآن" }),
        ));
      });
      const donut = el("div", {
        class: "ticket-priority-donut",
        style: `--normal:${(priorities.normal / priorityTotal) * 100}%;--high:${(priorities.high / priorityTotal) * 100}%;--management:${(priorities.management / priorityTotal) * 100}%;`,
      }, el("strong", { text: String(activeCount) }), el("small", { text: "مفتوحة" }));
      const satisfactionRows = [5, 4, 3, 2, 1].map((stars) => {
        const count = Number(ratings.find((item) => Number(item.rating) === stars)?.count || 0);
        const width = ratingCount ? Math.round((count / ratingCount) * 100) : 0;
        return el("div", { class: "ticket-rating-row" },
          el("span", { text: `${stars} ★` }),
          el("i", {}, el("b", { style: `width:${width}%` })),
          el("small", { text: String(count) }),
        );
      });
      const leaderboard = el("div", { class: "ticket-leaderboard" });
      (analytics.staff || []).slice(0, 5).forEach((item, index) => leaderboard.append(
        el("div", { class: "ticket-leader-row" },
          el("span", { class: "ticket-rank", text: String(index + 1).padStart(2, "0") }),
          el("div", {}, el("strong", { text: `عضو #${item.staff_id}` }), el("small", { text: `${item.resolved || 0} تذاكر محلولة` })),
          el("b", { text: item.avg_rating == null ? "—" : `${Number(item.avg_rating).toFixed(1)} ★` }),
        ),
      ));
      if (!leaderboard.children.length) leaderboard.append(el("div", { class: "ticket-next-empty", text: "ستظهر الترتيبات بعد حل التذاكر" }));
      const queueRows = state.tickets.active
        .filter((ticket) => state.ticketStatusFilter === "all" || (ticket.status || "active") === state.ticketStatusFilter)
        .slice(0, 8)
        .map((ticket) => el("button", {
          class: "ticket-queue-row",
          type: "button",
          onClick: () => openTicketDetail(ticket),
        },
          el("span", { class: `ticket-queue-priority ${ticket.priority || "normal"}` }),
          el("strong", { text: `#${ticket.id} · ${ticket.subject}` }),
          el("small", { text: ticket.category_label || "دعم" }),
          el("b", { text: ticket.claimed_by ? `#${ticket.claimed_by}` : "غير مستلمة" }),
        ));
      return el("div", { class: "ticket-next-content ticket-overview-content" },
        el("div", { class: "ticket-next-metrics" },
          metric("التذاكر المفتوحة", activeCount, "مزامنة مباشرة من SQLite", "amber"),
          metric("التذاكر المحلولة", closedCount, "كل السجلات المغلقة", "emerald"),
          metric("متوسط أول رد", formatDuration(overview.avg_response), "من وقت فتح التذكرة", "cyan"),
          metric("رضا الأعضاء", ratingAverage == null ? "—" : `${ratingAverage.toFixed(1)}/5`, `${ratingCount} تقييم محفوظ`, "rose"),
        ),
        el("div", { class: "ticket-bento-grid" },
          el("section", { class: "ticket-next-card ticket-priority-card" },
            el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "PRIORITIES" }), el("h3", { text: "توزيع الأولويات" })), el("span", { class: "ticket-card-icon", text: "◌" })),
            el("div", { class: "ticket-donut-wrap" }, donut, el("div", { class: "ticket-priority-legend" },
              [["normal", "عادية", "#a2a1aa"], ["high", "عالية", "#ffb547"], ["management", "إدارية", "#ff5b81"]].map(([key, label, color]) =>
                el("span", {}, el("i", { style: `background:${color}` }), label, el("b", { text: `${priorities[key]} · ${Math.round((priorities[key] / priorityTotal) * 100)}%` })),
              ),
            )),
          ),
          el("section", { class: "ticket-next-card ticket-satisfaction-card" },
            el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "MEMBER FEEDBACK" }), el("h3", { text: "رضا الأعضاء" })), el("strong", { class: "ticket-rating-score", text: ratingAverage == null ? "—" : `${ratingAverage.toFixed(1)} ★` })),
            el("div", { class: "ticket-rating-bars" }, satisfactionRows),
          ),
          el("section", { class: "ticket-next-card ticket-staff-card" },
            el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "STAFF LEADERBOARD" }), el("h3", { text: "أفضل الموظفين" })), el("span", { class: "ticket-card-icon", text: "↗" })),
            leaderboard,
          ),
          el("section", { class: "ticket-next-card ticket-activity-card" },
            el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "LIVE ACTIVITY" }), el("h3", { text: "النشاط المباشر" })), el("span", { class: "ticket-live-pill", text: "● LIVE" })),
            live,
          ),
        ),
        el("section", { class: "ticket-next-card ticket-queue-card" },
          el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "ACTIVE QUEUE" }), el("h3", { text: "طابور التذاكر" })), el("button", { class: "ticket-inline-action", type: "button", text: "تحديث", onClick: refreshTickets })),
          el("div", { class: "ticket-queue-grid" }, [["active", "قيد المعالجة"], ["waiting_staff", "بانتظار الدعم"], ["waiting_user", "بانتظار العضو"]].map(([key, label]) =>
            el("button", { class: `ticket-queue-chip ${state.ticketStatusFilter === key ? "selected" : ""}`, type: "button", onClick: () => { state.ticketStatusFilter = state.ticketStatusFilter === key ? "all" : key; renderPage(); } },
              el("strong", { text: String(state.tickets.active.filter((ticket) => (ticket.status || "active") === key).length) }), el("span", { text: label }),
            ),
          )),
          el("div", { class: "ticket-queue-list" },
            queueRows,
            state.tickets.active.length ? null : el("div", { class: "ticket-next-empty", text: "لا توجد تذاكر نشطة حالياً" }),
          ),
        ),
      );
    };
    const formatTicketDate = (value) => {
      if (!value) return "—";
      const date = new Date(String(value).replace(" ", "T") + (String(value).includes("Z") ? "" : "Z"));
      return Number.isNaN(date.getTime())
        ? String(value)
        : date.toLocaleString("ar", { dateStyle: "medium", timeStyle: "short" });
    };
    const ticketSearchInput = (placeholder, onChange) => {
      const input = el("input", {
        class: "studio-search ticket-collection-search",
        type: "search",
        value: state.ticketSearch,
        placeholder,
        "aria-label": placeholder,
      });
      let timer;
      input.oninput = () => {
        state.ticketSearch = input.value;
        onChange?.();
        clearTimeout(timer);
        timer = setTimeout(refreshTickets, 280);
      };
      return input;
    };
    const ticketStatusFilterBar = () => el("div", { class: "ticket-collection-filters" },
      [["all", "الكل"], ["active", "قيد المعالجة"], ["waiting_staff", "بانتظار الدعم"], ["waiting_user", "بانتظار العضو"]].map(([value, label]) =>
        el("button", {
          class: `ticket-collection-filter ${state.ticketStatusFilter === value ? "active" : ""}`,
          type: "button",
          text: label,
          onClick: () => {
            state.ticketStatusFilter = value;
            renderPage();
          },
        }),
      ),
    );
    const ticketRow = (ticket, archived = false) => {
      const priority = ticket.priority || "normal";
      const status = ticket.status || (archived ? "closed" : "active");
      const actions = [
        el("button", { class: "ticket-inline-action", type: "button", text: "التفاصيل", onClick: () => openTicketDetail(ticket) }),
      ];
      if (archived) {
        actions.push(el("button", { class: "ticket-inline-action", type: "button", text: "السجل", onClick: () => openTicketTranscript(ticket) }));
        if (status === "closed") {
          actions.push(el("button", { class: "ticket-inline-action", type: "button", text: "إعادة فتح", onClick: () => ticketAction(ticket, "reopen") }));
        }
      } else {
        actions.push(
          el("button", { class: "ticket-inline-action", type: "button", text: "إسناد", onClick: () => ticketAction(ticket, "reassign") }),
          el("button", { class: "ticket-inline-action danger", type: "button", text: "إغلاق", onClick: () => ticketAction(ticket, "close") }),
        );
      }
      return el("article", { class: `ticket-collection-row ${priority}` },
        el("div", { class: "ticket-collection-priority" }),
        el("div", { class: "ticket-collection-main" },
          el("div", { class: "ticket-collection-title" },
            el("strong", { text: `#${ticket.id} · ${ticket.subject || "بدون عنوان"}` }),
            el("span", { class: `status-tag ${status}`, text: ticketStatusLabels[status] || status }),
          ),
          el("small", { text: `${ticket.category_label || "دعم"} · العضو #${ticket.user_id || "—"} · ${ticket.claimed_by ? `المسؤول #${ticket.claimed_by}` : "غير مستلمة"}` }),
          el("p", { text: ticket.details || ticket.close_reason || "بدون تفاصيل إضافية" }),
        ),
        el("div", { class: "ticket-collection-meta" },
          el("span", { class: `priority-tag ${priority}`, text: ticketPriorityLabels[priority] || priority }),
          el("time", { text: formatTicketDate(ticket.closed_at || ticket.opened_at || ticket.created_at) }),
          el("div", { class: "ticket-collection-actions" }, actions),
        ),
      );
    };
    const ticketsListView = () => {
      const query = state.ticketSearch.trim().toLocaleLowerCase();
      const tickets = state.tickets.active.filter((ticket) => {
        const matchesStatus = state.ticketStatusFilter === "all" || (ticket.status || "active") === state.ticketStatusFilter;
        const haystack = `${ticket.id} ${ticket.subject || ""} ${ticket.category_label || ""} ${ticket.user_id || ""}`.toLocaleLowerCase();
        return matchesStatus && (!query || haystack.includes(query));
      });
      const list = el("div", { class: "ticket-collection-list" });
      if (!tickets.length) list.append(el("div", { class: "ticket-next-empty", text: "لا توجد تذاكر نشطة مطابقة للبحث الحالي" }));
      tickets.forEach((ticket) => list.append(ticketRow(ticket)));
      return el("div", { class: "ticket-next-content" },
        el("div", { class: "ticket-section-title-row" },
          el("div", {}, el("span", { class: "ticket-kicker", text: "ACTIVE TICKETS" }), el("h3", { text: "كل التذاكر النشطة" }), el("p", { text: "راجع الطابور الكامل ونفّذ الإسناد أو الإغلاق من دون فتح Discord." })),
          el("span", { class: "ticket-live-pill", text: `${tickets.length} معروض` }),
        ),
        el("section", { class: "ticket-next-card ticket-collection-card" },
          el("div", { class: "ticket-collection-toolbar" }, ticketSearchInput("ابحث بالرقم أو العنوان أو العضو…"), ticketStatusFilterBar()),
          list,
        ),
      );
    };
    const archiveView = () => {
      const query = state.ticketSearch.trim().toLocaleLowerCase();
      const tickets = state.tickets.archive.filter((ticket) => {
        const haystack = `${ticket.id} ${ticket.subject || ""} ${ticket.category_label || ""} ${ticket.user_id || ""} ${ticket.close_reason || ""}`.toLocaleLowerCase();
        return !query || haystack.includes(query);
      });
      const list = el("div", { class: "ticket-collection-list" });
      if (!tickets.length) list.append(el("div", { class: "ticket-next-empty", text: "لا توجد سجلات مغلقة مطابقة للبحث الحالي" }));
      tickets.forEach((ticket) => list.append(ticketRow(ticket, true)));
      return el("div", { class: "ticket-next-content" },
        el("div", { class: "ticket-section-title-row" },
          el("div", {}, el("span", { class: "ticket-kicker", text: "ARCHIVE VAULT" }), el("h3", { text: "أرشيف التذاكر" }), el("p", { text: "ارجع إلى التفاصيل والـ transcript وأعد فتح التذاكر المغلقة عند الحاجة." })),
          el("span", { class: "ticket-live-pill", text: `${tickets.length} سجل` }),
        ),
        el("section", { class: "ticket-next-card ticket-collection-card" },
          el("div", { class: "ticket-collection-toolbar" }, ticketSearchInput("ابحث في الأرشيف…")),
          list,
        ),
      );
    };
    const ratingsView = () => {
      const directRatings = state.ticketRatings || [];
      const distribution = [5, 4, 3, 2, 1].map((stars) => ({
        stars,
        count: directRatings.filter((item) => Number(item.stars ?? item.rating) === stars).length,
      }));
      const total = directRatings.length;
      const average = total
        ? directRatings.reduce((sum, item) => sum + Number(item.stars ?? item.rating ?? 0), 0) / total
        : Number(analytics.overview?.average_rating || 0) || null;
      const rows = el("div", { class: "ticket-rating-list" });
      if (!directRatings.length) rows.append(el("div", { class: "ticket-next-empty", text: "ستظهر تقييمات الأعضاء بعد إغلاق التذاكر واستلام التقييم." }));
      directRatings.forEach((rating) => rows.append(el("article", { class: "ticket-rating-item" },
        el("div", { class: "ticket-rating-stars", text: `${"★".repeat(Math.max(0, Math.min(5, Number(rating.stars || 0))))}${"☆".repeat(Math.max(0, 5 - Number(rating.stars || 0)))}` }),
        el("div", {}, el("strong", { text: `التذكرة #${rating.ticket_id}` }), el("small", { text: `${rating.subject || "بدون عنوان"} · العضو #${rating.user_id || "—"} · الموظف #${rating.staff_id || "—"}` })),
        el("time", { text: formatTicketDate(rating.created_at) }),
        rating.comment ? el("p", { text: rating.comment }) : null,
      )));
      return el("div", { class: "ticket-next-content" },
        el("div", { class: "ticket-section-title-row" },
          el("div", {}, el("span", { class: "ticket-kicker", text: "MEMBER FEEDBACK" }), el("h3", { text: "تقييمات الأعضاء" }), el("p", { text: "راقب رضا الأعضاء واربط كل تقييم بالتذكرة والموظف المسؤول." })),
          el("span", { class: "ticket-live-pill", text: `${total} تقييم` }),
        ),
        el("div", { class: "ticket-rating-summary-grid" },
          metric("متوسط التقييم", average == null ? "—" : `${average.toFixed(1)}/5`, `${total} تقييم محفوظ`, "amber"),
          metric("تقييمات 5 نجوم", distribution[0].count, total ? `${Math.round((distribution[0].count / total) * 100)}% من الإجمالي` : "لا توجد بيانات", "emerald"),
          el("section", { class: "ticket-next-card ticket-rating-distribution-card" },
            el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "RATING DISTRIBUTION" }), el("h3", { text: "توزيع النجوم" }))),
            el("div", { class: "ticket-rating-bars" }, distribution.map((item) => {
              const width = total ? Math.round((item.count / total) * 100) : 0;
              return el("div", { class: "ticket-rating-row" }, el("span", { text: `${item.stars} ★` }), el("i", {}, el("b", { style: `width:${width}%` })), el("small", { text: String(item.count) }));
            })),
          ),
        ),
        el("section", { class: "ticket-next-card ticket-collection-card" },
          el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "RATING LOG" }), el("h3", { text: "آخر التقييمات" })), el("button", { class: "ticket-inline-action", type: "button", text: "تحديث", onClick: refreshTickets })),
          rows,
        ),
      );
    };
    const panelsView = () => {
      const cards = state.ticketPanels.map((panel) => el("article", { class: "ticket-panel-tile" },
        (() => {
          const isDraft = !Number(panel.message_id);
          const panelTitle = panel.title || config.embed_title || "مركز الدعم والتذاكر";
          const panelDescription = panel.description || config.embed_description || "اختر القسم المناسب لطلبك";
          const panelColor = `#${Number(panel.color || 0x5865F2).toString(16).padStart(6, "0").slice(-6)}`;
          const preview = el("div", { class: "ticket-panel-preview-mini", style: `--panel-accent:${panelColor}` },
            el("span", { class: "ticket-kicker", text: "PR1ME SUPPORT" }),
            el("strong", { text: panelTitle }),
            el("small", { text: panelDescription }),
            el("div", { class: "ticket-panel-category-list" }, (panel.categories || []).slice(0, 4).map((item) =>
              el("span", {}, ticketEmojiNode(item.emoji || "🎫"), item.label || "قسم دعم"),
            )),
          );
          const actions = [
            el("button", { class: "ticket-inline-action", type: "button", text: "تحرير", onClick: () => { state.ticketCategories = (panel.categories || []).map((item) => ({ ...item })); state.ticketTab = "builder"; renderPage(); } }),
            isDraft
              ? el("button", { class: "ticket-inline-action publish ticket-publish-button", type: "button", text: "🚀 نشر في ديسكورد", onClick: (event) => publishPanel(panel, event.currentTarget) })
              : null,
            el("button", { class: "ticket-inline-action", type: "button", text: "نسخ", onClick: () => duplicatePanel(panel) }),
            el("button", { class: "ticket-inline-action", type: "button", text: "JSON", onClick: () => exportPanel(panel) }),
            el("button", { class: "ticket-inline-action danger", type: "button", text: "حذف", onClick: () => removePanel(panel) }),
          ];
          return [
            el("div", { class: "ticket-panel-tile-top" },
              el("span", { class: `ticket-panel-status ${isDraft ? "draft" : ""}`, text: isDraft ? "○ مسودة" : "● منشورة" }),
              el("small", { text: `v${panel.version || 1} · ${isDraft ? "بدون رسالة" : `#${panel.message_id}`}` }),
            ),
            preview,
            el("div", { class: "ticket-panel-tile-meta" },
              el("span", { text: `#${window.guildChannels?.[panel.channel_id] || panel.channel_id}` }),
              el("span", { text: `${panel.mode === "buttons" ? "أزرار" : "Dropdown"} · ${(panel.categories || []).length} أقسام` }),
            ),
            el("div", { class: "ticket-tile-actions" }, actions),
          ];
        })(),
      ));
      if (!cards.length) cards.push(el("div", { class: "ticket-next-empty", text: "لم تنشر أي لوحة بعد. أنشئ أول لوحة من المحرر المرئي." }));
      return el("div", { class: "ticket-next-content" },
        el("div", { class: "ticket-section-title-row" }, el("div", {}, el("span", { class: "ticket-kicker", text: "DISCORD PANELS" }), el("h3", { text: "لوحات التذاكر" }), el("p", { text: "أدر الرسائل المنشورة واتصل بالمحرر المرئي دون مغادرة لوحة التحكم." })), el("button", { class: "ticket-add-button", type: "button", text: "+ بانل جديد", onClick: () => { state.ticketCategories = state.ticketCategories.map((item) => ({ ...item })); state.ticketTab = "builder"; renderPage(); } })),
        el("div", { class: "ticket-panels-grid" }, cards),
      );
    };
    const builderView = (sectionsOnly = false) => {
      const form = el("form", { class: "ticket-visual-builder" },
        !sectionsOnly && el("div", { class: "ticket-builder-preview" },
          el("span", { class: "ticket-kicker", text: "VISUAL BUILDER" }),
          el("h3", { text: config.embed_title || "مركز الدعم والتذاكر" }),
          el("p", { text: config.embed_description || "اختر القسم المناسب لفتح تذكرة خاصة مع فريق الدعم." }),
          el("div", { class: "ticket-builder-options" }, categories.slice(0, 6).map((item) => el("span", {}, ticketEmojiNode(item.emoji || "🎫"), item.label))),
        ),
        !sectionsOnly && el("div", { class: "ticket-builder-form-grid" },
          el("label", {}, "قناة النشر", el("select", { name: "target_channel_id", class: "studio-input" }, channelOptions("اختر قناة نصية"))),
          el("label", {}, "عنوان اللوحة", el("input", { name: "embed_title", class: "studio-input", value: config.embed_title || "مركز الدعم والتذاكر", maxlength: "256" })),
          el("label", {}, "لون Accent", el("input", { name: "embed_color", class: "studio-input ticket-color-input", type: "color", value: `#${Number(config.embed_color || 0xF4B740).toString(16).padStart(6, "0").slice(-6)}` })),
          el("label", { class: "ticket-builder-wide" }, "الوصف", el("textarea", { name: "embed_description", class: "studio-textarea", rows: "2", maxlength: "4096", text: config.embed_description || "" })),
          el("label", { class: "ticket-builder-wide" }, "التذييل", el("input", { name: "footer_text", class: "studio-input", value: config.footer_text || "" })),
        ),
        el("div", { class: "ticket-builder-section-head" }, el("div", {}, el("span", { class: "ticket-kicker", text: "SECTION BUILDER" }), el("h3", { text: "أقسام البانل" }), el("small", { text: "اضبط emoji السيرفر، الفئة الأب، رتب الدعم ونموذج الفتح لكل قسم." })), el("button", { class: "ticket-inline-action", type: "button", text: "+ إضافة قسم", onClick: () => { if (categories.length >= 25) return toast("الحد الأقصى 25 قسماً"); categories.push({ key: `support_${Date.now()}`, label: "قسم دعم جديد", description: "", emoji: "🎫", support_role_ids: [], senior_role_ids: [], intake_fields: [] }); renderPage(); } })),
        ticketCategoryEditor(),
        el("div", { class: "ticket-builder-footer" },
          el("button", { class: "ticket-add-button", type: "submit", text: sectionsOnly ? "حفظ الأقسام" : "نشر البانل في Discord" }),
          !sectionsOnly && el("span", { class: "ticket-builder-note", text: "سيبقى المحرك الحالي والصلاحيات والقنوات كما هي." }),
        ),
      );
      form.onsubmit = (event) => { event.preventDefault(); sectionsOnly ? saveSections() : deployPanel(form); };
      return el("div", { class: "ticket-next-content" },
        el("div", { class: "ticket-section-title-row" }, el("div", {}, el("span", { class: "ticket-kicker", text: sectionsOnly ? "SECTION BUILDER" : "PANEL EDITOR" }), el("h3", { text: sectionsOnly ? "باني الأقسام" : "المحرر المرئي" })), state.ticketTab === "builder" && el("button", { class: "ticket-inline-action", type: "button", text: "إلغاء", onClick: () => { state.ticketTab = "panels"; renderPage(); } })),
        form,
      );
    };
    const settingsView = () => {
      const current = state.ticketSettings || config;
      const settingsForm = el("form", { class: "ticket-settings-layout" },
        el("section", { class: "ticket-next-card ticket-settings-card" },
          el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "GENERAL SETTINGS" }), el("h3", { text: "الإعدادات العامة" })), el("span", { class: "ticket-card-icon", text: "⚙" })),
          el("div", { class: "ticket-settings-grid" },
            el("label", {}, "الفئة المغلقة", el("select", { name: "closed_category_id", class: "studio-input" }, channelOptions("بدون نقل تلقائي", true))),
            el("label", {}, "قناة سجل التذاكر", el("select", { name: "log_channel_id", class: "studio-input" }, channelOptions("بدون قناة سجل"))),
            el("label", {}, "قناة التقييمات", el("select", { name: "evaluation_channel_id", class: "studio-input" }, channelOptions("بدون قناة تقييم"))),
            el("label", {}, "مهلة الإغلاق التلقائي (دقائق)", el("input", { name: "auto_close_minutes", class: "studio-input", type: "number", min: "0", max: "10080", value: current.auto_close_minutes || 0 })),
            el("label", {}, "حد التذاكر للعضو", el("input", { name: "open_limit", class: "studio-input", type: "number", min: "1", max: "20", value: current.open_limit || 1 })),
            el("label", {}, "نمط لوحة Discord", el("select", { name: "panel_mode", class: "studio-input" }, el("option", { value: "dropdown" }, "Dropdown"), el("option", { value: "buttons" }, "Buttons"))),
            el("label", { class: "ticket-builder-wide" }, "النص الإرشادي للمنتقي", el("input", { name: "select_placeholder", class: "studio-input", maxlength: "200", value: current.select_placeholder || "اختر القسم المناسب لطلبك" })),
          ),
          el("div", { class: "ticket-toggle-list" },
            el("label", {}, el("input", { name: "allow_user_close", type: "checkbox", checked: Boolean(current.allow_user_close) }), "السماح للعضو بإغلاق تذكرته"),
            el("label", {}, el("input", { name: "send_transcript_dm", type: "checkbox", checked: current.send_transcript_dm !== false }), "حفظ وإرسال transcript عند الإغلاق"),
          ),
        ),
        el("section", { class: "ticket-next-card ticket-blacklist-card" },
          el("div", { class: "ticket-card-heading" }, el("div", {}, el("span", { class: "ticket-kicker", text: "ACCESS CONTROL" }), el("h3", { text: "Blacklist" })), el("span", { class: "ticket-card-icon rose", text: "⊘" })),
          el("p", { class: "ticket-muted-copy", text: "امنع أعضاء محددين من فتح تذاكر جديدة دون التأثير على التذاكر الموجودة." }),
          el("form", { class: "ticket-blacklist-form" },
            el("input", { name: "user_id", class: "studio-input", placeholder: "Discord User ID", inputmode: "numeric", required: true }),
            el("input", { name: "duration_days", class: "studio-input", type: "number", min: "1", placeholder: "أيام (اختياري)" }),
            el("input", { name: "reason", class: "studio-input", placeholder: "سبب الحظر" }),
            el("button", { class: "ticket-inline-action danger", type: "submit", text: "حظر العضو" }),
          ),
          el("div", { class: "ticket-blacklist-list" }, state.ticketBlacklist.length ? state.ticketBlacklist.map((entry) => el("div", { class: "ticket-blacklist-row" }, el("span", {}, el("strong", { text: `#${entry.user_id}` }), el("small", { text: entry.reason || "بدون سبب" })), el("button", { class: "ticket-inline-action danger", type: "button", text: "إزالة", onClick: () => removeBlacklist(entry) }))) : el("div", { class: "ticket-next-empty", text: "القائمة السوداء فارغة" })),
        ),
        el("button", { class: "ticket-add-button ticket-settings-save", type: "submit", text: "حفظ الإعدادات" }),
      );
      settingsForm.elements.closed_category_id.value = current.closed_category_id || "";
      settingsForm.elements.log_channel_id.value = current.log_channel_id || "";
      settingsForm.elements.evaluation_channel_id.value = current.evaluation_channel_id || "";
      settingsForm.elements.panel_mode.value = current.panel_mode || "dropdown";
      settingsForm.onsubmit = (event) => {
        event.preventDefault();
        if (event.submitter?.closest(".ticket-blacklist-form")) return;
        saveControlSettings(settingsForm);
      };
      settingsForm.querySelector(".ticket-blacklist-form").onsubmit = (event) => { event.preventDefault(); addBlacklist(event.currentTarget); };
      return el("div", { class: "ticket-next-content" }, el("div", { class: "ticket-section-title-row" }, el("div", {}, el("span", { class: "ticket-kicker", text: "CLOSE & ARCHIVE" }), el("h3", { text: "الإعدادات العامة والسلوك" }), el("p", { text: "تحكم في النقل، الأرشفة، التقييم وقنوات السجل." }))), settingsForm);
    };
     const permissionsView = () => {
       const permissionDefaults = state.ticketPermissions && Object.keys(state.ticketPermissions).length
         ? state.ticketPermissions
         : (config.permissions_json || config.permissions || {});
      const permissionRows = [
        ["claim", "استلام التذكرة"],
        ["close", "إغلاق وأرشفة"],
        ["rename", "إعادة تسمية"],
        ["priority", "تغيير الأولوية"],
        ["transfer", "تحويل القسم"],
        ["add_member", "إضافة عضو"],
        ["remove_member", "إزالة عضو"],
        ["private_ticket", "تذكرة خاصة"],
        ["summon", "استدعاء عضو"],
        ["tag", "إدارة الوسوم"],
        ["note", "ملاحظة داخلية"],
        ["reopen", "إعادة فتح"],
      ];
      const matrix = el("div", { class: "ticket-permission-matrix" });
      const header = el("div", { class: "ticket-permission-row head" });
      header.append(el("strong", { text: "السلوك" }));
      roles.slice(0, 8).forEach((role) => header.append(el("span", { text: `@${role.name}` })));
      matrix.append(header);
      permissionRows.forEach(([key, label]) => {
        const row = el("div", { class: "ticket-permission-row" });
        row.append(el("strong", { text: label }));
        roles.slice(0, 8).forEach((role) => {
           const input = el("input", {
            type: "checkbox",
             class: "perm-checkbox",
            value: String(role.id),
            "data-permission-key": key,
             "data-permission-action": key,
             "data-role-id": String(role.id),
            checked: (permissionDefaults[key] || []).map(String).includes(String(role.id)),
          });
           const labelNode = el("label", {}, input, el("span", { text: "●" }));
           const initiallyChecked = Boolean(input.checked);
           labelNode.classList.toggle("is-checked", initiallyChecked);
           input.classList.toggle("checked", initiallyChecked);
           input.classList.toggle("is-checked", initiallyChecked);
           input.addEventListener("change", () => {
             labelNode.classList.toggle("is-checked", input.checked);
             input.classList.toggle("checked", input.checked);
             input.classList.toggle("is-checked", input.checked);
           });
           row.append(labelNode);
        });
        matrix.append(row);
      });
       const save = el("button", {
         class: "ticket-add-button perm-save-button",
        type: "button",
        text: "حفظ مصفوفة الصلاحيات",
         onClick: async (event) => {
          const permissions = {};
          matrix.querySelectorAll("[data-permission-key]").forEach((input) => {
            if (input.checked) (permissions[input.dataset.permissionKey] ||= []).push(input.value);
          });
           const button = event.currentTarget;
           button.disabled = true;
           button.replaceChildren(el("span", { class: "spinner" }), document.createTextNode(" جارٍ الحفظ…"));
           try {
             const response = await writeApi(`api/guilds/${state.guild.id}/tickets/permissions`, { permissions });
             const data = await readJson(response, {});
             if (!response.ok) {
               toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ الصلاحيات");
               return;
             }
             state.ticketPermissions = data.permissions || permissions;
             await loadAndHydratePermissions(state.guild.id);
             toast("✅ تم حفظ مصفوفة الصلاحيات وتثبيتها بنجاح!", "success", 2600);
           } catch (error) {
             if (error.message !== "unauth") toast("تعذر الاتصال بالخادم");
           } finally {
             button.disabled = false;
             button.textContent = "حفظ مصفوفة الصلاحيات";
           }
        },
      });
      return el("div", { class: "ticket-next-content" },
        el("div", { class: "ticket-section-title-row" },
          el("div", {},
            el("span", { class: "ticket-kicker", text: "PERMISSIONS" }),
            el("h3", { text: "الصلاحيات وسلوك الإغلاق" }),
            el("p", { text: "المصفوفة تحفظ الرتب المختارة وتبقى متوافقة مع فحوصات محرك التذاكر." }),
          ),
        ),
         el("section", { class: "ticket-next-card ticket-permissions-card" },
           el("div", { class: "table-responsive-wrapper" }, matrix),
          el("div", { class: "ticket-permissions-actions" }, save),
        ),
      );
    };
    function currentPermissions(value) {
      return value && typeof value === "object" ? value : {};
    }
    let body;
    if (state.ticketTab === "panels") body = panelsView();
    else if (state.ticketTab === "tickets") body = ticketsListView();
    else if (state.ticketTab === "archive") body = archiveView();
    else if (state.ticketTab === "ratings") body = ratingsView();
    else if (state.ticketTab === "builder") body = builderView(false);
    else if (state.ticketTab === "sections") body = builderView(true);
    else if (state.ticketTab === "settings") body = settingsView();
    else if (state.ticketTab === "permissions") body = permissionsView();
    else body = overviewView();
    const tabs = el("nav", { class: "ticket-next-tabs", "aria-label": "وحدات مركز التذاكر" },
      tab("overview", "نظرة عامة", "◈"), tab("panels", "البانلات", "▦"), tab("tickets", "التذاكر", "▤"), tab("archive", "الأرشيف", "◫"), tab("ratings", "التقييمات", "★"), tab("settings", "الإعدادات", "⚙"), tab("builder", "محرر البانل", "✦"), tab("sections", "باني الأقسام", "☷"), tab("permissions", "الصلاحيات", "⌘"),
    );
    return el("section", { id: "view-tickets", class: "tickets-view tickets-nextgen" },
      el("header", { class: "ticket-next-hero" },
        el("div", {}, el("span", { class: "ticket-kicker amber", text: "PR1ME / TICKET CRM" }), el("h2", { text: "مركز التذاكر" }), el("p", { text: "إدارة الدعم، اللوحات، الأداء، والصلاحيات من مساحة واحدة متصلة بمحرك Discord." })),
        el("div", { class: "ticket-next-hero-actions" }, el("span", { class: "ticket-live-pill", text: "● LIVE DATA" }), el("button", { class: "ticket-refresh-button", type: "button", text: "↻ تحديث", onClick: refreshTickets })),
      ),
      tabs,
      body,
    );
  }

  function commandsView() {
    const studio = state.commandStudio || { commands: [], roles: [], channels: [] };
    const prefixInput = el("input", {
      class: "prefix-input",
      value: state.draft?.prefix || "!",
      maxlength: "5",
      "aria-label": "بادئة الأوامر",
    });
    const prefixFeedback = el("span", { class: "prefix-feedback", text: "يؤثر فوراً على أوامر السيرفر" });
    const prefixForm = el("form", { class: "prefix-pill" },
      el("div", { class: "prefix-mark", text: "⌁" }),
      el("div", { class: "prefix-copy" },
        el("span", { class: "eyebrow", text: "DYNAMIC PREFIX" }),
        el("strong", { text: "بادئة الأوامر" }),
        prefixFeedback,
      ),
      prefixInput,
      el("button", { class: "btn prefix-save", type: "submit", text: "تطبيق" }),
    );
    prefixForm.onsubmit = (event) => {
      event.preventDefault();
      saveCommandPrefix(prefixInput, prefixFeedback);
    };
    const prefixExamples = el("div", { class: "prefix-examples" });
    const renderPrefixExamples = () => {
      const prefix = prefixInput.value.trim() || "!";
      prefixExamples.replaceChildren(
        el("span", { class: "eyebrow", text: "LIVE EXAMPLES" }),
        el("code", { text: `${prefix}help` }),
        el("code", { text: `${prefix}ticket status` }),
        el("code", { text: `${prefix}rules` }),
      );
    };
    prefixInput.oninput = renderPrefixExamples;
    renderPrefixExamples();
    const commandTabs = el("nav", { class: "command-tabs", "aria-label": "أقسام مركز الأوامر" });
    [
      ["commands", "الأوامر", "command-panel"],
      ["policy", "مصفوفة السياسات", "policy-panel"],
      ["responders", "الردود التلقائية", "auto-responder-panel"],
      ["audit", "النشاط والتدقيق", "command-audit-panel"],
    ].forEach(([key, label, target]) => commandTabs.append(el("button", {
      class: `command-tab ${state.commandTab === key ? "active" : ""}`,
      type: "button",
      "aria-selected": String(state.commandTab === key),
      text: label,
      onClick: () => {
        state.commandTab = key;
        document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "start" });
        commandTabs.querySelectorAll(".command-tab").forEach((item) => item.classList.toggle("active", item.textContent === label));
      },
    })));
    const commandMetrics = el("div", { class: "command-metrics" },
      el("article", { class: "command-metric accent-blue" }, el("small", { text: "إجمالي الأوامر" }), el("strong", { text: String(studio.commands.length) }), el("span", { text: "مسجلة في البوت" })),
      el("article", { class: "command-metric accent-green" }, el("small", { text: "متاحة الآن" }), el("strong", { text: String(studio.commands.filter((command) => command.enabled !== false).length) }), el("span", { text: "أمر مفعّل" })),
      el("article", { class: "command-metric accent-amber" }, el("small", { text: "تحتاج مراجعة" }), el("strong", { text: String(studio.commands.filter((command) => commandPermissionWarnings(command).length).length) }), el("span", { text: "تحذير صلاحيات" })),
      el("article", { class: "command-metric accent-purple" }, el("small", { text: "ردود تلقائية" }), el("strong", { text: String(state.autoResponses.length) }), el("span", { text: "قاعدة نشطة" })),
    );
    const search = el("input", {
      class: "studio-search",
      type: "search",
      placeholder: "ابحث عن أمر أو Cog…",
      value: state.commandSearch,
      "aria-label": "بحث في الأوامر",
    });
    search.oninput = () => {
      state.commandSearch = search.value;
      const rows = $("#command-rows");
      if (rows) rows.replaceWith(commandRows());
    };
    const cogChoices = [...new Set(studio.commands.map((command) => command.cog || "Commands"))].sort();
    const filterSelect = (label, value, options, key) => {
      const select = el("select", { class: "studio-input command-filter", "aria-label": label },
        options.map(([optionValue, optionLabel]) => el("option", { value: optionValue, text: optionLabel })),
      );
      select.value = value;
      select.onchange = () => {
        state[key] = select.value;
        renderPage();
      };
      return select;
    };
    const filterBar = el("div", { class: "command-filter-bar" },
      filterSelect("تصفية حسب Cog", state.commandCogFilter, [["all", "كل الـ Cogs"], ...cogChoices.map((cog) => [cog, cog])], "commandCogFilter"),
      filterSelect("تصفية حسب الحالة", state.commandStatusFilter, [["all", "كل الحالات"], ["enabled", "مفعّلة"], ["disabled", "معطّلة"], ["warning", "تحذير صلاحيات"]], "commandStatusFilter"),
      filterSelect("تصفية حسب الرتبة", state.commandRoleFilter, [["all", "كل الرتب"], ...(studio.roles || []).map((role) => [String(role.id), `@${role.name}`])], "commandRoleFilter"),
    );
    const quickFilters = el("div", { class: "command-quick-filters" },
      [["all", "الكل"], ["enabled", "مفعل"], ["disabled", "معطل"]].map(([value, label]) => el("button", {
        class: `command-quick-filter${state.commandStatusFilter === value ? " active" : ""}`,
        type: "button",
        text: label,
        onClick: () => {
          state.commandStatusFilter = value;
          renderPage();
        },
      })),
    );
    const bulkBar = el("div", { class: "command-bulk-bar" },
      el("label", { class: "bulk-select-all" },
        el("input", { type: "checkbox", checked: commandListForWorkspace().length > 0 && commandListForWorkspace().every((command) => state.selectedCommandIds.includes(String(command.command_name))), onChange: (event) => {
          const ids = commandListForWorkspace().map((command) => String(command.command_name));
          state.selectedCommandIds = event.target.checked ? [...new Set([...state.selectedCommandIds, ...ids])] : state.selectedCommandIds.filter((id) => !ids.includes(id));
          renderPage();
        } }),
        el("span", { text: `${state.selectedCommandIds.length} محدد` }),
      ),
      el("button", { class: "btn ghost", type: "button", disabled: !state.selectedCommandIds.length, text: "تفعيل المحدد", onClick: () => bulkUpdateCommands(true) }),
      el("button", { class: "btn ghost danger-outline", type: "button", disabled: !state.selectedCommandIds.length, text: "تعطيل المحدد", onClick: () => bulkUpdateCommands(false) }),
    );
    const commandPanel = card("قائمة الأوامر",
      el("div", { class: "command-panel" },
        el("div", { class: "commands-list-toolbar" }, search, quickFilters),
        filterBar,
        bulkBar,
        commandRows(),
      ),
    );
    commandPanel.id = "command-panel";
    const responderRoles = state.autoResponderMeta?.roles || studio.roles || [];
    const responderEmojis = state.autoResponderMeta?.emojis || [];
    const responderMembers = state.autoResponderMeta?.members || state.meta?.members || [];
    const targetUserId = el("input", { name: "target_user_id", type: "hidden" });
    const memberSearch = el("input", {
      class: "studio-input member-picker-search",
      type: "search",
      placeholder: "ابحث باسم العضو…",
      autocomplete: "off",
      "aria-label": "البحث عن عضو",
    });
    const memberOptions = el("div", { class: "member-picker-options" });
    const memberPicker = el(
      "div",
      { class: "member-picker" },
      memberSearch,
      memberOptions,
      targetUserId,
    );
    const renderMemberOptions = (query = "") => {
      const normalized = query.trim().toLocaleLowerCase();
      const filtered = responderMembers
        .filter((member) => !normalized || String(member.name || "").toLocaleLowerCase().includes(normalized))
        .slice(0, 100);
      memberOptions.replaceChildren(
        ...(filtered.length
          ? filtered.map((member) => el(
              "button",
              {
                type: "button",
                class: `member-picker-option${String(targetUserId.value) === String(member.id) ? " active" : ""}`,
                "data-member-id": member.id,
                onClick: () => {
                  targetUserId.value = String(member.id);
                  memberSearch.value = member.name;
                  memberOptions.querySelectorAll("[data-member-id]").forEach((item) => item.classList.remove("active"));
                  memberOptions.querySelector(`[data-member-id="${member.id}"]`)?.classList.add("active");
                  pulse();
                },
              },
              el("img", { src: member.avatar || "", alt: "", loading: "lazy" }),
              el("span", { text: member.name }),
            ))
          : [el("small", { class: "hint", text: "لا يوجد عضو مطابق" })]),
      );
    };
    memberSearch.oninput = () => renderMemberOptions(memberSearch.value);
    const setAutoMember = (memberId = "") => {
      targetUserId.value = String(memberId || "");
      const selected = responderMembers.find((member) => String(member.id) === String(memberId));
      memberSearch.value = selected?.name || "";
      renderMemberOptions(memberSearch.value);
    };
    renderMemberOptions();
    const targetScope = el(
      "div",
      { class: "auto-target-panel" },
      el("label", { text: "نطاق الاستهداف (Target Scope)" }),
      el(
        "select",
        { name: "target_type", class: "studio-input" },
        el("option", { value: "everyone" }, "الجميع (Everyone)"),
        el("option", { value: "role" }, "رتبة مخصصة (By Role)"),
        el("option", { value: "user" }, "عضو محدد (Specific Member)"),
      ),
      el(
        "label",
        { class: "auto-role-target", hidden: true },
        "الرتبة المستهدفة",
        el(
          "select",
          { name: "target_role_id", class: "studio-input" },
          el("option", { value: "" }, "اختر رتبة"),
          responderRoles.map((role) => el("option", { value: role.id }, `@${role.name}`)),
        ),
      ),
      el(
        "label",
        { class: "auto-user-target", hidden: true },
        "العضو المستهدف",
        memberPicker,
      ),
    );
    const reactionInput = el("input", { name: "reaction_emoji", type: "hidden" });
    const reactionManualInput = el("input", {
      class: "studio-input reaction-manual-input",
      maxlength: "100",
      placeholder: "أو اكتب إيموجي Unicode مثل 👍",
    });
    const reactionPreview = el("span", { class: "reaction-preview-empty", text: "لم يتم الاختيار" });
    const emojiGrid = el("div", { class: "emoji-picker-grid" });
    const emojiSearch = el("input", {
      class: "studio-input emoji-picker-search",
      type: "search",
      placeholder: "ابحث باسم الإيموجي…",
      autocomplete: "off",
      "aria-label": "البحث عن إيموجي",
    });
    const emojiPopover = el(
      "div",
      { class: "emoji-picker-popover", hidden: true },
      emojiSearch,
      emojiGrid,
    );
    const emojiToggle = el("button", {
      class: "emoji-picker-toggle",
      type: "button",
      "aria-expanded": "false",
      text: "😀 اختيار إيموجي التفاعل (انقر لفتح القائمة) ▼",
      onClick: () => {
        const open = emojiPopover.hasAttribute("hidden");
        emojiPopover.toggleAttribute("hidden", !open);
        emojiToggle.setAttribute("aria-expanded", String(open));
        if (open) emojiSearch.focus();
      },
    });
    const reactionPreviewPill = el("div", { class: "reaction-preview-pill", hidden: true }, reactionPreview);
    const setAutoReaction = (value = "", emoji = null) => {
      const normalized = String(value || "").trim();
      reactionInput.value = normalized;
      reactionManualInput.value = normalized.startsWith("<") ? "" : normalized;
      reactionPreviewPill.toggleAttribute("hidden", !normalized);
      reactionPreview.replaceChildren();
      if (!normalized) {
        reactionPreview.textContent = "لم يتم الاختيار";
      } else if (emoji?.url) {
        reactionPreview.append(
          el("img", { src: emoji.url, alt: emoji.name || "" }),
          el("span", { text: emoji.name || normalized }),
        );
      } else {
        reactionPreview.textContent = normalized;
      }
      if (normalized) {
        const clear = el("button", {
          class: "reaction-preview-clear",
          type: "button",
          title: "مسح الإيموجي",
          text: "✕",
          onClick: (event) => {
            event.stopPropagation();
            setAutoReaction("");
          },
        });
        reactionPreviewPill.append(clear);
      }
      emojiGrid.querySelectorAll("[data-reaction-chip]").forEach((chip) => {
        chip.classList.toggle("active", chip.dataset.reactionChip === normalized);
      });
    };
    const renderEmojiGrid = (query = "") => {
      const normalized = query.trim().toLocaleLowerCase();
      const filtered = responderEmojis.filter((emoji) => !normalized || String(emoji.name || "").toLocaleLowerCase().includes(normalized));
      emojiGrid.replaceChildren(
        ...(filtered.length
          ? filtered.map((emoji) => {
              const token = emoji.token || `<${emoji.animated ? "a" : ""}:${emoji.name}:${emoji.id}>`;
              return el("button", {
                class: "emoji-picker-chip",
                type: "button",
                "data-reaction-chip": token,
                title: emoji.name,
                onClick: () => {
                  setAutoReaction(token, emoji);
                  emojiPopover.setAttribute("hidden", "");
                  emojiToggle.setAttribute("aria-expanded", "false");
                  if (navigator.vibrate) navigator.vibrate(10);
                },
              }, el("img", { src: emoji.url, alt: emoji.name }), el("span", { text: emoji.name }));
            })
          : [el("small", { class: "hint", text: "لا يوجد إيموجي مطابق" })]),
      );
    };
    emojiSearch.oninput = () => renderEmojiGrid(emojiSearch.value);
    reactionManualInput.oninput = () => setAutoReaction(reactionManualInput.value);
    renderEmojiGrid();
    const reactionPicker = el(
      "div",
      { class: "auto-reaction-panel" },
      el("label", { text: "إيموجي التفاعل (Reaction Emoji)" }),
      el("div", { class: "reaction-picker-toolbar" }, emojiToggle, reactionPreviewPill),
      emojiPopover,
      reactionManualInput,
      reactionInput,
    );
    const form = el("form", { id: "auto-responder-form", class: "auto-form" },
      el("div", { class: "auto-form-heading" },
        el("div", { class: "eyebrow", text: "TRIGGER ENGINE" }),
        el("h3", { id: "auto-form-title", text: "إنشاء رد تلقائي" }),
        el("p", { text: "حوّل الكلمات المتكررة إلى ردود ذكية قابلة للتخصيص." }),
      ),
      el("label", { text: "المشغل" }),
      el("input", { name: "trigger", class: "studio-input", maxlength: "500", placeholder: "مثال: مرحباً أو ^help$" }),
      el("label", { text: "نوع المطابقة" }),
      el("div", { class: "match-badges" },
        ["exact", "contains", "regex"].map((type, index) => {
          const labels = { exact: "مطابقة تامة", contains: "يحتوي على الكلمة", regex: "تعبير نمطي Regex" };
          const button = el("button", {
            class: `match-badge${index === 0 ? " active" : ""}`,
            type: "button",
            "data-match-type": type,
            text: labels[type],
          });
          button.onclick = () => {
            form.querySelectorAll("[data-match-type]").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
            pulse();
          };
          return button;
        }),
      ),
      el("label", { text: "نص الرد" }),
      el("textarea", { name: "response", class: "studio-textarea", maxlength: "2000", placeholder: "اكتب الرد هنا…" }),
      el("div", { class: "variable-strip" },
        el("span", { text: "إدراج سريع:" }),
        ["{user}", "{channel}", "{server}", "{random:نعم|لا}"].map((variable) => {
          const chip = el("button", { class: "variable-chip", type: "button", text: variable });
          chip.onclick = () => insertVariable(form.elements.response, variable);
          return chip;
        }),
      ),
      el("div", { class: "auto-form-grid" },
        el("label", {}, "التبريد",
          el("input", { name: "cooldown_seconds", type: "range", min: "0", max: "60", step: "1", value: "5" }),
          el("output", { class: "range-output", text: "5s" }),
        ),
        el("label", {}, "نطاق القناة",
          el("select", { name: "channel_id", class: "studio-input" },
            el("option", { value: "" }, "كل القنوات"),
            (studio.channels || []).map((channel) => el("option", { value: channel.id }, `#${channel.name}`)),
          ),
        ),
      ),
      targetScope,
      reactionPicker,
      el("div", { class: "auto-form-actions" },
        el("button", { class: "btn primary", type: "submit", text: "حفظ القاعدة" }),
        el("button", { class: "btn ghost", type: "button", text: "مسح", onClick: () => setRuleForm() }),
      ),
    );
    const autoPreview = el("section", { class: "auto-live-preview" },
      el("div", { class: "section-heading compact" }, el("div", {}, el("div", { class: "eyebrow", text: "LIVE PREVIEW" }), el("h3", { text: "معاينة الرد" }))),
      el("div", { class: "auto-preview-message", text: "اكتب المشغل والرد لرؤية المعاينة هنا." }),
      el("button", { class: "btn ghost auto-test-button", type: "button", text: "اختبار محلي", onClick: () => {
        const trigger = form.elements.trigger.value.trim() || "المشغل";
        const response = form.elements.response.value.trim() || "لا يوجد رد بعد";
        toast(`اختبار «${trigger}»: ${response.slice(0, 90)}`, "info", 3200);
      } }),
    );
    form.append(autoPreview);
    const updateAutoPreview = () => {
      const trigger = form.elements.trigger.value.trim() || "المشغل";
      const response = form.elements.response.value.trim() || "اكتب نص الرد هنا…";
      autoPreview.querySelector(".auto-preview-message").replaceChildren(
        el("span", { class: "preview-trigger", text: trigger }),
        el("span", { text: response }),
      );
    };
    form.elements.trigger.oninput = updateAutoPreview;
    form.elements.response.oninput = updateAutoPreview;
    form._setAutoMember = setAutoMember;
    form._setAutoReaction = setAutoReaction;
    form.elements.target_type.onchange = () => updateAutoTargetFields(form);
    updateAutoTargetFields(form);
    updateAutoPreview();
    form.elements.cooldown_seconds.oninput = () => {
      form.querySelector(".range-output").textContent = `${form.elements.cooldown_seconds.value}s`;
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      saveAutoResponder(form);
    };
    const cards = el("div", { class: "trigger-grid" });
    if (!state.autoResponses.length) {
      cards.append(el("div", { class: "empty studio-empty", text: "لا توجد ردود تلقائية مفعّلة بعد" }));
    } else {
      state.autoResponses.forEach((rule) => {
        const cardNode = el("article", { class: "trigger-card" },
          el("div", { class: "trigger-card-top" },
            el("span", { class: "trigger-type", text: rule.match_type }),
            el("span", { class: "trigger-count", text: `${rule.execution_count || 0} تنفيذ` }),
          ),
          el("h4", { text: rule.trigger }),
          el("p", { text: rule.response }),
          el("small", { text: `${rule.channel_id ? "قناة محددة" : "كل القنوات"} · تبريد ${rule.cooldown_seconds}s` }),
          el("div", { class: "trigger-actions" },
            el("button", { class: "icon-action", type: "button", title: "نسخ المشغل", text: "⧉", onClick: () => navigator.clipboard?.writeText(rule.trigger).then(() => toast("تم نسخ المشغل", "success", 1600)) }),
            el("button", { class: "icon-action", type: "button", title: "تعديل", text: "✎", onClick: () => setRuleForm(rule) }),
            el("button", { class: "icon-action danger", type: "button", title: "حذف", text: "⌫", onClick: () => deleteAutoResponder(rule) }),
          ),
        );
        cards.append(cardNode);
      });
    }
    const policyPanel = card("Policy Matrix / مصفوفة الوصول", el("div", { class: "policy-panel-body" },
      el("p", { class: "hint", text: "عرض سريع لعلاقة الأوامر بالرتب المسموحة. افتح أي أمر لتعديل السياسة من التفاصيل." }),
      commandPolicyMatrix(),
    ));
    policyPanel.id = "policy-panel";
    const auditEntries = [
      ...(state.incidents || []).map((item) => ({
        title: item.action_type || item.action || item.type || "حدث أمني",
        detail: item.culprit_name || item.reason || item.mitigation_taken || "سجل وارد من محرك الحماية",
        at: item.timestamp,
      })),
      ...(studio.commands || []).filter((command) => command.last_used_at).map((command) => ({
        title: `/${command.command_name}`,
        detail: `آخر استخدام · ${command.cog || "Commands"}`,
        at: command.last_used_at,
      })),
    ].sort((a, b) => new Date(b.at || 0) - new Date(a.at || 0)).slice(0, 8);
    const auditPanel = card("Activity / سجل النشاط والتدقيق", el("div", { class: "command-audit-panel-body" },
      auditEntries.length
        ? auditEntries.map((entry, index) => el("div", { class: "audit-row" },
            el("span", { class: "audit-marker" }),
            el("div", {}, el("strong", { text: entry.title }), el("small", { text: `${entry.detail} · ${entry.at ? new Date(entry.at).toLocaleString("ar") : "وقت غير متاح" }` })),
          ))
        : [el("div", { class: "empty studio-empty", text: "لا توجد أحداث تدقيق مقدمة من الخادم بعد" })],
    ));
    auditPanel.id = "command-audit-panel";
    const autoCard = card("Visual Auto-Responder Studio", form);
    autoCard.id = "auto-responder-panel";
    return el("section", { id: "view-commands", class: "commands-view" },
      el("div", { class: "studio-hero commands-hero" },
        el("div", { class: "eyebrow", text: `${state.guild.name} / COMMANDS` }),
        el("h2", { text: "استوديو الأوامر والاختصارات" }),
        el("p", { text: "اضبط الوصول، بدّل prefix فورياً، وابنِ ردوداً تلقائية بواجهة AMOLED سريعة وواضحة." }),
      ),
      commandMetrics,
      commandPanel,
      prefixForm,
      prefixExamples,
      commandTabs,
      policyPanel,
      autoCard,
      el("section", { class: "active-trigger-section" },
        el("div", { class: "section-heading" },
          el("div", {}, el("div", { class: "eyebrow", text: "LIVE REGISTRY" }), el("h3", { text: "Active Triggers" })),
          el("span", { class: "live-dot", text: `${state.autoResponses.length} مفعّل` }),
        ),
        cards,
      ),
      auditPanel,
    );
  }
  function overviewMetric(label, value, hint, tone, view) {
    return el(
      "button",
      {
        class: `overview-metric metric-${tone}`,
        type: "button",
        onClick: () => navigateView(view),
      },
      el("span", { class: "metric-label", text: label }),
      el("strong", { text: String(value) }),
      el("small", { text: hint }),
    );
  }
  function dashboardBentoCard({
    eyebrow,
    title,
    description,
    value,
    valueLabel,
    icon,
    tone,
    view,
    span = 6,
    action = "فتح القسم",
  }) {
    return el(
      "article",
      { class: `dashboard-bento-card bento-span-${span} bento-tone-${tone}` },
      el(
        "div",
        { class: "dashboard-bento-head" },
        el("span", { class: "dashboard-bento-icon", text: icon, "aria-hidden": "true" }),
        el(
          "div",
          {},
          el("span", { class: "dashboard-bento-eyebrow", text: eyebrow }),
          el("h3", { text: title }),
        ),
      ),
      el("p", { class: "dashboard-bento-description", text: description }),
      el(
        "div",
        { class: "dashboard-bento-stat" },
        el("strong", { text: String(value) }),
        el("span", { text: valueLabel }),
      ),
      el(
        "button",
        {
          class: "dashboard-bento-action",
          type: "button",
          onClick: () => navigateView(view),
        },
        el("span", { text: action }),
        el("span", { text: "←", "aria-hidden": "true" }),
      ),
    );
  }
  function chartCard(title, subtitle, canvasId, tone = "blue") {
    const canvas = el("canvas", {
      class: `metric-chart chart-${tone}`,
      id: canvasId,
      width: "640",
      height: "220",
      role: "img",
      "aria-label": title,
    });
    return el(
      "section",
      { class: "overview-panel chart-card" },
      el("div", { class: "panel-heading" }, el("div", { class: "eyebrow", text: "LIVE TELEMETRY" }), el("h2", { text: title }), el("small", { text: subtitle })),
      canvas,
    );
  }
  function drawLine(canvas, values, color) {
    if (!canvas || !values.length) return;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 640;
    const height = 220;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = Math.max(max - min, 1);
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "#162338";
    ctx.lineWidth = 1;
    for (let row = 1; row < 4; row += 1) {
      const y = (height * row) / 4;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    const points = values.map((value, index) => [
      values.length === 1 ? width / 2 : (index / (values.length - 1)) * width,
      height - 18 - ((value - min) / span) * (height - 32),
    ]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.stroke();
    ctx.fillStyle = color;
    points.slice(-1).forEach(([x, y]) => {
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    });
  }
  function drawOverviewTrafficChart(canvas) {
    if (!canvas) return;
    const series = overviewSeriesData();
    if (!series.length) return;
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(canvas.clientWidth || 620, 280);
    const height = 220;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const left = 8;
    const right = width - 8;
    const top = 12;
    const bottom = height - 25;
    const configs = [
      ["members", "الأعضاء", "#f58cb0"],
      ["messages", "الرسائل", "#21d7a4"],
      ["joins", "الانضمام", "#8f86ff"],
      ["leaves", "المغادرة", "#ff6b89"],
      ["latency", "الاستجابة", "#62c9ff"],
    ];
    const values = configs.flatMap(([key]) => series.map((point) => point[key]).filter(Number.isFinite));
    const max = Math.max(...values, 1);
    const xAt = (index) => series.length === 1
      ? (left + right) / 2
      : left + (index / (series.length - 1)) * (right - left);
    const yAt = (value) => bottom - (value / max) * (bottom - top);
    ctx.strokeStyle = "#ffffff12";
    ctx.lineWidth = 1;
    for (let row = 0; row < 4; row += 1) {
      const y = top + ((bottom - top) * row) / 3;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
    }
    const points = [];
    configs.forEach(([key, label, color]) => {
      const line = series.map((point, index) => ({
        index,
        value: point[key],
        x: xAt(index),
        y: yAt(Number.isFinite(point[key]) ? point[key] : 0),
      })).filter((point) => Number.isFinite(point.value));
      if (!line.length) return;
      points.push({ key, label, color, line });
      ctx.beginPath();
      line.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();
      line.forEach((point) => {
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.arc(point.x, point.y, 3, 0, Math.PI * 2);
        ctx.fill();
      });
    });
    ctx.fillStyle = "#7690a9";
    ctx.font = "10px system-ui";
    series.forEach((point, index) => {
      if (index === 0 || index === series.length - 1 || series.length < 8) {
        ctx.fillText(point.label, Math.max(left, xAt(index) - 18), height - 6);
      }
    });
    const tooltip = canvas.parentElement?.querySelector(".overview-chart-tooltip");
    canvas.onpointermove = (event) => {
      if (!tooltip || !series.length) return;
      const rect = canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(width, event.clientX - rect.left));
      const index = series.length === 1
        ? 0
        : Math.round(((x - left) / Math.max(1, right - left)) * (series.length - 1));
      const point = series[Math.max(0, Math.min(series.length - 1, index))];
      tooltip.hidden = false;
      tooltip.textContent = `${point.label}  ·  ${points.map((item) => `${item.label}: ${overviewNumber(point[item.key])}`).join("  ·  ")}`;
    };
    canvas.onpointerleave = () => {
      if (tooltip) tooltip.hidden = true;
    };
  }
  function drawDashboardCharts() {
    const series = state.stats?.series || [];
    drawLine(
      $("#latency-chart"),
      series.map((point) => Number(point.latency_ms)).filter(Number.isFinite),
      "#38bdf8",
    );
    drawLine(
      $("#members-chart"),
      series.map((point) => Number(point.members)).filter(Number.isFinite),
      "#34d399",
    );
    drawOverviewTrafficChart($("#overview-traffic-chart"));
    document.querySelectorAll(".overview-live-counter").forEach((counter) => {
      const target = Number(counter.dataset.counterValue);
      if (!Number.isFinite(target)) return;
      const started = performance.now();
      const tick = (now) => {
        const progress = Math.min(1, (now - started) / 650);
        const eased = 1 - Math.pow(1 - progress, 3);
        counter.textContent = Math.round(target * eased).toLocaleString("en-US");
        if (progress < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }
  function operationsView(view) {
    const counts = state.stats?.counts || {};
    const labels = {
      moderation: ["المراقبة", "أحدث الإنذارات وإجراءات الإدارة", counts.infractions || 0, "infractions"],
      economy: ["الاقتصاد", "الحسابات المسجلة في الخزينة", counts.economy_accounts || 0, "economy"],
      community: ["المجتمع", "التذاكر المفتوحة والأرشيف", counts.tickets_active || 0, "tickets"],
      ai: ["الذكاء الاصطناعي", "أدوات الذكاء متاحة من أوامر Discord", counts.commands_enabled || 0, "commands"],
      system: ["النظام", "حالة الاتصالات والقياسات الحية", state.online ? "ONLINE" : "OFFLINE", "overview"],
    };
    const [title, description, value, target] = labels[view];
    const actions = (state.actions || []).slice(0, 8);
    const actionList = actions.length
      ? actions.map((action) => el(
          "div",
          { class: "activity-row" },
          el("span", { class: "activity-dot" }),
          el("div", {}, el("strong", { text: action.action || "إجراء" }), el("small", { text: action.reason || "تم تسجيل الإجراء" })),
        ))
      : [el("div", { class: "empty-row", text: "لا توجد إجراءات مسجلة لهذا السيرفر" })];
    return el(
      "section",
      { class: "operations-view" },
      el("div", { class: "section-intro" }, el("div", { class: "eyebrow", text: `${state.guild.name} / ${title.toUpperCase()}` }), el("h1", { text: title }), el("p", { text: description })),
      el(
        "div",
        { class: "overview-metrics" },
        overviewMetric("القيمة الحالية", value, "من الحالة الحية", "blue", target),
        overviewMetric("التذاكر المفتوحة", counts.tickets_active || 0, "Help Desk", "purple", "tickets"),
        overviewMetric("الحوادث والمخالفات", counts.infractions || 0, "سجل الإجراءات", "red", "security"),
        overviewMetric("الأتمتة النشطة", counts.auto_responses || 0, "ردود تلقائية", "green", "commands"),
      ),
      el(
        "section",
        { class: "overview-panel" },
        el("div", { class: "panel-heading" }, el("div", { class: "eyebrow", text: "ACTION STREAM" }), el("h2", { text: "آخر الإجراءات" })),
        el("div", { class: "overview-activity" }, ...actionList),
      ),
    );
  }
  function overviewView() {
    const openIncidents = state.incidents.length;
    const counts = state.stats?.counts || {};
    const members = Number(state.guild.members ?? counts.members ?? 0);
    const latestActions = [
      ...(state.actions || []).map((item) => ({
        title: item.action || item.action_type || "إجراء مسجل",
        detail: item.reason || item.target_name || "تمت معالجة الإجراء من النظام",
        at: item.timestamp || item.created_at,
        tone: "cyan",
      })),
      ...(state.incidents || []).map((item) => ({
        title: item.action || item.type || "تنبيه أمني",
        detail: item.reason || "تم تسجيل الحدث من محرك الحماية",
        at: item.timestamp || item.created_at,
        tone: "red",
      })),
    ].sort((a, b) => new Date(b.at || 0) - new Date(a.at || 0)).slice(0, 4);
    const serviceRows = [
      ["إدارة النظام", "إعدادات البوت وملفات السيرفر", "settings", "system"],
      ["التقارير", "التحليلات والإحصائيات", "analytics", "reports"],
      ["الحماية", "جدار الحماية والفحص", "security", "shield"],
      ["الإعدادات", "تخصيص النظام", "settings", "tune"],
    ];
    const serviceHealth = [
      ["خادم التطبيقات", state.online ? "يعمل بشكل طبيعي" : "الاتصال يحتاج مراجعة", state.online],
      ["قاعدة البيانات", "الحفظ والمزامنة يعملان", true],
      ["خدمة الحماية", state.lockdown ? "وضع الإغلاق مفعّل" : "مراقبة فعّالة", !state.lockdown || openIncidents > 0],
    ];
    const formatTime = (value) => value
      ? new Date(value).toLocaleTimeString("ar", { hour: "2-digit", minute: "2-digit" })
      : "الآن";
    const activityList = el("div", { class: "overview-activity" });
    if (!latestActions.length) {
      activityList.append(
        el("div", { class: "activity-empty", text: "لا توجد نشاطات جديدة في هذا السيرفر" }),
      );
    } else {
      latestActions.forEach((item) => {
        activityList.append(
          el("div", { class: `activity-row activity-${item.tone}` },
            el("span", { class: "activity-icon", "aria-hidden": "true" }),
            el("div", {},
              el("strong", { text: item.title }),
              el("small", { text: item.detail }),
            ),
            el("time", { text: formatTime(item.at) }),
          ),
        );
      });
    }
    const serviceList = el("div", { class: "service-list" });
    serviceRows.forEach(([title, description, view, icon]) => {
      serviceList.append(
        el("button", { class: "service-row", type: "button", onClick: () => navigateView(view) },
          el("span", { class: `service-icon service-icon-${icon}`, "aria-hidden": "true" }),
          el("span", {}, el("strong", { text: title }), el("small", { text: description })),
          el("span", { class: "service-chevron", text: "‹", "aria-hidden": "true" }),
        ),
      );
    });
    const healthList = el("div", { class: "health-list" });
    serviceHealth.forEach(([title, detail, online]) => {
      healthList.append(
        el("div", { class: "health-row" },
          el("span", { class: `health-icon ${online ? "" : "is-offline"}`, "aria-hidden": "true" }),
          el("span", {}, el("strong", { text: title }), el("small", { text: detail })),
          el("span", { class: `health-state ${online ? "" : "is-offline"}`, text: online ? "طبيعي" : "متوقف" }),
        ),
      );
    });
    return el(
      "section",
      { class: "overview-view" },
      el(
        "div",
        { class: "overview-hero" },
        el("div", { class: "overview-hero-art", "aria-hidden": "true" }),
        el("div", { class: "overview-hero-copy" },
          el("div", { class: "eyebrow", text: "PR1ME TEAM / CONTROL CENTER" }),
          el("h1", { text: "كل شيء تحت السيطرة." }),
          el("p", { text: "مراقبة، حماية، أداء، مستمر — نظامك يعمل بكفاءة وأمان." }),
          el("div", { class: "hero-meta" },
            el("span", { class: `status-pulse ${state.online ? "" : "offline"}` }),
            el("span", { text: state.online ? "النظام نشط" : "الاتصال يحتاج مراجعة" }),
            el("small", { text: `آخر تحديث: ${formatTime(new Date())}` }),
          ),
        ),
        el("div", { class: "overview-hero-status" },
          el("span", { class: `hero-shield ${state.online ? "" : "is-offline"}`, "aria-hidden": "true" }),
          el("div", {},
            el("strong", { text: state.online ? "آمن ومتصل" : "غير متصل" }),
            el("small", { text: `${members || "—"} عضو متصل` }),
          ),
        ),
      ),
      el(
        "div",
        { class: "overview-metrics" },
        overviewMetric("المستخدمون النشطون", members || "—", "مستخدم", "blue", "community"),
        overviewMetric("الحوادث الأمنية", openIncidents, openIncidents ? "تحتاج مراجعة" : "لا توجد تهديدات", "red", "security"),
        overviewMetric("الأجهزة المتصلة", counts.devices || members || "—", "جهاز متصل", "purple", "analytics"),
        overviewMetric("حالة النظام", state.online ? "آمن" : "مراجعة", "لا توجد تهديدات", "green", "system"),
      ),
      el("section", { class: "overview-panel overview-services" },
        el("div", { class: "panel-heading overview-panel-heading" },
          el("h2", { text: "الخدمات الرئيسية" }),
          el("button", { class: "text-link", type: "button", text: "عرض الكل", onClick: () => navigateView("settings") }),
        ),
        serviceList,
      ),
      el("div", { class: "overview-columns overview-lower-grid" },
        el("section", { class: "overview-panel overview-recent" },
          el("div", { class: "panel-heading overview-panel-heading" },
            el("h2", { text: "أحدث الأنشطة" }),
            el("button", { class: "text-link", type: "button", text: "عرض الكل", onClick: () => navigateView("security") }),
          ),
          activityList,
        ),
        el("section", { class: "overview-panel overview-health" },
          el("div", { class: "panel-heading" }, el("h2", { text: "حالة الخدمات" })),
          healthList,
        ),
      ),
    );
  }
  function overviewMetricValue(point, keys) {
    for (const key of keys) {
      const value = Number(point?.[key]);
      if (Number.isFinite(value)) return value;
    }
    return null;
  }
  function overviewSeriesData() {
    const raw = Array.isArray(state.stats?.series) ? state.stats.series : [];
    const rangeDays = state.overviewRange === "30d" ? 30 : state.overviewRange === "all" ? Infinity : 7;
    const cutoff = rangeDays === Infinity ? 0 : Date.now() - rangeDays * 86400000;
    return raw
      .map((point, index) => {
        const timestamp = Number(point.ts || point.timestamp || point.created_at || 0);
        const date = timestamp > 100000000000 ? new Date(timestamp) : new Date(timestamp * 1000);
        return {
          raw: point,
          index,
          timestamp: date.getTime() || Date.now(),
          label: date.getTime()
            ? date.toLocaleDateString("ar", { month: "short", day: "numeric" })
            : `قياس ${index + 1}`,
          members: overviewMetricValue(point, ["members", "member_count"]),
          latency: overviewMetricValue(point, ["latency_ms", "latency"]),
          messages: overviewMetricValue(point, ["messages", "message_count", "messages_count"]),
          joins: overviewMetricValue(point, ["joins", "join_count", "joins_count"]),
          leaves: overviewMetricValue(point, ["leaves", "leave_count", "leaves_count"]),
          voiceSessions: overviewMetricValue(point, ["voice_sessions", "voice_sessions_count"]),
          voiceMinutes: overviewMetricValue(point, ["avg_voice_minutes", "voice_minutes"]),
        };
      })
      .filter((point) => point.timestamp >= cutoff)
      .slice(-120);
  }
  function overviewNumber(value, fallback = "—") {
    return Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-US") : fallback;
  }
  function overviewTime(value) {
    if (!value) return "الآن";
    const numeric = Number(value);
    const date = Number.isFinite(numeric)
      ? new Date(numeric > 100000000000 ? numeric : numeric * 1000)
      : new Date(value);
    return Number.isNaN(date.getTime())
      ? "الآن"
      : date.toLocaleTimeString("ar", { hour: "2-digit", minute: "2-digit" });
  }
  function overviewCounterCard(label, value, hint, tone, icon, view, status = "") {
    const numeric = Number(value);
    return el(
      "button",
      {
        class: `overview-pro-kpi kpi-${tone} ${status ? `kpi-${status}` : ""}`,
        type: "button",
        onClick: () => navigateView(view),
      },
      el("span", { class: "overview-pro-kpi-icon", text: icon, "aria-hidden": "true" }),
      el("span", { class: "overview-pro-kpi-copy" },
        el("small", { text: label }),
        el("strong", {
          class: "overview-live-counter",
          text: Number.isFinite(numeric) ? "0" : String(value),
          "data-counter-value": Number.isFinite(numeric) ? String(numeric) : "",
        }),
        el("em", { text: hint }),
      ),
      status ? el("span", { class: "overview-kpi-status", text: status === "good" ? "طبيعي" : "يحتاج مراجعة" }) : null,
    );
  }
  function overviewSkeleton(label) {
    return el(
      "div",
      { class: "overview-skeleton-state", role: "status" },
      el("span", { class: "overview-skeleton-orb", "aria-hidden": "true" }),
      el("strong", { text: label }),
      el("small", { text: "بانتظار أول قياس من الخدمة…" }),
      el("div", { class: "overview-skeleton-lines", "aria-hidden": "true" },
        el("i"), el("i"), el("i"),
      ),
    );
  }
  function overviewTrafficChart() {
    const range = el("select", { class: "overview-range-select", "aria-label": "الفترة الزمنية" });
    [
      ["7d", "آخر 7 أيام"],
      ["30d", "هذا الشهر"],
      ["all", "كل القياسات"],
    ].forEach(([value, text]) => range.append(el("option", { value, text })));
    range.value = state.overviewRange;
    range.onchange = () => {
      state.overviewRange = range.value;
      sessionStorage.setItem("overview-range", range.value);
      renderPage();
    };
    const canvas = el("canvas", {
      id: "overview-traffic-chart",
      class: "overview-traffic-chart",
      height: "220",
      role: "img",
      "aria-label": "مخطط قياسات النشاط",
    });
    const series = overviewSeriesData();
    const availableMetrics = [
      ["members", "الأعضاء", "legend-members"],
      ["messages", "الرسائل", "legend-messages"],
      ["joins", "الانضمام", "legend-joins"],
      ["leaves", "المغادرة", "legend-leaves"],
      ["latency", "الاستجابة", "legend-latency"],
    ].filter(([key]) => series.some((point) => Number.isFinite(point[key])));
    const hasTraffic = availableMetrics.length > 0;
    return el(
      "section",
      { class: "overview-pro-card overview-traffic-card" },
      el("div", { class: "overview-pro-card-head" },
        el("div", {},
          el("span", { class: "overview-kicker", text: "GROWTH / TRAFFIC" }),
          el("h2", { text: "النمو وحركة السيرفر" }),
          el("p", { text: hasTraffic ? "قياسات حية من نفس مصدر الإحصائيات الحالي." : "ستظهر القياسات هنا عند وصول أول نبضة." }),
        ),
        range,
      ),
      hasTraffic ? canvas : overviewSkeleton("البث اللحظي"),
      el("div", { class: "overview-chart-legend" },
        ...availableMetrics.map(([, label, className]) => el("span", { class: className, text: label })),
      ),
      el("div", { class: "overview-chart-tooltip", role: "status", "aria-live": "polite", hidden: true }),
    );
  }
  function overviewPulsePanel() {
    const points = overviewSeriesData();
    const latest = points[points.length - 1];
    const latency = latest?.latency;
    const totalMembers = Number(state.guild?.members ?? latest?.members);
    const cachedMembers = Array.isArray(state.meta?.members) ? state.meta.members.length : null;
    const channelCount = Array.isArray(state.meta?.channels) ? state.meta.channels.length : 0;
    const eventCount = (state.actions || []).length + (state.incidents || []).length;
    const memberRatio = Number.isFinite(totalMembers) && totalMembers > 0 && cachedMembers != null
      ? Math.min(100, Math.round((cachedMembers / totalMembers) * 100))
      : null;
    const bars = [
      ["الاتصال", state.online ? "متصل" : "غير متصل", state.online ? 100 : 0, "cyan"],
      ["الأعضاء المحملون", cachedMembers == null ? "—" : `${overviewNumber(cachedMembers)}/${overviewNumber(totalMembers)}`, memberRatio, "green"],
      ["القنوات المعروفة", overviewNumber(channelCount), channelCount > 0 ? 100 : 0, "purple"],
      ["الأحداث المسجلة", overviewNumber(eventCount), Math.min(100, eventCount * 10), "pink"],
    ];
    const circumference = 2 * Math.PI * 44;
    const connectionPercent = state.online ? 100 : 0;
    return el(
      "section",
      { class: "overview-pro-card overview-pulse-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "ACTIVITY PULSE" }), el("h2", { text: "نبض النشاط" })),
        el("span", { class: `overview-live-chip ${state.online ? "" : "offline"}` }, state.online ? "LIVE" : "OFFLINE"),
      ),
      el("div", { class: "overview-pulse-layout" },
        el("div", { class: "overview-gauge" },
          el("svg", { viewBox: "0 0 110 110", "aria-hidden": "true" },
            el("circle", { class: "gauge-track", cx: "55", cy: "55", r: "44" }),
            el("circle", {
              class: "gauge-value",
              cx: "55",
              cy: "55",
              r: "44",
              "stroke-dasharray": `${(circumference * connectionPercent) / 100} ${circumference}`,
            }),
          ),
          el("strong", { text: Number.isFinite(latency) ? `${overviewNumber(latency)}ms` : state.online ? "متصل" : "—" }),
          el("small", { text: "زمن الاستجابة الفعلي" }),
        ),
        el("div", { class: "overview-pulse-bars" },
          bars.map(([label, value, width, tone]) => el("div", { class: `pulse-bar-row pulse-${tone}` },
              el("div", {}, el("span", { text: label }), el("b", { text: value })),
              el("i", {}, el("em", { style: `width:${width == null ? 0 : width}%` })),
            )),
        ),
      ),
    );
  }
  function overviewActivityStream() {
    const recent = [...(state.actions || []), ...(state.incidents || [])]
      .sort((a, b) => new Date(b.timestamp || b.created_at || 0) - new Date(a.timestamp || a.created_at || 0))
      .slice(0, 5);
    return el(
      "section",
      { class: "overview-pro-card overview-stream-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "LIVE STREAM" }), el("h2", { text: "آخر الأحداث" })),
        el("button", { class: "overview-inline-action", type: "button", text: "السجلات", onClick: () => navigateView("analytics") }),
      ),
      recent.length
        ? el("div", { class: "overview-stream-list" }, ...recent.map((item) => el("div", { class: "overview-stream-row" },
            el("span", { class: "overview-stream-dot" }),
            el("div", {}, el("strong", { text: item.action || item.action_type || item.type || "حدث جديد" }), el("small", { text: item.reason || item.target_name || "تمت المعالجة من محرك النظام" })),
            el("time", { text: overviewTime(item.timestamp || item.created_at) }),
          )))
        : overviewSkeleton("البث اللحظي"),
    );
  }
  function overviewMembersPanel() {
    const allMembers = Array.isArray(state.meta?.members) ? state.meta.members.filter(Boolean) : [];
    const members = allMembers.slice(0, 3);
    const featured = members[0];
    const totalMembers = Number(state.guild?.members);
    return el(
      "section",
      { class: "overview-pro-card overview-members-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "MEMBER INSIGHTS" }), el("h2", { text: "الأعضاء والنشاط" })),
        el("button", { class: "overview-inline-action", type: "button", text: "المجتمع", onClick: () => navigateView("community") }),
      ),
      featured
        ? el("div", { class: "overview-featured-member" },
            avatar(featured.avatar, featured.name),
             el("div", {}, el("strong", { text: featured.name }), el("small", { text: "عضو حقيقي من قائمة السيرفر" })),
             el("span", { class: "member-score", text: "متاح الآن" }),
          )
        : overviewSkeleton("تتبع المنضمين الجدد"),
      el("div", { class: "overview-member-insight-grid" },
        el("div", {}, el("span", { text: "الأعضاء المحملون فعلياً" }), el("strong", { text: allMembers.length ? overviewNumber(allMembers.length) : "لا توجد بيانات" })),
        el("div", {}, el("span", { text: "إجمالي Discord" }), el("strong", { class: "safe", text: Number.isFinite(totalMembers) ? overviewNumber(totalMembers) : "غير متاح" })),
      ),
    );
  }
  function overviewHeatmapPanel() {
    const dayLabels = ["أحد", "اثن", "ثلث", "أربع", "خمس", "جمع", "سبت"];
    const events = [...(state.actions || []), ...(state.incidents || [])];
    const cells = [];
    for (let day = 0; day < 7; day += 1) {
      for (let slot = 0; slot < 12; slot += 1) {
        const count = events.filter((item) => {
          const date = new Date(item.timestamp || item.created_at || 0);
          return date.getDay() === day && Math.floor(date.getHours() / 2) === slot;
        }).length;
        const level = Math.min(4, count);
        cells.push(el("button", {
          class: `heat-cell heat-${level}`,
          type: "button",
          title: `${dayLabels[day]} · ${slot * 2}:00 · ${count} أحداث`,
          "aria-label": `${dayLabels[day]}، ${slot * 2}:00، ${count} أحداث`,
        }));
      }
    }
    const written = state.overviewHeatMode === "written";
    return el(
      "section",
      { class: "overview-pro-card overview-heatmap-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "INTERACTION HEATMAP" }), el("h2", { text: "النشاط الكتابي والصوتي" })),
        el("div", { class: "overview-segmented" },
          el("button", { class: written ? "active" : "", type: "button", "aria-pressed": String(written), text: "كتابي", onClick: () => { state.overviewHeatMode = "written"; renderPage(); } }),
          el("button", { class: !written ? "active" : "", type: "button", "aria-pressed": String(!written), text: "صوتي", onClick: () => { state.overviewHeatMode = "voice"; renderPage(); } }),
        ),
      ),
      written ? el("div", { class: "overview-heatmap" }, dayLabels.map((label) => el("div", { class: "heat-row" }, el("span", { text: label }), ...cells.slice(dayLabels.indexOf(label) * 12, dayLabels.indexOf(label) * 12 + 12)))) : overviewSkeleton("بيانات الصوت"),
      el("div", { class: "overview-heatmap-footer" },
        el("span", { text: events.length ? `آخر ${events.length} أحداث مرصودة` : "لا توجد أحداث كافية بعد" }),
        el("span", { class: "heat-legend", text: "منخفض  ▪ ▪ ▪  مرتفع" }),
      ),
    );
  }
  function overviewChannelsPanel() {
    const channels = Array.isArray(state.meta?.channels) ? state.meta.channels.slice(0, 8) : [];
    return el(
      "section",
      { class: "overview-pro-card overview-channels-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "CHANNEL MATRIX" }), el("h2", { text: "القنوات والنظام" })),
        el("button", { class: "overview-inline-action", type: "button", text: "الإعدادات", onClick: () => navigateView("settings") }),
      ),
      channels.length
        ? el("div", { class: "overview-channel-list" }, ...channels.map((channel, index) => el("div", { class: "overview-channel-row" },
            el("span", { class: "channel-rank", text: String(index + 1).padStart(2, "0") }),
            el("div", {}, el("strong", { text: `# ${channel.name || "قناة"}` }), el("small", { text: channel.type || "Text Channel" })),
            el("span", { class: "channel-health", text: channel.type === "voice" ? "صوتي" : "متاح" }),
          )))
        : overviewSkeleton("قنوات السيرفر"),
      el("div", { class: "overview-system-matrix" },
        [
          ["التذاكر", state.tickets.active.length > 0, "tickets"],
          ["الاقتصاد", Boolean(state.economy.settings), "economy"],
          ["الإدارة", state.online, "settings"],
        ].map(([label, active, view]) => el("div", { class: `system-status-card ${active ? "is-active" : "is-disabled"}` },
          el("span", { text: active ? "●" : "—" }),
          el("div", {}, el("strong", { text: label }), el("small", { text: active ? "مفعل" : "غير مفعل" })),
          el("button", { type: "button", text: active ? "فتح" : "إعداد", onClick: () => navigateView(view) }),
        )),
      ),
    );
  }
  function analyticsNumber(value, fallback = "—") {
    if (value === null || value === undefined || value === "") return fallback;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toLocaleString("en-US") : fallback;
  }
  function analyticsDuration(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return hours ? `${hours}س ${minutes}د` : `${minutes}د`;
  }
  function overviewAnalyticsToolbar() {
    const ranges = [
      ["today", "اليوم"],
      ["7d", "7 أيام"],
      ["30d", "شهر"],
      ["3m", "3 أشهر"],
      ["year", "سنة"],
    ];
    return el(
      "section",
      { class: "analytics-toolbar" },
      el("div", { class: "analytics-toolbar-copy" },
        el("span", { class: "overview-kicker", text: "PR1ME ANALYTICS / COMMAND CENTER" }),
        el("strong", { text: "قراءة أعمق لسلوك السيرفر" }),
        el("small", { text: "كل قيمة هنا مأخوذة من Discord أو SQLite، بدون بيانات تجريبية." }),
      ),
      el("div", { class: "analytics-toolbar-actions" },
        el("span", {
          class: `analytics-live-badge ${state.analytics?.status_banner?.healthy === false ? "offline" : ""}`,
          text: state.analyticsLoading
            ? "SYNCING"
            : `LIVE • ${overviewTime(Date.now())}`,
        }),
        el("div", { class: "analytics-range-tabs", role: "tablist", "aria-label": "الفترة الزمنية" },
          ...ranges.map(([value, label]) => el("button", {
            class: state.analyticsRange === value ? "active" : "",
            type: "button",
            role: "tab",
            "aria-selected": String(state.analyticsRange === value),
            text: label,
            onClick: () => {
              if (state.analyticsRange === value) return;
              state.analyticsRange = value;
              sessionStorage.setItem("analytics-range", value);
              fetchGuildAnalytics(state.guild.id, value, true);
            },
          })),
        ),
      ),
    );
  }
  function overviewAnalyticsSummaryStrip() {
    const summary = state.analytics?.summary || {};
    const trend = Number(summary.activity_trend_pct);
    const trendText = Number.isFinite(trend) ? `${trend > 0 ? "+" : ""}${trend}%` : "—";
    return el(
      "div",
      { class: "analytics-summary-strip" },
      [
        ["إجمالي الرسائل", analyticsNumber(summary.total_messages), "رسالة في الفترة", "pink"],
        ["الكتّاب النشطون", analyticsNumber(summary.active_chatters), `${analyticsNumber(summary.active_chatters_pct)}% من الأعضاء`, "cyan"],
        ["اتجاه النشاط", trendText, "مقارنة بالفترة السابقة", trend >= 0 ? "green" : "red"],
        ["وقت الصوت", analyticsDuration(summary.total_voice_seconds), "جلسات مسجلة", "purple"],
      ].map(([label, value, hint, tone]) => el("div", { class: `analytics-summary-item tone-${tone}` },
        el("span", { text: label }),
        el("strong", { text: value }),
        el("small", { text: hint }),
      )),
    );
  }
  function overviewAnalyticsHealthPanel() {
    const health = state.analytics?.health_score || {};
    const score = health.score === null || health.score === undefined
      ? NaN
      : Number(health.score);
    const circumference = 2 * Math.PI * 48;
    const value = Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : 0;
    const metrics = [
      ["متصلون الآن", `${analyticsNumber(health.online_pct)}%`, "cyan"],
      ["كتّاب نشطون", `${analyticsNumber(health.active_writers_pct)}%`, "green"],
      ["الاحتفاظ", `${analyticsNumber(health.retention_pct)}%`, "purple"],
      ["الكثافة", health.chat_density || "—", "pink"],
    ];
    return el(
      "section",
      { class: "overview-pro-card analytics-health-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "SERVER HEALTH / SCORE" }), el("h2", { text: "نبض صحة السيرفر" })),
        el("span", { class: `overview-live-chip ${state.analytics?.status_banner?.healthy === false ? "offline" : ""}` }, health.status || "بانتظار البيانات"),
      ),
      state.analytics
        ? el("div", { class: "analytics-health-layout" },
            el("div", { class: "analytics-score-ring" },
              el("svg", { viewBox: "0 0 120 120", "aria-hidden": "true" },
                el("circle", { class: "score-ring-track", cx: "60", cy: "60", r: "48" }),
                el("circle", { class: "score-ring-value", cx: "60", cy: "60", r: "48", "stroke-dasharray": `${(circumference * value) / 100} ${circumference}` }),
              ),
              el("strong", { text: Number.isFinite(score) ? String(Math.round(score)) : "—" }),
              el("small", { text: "من 100" }),
            ),
            el("div", { class: "analytics-health-metrics" },
              ...metrics.map(([label, metric, tone]) => el("div", { class: `analytics-health-metric metric-${tone}` },
                el("span", { text: label }),
                el("strong", { text: metric }),
                el("i", {}, el("em", { style: `width:${tone === "pink" ? Math.min(100, Number(health.active_writers_pct) || 0) : Number(health[ tone === "cyan" ? "online_pct" : tone === "green" ? "active_writers_pct" : "retention_pct" ]) || 0}%` })),
              )),
            ),
          )
        : overviewSkeleton("جاري تحميل صحة السيرفر"),
      state.analytics?.status_banner
        ? el("div", { class: `analytics-status-banner ${state.analytics.status_banner.healthy ? "healthy" : "unhealthy"}` },
            el("span", { text: state.analytics.status_banner.healthy ? "●" : "!" }),
            el("span", { text: state.analytics.status_banner.text }),
          )
        : null,
    );
  }
  function overviewTopMessengerPanel() {
    const top = state.analytics?.top_messenger;
    return el(
      "section",
      { class: "overview-pro-card analytics-top-messenger-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "PERIOD STAR" }), el("h2", { text: "نجم الفترة" })),
        el("span", { class: "overview-inline-label", text: state.analyticsRange === "today" ? "اليوم" : state.analyticsRange }),
      ),
      top
        ? el("div", { class: "analytics-top-messenger" },
            avatar(top.avatar_url, top.username),
            el("div", {}, el("strong", { text: top.username }), el("small", { text: top.tag || "عضو في السيرفر" })),
            el("div", { class: "analytics-top-count" },
              el("strong", { text: analyticsNumber(top.message_count) }),
              el("small", { text: "رسالة" }),
            ),
            el("span", { class: "analytics-role-badge", text: top.role_badge || "عضو" }),
          )
        : overviewSkeleton("ستظهر نجمة الفترة بعد تسجيل الرسائل"),
    );
  }
  function overviewAnalyticsHeatmap() {
    const data = state.analytics?.heatmap?.[state.analyticsHeatMode];
    const dayLabels = ["أحد", "اثن", "ثلث", "أربع", "خمس", "جمع", "سبت"];
    const golden = state.analytics?.golden_hour;
    const hasData = Array.isArray(data) && data.some((row) => row.some((value) => Number(value) > 0));
    const cells = hasData
      ? el("div", { class: "analytics-heatmap-grid" },
          el("div", { class: "analytics-heatmap-hours", "aria-hidden": "true" },
            el("span"),
            ...Array.from({ length: 24 }, (_, hour) => el("span", { text: hour % 6 === 0 ? `${hour}` : "" })),
          ),
          ...data.map((row, day) => el("div", { class: "analytics-heatmap-row" },
            el("span", { text: dayLabels[day] }),
            ...row.map((value, hour) => el("button", {
              class: "analytics-heat-cell",
              type: "button",
              style: `--heat-value:${Math.max(0, Math.min(100, Number(value) || 0))}%`,
              title: `${dayLabels[day]} ${hour}:00 — ${Number(value) || 0}%`,
              "aria-label": `${dayLabels[day]}، الساعة ${hour}، شدة النشاط ${Number(value) || 0}%`,
            })),
          )),
        )
      : overviewSkeleton(state.analytics ? "لا توجد حركة مسجلة في الفترة" : "جاري تحميل heatmap");
    return el(
      "section",
      { class: "overview-pro-card analytics-heatmap-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "ACTIVITY MATRIX / 7 × 24" }), el("h2", { text: "مصفوفة النشاط" })),
        el("div", { class: "overview-segmented" },
          el("button", { class: state.analyticsHeatMode === "written" ? "active" : "", type: "button", text: "كتابي", "aria-pressed": String(state.analyticsHeatMode === "written"), onClick: () => { state.analyticsHeatMode = "written"; renderPage(); } }),
          el("button", { class: state.analyticsHeatMode === "voice" ? "active" : "", type: "button", text: "صوتي", "aria-pressed": String(state.analyticsHeatMode === "voice"), onClick: () => { state.analyticsHeatMode = "voice"; renderPage(); } }),
        ),
      ),
      cells,
      el("div", { class: "analytics-golden-hour" },
        el("span", { class: "analytics-golden-icon", text: "✦", "aria-hidden": "true" }),
        el("div", {},
          el("span", { class: "overview-kicker", text: "GOLDEN HOUR" }),
          el("strong", { text: golden?.day && golden.day !== "—" ? `${golden.day} · ${golden.window}` : "الساعة الذهبية بانتظار البيانات" }),
          el("small", { text: golden?.multiplier ? `${golden.multiplier}x المعدل — ${golden.tip}` : golden?.tip || "ستظهر التوصية بعد تسجيل نشاط كافٍ." }),
        ),
      ),
    );
  }
  function overviewAnalyticsChannels() {
    const traffic = state.analytics?.channels_traffic || {};
    const active = Array.isArray(traffic.active) ? traffic.active : [];
    const dead = Array.isArray(traffic.dead) ? traffic.dead : [];
    return el(
      "section",
      { class: "overview-pro-card analytics-channel-card" },
      el("div", { class: "overview-pro-card-head compact" },
        el("div", {}, el("span", { class: "overview-kicker", text: "TRAFFIC MONITOR" }), el("h2", { text: "حركة القنوات" })),
        el("span", { class: "overview-inline-label", text: `${analyticsNumber(active.length)} نشطة` }),
      ),
      active.length
        ? el("div", { class: "analytics-channel-list" }, ...active.map((channel, index) => el("div", { class: "analytics-channel-item" },
            el("span", { class: `analytics-rank rank-${index + 1}`, text: String(index + 1).padStart(2, "0") }),
            el("div", { class: "analytics-channel-copy" },
              el("div", {}, el("strong", { text: `#${channel.name}` }), el("small", { text: `${analyticsNumber(channel.count)} رسالة · ${analyticsNumber(channel.percentage)}%` })),
              el("i", {}, el("em", { style: `width:${Math.min(100, Number(channel.percentage) || 0)}%` })),
            ),
          )))
        : overviewSkeleton("ستظهر حركة القنوات بعد تسجيل الرسائل"),
      el("details", { class: "analytics-dead-channels" },
        el("summary", {}, el("span", { text: "قنوات خاملة / ميتة" }), el("b", { text: analyticsNumber(dead.length) })),
        dead.length
          ? el("div", { class: "analytics-dead-list" }, ...dead.slice(0, 12).map((channel) => el("div", {},
              el("span", { text: `#${channel.name}` }),
              el("small", { text: "0 رسالة في الفترة" }),
            )))
          : el("p", { text: "لا توجد قنوات خاملة ضمن القنوات المعروفة." }),
      ),
    );
  }
  function enhancedOverviewView() {
    const counts = state.stats?.counts || {};
    const members = Number(state.guild?.members ?? counts.members ?? 0);
    const incidents = state.incidents.length;
    const series = overviewSeriesData();
    const latest = series[series.length - 1];
    const updatedAt = state.stats?.updated_at || latest?.timestamp;
    return el(
      "section",
      { class: "overview-view overview-pro-view" },
      el("div", { class: "overview-brand-lockup" },
        el("strong", { text: "PR1ME" }),
        el("span", { text: "◌" }),
        el("small", { text: "PR1ME TEAM CONTROL" }),
      ),
      overviewAnalyticsToolbar(),
      el("section", { class: "overview-command-center" },
        el("div", { class: "command-center-grid" },
          el("div", {},
            el("span", { class: "overview-kicker", text: "COMMAND CENTER / LIVE" }),
            el("h1", { text: "مركز القيادة" }),
            el("p", { text: "كل ما يهمك عن مجتمعك، في شاشة واحدة واضحة وسريعة." }),
            el("div", { class: "overview-live-meta" },
              el("span", { class: `overview-live-chip ${state.online ? "" : "offline"}` }, state.online ? "● LIVE" : "○ OFFLINE"),
              el("span", { text: `آخر تحديث ${overviewTime(updatedAt)}` }),
              el("span", { text: latest ? latest.label : "بانتظار القياسات" }),
            ),
          ),
          el("div", { class: "command-center-orb", "aria-hidden": "true" }, el("span", { text: state.online ? "P" : "!" })),
        ),
        el("div", { class: "overview-command-actions" },
          el("button", { type: "button", text: "تحديث الآن", onClick: () => refreshDashboardStats(state.guild.id, true) }),
          el("button", { type: "button", text: "تصدير PDF", onClick: () => toast("التصدير سيستخدم بيانات القياسات الحالية", "success", 1800) }),
        ),
      ),
      overviewAnalyticsSummaryStrip(),
      el("div", { class: "overview-pro-kpis" },
        overviewCounterCard("أعضاء السيرفر", members, "من Discord", "pink", "♟", "community"),
        overviewCounterCard("حسابات الاقتصاد", counts.economy_accounts ?? "—", "من قاعدة البيانات", "cyan", "◉", "economy"),
        overviewCounterCard("التذاكر المفتوحة", counts.tickets_active ?? "—", "من السيرفر", "red", "!", "tickets"),
        overviewCounterCard("الأوامر المفعلة", counts.commands_enabled ?? "—", "من سجل الأوامر", "purple", "✦", "commands"),
      ),
      el("div", { class: "overview-pro-grid overview-grid-primary" },
        overviewAnalyticsHealthPanel(),
        overviewTrafficChart(),
      ),
      el("div", { class: "overview-pro-grid overview-grid-secondary" },
        overviewActivityStream(),
        overviewTopMessengerPanel(),
      ),
      el("div", { class: "overview-pro-grid analytics-grid-bottom" },
        overviewAnalyticsHeatmap(),
        overviewAnalyticsChannels(),
      ),
      overviewChannelsPanel(),
    );
  }
  function settingsView() {
    const general = el("div", { class: "fields" });
    general.append(
      input("prefix", "بادئة الأوامر", "text", {
        minlength: "1",
        maxlength: "5",
        required: true,
      }),
    );
    const protect = el("div", { class: "security-settings-stack" }),
      switches = el("div", { class: "field wide security-toggle-list" });
    switches.append(
      toggle("anti_nuke", "حماية من التخريب الجماعي"),
      toggle("captcha_enabled", "تفعيل كابتشا التحقق"),
      toggle("anti_invites", "حظر دعوات Discord"),
      toggle("anti_links", "حظر الروابط المشبوهة"),
      toggle("anti_spam", "تفعيل رادار السبام"),
      toggle("anti_mass_mention", "حماية المنشن الجماعي"),
    );
    if (state.draft.anti_spam) {
      const spamFields = el("div", { class: "fields security-rule-grid" });
      spamFields.append(
        input("anti_spam_max_messages", "الحد الأقصى للرسائل", "number", { min: "1", max: "100" }),
        input("anti_spam_time_window_seconds", "النافذة الزمنية (ثوانٍ)", "number", { min: "1", max: "3600" }),
        settingSelect("anti_spam_action", "الإجراء التلقائي", [
          ["warn_delete", "حذف الرسائل وتنبيه"],
          ["timeout", "كتم مؤقت"],
          ["kick", "طرد"],
          ["ban", "حظر"],
        ]),
        ...(state.draft.anti_spam_action === "timeout"
          ? [input("anti_spam_timeout_duration_minutes", "مدة الكتم (دقائق)", "number", { min: "1", max: "10080" })]
          : []),
        multiSettingSelect(
          "anti_spam_ignored_role_ids",
          "الرتب المستثناة",
          "role",
          "أعضاء هذه الرتب لا تُطبّق عليهم حماية السبام.",
        ),
        multiSettingSelect(
          "anti_spam_ignored_channel_ids",
          "القنوات المستثناة",
          "channel",
          "اتركها فارغة لتطبيق الحماية على جميع القنوات.",
        ),
      );
      protect.append(
        securityAccordion(
          "anti-spam",
          "إعدادات رادار السبام",
          "الحدود، الإجراء، والاستثناءات",
          spamFields,
        ),
      );
    }
    if (state.draft.anti_mass_mention) {
      const mentionFields = el("div", { class: "fields security-rule-grid" });
      mentionFields.append(
        input("anti_mention_max_per_message", "أقصى منشن في الرسالة", "number", { min: "1", max: "100" }),
        toggle("anti_mention_target_enabled", "منع تكرار منشن نفس العضو"),
        ...(state.draft.anti_mention_target_enabled
          ? [
              input("anti_mention_target_max_repeats", "أقصى تكرار لنفس العضو", "number", { min: "1", max: "100" }),
              input("anti_mention_target_time_window_seconds", "نافذة تكرار المنشن (ثوانٍ)", "number", { min: "1", max: "3600" }),
            ]
          : []),
        settingSelect("anti_mention_action", "الإجراء التلقائي", [
          ["warn_delete", "حذف الرسائل وتنبيه"],
          ["timeout", "كتم مؤقت"],
          ["kick", "طرد"],
          ["ban", "حظر"],
        ]),
        ...(state.draft.anti_mention_action === "timeout"
          ? [input("anti_mention_timeout_duration_minutes", "مدة الكتم (دقائق)", "number", { min: "1", max: "10080" })]
          : []),
        multiSettingSelect(
          "anti_mention_ignored_role_ids",
          "الرتب المستثناة",
          "role",
          "مثل المشرفين أو فريق الإدارة.",
        ),
        multiSettingSelect(
          "anti_mention_ignored_channel_ids",
          "القنوات المستثناة",
          "channel",
          "لن تُحتسب المنشنات في القنوات المحددة.",
        ),
      );
      protect.append(
        securityAccordion(
          "anti-mention",
          "إعدادات حماية المنشن",
          "المنشن العام والتكرار الموجّه",
          mentionFields,
        ),
      );
    }
    protect.append(
      switches,
      input("anti_alt_days", "عمر الحساب الأدنى (أيام)", "number", { min: "0", max: "365" }),
      selector("captcha_role_id", "رتبة اجتياز الكابتشا", "role"),
      selector("log_channel_id", "قناة السجل", "channel"),
    );
    const managementRoles = el("div", { class: "fields security-rule-grid" });
    managementRoles.append(
      managementRoleSelect("admin", "رتبة PRIME Admin"),
      managementRoleSelect("moderator", "رتبة PRIME Moderator"),
      managementRoleSelect("staff", "رتبة PRIME Staff"),
      el("p", {
        class: "hint wide",
        text: "المالك وAdministrator يُحددان من Discord. هذه الرتب تضيف شروط PRIME فقط ولا تمنح صلاحيات Discord؛ تبقى صلاحيات Discord وترتيب الرتب شرطاً لكل إجراء. إذا تركت المستويات فارغة يستمر السلوك الحالي.",
      }),
    );
    protect.append(
      securityAccordion(
        "management-roles",
        "مستويات إدارة PRIME",
        "ربط رتبة مستقلة بكل مستوى في هذا السيرفر",
        managementRoles,
        false,
      ),
    );
    const econ = el("div", { class: "fields" }),
      tax = input("economy_tax", "ضريبة الاقتصاد", "number", { min: "0", max: "100", step: "0.5" });
    tax.classList.add("suffix");
    tax.append(el("span", { text: "%" }));
    econ.append(tax, input("daily_amount", "المبلغ اليومي", "number", { min: "0", max: "1000000", step: "1" }));
    return el(
      "section",
      { class: "settings-view" },
      el("div", { class: "section-intro" }, el("div", { class: "eyebrow", text: `${state.guild.name} / SETTINGS` }), el("h1", { text: "الإعدادات" }), el("p", { text: "الإعدادات المتقدمة للبوت والحماية والاقتصاد." })),
      card("عام", general),
      card("الحماية", protect),
      card("الاقتصاد", econ),
      el("footer", { class: "footer", text: "الإعدادات تُحفظ في قاعدة بيانات البوت وتُطبّق على الميزات المرتبطة بها" }),
    );
  }
  function gamingView() {
    const channels = state.meta?.channels || [];
    const channelOptions = channels
      .map((channel) => el("option", {
        value: channel.id,
        text: `#${channel.name}${channel.type && channel.type !== "text" ? ` (${channel.type})` : ""}`,
      }));
    const form = el(
      "form",
      { class: "fields gaming-deploy-form" },
      el("label", {}, el("span", { text: "عنوان السكريم" }), el("input", { name: "title", required: true, maxlength: "150", placeholder: "Friday Night Scrim" })),
      el("label", {}, el("span", { text: "اللعبة" }), el("input", { name: "game_type", required: true, maxlength: "80", placeholder: "Valorant / PUBG / FIFA" })),
      el("label", {}, el("span", { text: "القناة" }), el("select", { name: "target_channel_id", required: true }, el("option", { value: "", text: "اختر قناة النشر" }), channelOptions)),
      el("label", {}, el("span", { text: "حجم الفريق" }), el("input", { name: "team_size", type: "number", min: "1", max: "16", value: "5" })),
      el("label", {}, el("span", { text: "عدد المقاعد" }), el("input", { name: "max_slots", type: "number", min: "1", max: "128", value: "8" })),
      el("button", { class: "btn primary", type: "submit", text: "نشر لوحة سكريم" }),
    );
    form.onsubmit = async (event) => {
      event.preventDefault();
      const body = Object.fromEntries(new FormData(form).entries());
      body.team_size = Number(body.team_size);
      body.max_slots = Number(body.max_slots);
      const button = event.submitter || form.querySelector("button[type=submit]");
      if (button) button.disabled = true;
      try {
        const response = await writeApi(`api/guild/${state.guild.id}/gaming/deploy`, body);
        const data = await readJson(response, {});
        if (!response.ok) {
          toast(data.fields ? Object.values(data.fields)[0] : "تعذر نشر لوحة السكريم", "warn");
          return;
        }
        toast("✅ نُشرت لوحة السكريم في Discord", "success", 3000);
        await refreshGaming();
      } catch (error) {
        if (error.message !== "unauth") toast("تعذر نشر لوحة السكريم", "warn");
      } finally {
        if (button) button.disabled = false;
      }
    };
    const list = el("div", { class: "gaming-list" });
    if (!state.gaming.length) {
      list.append(el("div", { class: "empty studio-empty", text: "لا توجد لوحات سكريم محفوظة بعد" }));
    } else {
      state.gaming.forEach((scrim) => {
        const closed = !scrim.is_active;
        const registrations = scrim.registrations || [];
        const roster = registrations.length
          ? registrations.map((item) => `#${item.slot_number} ${item.team_name}${item.checked_in ? " ✅" : ""}`).join(" · ")
          : "لا توجد فرق مسجلة";
        const actions = [];
        if (!closed) {
          actions.push(el("button", {
            class: "btn danger",
            type: "button",
            text: "إغلاق التسجيل",
            onClick: async (event) => {
              if (!confirm(`إغلاق سكريم «${scrim.title}»؟`)) return;
              const button = event.currentTarget;
              button.disabled = true;
              try {
                const response = await writeApi(`api/guild/${state.guild.id}/gaming/close`, { scrim_id: scrim.id });
                if (response.ok) {
                  toast("تم إغلاق السكريم", "success", 2500);
                  await refreshGaming();
                } else toast("تعذر إغلاق السكريم", "warn");
              } catch (error) {
                if (error.message !== "unauth") toast("تعذر إغلاق السكريم", "warn");
              } finally {
                button.disabled = false;
              }
            },
          }));
          actions.push(el("button", {
            class: "btn ghost",
            type: "button",
            text: "إرسال بيانات الغرفة",
            onClick: () => openCredentialPrompt(scrim.id),
          }));
        }
        list.append(el("article", { class: `overview-panel gaming-card ${closed ? "is-closed" : ""}` },
          el("div", { class: "gaming-card-head" },
            el("div", {}, el("span", { class: "eyebrow", text: `${scrim.game_type} / ${closed ? "CLOSED" : "LIVE"}` }), el("h3", { text: scrim.title })),
            el("strong", { text: `${scrim.occupied_slots || 0}/${scrim.max_slots}` }),
          ),
          el("p", { class: "muted", text: `حجم الفريق ${scrim.team_size} · ${roster}` }),
          el("div", { class: "gaming-card-actions" }, actions),
        ));
      });
    }
    return el("section", { id: "view-gaming", class: "gaming-view" },
      el("div", { class: "section-intro" }, el("div", { class: "eyebrow", text: `${state.guild.name} / GAMING OPS` }), el("h1", { text: "Gaming, Scrims & Esports" }), el("p", { text: "أنشئ لوحات سكريم تفاعلية، راقب الحجوزات، وأرسل بيانات الغرف من مكان واحد." })),
      card("نشر لوحة جديدة", form),
      el("div", { class: "section-intro compact-intro" }, el("h2", { text: "اللوحات المحفوظة" }), el("p", { text: "الحجوزات تُحفظ وتستمر بعد إعادة تشغيل البوت." })),
      list,
    );
  }
  async function refreshGaming() {
    if (!state.guild?.id) return;
    try {
      const response = await api(`api/guild/${state.guild.id}/gaming`);
      if (response.ok) {
        state.gaming = (await readJson(response, { scrims: [] })).scrims || [];
        if (state.activeView === "gaming") renderPage();
      }
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحديث مركز السكريمات");
    }
  }
  async function openCredentialPrompt(scrimId) {
    const credentials = prompt("أدخل بيانات غرفة اللعب (الرابط/الكود):", "");
    if (!credentials?.trim()) return;
    try {
      const response = await writeApi(`api/guild/${state.guild.id}/gaming/credentials`, { scrim_id: scrimId, credentials: credentials.trim() });
      if (response.ok) toast("تم إرسال بيانات الغرفة إلى قناة السكريم", "success", 3000);
      else toast("تعذر إرسال بيانات الغرفة", "warn");
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر إرسال بيانات الغرفة", "warn");
    }
  }
  function channelOptions(includeEmpty = true) {
    const channels = state.meta?.channels || [];
    return [
      ...(includeEmpty ? [el("option", { value: "", text: "اختر قناة" })] : []),
      ...channels.map((channel) => el("option", {
        value: channel.id,
        text: `#${channel.name}`,
      })),
    ];
  }
  function roleOptions(includeEmpty = true) {
    const roles = state.meta?.roles || [];
    return [
      ...(includeEmpty ? [el("option", { value: "", text: "اختر رتبة" })] : []),
      ...roles.map((role) => el("option", { value: role.id, text: role.name })),
    ];
  }
  async function refreshClanOps() {
    if (!state.guild?.id) return;
    const id = state.guild.id;
    const fallback = [
      { applications: [] },
      { roster: [] },
      { scrims: [] },
      { config: {}, categories: [] },
    ];
    try {
      const results = await Promise.allSettled([
        optionalJson(`api/guild/${id}/clan/applications?status=all`, fallback[0]),
        optionalJson(`api/guild/${id}/clan/roster`, fallback[1]),
        optionalJson(`api/guild/${id}/clan/scrims`, fallback[2]),
        optionalJson(`api/guild/${id}/tickets/dropdown-config`, fallback[3]),
      ]);
      if (state.guild?.id !== id) return;
      const value = (index) => results[index].status === "fulfilled" ? results[index].value : fallback[index];
      const applications = value(0);
      const roster = value(1);
      const scrims = value(2);
      const dropdown = value(3);
      state.clanOps = {
        applications: applications.applications || [],
        roster: roster.roster || [],
        scrims: scrims.scrims || [],
        dropdown: { config: dropdown.config || {}, categories: dropdown.categories || [] },
      };
      state.ticketDropdown = {
        config: { ...state.ticketDropdown.config, ...(dropdown.config || {}) },
        categories: Array.isArray(dropdown.categories) && dropdown.categories.length
          ? dropdown.categories
          : state.ticketCategories.map((item) => ({ ...item })),
      };
      if (state.activeView === "clan" || state.activeView === "tickets") renderPage();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحديث عمليات الكلان");
    }
  }
  async function clanApplicationAction(application, action, roleId) {
    if (action === "approve" && !roleId) return toast("اختر رتبة أعضاء الكلان", "warn");
    try {
      const response = await writeApi(
        `api/guild/${state.guild.id}/clan/applications/${application.id}/action`,
        { action, clan_member_role_id: roleId || null },
      );
      const data = await readJson(response, {});
      if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر تحديث الطلب", "warn");
      toast(action === "approve" ? "تم قبول الطلب وإسناد الرتبة" : "تم رفض طلب الانضمام", "success", 2600);
      await refreshClanOps();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تنفيذ إجراء طلب الكلان");
    }
  }
  async function saveClanRoster(form) {
    const body = Object.fromEntries(new FormData(form).entries());
    body.display_order = Number(body.display_order || 0);
    try {
      const response = await writeApi(`api/guild/${state.guild.id}/clan/roster`, body);
      const data = await readJson(response, {});
      if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ اللاعب", "warn");
      toast("تم حفظ اللاعب في التشكيلة", "success", 2200);
      form.reset();
      await refreshClanOps();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال لحفظ التشكيلة");
    }
  }
  async function deleteClanRosterPlayer(player) {
    if (!confirm(`حذف ${player.player_name} من التشكيلة؟`)) return;
    try {
      const response = await writeApi(`api/guild/${state.guild.id}/clan/roster`, { action: "delete", id: player.id });
      if (!response.ok) return toast("تعذر حذف اللاعب", "warn");
      toast("تم حذف اللاعب", "success", 1800);
      await refreshClanOps();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر حذف اللاعب");
    }
  }
  async function publishClanRoster(form) {
    const body = Object.fromEntries(new FormData(form).entries());
    if (!body.target_channel_id) return toast("اختر قناة نشر التشكيلة", "warn");
    try {
      const response = await writeApi(`api/guild/${state.guild.id}/clan/roster/publish`, body);
      const data = await readJson(response, {});
      if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر نشر التشكيلة", "warn");
      form.elements.message_id.value = data.message_id || "";
      toast("تم نشر التشكيلة في Discord", "success", 2800);
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر نشر التشكيلة");
    }
  }
  async function saveScrimLog(form) {
    const body = Object.fromEntries(new FormData(form).entries());
    body.score_prime = Number(body.score_prime);
    body.score_enemy = Number(body.score_enemy);
    try {
      const response = await writeApi(`api/guild/${state.guild.id}/clan/scrims`, body);
      const data = await readJson(response, {});
      if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ نتيجة السكريم", "warn");
      form.reset();
      toast("تم حفظ نتيجة السكريم", "success", 2200);
      await refreshClanOps();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر حفظ نتيجة السكريم");
    }
  }
  function ticketDropdownCategoriesEditor() {
    const wrap = el("div", { class: "clan-category-editor" });
    const categories = state.ticketDropdown.categories;
    categories.forEach((category, index) => {
      const row = el("div", { class: "clan-category-row" });
      const label = el("input", { class: "studio-input", name: "label", maxlength: "80", value: category.label || "", placeholder: "اسم التصنيف" });
      const description = el("input", { class: "studio-input", name: "description", maxlength: "100", value: category.description || "", placeholder: "وصف مختصر" });
      const emoji = el("input", { class: "studio-input", name: "emoji", maxlength: "2", value: category.emoji || "T", placeholder: "رمز" });
      const role = el("select", { class: "studio-input", name: "role_id" }, roleOptions());
      role.value = category.role_id || "";
      const parent = el("select", { class: "studio-input", name: "category_id" }, [
        el("option", { value: "", text: "بدون فئة أب" }),
        ...(state.meta?.categories || []).map((item) => el("option", { value: item.id, text: item.name })),
      ]);
      parent.value = category.category_id || "";
      const update = () => {
        Object.assign(categories[index], {
          label: label.value,
          description: description.value,
          emoji: emoji.value,
          role_id: role.value || null,
          category_id: parent.value || null,
        });
      };
      [label, description, emoji, role, parent].forEach((node) => node.addEventListener(node.tagName === "SELECT" ? "change" : "input", update));
      row.append(
        el("span", { class: "ticket-category-index", text: String(index + 1).padStart(2, "0") }),
        label,
        description,
        emoji,
        role,
        parent,
        el("button", {
          class: "icon-action danger",
          type: "button",
          text: "×",
          title: "حذف التصنيف",
          disabled: categories.length <= 1,
          onClick: () => {
            if (categories.length <= 1) return;
            categories.splice(index, 1);
            renderPage();
          },
        }),
      );
      wrap.append(row);
    });
    return wrap;
  }
  async function saveTicketDropdownConfig(form, publish = false) {
    const body = {
      channel_id: form.elements.channel_id.value || null,
      message_id: form.elements.message_id.value || null,
      embed_title: form.elements.embed_title.value.trim(),
      embed_description: form.elements.embed_description.value.trim(),
      embed_color: form.elements.embed_color.value,
      footer_text: form.elements.footer_text.value.trim(),
      categories: state.ticketDropdown.categories,
    };
    const endpoint = publish
      ? `api/guild/${state.guild.id}/tickets/dropdown-config/publish`
      : `api/guild/${state.guild.id}/tickets/dropdown-config`;
    try {
      const response = await writeApi(endpoint, body);
      const data = await readJson(response, {});
      if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ لوحة التذاكر", "warn");
      state.ticketDropdown = { config: data.config || body, categories: data.categories || body.categories };
      form.elements.message_id.value = data.config?.message_id || "";
      pulse();
      toast(publish ? "تم نشر لوحة التذاكر الجديدة" : "تم حفظ إعدادات لوحة التذاكر", "success", 2800);
      renderPage();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بحفظ لوحة التذاكر");
    }
  }
  function ticketDropdownBuilder() {
    const config = state.ticketDropdown.config || {};
    const categories = state.ticketDropdown.categories.length
      ? state.ticketDropdown.categories
      : (state.ticketCategories || []).map((item) => ({ ...item }));
    state.ticketDropdown.categories = categories;
    const form = el("form", { class: "clan-ops-form ticket-dropdown-form" },
      el("div", { class: "panel-heading" },
        el("div", {}, el("span", { class: "eyebrow", text: "TICKET BUILDER / PERSISTENT DROPDOWN" }), el("h3", { text: "باني لوحة التذاكر" })),
        el("small", { text: "يحفظ إعدادات اللوحة الجديدة دون حذف اللوحات القديمة." }),
      ),
      el("div", { class: "fields clan-form-grid" },
        el("label", {}, el("span", { text: "قناة النشر" }), el("select", { name: "channel_id", required: true }, channelOptions())),
        el("label", {}, el("span", { text: "معرف الرسالة للتحديث (اختياري)" }), el("input", { name: "message_id", class: "studio-input", inputmode: "numeric", value: config.message_id || "" })),
        el("label", {}, el("span", { text: "عنوان اللوحة" }), el("input", { name: "embed_title", class: "studio-input", maxlength: "256", required: true, value: config.embed_title || "مركز الدعم والتذاكر" })),
        el("label", {}, el("span", { text: "لون اللوحة" }), el("input", { name: "embed_color", type: "color", class: "studio-input", value: config.embed_color || "#5865F2" })),
        el("label", { class: "form-wide" }, el("span", { text: "وصف اللوحة" }), el("textarea", { name: "embed_description", class: "studio-textarea", maxlength: "4096", rows: "2" })),
        el("label", { class: "form-wide" }, el("span", { text: "التذييل" }), el("input", { name: "footer_text", class: "studio-input", maxlength: "2048", value: config.footer_text || "" })),
      ),
      el("div", { class: "clan-category-heading" }, el("div", {}, el("h4", { text: "تصنيفات الـ Dropdown" }), el("small", { text: "حتى 25 تصنيفاً مع رتبة وفئة قنوات اختيارية." })),
        el("button", { class: "btn ghost", type: "button", text: "＋ إضافة تصنيف", onClick: () => {
          if (state.ticketDropdown.categories.length >= 25) return toast("الحد الأقصى 25 تصنيفاً", "warn");
          state.ticketDropdown.categories.push({ label: "تصنيف جديد", description: "", emoji: "T", role_id: null, category_id: null });
          renderPage();
        } }),
      ),
      ticketDropdownCategoriesEditor(),
      el("div", { class: "form-actions" },
        el("button", { class: "btn ghost", type: "submit", text: "حفظ الإعدادات" }),
        el("button", { class: "btn primary", type: "button", text: "حفظ ونشر اللوحة", onClick: () => saveTicketDropdownConfig(form, true) }),
      ),
    );
    form.elements.channel_id.value = config.channel_id || "";
    form.elements.embed_description.value = config.embed_description || "";
    form.onsubmit = (event) => { event.preventDefault(); saveTicketDropdownConfig(form); };
    return card("Ticket Builder", form);
  }
  function clanOpsView() {
    const applications = el("div", { class: "clan-list" });
    const roleSelect = el("select", { class: "studio-input", "aria-label": "رتبة أعضاء الكلان" }, roleOptions());
    if (!state.clanOps.applications.length) applications.append(el("div", { class: "empty studio-empty", text: "لا توجد طلبات انضمام حالياً" }));
    state.clanOps.applications.forEach((application) => {
      const actions = el("div", { class: "clan-card-actions" });
      if (application.status === "pending") {
        actions.append(
          el("button", { class: "btn primary", type: "button", text: "قبول وإسناد الرتبة", onClick: () => clanApplicationAction(application, "approve", roleSelect.value) }),
          el("button", { class: "btn danger", type: "button", text: "رفض", onClick: () => clanApplicationAction(application, "reject") }),
        );
      }
      applications.append(el("article", { class: "overview-panel clan-item" },
        el("div", { class: "clan-item-head" }, el("div", {}, el("span", { class: "eyebrow", text: `APPLICATION #${application.id}` }), el("h4", { text: application.username })), el("span", { class: `status-tag ${application.status}`, text: application.status })),
        el("p", { text: `K/D: ${application.kd_ratio || "—"} · الجهاز: ${application.device || "—"} · Discord: ${application.user_id}` }),
        el("small", { class: "muted", text: application.notes || "بدون ملاحظات" }),
        actions,
      ));
    });
    const rosterForm = el("form", { class: "clan-ops-form" },
      el("div", { class: "fields clan-form-grid" },
        el("label", {}, el("span", { text: "التشكيلة" }), el("select", { name: "lineup_name" }, el("option", { value: "Lineup A", text: "Lineup A" }), el("option", { value: "Lineup B", text: "Lineup B" }), el("option", { value: "Subs", text: "Subs" }))),
        el("label", {}, el("span", { text: "Discord ID" }), el("input", { name: "player_id", class: "studio-input", inputmode: "numeric", required: true, placeholder: "123456789012345678" })),
        el("label", {}, el("span", { text: "اسم اللاعب" }), el("input", { name: "player_name", class: "studio-input", maxlength: "100", required: true })),
        el("label", {}, el("span", { text: "المركز" }), el("input", { name: "role_title", class: "studio-input", maxlength: "80", placeholder: "IGL / Fragger / Support" })),
        el("label", {}, el("span", { text: "الترتيب" }), el("input", { name: "display_order", class: "studio-input", type: "number", min: "0", max: "999", value: "0" })),
      ),
      el("button", { class: "btn primary", type: "submit", text: "إضافة لاعب للتشكيلة" }),
    );
    rosterForm.onsubmit = (event) => { event.preventDefault(); saveClanRoster(rosterForm); };
    const rosterList = el("div", { class: "clan-roster-grid" });
    ["Lineup A", "Lineup B", "Subs"].forEach((lineup) => {
      const players = state.clanOps.roster.filter((item) => item.lineup_name === lineup);
      rosterList.append(el("section", { class: "clan-lineup" },
        el("div", { class: "panel-heading" }, el("h4", { text: lineup }), el("small", { text: `${players.length} لاعبين` })),
        players.length ? players.map((player) => el("div", { class: "clan-roster-player" },
          el("span", { text: player.player_name }),
          el("small", { text: `${player.role_title || "لاعب"} · ${player.player_id}` }),
          el("button", { class: "icon-action danger", type: "button", text: "×", title: "حذف", onClick: () => deleteClanRosterPlayer(player) }),
        )) : el("div", { class: "empty studio-empty", text: "لا يوجد لاعبون" }),
      ));
    });
    const publishForm = el("form", { class: "clan-ops-form" },
      el("div", { class: "fields clan-form-grid" },
        el("label", {}, el("span", { text: "قناة التشكيلة" }), el("select", { name: "target_channel_id", required: true }, channelOptions())),
        el("label", {}, el("span", { text: "معرف الرسالة (للتحديث)" }), el("input", { name: "message_id", class: "studio-input", inputmode: "numeric" })),
        el("label", {}, el("span", { text: "عنوان النشر" }), el("input", { name: "title", class: "studio-input", value: "PR1ME TEAM · Clan Roster" })),
      ),
      el("button", { class: "btn ghost", type: "submit", text: "نشر التشكيلة في Discord" }),
    );
    publishForm.onsubmit = (event) => { event.preventDefault(); publishClanRoster(publishForm); };
    const scrimForm = el("form", { class: "clan-ops-form" },
      el("div", { class: "fields clan-form-grid" },
        el("label", {}, el("span", { text: "الخصم" }), el("input", { name: "opponent_name", class: "studio-input", required: true })),
        el("label", {}, el("span", { text: "نتيجة PRIME" }), el("input", { name: "score_prime", class: "studio-input", type: "number", min: "0", max: "999", required: true })),
        el("label", {}, el("span", { text: "نتيجة الخصم" }), el("input", { name: "score_enemy", class: "studio-input", type: "number", min: "0", max: "999", required: true })),
        el("label", {}, el("span", { text: "الخريطة" }), el("input", { name: "map_name", class: "studio-input", maxlength: "80" })),
        el("label", {}, el("span", { text: "النتيجة" }), el("select", { name: "result" }, el("option", { value: "win", text: "فوز" }), el("option", { value: "loss", text: "خسارة" }), el("option", { value: "draw", text: "تعادل" }))),
      ),
      el("button", { class: "btn primary", type: "submit", text: "حفظ نتيجة السكريم" }),
    );
    scrimForm.onsubmit = (event) => { event.preventDefault(); saveScrimLog(scrimForm); };
    const scrims = el("div", { class: "clan-scrim-list" });
    if (!state.clanOps.scrims.length) scrims.append(el("div", { class: "empty studio-empty", text: "لا توجد نتائج سكريم محفوظة" }));
    state.clanOps.scrims.forEach((scrim) => scrims.append(el("div", { class: "clan-scrim-row" },
      el("strong", { text: `PRIME ${scrim.score_prime} — ${scrim.score_enemy} ${scrim.opponent_name}` }),
      el("span", { class: `status-tag ${scrim.result}`, text: scrim.result }),
      el("small", { text: `${scrim.map_name || "بدون خريطة"} · ${scrim.timestamp || ""}` }),
    )));
    roleSelect.classList.add("clan-application-role");
    return el("section", { id: "view-clan", class: "clan-ops-view" },
      el("div", { class: "section-intro" }, el("div", { class: "eyebrow", text: `${state.guild.name} / CLAN OPS` }), el("h1", { text: "الكلان والتنافس" }), el("p", { text: "راجع طلبات الانضمام، أدر التشكيلات، وسجل تاريخ السكريمات من مركز واحد." })),
      card("طلبات الانضمام", el("div", { class: "clan-panel" }, el("label", { class: "clan-role-picker" }, el("span", { text: "رتبة القبول" }), roleSelect), applications)),
      card("منشئ التشكيلات", rosterForm, rosterList),
      card("نشر التشكيلة", publishForm),
      card("سجل السكريمات", scrimForm, scrims),
    );
  }
  function broadcastPreview(draft) {
    const bot = state.meta?.bot || { name: "PR1ME TEAM", avatar: "" };
    const messageBody = el("div", { class: "broadcast-discord-message" },
      bot.avatar
        ? el("img", { class: "broadcast-avatar", src: bot.avatar, alt: bot.name })
        : el("span", { class: "broadcast-avatar-fallback", text: "✦" }),
      el("div", { class: "broadcast-message-copy" },
        el("div", { class: "broadcast-author" },
          el("strong", { text: bot.name }),
          el("span", { class: "broadcast-bot-badge", text: "BOT" }),
          el("small", { text: "اليوم في 12:00" }),
        ),
        draft.content
          ? el("p", { class: "broadcast-content-preview", text: draft.content })
          : null,
        draft.mode === "embed"
          ? el("article", {
              class: "broadcast-embed-preview",
              style: `--broadcast-color: ${draft.color || "#6366F1"}`,
            },
            draft.title ? el("h4", { text: draft.title }) : null,
            draft.description ? el("p", { text: draft.description }) : null,
            draft.thumbnail_url
              ? el("img", { class: "broadcast-thumb-preview", src: draft.thumbnail_url, alt: "" })
              : null,
            draft.image_url
              ? el("img", { class: "broadcast-image-preview", src: draft.image_url, alt: "" })
              : null,
            draft.footer ? el("small", { class: "broadcast-footer-preview", text: draft.footer }) : null,
          )
          : null,
      ),
    );
    return el("div", { class: "broadcast-preview-shell" },
      el("div", { class: "broadcast-preview-label", text: "LIVE DISCORD PREVIEW" }),
      messageBody,
    );
  }
  async function refreshBroadcastHistory() {
    if (!state.guild?.id) return;
    try {
      const response = await api(`api/guild/${state.guild.id}/broadcast/history`);
      const data = await readJson(response, { history: [] });
      if (response.ok) {
        state.broadcast.history = data.history || [];
        if (state.activeView === "broadcast") renderPage();
      }
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحميل سجل الإعلانات", "warn");
    }
  }
  async function sendBroadcast(form) {
    const draft = { ...state.broadcast.draft };
    if (!draft.channel_id) return toast("اختر قناة النشر أولاً", "warn");
    if (draft.mode === "text" && !draft.content.trim()) return toast("اكتب نص الرسالة أولاً", "warn");
    if (draft.mode === "embed" && ![draft.content, draft.title, draft.description].some((value) => String(value || "").trim())) {
      return toast("أضف عنواناً أو وصفاً للإعلان", "warn");
    }
    if (!confirm("هل تريد نشر هذا الإعلان الآن في Discord؟")) return;
    const button = form.querySelector(".broadcast-send-button");
    if (button) {
      button.disabled = true;
      button.textContent = "جارٍ النشر…";
    }
    try {
      const response = await writeApi(`api/guild/${state.guild.id}/broadcast/send`, draft);
      const data = await readJson(response, {});
      if (!response.ok) {
        return toast(data.fields ? Object.values(data.fields)[0] : "تعذر نشر الإعلان", "warn");
      }
      toast("تم نشر الإعلان بنجاح", "success", 3000);
      await refreshBroadcastHistory();
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بنشر الإعلان", "warn");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "إرسال الإعلان الآن";
      }
    }
  }
  function saveBroadcastDraft() {
    if (!state.guild?.id) return;
    const key = `prime-broadcast-drafts:${state.guild.id}`;
    const drafts = JSON.parse(localStorage.getItem(key) || "[]");
    drafts.unshift({ ...state.broadcast.draft, saved_at: new Date().toISOString() });
    localStorage.setItem(key, JSON.stringify(drafts.slice(0, 10)));
    toast("تم حفظ المسودة على هذا الجهاز", "success", 2200);
  }
  function loadBroadcastDraft(draft) {
    state.broadcast.draft = { ...state.broadcast.draft, ...draft };
    renderPage();
  }
  function broadcastView() {
    const draft = state.broadcast.draft;
    const form = el("form", { class: "broadcast-studio-form" });
    const preview = el("div", { class: "broadcast-preview-slot" });
    const updateDraft = (field, event) => {
      state.broadcast.draft[field] = event.target.value;
      preview.replaceChildren(broadcastPreview(state.broadcast.draft));
      if (field === "mode") form.classList.toggle("broadcast-text-mode", event.target.value === "text");
    };
    const input = (field, label, type = "text", props = {}) => {
      const node = el("input", {
        class: "studio-input",
        type,
        name: field,
        value: draft[field] || "",
        ...props,
      });
      node.addEventListener("input", (event) => updateDraft(field, event));
      return el("label", {}, el("span", { text: label }), node);
    };
    const textarea = (field, label, props = {}) => {
      const node = el("textarea", {
        class: "studio-textarea",
        name: field,
        rows: "4",
        ...props,
      });
      node.value = draft[field] || "";
      node.addEventListener("input", (event) => updateDraft(field, event));
      return el("label", { class: "form-wide" }, el("span", { text: label }), node);
    };
    const mode = el("select", { class: "studio-input", name: "mode" },
      el("option", { value: "embed", text: "إعلان مدمج (Rich Embed)" }),
      el("option", { value: "text", text: "رسالة عادية (Text)" }),
    );
    mode.value = draft.mode;
    mode.addEventListener("change", (event) => updateDraft("mode", event));
    const channel = el("select", { class: "studio-input", name: "channel_id" },
      el("option", { value: "", text: "اختر قناة النشر" }),
      ...(state.meta?.channels || []).map((item) => el("option", { value: item.id, text: `#${item.name}` })),
    );
    channel.value = draft.channel_id || "";
    channel.addEventListener("change", (event) => updateDraft("channel_id", event));
    const mention = el("select", { class: "studio-input", name: "mention_type" },
      el("option", { value: "none", text: "بدون منشن" }),
      el("option", { value: "everyone", text: "@everyone" }),
      el("option", { value: "here", text: "@here" }),
    );
    mention.value = draft.mention_type || "none";
    mention.addEventListener("change", (event) => updateDraft("mention_type", event));
    form.append(
      el("div", { class: "broadcast-form-grid" },
        el("label", {}, el("span", { text: "نوع الرسالة" }), mode),
        el("label", {}, el("span", { text: "قناة النشر" }), channel),
        el("label", {}, el("span", { text: "المنشن" }), mention),
        input("color", "لون الـ Embed", "color", { value: draft.color || "#6366F1" }),
        input("title", "عنوان الإعلان", "text", { maxlength: "256", placeholder: "إعلان مهم من PR1ME TEAM" }),
        textarea("description", "الوصف / متن الإعلان", { maxlength: "4096", placeholder: "اكتب تفاصيل الإعلان هنا…" }),
        textarea("content", "نص الرسالة أو محتوى المنشن", { maxlength: "4000", rows: "3", placeholder: "يمكن تركه فارغاً عند استخدام Embed فقط." }),
        input("thumbnail_url", "رابط الصورة المصغرة", "url", { placeholder: "https://..." }),
        input("image_url", "رابط الصورة الرئيسية", "url", { placeholder: "https://..." }),
        input("footer", "التذييل", "text", { maxlength: "2048" }),
      ),
      el("div", { class: "broadcast-actions" },
        el("button", { class: "btn ghost", type: "button", text: "حفظ كمسودة", onClick: saveBroadcastDraft }),
        el("button", { class: "btn primary broadcast-send-button", type: "submit", text: "إرسال الإعلان الآن" }),
      ),
    );
    form.onsubmit = (event) => { event.preventDefault(); sendBroadcast(form); };
    preview.append(broadcastPreview(draft));
    const savedDrafts = (() => {
      try { return JSON.parse(localStorage.getItem(`prime-broadcast-drafts:${state.guild.id}`) || "[]"); }
      catch { return []; }
    })();
    const historyRows = [...(state.broadcast.history || []), ...savedDrafts.map((item) => ({ ...item, message_type: "draft", id: `draft-${item.saved_at}` }))];
    const history = el("div", { class: "broadcast-history-list" });
    if (!historyRows.length) history.append(el("div", { class: "empty studio-empty", text: "لا توجد إعلانات أو مسودات بعد." }));
    historyRows.forEach((item) => history.append(el("article", { class: "broadcast-history-row" },
      el("div", {}, el("strong", { text: item.title || (item.message_type === "text" ? "رسالة عادية" : "إعلان بدون عنوان") }), el("small", { text: `${item.message_type === "draft" ? "مسودة محلية" : item.message_type} · ${item.sent_at || item.saved_at || ""}` })),
      el("button", {
        class: "btn ghost",
        type: "button",
        text: "نسخ إلى المحرر",
        onClick: () => loadBroadcastDraft({
          ...item,
          description: item.description || (item.message_type === "embed" && !item.content ? item.content : ""),
        }),
      }),
    )));
    return el("section", { id: "view-broadcast", class: "broadcast-view" },
      el("div", { class: "section-intro" },
        el("div", { class: "eyebrow", text: `${state.guild.name} / BROADCAST STUDIO` }),
        el("h1", { text: "صانع الرسائل والإعلانات" }),
        el("p", { text: "أنشئ رسالة عادية أو Embed غني، شاهد المعاينة مباشرة، ثم انشرها إلى قناة Discord بصلاحيات آمنة." }),
      ),
      el("div", { class: "broadcast-studio-grid" },
        card("إعداد الإعلان", form),
        el("section", { class: "card broadcast-preview-card" }, preview),
      ),
      card("سجل الإعلانات السابقة والمسودات", history),
    );
  }
  const subscriptionEvents = [
    ["created", "تفعيل الاشتراك"],
    ["renewal", "تجديد الاشتراك"],
    ["expiring", "تذكير قرب الانتهاء"],
    ["expired", "انتهاء الاشتراك"],
  ];
  const subscriptionTabs = [
    ["overview", "نظرة عامة"],
    ["subscriptions", "الاشتراكات"],
    ["settings", "الإعدادات"],
    ["plans", "الخطط"],
    ["reminders", "التذكيرات"],
    ["templates", "القوالب"],
    ["logs", "السجلات والتسليم"],
  ];
  const subscriptionStatuses = {
    active: "نشط",
    expired: "منتهي",
    cancelled: "ملغي",
    pending: "قيد الانتظار",
    sending: "جارٍ الإرسال",
    sent: "تم الإرسال",
    failed: "فشل",
    cancelled_notification: "ملغي",
  };
  const subNumber = (value) => Number(value || 0).toLocaleString("en-US");
  const subDate = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? String(value)
      : date.toLocaleString("ar", { dateStyle: "medium", timeStyle: "short" });
  };
  function subStatus(value) {
    const status = String(value || "");
    return el("span", {
      class: `sub-status sub-status-${status.replace(/[^a-z_]/g, "")}`,
      text: subscriptionStatuses[status] || status || "—",
    });
  }
  function subButton(label, handler, style = "ghost", disabled = false) {
    return el("button", {
      class: `btn ${style}`,
      type: "button",
      disabled,
      text: label,
      onClick: handler,
    });
  }
  function subField(label, node, hint = "") {
    return el(
      "label",
      { class: "subscription-field" },
      el("span", { text: label }),
      node,
      hint ? el("small", { text: hint }) : null,
    );
  }
  function subInput(name, label, value, type = "text", attrs = {}, hint = "") {
    return subField(
      label,
      el("input", {
        name,
        type,
        value: value == null ? "" : String(value),
        ...attrs,
      }),
      hint,
    );
  }
  function subCheck(name, label, checked) {
    return el(
      "label",
      { class: "subscription-check" },
      el("input", { name, type: "checkbox", checked: !!checked }),
      el("span", { text: label }),
    );
  }
  function subSelect(name, label, options, selected, attrs = {}, hint = "") {
    const select = el("select", { name, ...attrs });
    const hasSelectedOption = selected != null
      && String(selected) !== ""
      && options.some(([value]) => String(value) === String(selected));
    const safeOptions = hasSelectedOption || selected == null || String(selected) === ""
      ? options
      : [...options, [String(selected), `القيمة المحفوظة (${String(selected)})`]];
    safeOptions.forEach(([value, text]) => {
      select.append(el("option", { value: String(value), text }));
    });
    select.value = selected == null ? "" : String(selected);
    return subField(label, select, hint);
  }
  function subChannels() {
    return Object.values(window.guildChannels || {}).map((channel) => [
      String(channel.id),
      `#${channel.name}`,
    ]);
  }
  function subPlans(enabledOnly = false) {
    const plans = state.subscriptionDashboard.data?.plans || [];
    return plans
      .filter((plan) => !enabledOnly || plan.enabled)
      .map((plan) => [plan.plan_id, plan.name]);
  }
  function subTemplates(eventType) {
    return (state.subscriptionDashboard.data?.templates || [])
      .filter((template) => template.enabled && (!eventType || template.event_type === eventType))
      .map((template) => [template.template_id, template.name]);
  }
  function subSelectOptions(options, emptyLabel = "بدون تحديد") {
    return [["", emptyLabel], ...options];
  }
  async function loadSubscriptionDashboard(guildId = state.guild?.id, redraw = true) {
    const viewState = state.subscriptionDashboard;
    if (!guildId || (viewState.loading && viewState.guildId === guildId)) return;
    viewState.loading = true;
    viewState.error = "";
    viewState.guildId = guildId;
    if (redraw && state.activeView === "subscriptions") renderPage();
    try {
      const response = await api(`api/guild/${guildId}/subscriptions`, { cache: "no-store" });
      const data = await readJson(response, {});
      if (!response.ok) throw new Error(data.message || data.error || "تعذر تحميل بيانات الاشتراكات");
      if (state.guild?.id !== guildId) return;
      viewState.data = data;
    } catch (error) {
      if (error.message === "unauth") return;
      if (state.guild?.id === guildId) viewState.error = error.message || "تعذر تحميل بيانات الاشتراكات";
    } finally {
      if (viewState.guildId === guildId) viewState.loading = false;
      if (state.guild?.id === guildId && state.activeView === "subscriptions") renderPage();
    }
  }
  async function subscriptionPost(path, body, successMessage) {
    const guildId = state.guild?.id;
    if (!guildId) return null;
    try {
      const response = await writeApi(`api/guild/${guildId}/subscriptions${path}`, body);
      const data = await readJson(response, {});
      if (response.status === 409) {
        toast("تغيرت إعدادات الاشتراكات في جلسة أخرى. حمّل النسخة الحالية ثم أعد تطبيق تعديلاتك.", "warn", 6000);
        await loadSubscriptionDashboard(guildId, false);
        return null;
      }
      if (!response.ok) {
        toast(data.message || (data.error === "member_verification_unavailable"
          ? "تعذر التحقق من عضوية المستخدم حالياً"
          : "تعذر حفظ التغيير"), "error", 5000);
        return null;
      }
      toast(successMessage, "success", 3000);
      await loadSubscriptionDashboard(guildId, false);
      return data;
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم. لم نغيّر البيانات محلياً.", "warn", 5000);
      return null;
    }
  }
  let subscriptionDialogSequence = 0;
  const subscriptionFocusableSelector = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  function subscriptionDialog(title) {
    const titleId = `subscription-dialog-title-${++subscriptionDialogSequence}`;
    const back = el("div", {
      class: "modal-back",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
    });
    const modal = el("div", { class: "modal subscription-modal", tabindex: "-1" },
      el("h2", { id: titleId, text: title }),
    );
    back.append(modal);
    let returnFocus = null;
    let open = false;
    const close = () => {
      if (!open) return;
      open = false;
      document.removeEventListener("keydown", onKeydown, true);
      back.remove();
      if (returnFocus?.isConnected) returnFocus.focus();
    };
    const onKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...modal.querySelectorAll(subscriptionFocusableSelector)]
        .filter((node) => node.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        modal.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !modal.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    const show = () => {
      returnFocus = document.activeElement;
      document.body.append(back);
      open = true;
      document.addEventListener("keydown", onKeydown, true);
      back.addEventListener("click", (event) => {
        if (event.target === back) close();
      });
      const initialFocus = modal.querySelector(subscriptionFocusableSelector) || modal;
      initialFocus.focus();
    };
    return { back, modal, close, show };
  }
  function subFormModal(title, form, subtitle = "") {
    const dialog = subscriptionDialog(title);
    const { modal, close, show } = dialog;
    form.append(
      el("div", { class: "modal-actions" },
        el("button", { class: "btn primary", type: "submit", text: "تأكيد" }),
        el("button", { class: "btn ghost", type: "button", text: "إلغاء", onClick: close }),
      ),
    );
    modal.append(
      subtitle ? el("p", { text: subtitle }) : null,
      form,
    );
    show();
    return { close, form };
  }
  function subscriptionOperationKey(kind) {
    const nonce = globalThis.crypto?.randomUUID?.()
      || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `dashboard:${kind}:${state.guild.id}:${nonce}`;
  }
  function openSubscriptionGrant() {
    const form = el("form", { class: "subscription-form-grid" });
    const planOptions = subSelectOptions(subPlans(true), "الخطة الافتراضية للسيرفر");
    form.append(
      subInput("user_id", "معرّف عضو Discord", "", "text", {
        inputmode: "numeric",
        minlength: "15",
        maxlength: "22",
        pattern: "[0-9]{15,22}",
        required: true,
        dir: "ltr",
        placeholder: "مثال: 123456789012345678",
      }, "يجب أن يكون العضو موجوداً في هذا السيرفر."),
      subSelect("plan_id", "الخطة", planOptions, state.subscriptionDashboard.data?.settings?.default_plan_id),
      subInput("duration_days", "المدة بالأيام", "", "number", {
        min: "1",
        max: "36500",
        step: "1",
        placeholder: "استخدم مدة الخطة أو الافتراضي",
      }, "اتركها فارغة لاستخدام مدة الخطة المختارة أو إعداد السيرفر."),
    );
    const modal = subFormModal("إنشاء اشتراك", form, "سيُسجل XP في محرك المستويات الحالي، مع منع تكرار العملية عند إعادة الطلب.");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const body = {
        user_id: String(values.get("user_id") || "").trim(),
        idempotency_key: subscriptionOperationKey("grant"),
      };
      const planId = String(values.get("plan_id") || "");
      const days = String(values.get("duration_days") || "").trim();
      if (planId) body.plan_id = planId;
      if (days) body.duration_days = Number(days);
      const result = await subscriptionPost("/grant", body, "تم إنشاء الاشتراك.");
      if (result) {
        modal.close();
        const amount = Number(result.xp?.amount || 0);
        toast(`تم تسجيل ${subNumber(amount)} XP في محرك المستويات`, "success", 3500);
      }
    });
  }
  function openSubscriptionRenew(record) {
    const plan = (state.subscriptionDashboard.data?.plans || [])
      .find((item) => item.plan_id === record.plan_id);
    const form = el("form", { class: "subscription-form-grid" });
    form.append(
      el("p", { class: "subscription-form-note", text: `تجديد اشتراك العضو ${record.user_id} · ${record.subscription_id}` }),
      subInput("duration_days", "المدة المضافة بالأيام", plan?.duration_days || state.subscriptionDashboard.data?.settings?.renewal_duration_days || 30, "number", {
        min: "1",
        max: "36500",
        step: "1",
        required: true,
      }),
    );
    const modal = subFormModal("تجديد الاشتراك", form, "تُضاف المدة بعد تاريخ الانتهاء الحالي إذا كان الاشتراك ما زال سارياً.");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const days = Number(new FormData(form).get("duration_days"));
      const result = await subscriptionPost(
        `/${encodeURIComponent(record.subscription_id)}/renew`,
        { duration_days: days, idempotency_key: subscriptionOperationKey("renew") },
        "تم تجديد الاشتراك.",
      );
      if (result) {
        modal.close();
        toast(`XP التجديد: ${subNumber(result.xp?.amount || 0)}`, "success", 3000);
      }
    });
  }
  function openSubscriptionAdjust(record) {
    const currentDate = new Date(record.end_date);
    const localDate = Number.isNaN(currentDate.getTime())
      ? ""
      : new Date(currentDate.getTime() - currentDate.getTimezoneOffset() * 60000)
        .toISOString().slice(0, 16);
    const form = el("form", { class: "subscription-form-grid" });
    form.append(
      subInput("end_date", "تاريخ الانتهاء الجديد", localDate, "datetime-local"),
      subSelect("status", "الحالة", [
        ["", "بدون تغيير"],
        ["active", "نشط"],
        ["expired", "منتهي"],
        ["cancelled", "ملغي"],
      ], ""),
      subInput("reason", "سبب التعديل", "", "text", { maxlength: "500", required: true }),
    );
    const modal = subFormModal("تصحيح سجل الاشتراك", form, "التصحيح إداري ومسجل في سجل التدقيق. لا تُعدّل هذه القيم إلا لتصحيح سجل غير دقيق.");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const changes = {};
      const endDate = String(values.get("end_date") || "");
      const status = String(values.get("status") || "");
      if (endDate) changes.end_date = new Date(endDate).toISOString();
      if (status) changes.status = status;
      if (!Object.keys(changes).length) {
        toast("غيّر تاريخ الانتهاء أو الحالة أولاً", "warn");
        return;
      }
      const result = await subscriptionPost(
        `/${encodeURIComponent(record.subscription_id)}/adjust`,
        {
          changes,
          reason: String(values.get("reason") || "").trim(),
          idempotency_key: subscriptionOperationKey("adjust"),
        },
        "تم تصحيح سجل الاشتراك وتسجيل التعديل.",
      );
      if (result) modal.close();
    });
  }
  async function openSubscriptionDetails(record) {
    const dialog = subscriptionDialog(`الاشتراك ${record.subscription_id}`);
    const { modal, close, show } = dialog;
    const body = el("div", { class: "subscription-detail-body" },
      el("p", { class: "subscription-form-note", text: "جارٍ تحميل سجل الاشتراك والتدقيق…" }),
    );
    modal.append(
      el("div", { class: "modal-actions" }, subButton("إغلاق", close)),
      body,
    );
    show();
    try {
      const response = await api(`api/guild/${state.guild.id}/subscriptions/${encodeURIComponent(record.subscription_id)}`, { cache: "no-store" });
      const data = await readJson(response, {});
      if (!response.ok) throw new Error(data.message || "تعذر تحميل السجل");
      const history = data.history || [];
      const audit = data.admin_audit || [];
      body.replaceChildren(
        el("div", { class: "subscription-detail-summary" },
          el("span", { text: `العضو: ${record.user_id}` }),
          subStatus(record.status),
          el("span", { text: `البداية: ${subDate(record.start_date)}` }),
          el("span", { text: `النهاية: ${subDate(record.end_date)}` }),
        ),
        el("h3", { text: "تاريخ الأحداث" }),
        history.length ? el("div", { class: "subscription-audit-list" },
          ...history.map((item) => el("article", { class: "subscription-audit-row" },
            el("strong", { text: item.event_type }),
            el("span", { text: `${subscriptionStatuses[item.status] || item.status || ""} · ${subDate(item.created_at)}` }),
            item.details && Object.keys(item.details).length
              ? el("small", { text: JSON.stringify(item.details) })
              : null,
          )),
        ) : el("p", { class: "empty-row", text: "لا توجد أحداث محفوظة." }),
        el("h3", { text: "تعديلات المسؤولين" }),
        audit.length ? el("div", { class: "subscription-audit-list" },
          ...audit.map((item) => el("article", { class: "subscription-audit-row" },
            el("strong", { text: `${item.event_type || item.operation} · المسؤول ${item.actor_id}` }),
            el("span", { text: subDate(item.created_at) }),
            item.after ? el("small", { text: JSON.stringify(item.after) }) : null,
          )),
        ) : el("p", { class: "empty-row", text: "لا توجد تعديلات إدارية." }),
      );
    } catch (error) {
      body.replaceChildren(el("p", { class: "subscription-error", role: "alert", text: error.message || "تعذر تحميل السجل" }));
    }
  }
  async function cancelSubscription(record) {
    if (!window.confirm(`إلغاء الاشتراك ${record.subscription_id} للعضو ${record.user_id}؟`)) return;
    const reason = window.prompt("سبب الإلغاء (اختياري):", "") ?? null;
    if (reason === null) return;
    await subscriptionPost(
      `/${encodeURIComponent(record.subscription_id)}/cancel`,
      { reason, idempotency_key: subscriptionOperationKey("cancel") },
      "تم إلغاء الاشتراك وتسجيل الإجراء.",
    );
  }
  function subscriptionRecordsView(records) {
    const root = el("section", { class: "subscription-panel" });
    const filterForm = el("form", { class: "subscription-filter-form" });
    const search = el("input", {
      name: "q",
      type: "search",
      value: state.subscriptionDashboard.query,
      placeholder: "ابحث بمعرّف الاشتراك أو العضو أو الخطة",
      "aria-label": "بحث الاشتراكات",
    });
    const status = el("select", { name: "status", "aria-label": "تصفية حسب الحالة" },
      ["all", "active", "expired", "cancelled"].map((value) =>
        el("option", {
          value,
          text: value === "all" ? "كل الحالات" : subscriptionStatuses[value],
        }),
      ),
    );
    status.value = state.subscriptionDashboard.status;
    filterForm.append(search, status,
      el("button", { class: "btn ghost", type: "submit", text: "تصفية" }),
    );
    filterForm.addEventListener("submit", (event) => {
      event.preventDefault();
      state.subscriptionDashboard.query = search.value.trim();
      state.subscriptionDashboard.status = status.value;
      renderPage();
    });
    const q = state.subscriptionDashboard.query.toLocaleLowerCase();
    const filtered = records.filter((record) => {
      if (state.subscriptionDashboard.status !== "all" && record.status !== state.subscriptionDashboard.status) return false;
      return !q || [record.subscription_id, record.user_id, record.plan_id]
        .some((value) => String(value || "").toLocaleLowerCase().includes(q));
    });
    const table = el("div", {
      class: "subscription-table-wrap",
      tabindex: "0",
      role: "region",
      "aria-label": "جدول الاشتراكات؛ مرّر أفقياً لعرض كل الأعمدة",
    });
    if (!filtered.length) {
      table.append(el("div", { class: "empty-row", text: records.length ? "لا توجد نتائج تطابق التصفية." : "لا توجد اشتراكات مسجلة في هذا السيرفر." }));
    } else {
      const body = el("tbody");
      filtered.forEach((record) => {
        const plan = (state.subscriptionDashboard.data?.plans || [])
          .find((item) => item.plan_id === record.plan_id);
        const actions = el("div", { class: "subscription-row-actions" },
          subButton("التفاصيل", () => openSubscriptionDetails(record)),
          record.status !== "cancelled" && record.status !== "expired"
            ? subButton("تجديد", () => openSubscriptionRenew(record), "primary")
            : record.status === "expired"
              ? subButton("تجديد", () => openSubscriptionRenew(record), "primary")
              : null,
          record.status === "active"
            ? subButton("إلغاء", () => cancelSubscription(record), "cancel")
            : null,
          subButton("تصحيح", () => openSubscriptionAdjust(record)),
        );
        body.append(el("tr", {},
          el("td", {}, el("strong", { text: record.subscription_id }), el("small", { class: "sub-cell-meta", text: plan?.name || record.plan_id || "بدون خطة" })),
          el("td", { dir: "ltr", text: String(record.user_id) }),
          el("td", {}, subStatus(record.status)),
          el("td", { text: subDate(record.end_date) }),
          el("td", {}, actions),
        ));
      });
      table.append(el("table", {},
        el("thead", {}, el("tr", {},
          el("th", { text: "الاشتراك / الخطة" }),
          el("th", { text: "العضو" }),
          el("th", { text: "الحالة" }),
          el("th", { text: "ينتهي في" }),
          el("th", { text: "الإجراءات" }),
        )),
        body,
      ));
    }
    root.append(filterForm, table);
    return root;
  }
  function subscriptionSettingsView(data) {
    const settings = data.settings || {};
    const rules = settings.notification_rules || {};
    const form = el("form", { class: "subscription-config-form" });
    const top = el("div", { class: "subscription-form-grid" });
    [
      ["enabled", "تفعيل نظام الاشتراكات"],
      ["new_subscription_enabled", "السماح بالاشتراكات الجديدة"],
      ["renewal_enabled", "السماح بالتجديد"],
      ["xp_enabled", "منح XP عبر محرك المستويات"],
      ["notifications_enabled", "تفعيل الإشعارات"],
      ["expiry_enabled", "تطبيق إجراء انتهاء الاشتراك"],
      ["expiry_detection_enabled", "تشغيل فحص الاشتراكات المستحقة"],
      ["reminders_enabled", "تشغيل التذكيرات"],
    ].forEach(([key, label]) => top.append(subCheck(key, label, settings[key])));
    const fields = el("div", { class: "subscription-form-grid" },
      subInput("new_xp_base", "XP الأساسي عند الإنشاء", settings.new_xp_base, "number", { min: "0", max: "100000", step: "1" }),
      subInput("renewal_xp_base", "XP الأساسي عند التجديد", settings.renewal_xp_base, "number", { min: "0", max: "100000", step: "1" }),
      subInput("level_step_xp", "XP الإضافي لكل مستوى", settings.level_step_xp, "number", { min: "0", max: "100000", step: "1" }),
      subInput("new_xp_jitter", "حد الزيادة العشوائية للإنشاء", settings.new_xp_jitter, "number", { min: "0", max: "100000", step: "1" }),
      subInput("renewal_xp_jitter", "حد الزيادة العشوائية للتجديد", settings.renewal_xp_jitter, "number", { min: "0", max: "100000", step: "1" }),
      subInput("new_xp_cap", "الحد الأعلى لـXP الإنشاء", settings.new_xp_cap, "number", { min: "0", max: "100000", step: "1" }),
      subInput("renewal_xp_cap", "الحد الأعلى لـXP التجديد", settings.renewal_xp_cap, "number", { min: "0", max: "100000", step: "1" }),
      subInput("xp_multiplier", "مضاعف XP", settings.xp_multiplier, "number", { min: "0", max: "100", step: "0.01" }),
      subInput("default_duration_days", "مدة الاشتراك الافتراضية بالأيام", settings.default_duration_days, "number", { min: "1", max: "36500", step: "1" }),
      subInput("renewal_duration_days", "مدة التجديد الافتراضية بالأيام", settings.renewal_duration_days, "number", { min: "1", max: "36500", step: "1" }),
      subInput("notification_claim_timeout_minutes", "مهلة استعادة الإشعار العالق بالدقائق", settings.notification_claim_timeout_minutes, "number", { min: "1", max: "120", step: "1" }),
      subSelect("expiry_action", "إجراء الانتهاء", [
        ["expire", "تغيير الحالة إلى منتهي"],
        ["cancel", "تغيير الحالة إلى ملغي"],
        ["keep_active", "إبقاء الحالة نشطة"],
      ], settings.expiry_action || "expire"),
      subSelect("default_plan_id", "الخطة الافتراضية", subSelectOptions(subPlans(true), "بدون خطة افتراضية"), settings.default_plan_id || ""),
    );
    const routing = el("div", { class: "subscription-routing-list" });
    subscriptionEvents.forEach(([eventType, label]) => {
      const rule = rules[eventType] || {};
      const eventTemplates = subSelectOptions(subTemplates(eventType), "استخدم القالب الافتراضي");
      routing.append(el("article", { class: "subscription-routing-card" },
        el("h3", { text: label }),
        el("div", { class: "subscription-checks" },
          subCheck(`rule_${eventType}_enabled`, "تفعيل الحدث", rule.enabled !== false),
          subCheck(`rule_${eventType}_dm_enabled`, "إرسال DM", rule.dm_enabled !== false),
          subCheck(`rule_${eventType}_channel_enabled`, "إرسال إلى قناة", !!rule.channel_enabled),
        ),
        el("div", { class: "subscription-form-grid" },
          subSelect(`rule_${eventType}_channel_id`, "قناة الإرسال", subSelectOptions(subChannels()), rule.channel_id || ""),
          subSelect(`rule_${eventType}_template_id`, "القالب", eventTemplates, rule.template_id || ""),
        ),
      ));
    });
    form.append(
      el("section", { class: "subscription-panel" },
        el("h3", { text: "حالة النظام ومحرك XP" }),
        top,
        fields,
      ),
      el("section", { class: "subscription-panel" },
        el("h3", { text: "توجيه الإشعارات حسب الحدث" }),
        routing,
      ),
      el("div", { class: "subscription-form-actions" },
        el("button", { class: "btn primary", type: "submit", text: "حفظ إعدادات الاشتراكات" }),
        el("span", { class: "subscription-muted", text: `مراجعة الإعدادات ${settings.revision ?? 0} · الحفظ يرفض التعديلات القديمة تلقائياً` }),
      ),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const body = {
        expected_revision: Number(settings.revision || 0),
        notification_rules: {},
      };
      const boolKeys = [
        "enabled", "new_subscription_enabled", "renewal_enabled",
        "xp_enabled", "notifications_enabled", "expiry_enabled",
        "expiry_detection_enabled", "reminders_enabled",
      ];
      boolKeys.forEach((key) => {
        body[key] = form.elements.namedItem(key).checked;
      });
      const intKeys = [
        "new_xp_base", "renewal_xp_base", "level_step_xp", "new_xp_jitter",
        "renewal_xp_jitter", "new_xp_cap", "renewal_xp_cap",
        "default_duration_days", "renewal_duration_days",
        "notification_claim_timeout_minutes",
      ];
      intKeys.forEach((key) => {
        body[key] = Number(values.get(key));
      });
      body.xp_multiplier = Number(values.get("xp_multiplier"));
      body.expiry_action = values.get("expiry_action");
      body.default_plan_id = values.get("default_plan_id") || null;
      subscriptionEvents.forEach(([eventType]) => {
        body.notification_rules[eventType] = {
          enabled: form.elements.namedItem(`rule_${eventType}_enabled`).checked,
          dm_enabled: form.elements.namedItem(`rule_${eventType}_dm_enabled`).checked,
          channel_enabled: form.elements.namedItem(`rule_${eventType}_channel_enabled`).checked,
          channel_id: values.get(`rule_${eventType}_channel_id`) || null,
          template_id: values.get(`rule_${eventType}_template_id`) || null,
        };
      });
      await subscriptionPost("/settings", body, "تم حفظ إعدادات الاشتراكات وتسجيلها في التدقيق.");
    });
    return form;
  }
  function subscriptionPlanForm(plan = null) {
    const form = el("form", { class: "subscription-entity-form" });
    const planOverrides = plan?.notification_overrides || {};
    const globalRules = state.subscriptionDashboard.data?.settings?.notification_rules || {};
    const planRouting = el("div", { class: "subscription-plan-routing" });
    subscriptionEvents.forEach(([eventType, label]) => {
      const override = planOverrides[eventType] || {};
      const globalRule = globalRules[eventType] || {};
      const effective = (key, fallback) =>
        Object.prototype.hasOwnProperty.call(override, key) ? override[key] : fallback;
      planRouting.append(el("article", { class: "subscription-routing-card" },
        el("h4", { text: label }),
        subCheck(`override_${eventType}`, "تخصيص توجيه هذا الحدث لهذه الخطة", Object.keys(override).length > 0),
        el("div", { class: "subscription-checks" },
          subCheck(`override_${eventType}_enabled`, "الإشعار مفعل", effective("enabled", globalRule.enabled !== false)),
          subCheck(`override_${eventType}_dm_enabled`, "إرسال DM", effective("dm_enabled", globalRule.dm_enabled !== false)),
          subCheck(`override_${eventType}_channel_enabled`, "إرسال إلى قناة", !!effective("channel_enabled", globalRule.channel_enabled)),
        ),
        el("div", { class: "subscription-form-grid" },
          subSelect(
            `override_${eventType}_channel_id`,
            "القناة",
            subSelectOptions(subChannels()),
            effective("channel_id", globalRule.channel_id || ""),
          ),
          subSelect(
            `override_${eventType}_template_id`,
            "القالب",
            subSelectOptions(subTemplates(eventType), "استخدم القالب العام"),
            effective("template_id", globalRule.template_id || ""),
          ),
        ),
      ));
    });
    form.append(
      el("input", { type: "hidden", name: "plan_id", value: plan?.plan_id || "" }),
      el("div", { class: "subscription-form-grid" },
        subInput("name", "اسم الخطة", plan?.name || "", "text", { maxlength: "80", required: true }),
        subInput("duration_days", "المدة بالأيام", plan?.duration_days ?? 30, "number", { min: "1", max: "36500", required: true }),
        subInput("description", "الوصف", plan?.description || "", "text", { maxlength: "500" }),
        subInput("price_cents", "السعر بأصغر وحدة نقدية (اختياري)", plan?.price_cents ?? "", "number", { min: "0", max: "1000000000", step: "1" }, "مثال: 499 = 4.99 من العملة المحددة. لا توجد معالجة دفع."),
        subInput("currency", "رمز العملة", plan?.currency || "USD", "text", { minlength: "3", maxlength: "3", pattern: "[A-Za-z]{3}", dir: "ltr" }),
        subInput("new_xp_base", "XP الإنشاء (فارغ = افتراضي السيرفر)", plan?.new_xp_base ?? "", "number", { min: "0", max: "100000", step: "1" }),
        subInput("renewal_xp_base", "XP التجديد (فارغ = افتراضي السيرفر)", plan?.renewal_xp_base ?? "", "number", { min: "0", max: "100000", step: "1" }),
        subInput("xp_multiplier", "مضاعف XP (فارغ = افتراضي السيرفر)", plan?.xp_multiplier ?? "", "number", { min: "0", max: "100", step: "0.01" }),
        subSelect("expiry_action", "إجراء الانتهاء", [
          ["", "افتراضي السيرفر"],
          ["expire", "منتهي"],
          ["cancel", "ملغي"],
          ["keep_active", "إبقاء نشط"],
        ], plan?.expiry_action || ""),
      ),
      el("div", { class: "subscription-checks" },
        subCheck("xp_enabled", "منح XP لهذه الخطة", plan?.xp_enabled !== false),
        subCheck("notifications_enabled", "إشعارات الخطة", plan?.notifications_enabled !== false),
        subCheck("reminders_enabled", "تذكيرات الانتهاء", plan?.reminders_enabled !== false),
        subCheck("enabled", "الخطة متاحة", plan?.enabled !== false),
      ),
      el("section", { class: "subscription-plan-routing-panel" },
        el("h4", { text: "توجيه الإشعارات الخاص بالخطة" }),
        el("p", { class: "subscription-muted", text: "اختياري: فعّل التخصيص للحدث لتجاوز توجيه السيرفر لهذه الخطة فقط." }),
        planRouting,
      ),
      el("div", { class: "subscription-form-actions" },
        el("button", { class: "btn primary", type: "submit", text: plan ? "حفظ الخطة" : "إنشاء الخطة" }),
        plan?.enabled ? subButton("تعطيل", async () => {
          if (!window.confirm(`تعطيل الخطة «${plan.name}»؟ ستبقى الاشتراكات الحالية محفوظة.`)) return;
          const result = await subscriptionPost(`/plans/${encodeURIComponent(plan.plan_id)}/disable`, {}, "تم تعطيل الخطة.");
          if (result) renderPage();
        }, "cancel") : null,
      ),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const optionalNumber = (key) => String(values.get(key) || "").trim() === ""
        ? null
        : Number(values.get(key));
      const body = {
        name: String(values.get("name") || "").trim(),
        description: String(values.get("description") || ""),
        duration_days: Number(values.get("duration_days")),
        price_cents: optionalNumber("price_cents"),
        currency: String(values.get("currency") || "USD").trim().toUpperCase(),
        xp_enabled: form.elements.namedItem("xp_enabled").checked,
        new_xp_base: optionalNumber("new_xp_base"),
        renewal_xp_base: optionalNumber("renewal_xp_base"),
        xp_multiplier: optionalNumber("xp_multiplier"),
        notification_overrides: {},
        notifications_enabled: form.elements.namedItem("notifications_enabled").checked,
        reminders_enabled: form.elements.namedItem("reminders_enabled").checked,
        expiry_action: values.get("expiry_action") || null,
        enabled: form.elements.namedItem("enabled").checked,
      };
      subscriptionEvents.forEach(([eventType]) => {
        if (!form.elements.namedItem(`override_${eventType}`).checked) return;
        body.notification_overrides[eventType] = {
          enabled: form.elements.namedItem(`override_${eventType}_enabled`).checked,
          dm_enabled: form.elements.namedItem(`override_${eventType}_dm_enabled`).checked,
          channel_enabled: form.elements.namedItem(`override_${eventType}_channel_enabled`).checked,
          channel_id: values.get(`override_${eventType}_channel_id`) || null,
          template_id: values.get(`override_${eventType}_template_id`) || null,
        };
      });
      const planId = String(values.get("plan_id") || "");
      if (planId) body.plan_id = planId;
      const result = await subscriptionPost("/plans", body, planId ? "تم حفظ الخطة." : "تم إنشاء الخطة.");
      if (result) renderPage();
    });
    return form;
  }
  function subscriptionPlansView(data) {
    const plans = data.plans || [];
    return el("div", { class: "subscription-stack" },
      el("section", { class: "subscription-panel" },
        el("h3", { text: "إضافة خطة" }),
        el("p", { class: "subscription-muted", text: "الخطط تصنيف ومدد ومزايا XP وإشعارات؛ لا تُنشئ عملية دفع أو تحصيل." }),
        subscriptionPlanForm(),
      ),
      el("div", { class: "subscription-entity-grid" },
        ...(plans.length
          ? plans.map((plan) => el("section", { class: `subscription-panel subscription-entity${plan.enabled ? "" : " is-disabled"}` },
            el("div", { class: "subscription-entity-head" },
              el("div", {}, el("h3", { text: plan.name }), el("small", { text: `${plan.duration_days} يوم · ${plan.plan_id}` })),
              subStatus(plan.enabled ? "active" : "disabled"),
            ),
            plan.description ? el("p", { class: "subscription-muted", text: plan.description }) : null,
            subscriptionPlanForm(plan),
          ))
          : [el("div", { class: "empty-row", text: "لا توجد خطط مخصصة؛ يمكن للنظام استخدام المدة الافتراضية." })]),
      ),
    );
  }
  function subscriptionReminderForm(reminder = null) {
    const form = el("form", { class: "subscription-entity-form" });
    const planSelect = el("select", { name: "plan_ids", multiple: true, size: "4" },
      subPlans().map(([id, name]) => el("option", { value: id, text: name })),
    );
    const selectedPlans = new Set(reminder?.conditions?.plan_ids || []);
    [...planSelect.options].forEach((option) => {
      option.selected = selectedPlans.has(option.value);
    });
    form.append(
      el("input", { type: "hidden", name: "reminder_id", value: reminder?.reminder_id || "" }),
      el("div", { class: "subscription-form-grid" },
        subInput("name", "اسم التذكير", reminder?.name || "", "text", { maxlength: "80", required: true }),
        subInput("hours_before", "قبل الانتهاء (ساعات)", reminder?.hours_before ?? 24, "number", { min: "1", max: "876000", step: "1", required: true }),
        subSelect("template_id", "قالب تذكير", subSelectOptions(subTemplates("expiring"), "القالب الافتراضي"), reminder?.template_id || ""),
        subSelect("channel_id", "قناة الإرسال", subSelectOptions(subChannels()), reminder?.channel_id || ""),
        subField("قصر التذكير على الخطط (اختياري)", planSelect, "اترك الكل غير محدد لتطبيقه على جميع الخطط."),
      ),
      el("div", { class: "subscription-checks" },
        subCheck("enabled", "التذكير مفعل", reminder?.enabled !== false),
        subCheck("dm_enabled", "إرسال DM", reminder?.dm_enabled !== false),
        subCheck("channel_enabled", "إرسال إلى القناة", !!reminder?.channel_enabled),
      ),
      el("div", { class: "subscription-form-actions" },
        el("button", { class: "btn primary", type: "submit", text: reminder ? "حفظ التذكير" : "إنشاء التذكير" }),
        reminder?.enabled ? subButton("تعطيل", async () => {
          if (!window.confirm(`تعطيل التذكير «${reminder.name}»؟`)) return;
          const result = await subscriptionPost(`/reminders/${encodeURIComponent(reminder.reminder_id)}/disable`, {}, "تم تعطيل التذكير.");
          if (result) renderPage();
        }, "cancel") : null,
      ),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const planIds = [...planSelect.selectedOptions].map((option) => option.value);
      const body = {
        name: String(values.get("name") || "").trim(),
        hours_before: Number(values.get("hours_before")),
        enabled: form.elements.namedItem("enabled").checked,
        dm_enabled: form.elements.namedItem("dm_enabled").checked,
        channel_enabled: form.elements.namedItem("channel_enabled").checked,
        channel_id: values.get("channel_id") || null,
        template_id: values.get("template_id") || null,
        conditions: planIds.length ? { plan_ids: planIds } : {},
      };
      const reminderId = String(values.get("reminder_id") || "");
      if (reminderId) body.reminder_id = reminderId;
      const result = await subscriptionPost("/reminders", body, reminderId ? "تم حفظ التذكير." : "تم إنشاء التذكير.");
      if (result) renderPage();
    });
    return form;
  }
  function subscriptionRemindersView(data) {
    const reminders = data.reminders || [];
    return el("div", { class: "subscription-stack" },
      el("section", { class: "subscription-panel" },
        el("h3", { text: "إضافة قاعدة تذكير" }),
        el("p", { class: "subscription-muted", text: "القواعد محفوظة في قاعدة البيانات وتطبقها مهمة البوت تلقائياً؛ عند التأخر يرسل العامل أقرب تذكير مستحق فقط." }),
        subscriptionReminderForm(),
      ),
      el("div", { class: "subscription-entity-grid" },
        ...(reminders.length
          ? reminders.map((reminder) => el("section", { class: `subscription-panel subscription-entity${reminder.enabled ? "" : " is-disabled"}` },
            el("div", { class: "subscription-entity-head" },
              el("div", {}, el("h3", { text: reminder.name }), el("small", { text: `قبل ${reminder.hours_before} ساعة · ${reminder.reminder_id}` })),
              subStatus(reminder.enabled ? "active" : "disabled"),
            ),
            subscriptionReminderForm(reminder),
          ))
          : [el("div", { class: "empty-row", text: "لا توجد قواعد تذكير." })]),
      ),
    );
  }
  function subscriptionTemplateForm(template = null) {
    const form = el("form", { class: "subscription-entity-form" });
    const eventOptions = subscriptionEvents.map(([value, label]) => [value, label]);
    form.append(
      el("input", { type: "hidden", name: "template_id", value: template?.template_id || "" }),
      el("div", { class: "subscription-form-grid" },
        subInput("name", "اسم القالب", template?.name || "", "text", { maxlength: "80", required: true }),
        subSelect("event_type", "نوع الحدث", eventOptions, template?.event_type || "created", template?.is_default ? { disabled: true } : {}),
      ),
      subField("نص الرسالة",
        el("textarea", { name: "content", rows: "4", maxlength: "1000", required: true, placeholder: "استخدم الحقول مثل {user} و{server} و{end_date} و{xp}." }, template?.content || ""),
        "الحقول المتاحة: {user} {name} {server} {plan} {start_date} {end_date} {days_remaining} {subscription_id} {xp} {level} {message} {remaining}."),
      el("div", { class: "subscription-checks" }, subCheck("enabled", "القالب متاح للاختيار", template?.enabled !== false)),
      el("div", { class: "subscription-form-actions" },
        el("button", { class: "btn primary", type: "submit", text: template ? "حفظ القالب" : "إنشاء القالب" }),
        template?.enabled && !template.is_default
          ? subButton("تعطيل", async () => {
            if (!window.confirm(`تعطيل القالب «${template.name}»؟`)) return;
            const result = await subscriptionPost(`/templates/${encodeURIComponent(template.template_id)}/disable`, {}, "تم تعطيل القالب.");
            if (result) renderPage();
          }, "cancel")
          : null,
      ),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = new FormData(form);
      const body = {
        name: String(values.get("name") || "").trim(),
        event_type: String(values.get("event_type") || "created"),
        content: String(values.get("content") || ""),
        enabled: form.elements.namedItem("enabled").checked,
      };
      const templateId = String(values.get("template_id") || "");
      if (templateId) body.template_id = templateId;
      const result = await subscriptionPost("/templates", body, templateId ? "تم حفظ القالب." : "تم إنشاء القالب.");
      if (result) renderPage();
    });
    return form;
  }
  function subscriptionTemplatesView(data) {
    const templates = data.templates || [];
    return el("div", { class: "subscription-stack" },
      el("section", { class: "subscription-panel" },
        el("h3", { text: "إنشاء قالب" }),
        subscriptionTemplateForm(),
      ),
      ...subscriptionEvents.map(([eventType, label]) => {
        const entries = templates.filter((template) => template.event_type === eventType);
        return el("section", { class: "subscription-panel" },
          el("h3", { text: label }),
          entries.length
            ? el("div", { class: "subscription-entity-grid" },
              ...entries.map((template) => el("article", { class: `subscription-panel subscription-template-card${template.enabled ? "" : " is-disabled"}` },
                el("div", { class: "subscription-entity-head" },
                  el("div", {}, el("h4", { text: template.name }), el("small", { text: template.is_default ? "قالب افتراضي" : template.template_id })),
                  subStatus(template.enabled ? "active" : "disabled"),
                ),
                subscriptionTemplateForm(template),
              )),
            )
            : el("p", { class: "empty-row", text: "لا توجد قوالب لهذا الحدث." }),
        );
      }),
    );
  }
  function subscriptionLogsView(data) {
    const notifications = data.notifications || [];
    const controlAudit = data.control_audit || [];
    const adminAudit = data.admin_audit || [];
    const rows = (items, renderRow, empty) => items.length
      ? el("div", { class: "subscription-audit-list" }, ...items.map(renderRow))
      : el("p", { class: "empty-row", text: empty });
    return el("div", { class: "subscription-stack" },
      el("section", { class: "subscription-panel" },
        el("h3", { text: "حالة التسليم والإشعارات" }),
        el("p", { class: "subscription-muted", text: "آخر 100 إشعار، مع الوجهة والحالة ورسالة الخطأ عند الفشل." }),
        rows(notifications, (item) => {
          const delivery = item.payload || {};
          const destinations = [
            delivery.dm_enabled ? "DM" : "",
            delivery.channel_enabled
              ? `قناة ${window.guildChannels?.[String(delivery.channel_id)]?.name
                ? `#${window.guildChannels[String(delivery.channel_id)].name}`
                : delivery.channel_id || "غير محددة"}`
              : "",
          ].filter(Boolean).join(" + ");
          return el("article", { class: "subscription-audit-row" },
            el("strong", { text: `${item.event_type} · ${item.subscription_id}` }),
            subStatus(item.status),
            el("span", { text: destinations || "لا توجد وجهة محفوظة" }),
            el("small", { text: `${subDate(item.created_at)}${item.last_error ? ` · ${item.last_error}` : ""}` }),
          );
        }, "لا توجد إشعارات مسجلة."),
      ),
      el("section", { class: "subscription-panel" },
        el("h3", { text: "سجل تعديلات الإعدادات والخطط" }),
        rows(controlAudit, (item) => el("article", { class: "subscription-audit-row" },
          el("strong", { text: `${item.entity_type} · ${item.operation} · ${item.entity_id}` }),
          el("span", { text: `المسؤول ${item.actor_id} · ${subDate(item.created_at)}` }),
        ), "لا توجد تغييرات على عناصر التحكم."),
      ),
      el("section", { class: "subscription-panel" },
        el("h3", { text: "سجل الاشتراكات الإدارية" }),
        rows(adminAudit, (item) => el("article", { class: "subscription-audit-row" },
          el("strong", { text: `${item.event_type || "تعديل"} · ${item.subscription_id}` }),
          el("span", { text: `العضو ${item.user_id} · المسؤول ${item.actor_id}` }),
          el("small", { text: `${subDate(item.created_at)}${item.after?.reason ? ` · ${item.after.reason}` : ""}` }),
        ), "لا توجد إجراءات إدارية مسجلة."),
      ),
    );
  }
  function subscriptionDashboardView() {
    const viewState = state.subscriptionDashboard;
    if (!viewState.data || viewState.guildId !== state.guild?.id) {
      if (!viewState.loading) queueMicrotask(() => loadSubscriptionDashboard(state.guild?.id));
      return el("section", { id: "view-subscriptions", class: "subscription-view" },
        el("div", { class: "section-intro" },
          el("div", { class: "eyebrow", text: `${state.guild?.name || "PRIME"} / SUBSCRIPTION CONTROL` }),
          el("h1", { text: "مركز الاشتراكات" }),
        ),
        viewState.error
          ? el("div", { class: "subscription-error", role: "alert" },
            el("span", { text: viewState.error }),
            subButton("إعادة المحاولة", () => loadSubscriptionDashboard(state.guild?.id), "primary"),
          )
          : el("div", { class: "loading" }, el("div", { class: "skeleton" }), el("p", { text: "جارٍ تحميل بيانات الاشتراكات المحفوظة…" })),
      );
    }
    const data = viewState.data;
    const stats = data.analytics || {};
    const tab = subscriptionTabs.some(([key]) => key === viewState.tab) ? viewState.tab : "overview";
    const tabs = el("nav", { class: "subscription-tabs", "aria-label": "أقسام الاشتراكات" },
      ...subscriptionTabs.map(([key, label]) => el("button", {
        type: "button",
        class: tab === key ? "is-active" : "",
        "aria-pressed": String(tab === key),
        text: label,
        onClick: () => {
          viewState.tab = key;
          sessionStorage.setItem("subscription-tab", key);
          renderPage();
        },
      })),
    );
    let panel;
    if (tab === "subscriptions") {
      panel = el("div", { class: "subscription-stack" },
        el("section", { class: "subscription-panel" },
          el("div", { class: "subscription-entity-head" },
            el("div", {}, el("h3", { text: "سجلات الاشتراك" }), el("small", { text: "حتى 100 سجل حديث؛ المعرفات تبقى كنصوص Discord." })),
            subButton("＋ إنشاء اشتراك", openSubscriptionGrant, "primary"),
          ),
          subscriptionRecordsView(data.subscriptions || []),
        ),
      );
    } else if (tab === "settings") {
      panel = subscriptionSettingsView(data);
    } else if (tab === "plans") {
      panel = subscriptionPlansView(data);
    } else if (tab === "reminders") {
      panel = subscriptionRemindersView(data);
    } else if (tab === "templates") {
      panel = subscriptionTemplatesView(data);
    } else if (tab === "logs") {
      panel = subscriptionLogsView(data);
    } else {
      panel = el("div", { class: "subscription-stack" },
        el("div", { class: "subscription-stat-grid" },
          ["active_subscriptions", "expired_subscriptions", "expiring_soon", "new_subscriptions_today", "renewals_this_month", "total_subscription_xp"]
            .map((key, index) => {
              const labels = ["اشتراكات نشطة", "اشتراكات منتهية", "تنتهي خلال 7 أيام", "جديدة اليوم", "تجديدات هذا الشهر", "XP الاشتراكات"];
              return el("article", { class: "subscription-stat-card" },
                el("small", { text: labels[index] }),
                el("strong", { text: subNumber(stats[key]) }),
              );
            }),
        ),
        el("section", { class: "subscription-panel" },
          el("div", { class: "subscription-entity-head" },
            el("div", {}, el("h3", { text: "أحدث الاشتراكات" }), el("small", { text: "تُحدّث الأرقام بعد كل قراءة للبيانات المحفوظة." })),
            subButton("عرض الاشتراكات", () => { viewState.tab = "subscriptions"; renderPage(); }),
          ),
          subscriptionRecordsView((data.subscriptions || []).slice(0, 8)),
        ),
        el("section", { class: "subscription-panel" },
          el("div", { class: "subscription-entity-head" },
            el("div", {}, el("h3", { text: "آخر الإشعارات" }), el("small", { text: "نتائج التسليم الفعلية من عامل البوت." })),
            subButton("سجل التسليم", () => { viewState.tab = "logs"; renderPage(); }),
          ),
          (data.notifications || []).length
            ? el("div", { class: "subscription-audit-list" },
              ...(data.notifications || []).slice(0, 5).map((item) => el("article", { class: "subscription-audit-row" },
                el("strong", { text: `${item.event_type} · ${item.subscription_id}` }),
                subStatus(item.status),
                el("small", { text: `${subDate(item.created_at)}${item.last_error ? ` · ${item.last_error}` : ""}` }),
              )),
            )
            : el("p", { class: "empty-row", text: "لا توجد إشعارات بعد." }),
        ),
      );
    }
    return el("section", { id: "view-subscriptions", class: "subscription-view" },
      el("div", { class: "section-intro" },
        el("div", { class: "eyebrow", text: `${state.guild?.name || "PRIME"} / SUBSCRIPTION CONTROL` }),
        el("h1", { text: "مركز الاشتراكات" }),
        el("p", { text: "تحكم في الخطط وXP والتذكيرات ورسائل الأعضاء، مع سجل تدقيق موحد فوق قاعدة البيانات ومحرك المستويات الحالي." }),
      ),
      el("div", { class: "subscription-toolbar" },
        tabs,
        subButton(viewState.loading ? "جارٍ التحديث…" : "تحديث البيانات", () => loadSubscriptionDashboard(state.guild?.id), "ghost", viewState.loading),
      ),
      viewState.error ? el("div", { class: "subscription-error", role: "alert", text: viewState.error }) : null,
      panel,
    );
  }
  function economyView() {
    const snapshot = state.economy.settings || { settings: {} };
    const config = snapshot.settings || {};
    const channels = state.meta?.channels || [];
    const roles = state.meta?.roles || [];
    const channelSelect = el(
      "select",
      { name: "leaderboard_channel_id", required: true },
      el("option", { value: "0", text: "لا توجد قناة مثبتة" }),
      channels.map((channel) => el("option", { value: channel.id, text: `#${channel.name}` })),
    );
    channelSelect.value = String(config.leaderboard_channel_id || "0");
    const supportSelect = el(
      "select",
      { name: "economy_support_role_ids", multiple: true, size: "4" },
      roles.map((role) => el("option", { value: role.id, text: role.name })),
    );
    const selectedSupportRoles = new Set((config.economy_support_role_ids || []).map(String));
    supportSelect.querySelectorAll("option").forEach((option) => {
      option.selected = selectedSupportRoles.has(String(option.value));
    });
    const form = el(
      "form",
      { class: "fields economy-config-form" },
      el("label", {}, el("span", { text: "قناة لوحة المتصدرين" }), channelSelect),
      el("label", {}, el("span", { text: "المكافأة اليومية الأساسية" }), el("input", { name: "daily_base_amount", type: "number", min: "0", max: "1000000", value: String(config.daily_base_amount ?? 200) })),
      el("div", { class: "economy-role-multipliers" },
        el("div", { class: "panel-heading" }, el("h3", { text: "مضاعفات الرتب" }), el("small", { text: "اترك القيمة فارغة لإلغاء المضاعف" })),
        ...roles.map((role) => {
          const input = el("input", { type: "number", min: "0.1", max: "10", step: "0.1", value: state.economy.multipliers[String(role.id)] ?? "", "data-role-multiplier": role.id, placeholder: "1.0×" });
          return el("label", { class: "economy-role-row" }, el("span", { text: role.name }), input);
        }),
      ),
      el("label", {}, el("span", { text: "أدوار دعم الاقتصاد" }), supportSelect),
      el("button", { class: "btn primary", type: "submit", text: "حفظ وتثبيت اللوحة 📌" }),
    );
    form.onsubmit = async (event) => {
      event.preventDefault();
      const multipliers = {};
      form.querySelectorAll("[data-role-multiplier]").forEach((input) => {
        if (input.value) multipliers[input.dataset.roleMultiplier] = Number(input.value);
      });
      const body = {
        leaderboard_channel_id: channelSelect.value,
        daily_base_amount: Number(form.elements.daily_base_amount.value),
        role_multipliers: multipliers,
        economy_support_role_ids: [...supportSelect.selectedOptions].map((option) => option.value),
      };
      const response = await writeApi(`api/guild/${state.guild.id}/economy/config`, body);
      const data = await readJson(response, {});
      if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر حفظ إعدادات الاقتصاد");
      toast("✅ تم حفظ إعدادات الاقتصاد وتثبيت اللوحة", "success", 3000);
      await refreshEconomy();
    };
    const openAdjust = (row) => {
      const back = el("div", { class: "modal-back", role: "dialog", "aria-modal": "true" });
      const wallet = el("input", { name: "wallet_delta", type: "number", placeholder: "مثال: 500 أو -250", value: "0" });
      const edit = el("form", { class: "fields" },
        el("label", {}, el("span", { text: "إضافة/خصم رصيد" }), wallet),
        el("button", { class: "btn primary", type: "submit", text: "تنفيذ التعديل فوراً" }),
      );
      edit.onsubmit = async (event) => {
        event.preventDefault();
        const response = await writeApi(`api/guild/${state.guild.id}/economy/adjust`, {
          user_id: row.user_id,
          wallet_delta: Number(wallet.value || 0),
        });
        const data = await readJson(response, {});
        if (!response.ok) return toast(data.fields ? Object.values(data.fields)[0] : "تعذر تعديل الحساب");
        back.remove();
        toast("✅ تم تحديث حساب العضو", "success", 2500);
        await refreshEconomy();
      };
      back.append(el("div", { class: "modal" },
        el("h2", { text: `تعديل حساب ${row.user_id}` }),
        el("p", { text: `الرصيد الحالي: ${(Number(row.balance) + Number(row.bank)).toLocaleString()}` }),
        edit,
        el("button", { class: "btn ghost", type: "button", text: "إلغاء", onClick: () => back.remove() }),
      ));
      document.body.append(back);
    };
    const rows = state.economy.wealth.length
      ? state.economy.wealth.map((row, index) => el("button", { class: "economy-user-row", type: "button", onClick: () => openAdjust(row) },
        el("span", { text: `#${index + 1}` }),
        el("strong", { text: `عضو ${row.user_id}` }),
        el("span", { text: `${Number(row.total).toLocaleString()} عملة` }),
      ))
      : [el("div", { class: "empty-row", text: "لا توجد حسابات اقتصادية بعد" })];
    return el("section", { id: "view-economy", class: "economy-view" },
      el("div", { class: "section-intro" }, el("div", { class: "eyebrow", text: `${state.guild.name} / ECONOMY CONTROL` }), el("h1", { text: "الاقتصاد والمتصدرون" }), el("p", { text: "مكافآت يومية، مضاعفات للرتب، ولوحة متصدرين حية لا تختفي عند التحديث." })),
      card("إعدادات الاقتصاد واللوحة الحية", form),
      el("section", { class: "overview-panel economy-leaderboard-panel" },
        el("div", { class: "panel-heading" }, el("div", { class: "eyebrow", text: "LIVE BALANCE MANAGER" }), el("h2", { text: "أغنى الأعضاء" }), el("small", { text: "اضغط على أي صف لفتح التعديل السريع" })),
        el("div", { class: "economy-user-list" }, rows),
      ),
    );
  }
  async function refreshEconomy() {
    if (!state.guild?.id) return;
    try {
      const response = await api(`api/guild/${state.guild.id}/economy`);
      if (response.ok) {
        const data = await readJson(response, {});
        state.economy = {
          wealth: data.wealth || [],
          settings: data.settings || { settings: {} },
          multipliers: data.multipliers || {},
        };
        if (state.activeView === "economy") renderPage();
      }
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر تحديث مركز الاقتصاد");
    }
  }
  function analyticsView() {
    const categories = [
      ["log_sanctions", "⚔️", "سجل العقوبات", "الباند والطرد والكتم", "#DC2626"],
      ["log_violations", "⚠️", "سجل المخالفات", "الإنذارات والإجراءات التصحيحية", "#EAB308"],
      ["log_automod", "🛡️", "سجل Auto-Mod", "الروابط والكلمات والسبام والمنشن", "#F97316"],
      ["log_ticket", "🎫", "سجل التذاكر", "الفتح والاستلام والإغلاق والتصعيد", "#14B8A6"],
      ["log_channel", "📁", "سجل القنوات", "إنشاء وتعديل وحذف القنوات", "#10B981"],
      ["log_server", "🏰", "سجل السيرفر", "تعديلات إعدادات السيرفر", "#3B82F6"],
      ["log_member", "👤", "سجل الأعضاء", "الدخول والمغادرة وتغييرات العضوية", "#22C55E"],
      ["log_message", "💬", "سجل الرسائل", "حذف وتعديل الرسائل", "#EF4444"],
      ["log_voice", "🎙️", "سجل النشاط الصوتي", "الدخول والخروج والتنقل", "#06B6D4"],
      ["log_react", "👍", "سجل التفاعلات", "إضافة التفاعلات على الرسائل", "#EC4899"],
      ["log_roles", "🎭", "سجل الرتب والصلاحيات", "إضافة الرتب وتعديلها", "#8B5CF6"],
    ];
    const channels = state.meta?.channels || [];
    const routeState = state.logRouting?.channels || {};
    const cards = categories.map(([key, icon, title, hint, accent]) => {
      const select = el(
        "select",
        { class: "analytics-channel-select", "aria-label": title },
        el("option", { value: "0" }, "✕ غير مفعّلة"),
        ...channels.map((channel) => el("option", { value: String(channel.id) }, `#${channel.name}`)),
      );
      select.value = String(routeState[key] || "0");
      select.onchange = () => { state.logRouting.channels[key] = select.value; };
      return el(
        "article",
        { class: "analytics-route-card", style: `--route-accent:${accent}` },
        el("div", { class: "analytics-route-head" },
          el("span", { class: "analytics-route-icon", text: icon }),
          el("div", {}, el("strong", { text: title }), el("small", { text: hint })),
          el("span", { class: "analytics-route-accent", text: "LIVE" }),
        ),
        select,
        el("div", { class: "analytics-route-actions" },
          el("button", {
            class: "btn analytics-test-button", type: "button", title: "إرسال رسالة اختبار إلى القناة المختارة",
            text: "🧪 إرسال اختبار",
            onClick: async (event) => {
              pulse();
              const button = event.currentTarget;
              button.disabled = true;
              try {
                const response = await writeApi(`api/guild/${state.guild.id}/logs/test/${key}`, {});
                const data = await readJson(response, {});
                const message = {
                  category_unassigned: "عيّن قناة لهذا التصنيف أولاً ثم احفظ التوزيع",
                  missing_send_permission: "البوت لا يملك صلاحية إرسال الرسائل في هذه القناة",
                  discord_unavailable: "تعذر الوصول إلى Discord حالياً",
                }[data.error] || "تعذر إرسال التجربة";
                toast(response.ok ? "تم إرسال رسالة الاختبار إلى Discord" : message, response.ok ? "success" : "warn");
              } finally {
                button.disabled = false;
              }
            },
          }),
          el("button", {
            class: "btn analytics-disable-button", type: "button", title: "تعطيل هذا التصنيف",
            text: "✕ تعطيل",
            onClick: () => { select.value = "0"; state.logRouting.channels[key] = "0"; },
          }),
        ),
      );
    });
    return el("section", { id: "view-analytics", class: "analytics-view" },
      el("div", { class: "studio-hero analytics-hero" },
        el("div", { class: "eyebrow", text: `${state.guild.name} / ANALYTICS CONTROL` }),
        el("h2", { text: "موزع السجلات الاحترافي" }),
        el("p", { text: "وجّه كل فئة إلى قناتها الخاصة وراجع شكل الإمبد الحقيقي قبل تفعيل السجل." }),
        el("button", {
          class: "btn analytics-save-button", type: "button", text: "💾 حفظ توزيع قنوات السجلات",
          onClick: async (event) => {
            pulse();
            const button = event.currentTarget;
            button.disabled = true;
            try {
              const response = await writeApi(
                `api/guild/${state.guild.id}/logs/channels`,
                { channels: state.logRouting.channels },
              );
              const data = await readJson(response, {});
              if (response.ok) {
                state.logRouting = data;
                toast("تم حفظ توزيع السجلات وتحديث الذاكرة مباشرة", "success");
                renderPage();
              } else {
                toast(Object.values(data.fields || {})[0] || "تعذر حفظ توزيع السجلات", "warn");
              }
            } finally {
              button.disabled = false;
            }
          },
        }),
      ),
       el("div", { class: "analytics-route-grid" }, ...cards),
    );
  }
  // ===== Leveling view (Phase 7): frontend-only, guild-scoped local drafts =====
  const LV_TABS = [
    ["general", "عام"], ["public", "اللوحة العامة"], ["points", "النقاط"], ["voice", "الصوت"], ["rewards", "المكافآت"],
    ["card", "البطاقة"], ["messages", "الرسائل"], ["prime", "PRIME TOP"], ["streak", "سلسلة النشاط"], ["data", "البيانات"],
  ];
  const LV_LAYOUTS = { vertical: [560, 900, "عمودية"], stats: [1000, 420, "إحصائيات"], minimal: [900, 230, "مصغّرة"], ring: [620, 680, "حلقة"], classic: [1000, 340, "كلاسيكية"], banner: [1000, 300, "شريطية"], square: [640, 640, "مربّعة"], spotlight: [1000, 500, "كشاف"] };
  const LV_PARTS = { none: "بدون", sparks: "شرارات", shine: "لمعان", embers: "جمر", snow: "ثلج", petals: "بتلات", neon: "نيون" };
  const LV_STREAK_TEMPLATE_VARS = {
    duplicate: new Set(["user", "username", "mention", "streak", "current_streak", "best", "best_streak", "stage", "stage_name", "next_stage", "remaining", "progress", "time_remaining", "server", "server_rank", "global_rank"]),
    success: new Set(["user", "username", "mention", "streak", "current_streak", "best", "best_streak", "stage", "stage_name", "next_stage", "remaining", "progress", "time_remaining", "server", "server_rank", "global_rank"]),
    stageUp: new Set(["user", "username", "mention", "streak", "current_streak", "best", "best_streak", "stage", "stage_name", "next_stage", "remaining", "progress", "time_remaining", "server", "server_rank", "global_rank"]),
    milestone: new Set(["user", "username", "mention", "streak", "current_streak", "best", "best_streak", "stage", "stage_name", "next_stage", "remaining", "progress", "time_remaining", "server", "server_rank", "global_rank", "threshold"]),
    reminder: new Set(["user", "username", "mention", "streak", "current_streak", "best", "best_streak", "stage", "stage_name", "next_stage", "remaining", "progress", "time_remaining", "server", "server_rank", "global_rank"]),
  };
  const LV_CARD_STYLE_KEYS = ["layout", "particles", "color", "bg", "animated", "showStats", "glowStrength", "particleDensity", "particleColor", "barStyle", "frame", "bgOverlay", "bgBlur", "animationEnabled", "animationStyle", "animationIntensity", "stats"];
  const lvCardStyleSnapshot = (card) => Object.fromEntries(LV_CARD_STYLE_KEYS.map((key) => [key, clone(card[key])]));
  const lvDefaults = () => ({
    public: { enabled: false, slug: "" },
    general: { enabled: true, text: true, reaction: false, streak: true },
    points: { xpMultiplier: 1, minXp: 15, maxXp: 25, cooldown: 60, roleMult: [], chanMult: [], boosts: [], allowedChannels: [], bl: { channels: [], users: [], roles: [] } },
    voice: { enabled: true, xpPerMin: 20, muteBlock: true, deafBlock: true, minMembers: 2, dimEnabled: false, dimThreshold: 60, dimRate: 50, separate: true },
    rewards: { highestOnly: true, list: [] },
    card: { layout: "vertical", particles: "none", color: "#38bdf8", bg: "", animated: true, showStats: true, glowStrength: 54, particleDensity: 46, particleColor: "accent", barStyle: "gradient", frame: "auto", bgOverlay: 28, bgBlur: 4, animationEnabled: true, animationStyle: "beam", animationIntensity: 62, stats: { messages: true, voice: true, streak: true, serverRank: true }, presets: [] },
    messages: {
      levelup: { on: true, channel: "", tpl: "مبروك {user}! وصلت إلى المستوى {level} في {server}." },
      milestone: { on: true, channel: "", tpl: "{user} حقق إنجازاً جديداً عند المستوى {level}." },
      overtake: { on: false, channel: "", tpl: "{passer} تجاوز {passed} وأصبح في المركز {rank}." },
      role_promotion: { on: false, channel: "", tpl: "مبروك {mention}! حصلت على رتبة {role}." },
    },
    streak: {
      enabled: true, channel: "", dailyXp: 50, maxCap: 500,
      timezone: "Asia/Riyadh", resetTime: "00:00",
      progressCardEnabled: true, successReaction: "🔥",
      messages: {
        duplicate: { enabled: true, message: "🔥 تم تسجيل ستريكك اليوم بالفعل.\nستريكك الحالي: {streak} يوم\nأفضل ستريك: {best} يوم\nالمرحلة: {stage_name}\n⏳ الستريك القادم بعد {time_remaining}." },
        success: { enabled: true, message: "🔥 {streak} يوم · {stage_name}" },
        stageUp: { enabled: true, message: "{user} وصل إلى مرحلة {stage_name} بعد {streak} يومًا متواصلًا." },
        milestone: { enabled: true, message: "🎉 {user} حقق إنجازًا جديدًا عند {threshold} يومًا من الستريك." },
        reminder: { enabled: true, time: "21:00", message: "🔥 لا تنسَ تسجيل ستريكك اليوم. ستريكك الحالي: {streak} يوم." },
      },
      stages: [], milestones: [],
    },
  });
  const lvMerge = (base, src) => {
    if (Array.isArray(base)) return Array.isArray(src) ? src : base;
    if (base && typeof base === "object") {
      const o = {};
      Object.keys(base).forEach((k) => { o[k] = lvMerge(base[k], src && typeof src === "object" ? src[k] : undefined); });
      if (src && typeof src === "object") Object.keys(src).forEach((k) => { if (!(k in o)) o[k] = src[k]; });
      return o;
    }
    return typeof src === typeof base ? src : base;
  };
  const lvObjs = (a, fn) => (Array.isArray(a) ? a.filter((x) => x && typeof x === "object" && !Array.isArray(x)).map(fn) : []);
  const lvStrs = (a) => (Array.isArray(a) ? a.filter((x) => typeof x === "string" || typeof x === "number").map(String) : []);
  const lvSan = (d) => {
    d.public = {
      enabled: d.public?.enabled === true,
      slug: typeof d.public?.slug === "string" ? d.public.slug.trim() : "",
    };
    const p = d.points;
    p.roleMult = lvObjs(p.roleMult, (m) => ({ id: String(m.id ?? ""), mult: typeof m.mult === "number" ? m.mult : "" }));
    p.chanMult = lvObjs(p.chanMult, (m) => ({ id: String(m.id ?? ""), mult: typeof m.mult === "number" ? m.mult : "" }));
    p.boosts = lvObjs(p.boosts, (b) => ({
      id: typeof b.id === "string" ? b.id : "",
      label: String(b.label ?? ""),
      mult: typeof b.mult === "number" ? b.mult : "",
      hours: typeof b.hours === "number" ? b.hours : "",
      expiresAt: typeof b.expiresAt === "string" ? b.expiresAt : "",
    }));
    ["channels", "users", "roles"].forEach((k) => { p.bl[k] = lvStrs(p.bl[k]); });
    p.allowedChannels = lvStrs(p.allowedChannels);
    d.rewards.list = lvObjs(d.rewards.list, (r) => ({ level: typeof r.level === "number" ? r.level : "", role: String(r.role ?? ""), type: r.type === "voice" ? "voice" : "text" }));
    const card = d.card;
    card.presets = lvObjs(card.presets, (preset) => {
      const snapshot = lvCardStyleSnapshot(lvMerge(lvDefaults().card, preset));
      return { id: String(preset.id || `preset-${Math.random().toString(36).slice(2, 9)}`).slice(0, 80), name: String(preset.name || "إعداد محفوظ").slice(0, 40), ...snapshot };
    }).slice(0, 8);
    card.stats = lvMerge(lvDefaults().card.stats, card.stats);
    const streakDefaults = lvDefaults().streak;
    const streak = lvMerge(streakDefaults, d.streak);
    streak.enabled = streak.enabled === true;
    streak.channel = typeof streak.channel === "string" ? streak.channel : "";
    streak.dailyXp = typeof streak.dailyXp === "number" ? streak.dailyXp : "";
    streak.maxCap = typeof streak.maxCap === "number" ? streak.maxCap : "";
    streak.timezone = "Asia/Riyadh";
    streak.resetTime = "00:00";
    streak.progressCardEnabled = streak.progressCardEnabled === true;
    streak.successReaction = typeof streak.successReaction === "string" ? streak.successReaction : "";
    streak.messages = lvMerge(streakDefaults.messages, streak.messages);
    Object.keys(streakDefaults.messages).forEach((key) => {
      const item = streak.messages[key];
      streak.messages[key] = {
        enabled: item.enabled === true,
        message: typeof item.message === "string" ? item.message : "",
        ...(key === "reminder" ? { time: typeof item.time === "string" ? item.time : "21:00" } : {}),
      };
    });
    streak.stages = lvObjs(streak.stages, (stage) => ({
      stage_key: String(stage.stage_key ?? ""),
      threshold: typeof stage.threshold === "number" ? stage.threshold : "",
      name: String(stage.name ?? ""),
      message: typeof stage.message === "string" ? stage.message : null,
      image: typeof stage.image === "string" ? stage.image : null,
      color: typeof stage.color === "string" ? stage.color : "#5865f2",
      reaction: typeof stage.reaction === "string" ? stage.reaction : null,
      description: typeof stage.description === "string" ? stage.description : "",
      glow: typeof stage.glow === "number" ? stage.glow : 0,
      particle: typeof stage.particle === "string" ? stage.particle : "none",
      enabled: stage.enabled === true,
    }));
    streak.milestones = lvObjs(streak.milestones, (milestone) => ({
      threshold: typeof milestone.threshold === "number" ? milestone.threshold : "",
      message: typeof milestone.message === "string" ? milestone.message : "",
      image: typeof milestone.image === "string" ? milestone.image : null,
      reaction: typeof milestone.reaction === "string" ? milestone.reaction : null,
      enabled: milestone.enabled === true,
    }));
    d.streak = streak;
    return d;
  };
  function lvState() {
    const gid = state.guild?.id || "none";
    if (!state.leveling || state.leveling.gid !== gid) {
      const defaults = lvDefaults();
      state.leveling = {
        gid, saved: clone(defaults), draft: clone(defaults), revision: 0,
        configured: false, loaded: false, loadError: false, analytics: null,
        leaderboard: null, dataMode: "text",
        tab: sessionStorage.getItem("leveling-tab") || "general", errors: [],
      };
      if (!LV_TABS.some((t) => t[0] === state.leveling.tab)) state.leveling.tab = "general";
    }
    return state.leveling;
  }
  async function loadLevelingData(gid) {
    const previous = state.leveling?.gid === gid ? state.leveling : null;
    const tab = previous?.tab || sessionStorage.getItem("leveling-tab") || "general";
    try {
      const prefix = `api/guild/${gid}/leveling`;
      const [settingsResponse, analyticsResponse, leaderboardResponse] = await Promise.all([
        api(`${prefix}/settings`),
        api(`${prefix}/analytics`),
        api(`${prefix}/leaderboard?mode=text&limit=20&offset=0`),
      ]);
      const snapshot = await readJson(settingsResponse, null);
      if (!settingsResponse.ok || !snapshot?.draft) throw Error("settings");
      const analytics = await readJson(analyticsResponse, null);
      const leaderboard = await readJson(leaderboardResponse, null);
      if (state.guild?.id !== gid) return false;
      const saved = lvSan(lvMerge(lvDefaults(), snapshot.draft));
      state.leveling = {
        gid, saved, draft: clone(saved), revision: snapshot.revision || 0,
        configured: Boolean(snapshot.configured), loaded: true, loadError: false,
        analytics: analyticsResponse.ok ? analytics : null,
        analyticsError: !analyticsResponse.ok,
        leaderboard: leaderboardResponse.ok ? leaderboard : null,
        leaderboardError: !leaderboardResponse.ok,
        dataMode: previous?.dataMode || "text", tab, errors: [],
      };
      return true;
    } catch (error) {
      if (error.message === "unauth") throw error;
      if (state.guild?.id !== gid) return false;
      const defaults = lvDefaults();
      state.leveling = {
        gid, saved: clone(defaults), draft: clone(defaults), revision: 0,
        configured: false, loaded: false, loadError: true,
        analytics: null, leaderboard: null, dataMode: "text", tab, errors: [],
      };
      return false;
    }
  }
  async function lvLoadBoard(mode, offset = 0, append = false) {
    const s = lvState();
    if (mode !== "text" && mode !== "voice") return;
    s.dataMode = mode;
    try {
      const response = await api(
        `api/guild/${s.gid}/leveling/leaderboard?mode=${mode}&limit=20&offset=${offset}`,
      );
      const data = await readJson(response, null);
      if (!response.ok || !data) throw Error("leaderboard");
      if (append && s.leaderboard?.mode === mode) {
        data.rows = [...(s.leaderboard.rows || []), ...(data.rows || [])];
      }
      s.leaderboard = data;
      s.leaderboardError = false;
    } catch (error) {
      if (error.message === "unauth") throw error;
      s.leaderboardError = true;
      if (!append) s.leaderboard = null;
    }
    if ($(".leveling-view")) lvRender();
  }
  const lvDirty = () => { const s = lvState(); return JSON.stringify(s.draft) !== JSON.stringify(s.saved); };
  function lvTouch() {
    const s = lvState();
    lvStatus();
    lvPreviews();
    drawLvCard();
  }
  function lvStatus() {
    const n = $(".leveling-state-pill");
    if (!n) return;
    const dirtyNow = lvDirty();
    n.textContent = lvState().loadError
      ? "تعذر تحميل إعدادات نظام المستويات من السيرفر"
      : dirtyNow ? "تعديلات غير محفوظة على السيرفر"
        : lvState().configured ? "الإعدادات متزامنة مع السيرفر" : "إعدادات السيرفر الافتراضية";
    n.classList.toggle("is-dirty", dirtyNow);
  }
  const lvGet = (path) => path.reduce((o, k) => o?.[k], lvState().draft);
  const lvSet = (path, v) => { path.slice(0, -1).reduce((o, k) => o[k], lvState().draft)[path[path.length - 1]] = v; lvTouch(); };
  let lvUid = 0;
  const lvId = () => `lv-${++lvUid}`;
  function lvField(label, control, hint) {
    const target = control.id ? control : control.querySelector?.("input,select,textarea") || control;
    const id = target.id || lvId();
    target.id = id;
    return el("div", { class: "leveling-field" }, el("label", { for: id, text: label }), control, hint ? el("small", { text: hint }) : null);
  }
  function lvSwitch(path, label, hint, onChange) {
    const id = lvId();
    const b = el("button", { class: "leveling-switch", id, type: "button", role: "switch", "aria-checked": String(Boolean(lvGet(path))), "aria-labelledby": `${id}-l` }, el("i"));
    b.addEventListener("click", () => {
      const v = !lvGet(path);
      lvSet(path, v);
      b.setAttribute("aria-checked", String(v));
      onChange?.(v);
    });
    return el("div", { class: "leveling-row" }, el("span", { class: "leveling-row-copy" }, el("b", { id: `${id}-l`, text: label }), hint ? el("small", { text: hint }) : null), b);
  }
  function lvNum(path, label, min, max, hint, slider) {
    const step = path[1] === "xpMultiplier" ? 0.1 : 1;
    const n = el("input", { type: "number", min, max, step, inputmode: "decimal", value: lvGet(path) });
    const cur = lvGet(path);
    const r = slider ? el("input", { type: "range", class: "leveling-range", min, max, step, value: typeof cur === "number" ? cur : min, "aria-label": `${label} (شريط)` }) : null;
    n.addEventListener("input", () => { lvSet(path, n.value === "" ? "" : Number(n.value)); if (r && n.value !== "") r.value = n.value; });
    r?.addEventListener("input", () => { n.value = r.value; lvSet(path, Number(r.value)); });
    return lvField(label, r ? el("div", { class: "leveling-numslider" }, n, r) : n, hint || `من ${min} إلى ${max}`);
  }
  function lvText(path, label, attrs = {}, hint) {
    const n = el("input", { type: "text", maxlength: 300, dir: "auto", value: lvGet(path), ...attrs });
    n.addEventListener("input", () => lvSet(path, n.value));
    return lvField(label, n, hint);
  }
  function lvArea(path, label, hint, onChange) {
    const n = el("textarea", { rows: 3, maxlength: 500, dir: "auto" });
    n.value = lvGet(path);
    n.addEventListener("input", () => {
      lvSet(path, n.value);
      onChange?.(n.value);
    });
    return lvField(label, n, hint);
  }
  function lvSelect(path, label, opts, hint, onChange) {
    const n = el("select", {});
    opts.forEach(([v, t]) => n.append(el("option", { value: v, text: t })));
    n.value = lvGet(path);
    n.addEventListener("change", () => { lvSet(path, n.value); onChange?.(); });
    return lvField(label, n, hint);
  }
  const lvAssignable = (r) => r && !r.managed && r.assignable !== false && String(r.id) !== String(state.guild?.id);
  const lvRoleOpts = (empty) => [["", empty], ...(state.meta?.roles || []).filter(lvAssignable).map((r) => [String(r.id), r.name])];
  const lvChanOpts = (empty) => [["", empty], ...(state.meta?.channels || []).map((c) => [String(c.id), `#${c.name}`])];
  function lvPicker(path, label, kind) {
    const list = kind === "role" ? state.meta?.roles : state.meta?.channels;
    if (!list?.length) return el("div", { class: "leveling-unavail" }, el("b", { text: label }), el("small", { text: "غير متاح: لم تصل قائمة الرتب أو القنوات من بيانات السيرفر." }));
    const wrap = el("div", { class: "leveling-field" });
    const render = () => {
      const cur = lvGet(path);
      const sel = el("select", { "aria-label": `إضافة إلى ${label}` });
      sel.append(el("option", { value: "", text: "اختر للإضافة" }));
      list.filter((x) => !cur.includes(String(x.id))).forEach((x) => sel.append(el("option", { value: String(x.id), text: kind === "role" ? x.name : `#${x.name}` })));
      sel.addEventListener("change", () => { if (sel.value) { lvSet(path, [...cur, sel.value]); render(); } });
      wrap.replaceChildren(el("label", { text: label }), sel, el("div", { class: "leveling-chips" }, ...cur.map((id) => {
        const item = list.find((x) => String(x.id) === id);
        return el("button", { type: "button", class: "leveling-chip", "aria-label": `إزالة ${item?.name || "عنصر غير معروف"}`, onClick: () => { lvSet(path, cur.filter((x) => x !== id)); render(); } }, `${kind === "role" ? "" : "#"}${item?.name || "غير معروف"}  ×`);
      })));
    };
    render();
    return wrap;
  }
  function lvUsers(path, label) {
    const wrap = el("div", { class: "leveling-field" });
    const render = () => {
      const cur = lvGet(path);
      const inp = el("input", { type: "text", inputmode: "numeric", maxlength: 20, placeholder: "معرّف العضو (أرقام)", "aria-label": label });
      const add = el("button", { type: "button", class: "leveling-btn", text: "إضافة", onClick: () => {
        const v = inp.value.trim();
        if (!/^\d{15,20}$/.test(v) || cur.includes(v)) { inp.setAttribute("aria-invalid", "true"); return; }
        lvSet(path, [...cur, v]); render();
      } });
      wrap.replaceChildren(el("label", { text: label }), el("div", { class: "leveling-inline" }, inp, add), el("div", { class: "leveling-chips" }, ...cur.map((id) => el("button", { type: "button", class: "leveling-chip", "aria-label": `إزالة ${id}`, onClick: () => { lvSet(path, cur.filter((x) => x !== id)); render(); } }, `${id}  ×`))));
    };
    render();
    return wrap;
  }
  function lvMultList(path, label, kind) {
    const wrap = el("div", { class: "leveling-field" });
    const render = () => {
      const cur = lvGet(path);
      const opts = kind === "role" ? lvRoleOpts("اختر رتبة") : lvChanOpts("اختر قناة");
      const rows = cur.map((r, i) => {
        const s = el("select", { "aria-label": kind === "role" ? "الرتبة" : "القناة" });
        opts.forEach(([v, t]) => s.append(el("option", { value: v, text: t })));
        s.value = r.id;
        s.addEventListener("change", () => { r.id = s.value; lvTouch(); });
        const m = el("input", { type: "number", min: 0, max: 10, step: 0.1, value: r.mult, "aria-label": "المضاعف" });
        m.addEventListener("input", () => { r.mult = m.value === "" ? "" : Number(m.value); lvTouch(); });
        return el("div", { class: "leveling-inline" }, s, m, el("button", { type: "button", class: "leveling-btn ghost", text: "حذف", onClick: () => { cur.splice(i, 1); lvTouch(); render(); } }));
      });
      const disabled = opts.length < 2;
      wrap.replaceChildren(el("label", { text: label }), ...rows, disabled ? el("small", { text: "غير متاح: قائمة الرتب أو القنوات غير محملة." }) : null,
        el("button", { type: "button", class: "leveling-btn", disabled, text: "إضافة مضاعف", onClick: () => { cur.push({ id: "", mult: 1.5 }); lvTouch(); render(); } }));
    };
    render();
    return wrap;
  }
  function lvBoosts() {
    const wrap = el("div", { class: "leveling-field" });
    const render = () => {
      const cur = lvGet(["points", "boosts"]);
      const rows = cur.map((b, i) => {
        const n = el("input", { type: "text", maxlength: 40, value: b.label, "aria-label": "اسم التعزيز", dir: "auto" });
        n.addEventListener("input", () => { b.label = n.value; lvTouch(); });
        const m = el("input", { type: "number", min: 1, max: 10, step: 0.1, value: b.mult, "aria-label": "المضاعف" });
        m.addEventListener("input", () => { b.mult = m.value === "" ? "" : Number(m.value); lvTouch(); });
        const h = el("input", { type: "number", min: 1, max: 168, step: 1, value: b.hours, "aria-label": "المدة بالساعات" });
        h.addEventListener("input", () => { b.hours = h.value === "" ? "" : Number(h.value); lvTouch(); });
        return el("div", { class: "leveling-inline" }, n, m, h, el("button", { type: "button", class: "leveling-btn ghost", text: "حذف", onClick: () => { cur.splice(i, 1); lvTouch(); render(); } }));
      });
      wrap.replaceChildren(el("label", { text: "تعزيزات مؤقتة (الاسم / المضاعف / الساعات)" }), ...rows,
        el("button", { type: "button", class: "leveling-btn", text: "إضافة تعزيز", onClick: () => { cur.push({ label: "تعزيز نهاية الأسبوع", mult: 2, hours: 24 }); lvTouch(); render(); } }));
    };
    render();
    return wrap;
  }
  const lvCard = (title, sub, ...kids) => el("section", { class: "leveling-card" }, el("header", {}, el("h3", { text: title }), sub ? el("small", { text: sub }) : null), ...kids);
  const lvGrid = (...k) => el("div", { class: "leveling-grid" }, ...k);
  const lvDemoTag = (t = "بيانات توضيحية") => el("span", { class: "leveling-demo", text: t });
  const lvFmt = (n) => new Intl.NumberFormat("ar-EG").format(n);
  const lvSeconds = (seconds) => lvMin(Math.floor((Number(seconds) || 0) / 60));
  function lvChart(rows, label) {
    const NS = "http://www.w3.org/2000/svg", W = 420, H = 150, pad = 18, mx = Math.max(...rows.map((r) => r[1]));
    const mk = (t, a) => { const n = document.createElementNS(NS, t); Object.entries(a).forEach(([k, v]) => n.setAttribute(k, v)); return n; };
    const svg = mk("svg", { viewBox: `0 0 ${W} ${H + 20}`, class: "leveling-chart", role: "img", "aria-label": label, preserveAspectRatio: "xMidYMid meet" });
    const gid = lvId();
    const defs = mk("defs", {}), lg = mk("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
    [["0", "#22d3ee", .55], ["1", "#6366f1", .04]].forEach(([o, c, op]) => lg.append(mk("stop", { offset: o, "stop-color": c, "stop-opacity": op })));
    const sg = mk("linearGradient", { id: `${gid}s`, x1: 0, y1: 0, x2: 1, y2: 0 });
    [["0", "#22d3ee"], ["0.55", "#6366f1"], ["1", "#a855f7"]].forEach(([o, c]) => sg.append(mk("stop", { offset: o, "stop-color": c })));
    defs.append(lg, sg); svg.append(defs);
    const pts = rows.map((r, i) => [pad + i * (W - 2 * pad) / (rows.length - 1), H - pad - (r[1] / mx) * (H - 2 * pad)]);
    const line = pts.map((q, i) => `${i ? "L" : "M"}${q[0].toFixed(1)} ${q[1].toFixed(1)}`).join(" ");
    svg.append(mk("path", { d: `${line} L${pts[pts.length - 1][0]} ${H - pad} L${pts[0][0]} ${H - pad} Z`, fill: `url(#${gid})` }), mk("path", { d: line, fill: "none", stroke: `url(#${gid}s)`, "stroke-width": 3, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    pts.forEach((q, i) => { svg.append(mk("circle", { cx: q[0], cy: q[1], r: 4, fill: "#030712", stroke: "#22d3ee", "stroke-width": 2 })); const t = mk("text", { x: q[0], y: H + 12, "text-anchor": "middle", fill: "#8aa4c8", "font-size": 11 }); t.textContent = rows[i][0]; svg.append(t); });
    return el("div", { class: "leveling-chart-wrap" }, svg);
  }
  function lvDist(rows = []) {
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) return el("p", { class: "leveling-unavail", text: "لا توجد بيانات توزيع مستويات محفوظة بعد." });
    const max = Math.max(1, ...list.map((row) => Number(row.members) || 0));
    return el("div", { class: "leveling-dist" }, ...list.map((row) => {
      const level = Number(row.level) || 0, members = Number(row.members) || 0;
      return el("div", { class: "leveling-dist-row" },
        el("span", { text: `المستوى ${lvFmt(level)}` }),
        el("div", { class: "leveling-meter", role: "img", "aria-label": `${lvFmt(members)} أعضاء في المستوى ${lvFmt(level)}` },
          el("i", { style: `width:${(members / max * 100).toFixed(1)}%` })),
        el("b", { text: lvFmt(members) }));
    }));
  }
  function lvIdentity() {
    const name = state.session?.username, av = state.session?.avatar;
    return el("div", { class: "leveling-avatar-status", role: "status" }, avatar(av, name || "?"),
      el("small", { text: name ? `المعاينة تستخدم حساب المسؤول المسجّل: ${name}${av ? "" : " (بدون صورة، تظهر صورة افتراضية)"}.` : "تتطلب معاينة البطاقة حساب مسؤول مصرحاً به في السيرفر." }));
  }
  function lvPreviews() {
    const d = lvState().draft, me = state.session?.username || "عضو تجريبي";
    const vars = {
      user: me, username: me, mention: `<@${state.session?.id || "123"}>`,
      level: "12", old_level: "11", xp: "1,250", required_xp: "2,000",
      progress: "62", rank: "3", total_members: "250", messages: "84",
      voice_time: "3.5 س", streak: "7", server: state.guild?.name || "السيرفر",
      period: "weekly", role: "Elite", passer: me, passed: "ياسر",
    };
    document.querySelectorAll(".leveling-msg-preview").forEach((box) => {
      const m = d.messages[box.dataset.msgKey];
      if (!m) return;
      const ch = (state.meta?.channels || []).find((c) => String(c.id) === m.channel);
      const text = String(m.tpl).replace(/\{(\w+)\}/g, (all, k) => (k in vars ? vars[k] : all));
      box.replaceChildren(
        el("small", { class: "leveling-mock-tag", text: "معاينة وهمية محلية، لا تُرسل إلى ديسكورد" }),
        el("div", { class: `leveling-discord${m.on ? "" : " is-off"}` }, avatar(state.session?.avatar, "P"),
          el("div", {}, el("div", { class: "leveling-discord-head" }, el("b", { text: "PRIME" }), el("span", { text: "BOT" }), el("small", { text: ch ? `#${ch.name}` : "القناة الحالية" })),
            el("p", { text: m.on ? text : "هذا الإشعار معطل حالياً." }))));
    });
    document.querySelectorAll(".leveling-prime-preview").forEach((box) => {
      const kind = box.dataset.preview;
      const cfg = d.prime || {};
      let title = "معاينة محلية";
      let body = "لن تُرسل هذه المعاينة إلى Discord.";
      if (kind === "levelup") {
        title = cfg.levelup?.embedTitle || "Level Up";
        body = String(d.messages?.levelup?.tpl || "")
          .replace(/\{(\w+)\}/g, (all, key) => key in vars ? vars[key] : all);
      } else if (kind === "top") {
        title = cfg.top?.embedTitle || "PRIME TOP";
        body = cfg.top?.embedMessage || "";
      } else if (kind.startsWith("periodic-")) {
        const period = kind.slice("periodic-".length);
        const item = cfg.periodic?.[period] || {};
        title = item.embedTitle || `PRIME ${period.toUpperCase()}`;
        body = String(item.message || "").replace(/\{(\w+)\}/g, (all, key) => (
          key === "message" ? all : key in vars ? vars[key] : all
        ));
      }
      box.replaceChildren(
        el("small", { class: "leveling-mock-tag", text: "معاينة محلية فقط — لم تُرسل" }),
        el("div", { class: "leveling-discord" },
          avatar(state.session?.avatar, "P"),
          el("div", {}, el("div", { class: "leveling-discord-head" },
            el("b", { text: "PRIME" }), el("span", { text: "BOT" })),
          el("strong", { text: title }), el("p", { text: body })),
        ),
      );
    });
  }
  function lvTabGeneral() {
    const st = state.stats || {};
    const analytics = lvState().analytics;
    const totals = analytics?.totals || {};
    const members = [st.guild?.members, state.meta?.guild?.members, state.guild?.members].find((v) => typeof v === "number" ? Number.isFinite(v) : (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))));
    const cell = (l, v) => el("div", { class: "leveling-stat" }, el("small", { text: l }), el("b", { text: v }));
    return el("div", { class: "leveling-stack" },
      lvCard("بيانات السيرفر الحقيقية", "من بيانات لوحة التحكم الحالية",
        lvGrid(cell("السيرفر", state.guild?.name || "غير متاح"), cell("الأعضاء", members != null ? lvFmt(Number(members)) : "غير متاح"),
          cell("القنوات", state.meta?.channels ? lvFmt(state.meta.channels.length) : "غير متاح"), cell("الرتب", state.meta?.roles ? lvFmt(state.meta.roles.length) : "غير متاح"))),
      lvCard("حالة الأنظمة", "إعدادات محفوظة فعلياً في قاعدة بيانات نظام المستويات",
        lvSwitch(["general", "enabled"], "تفعيل نظام المستويات"), lvSwitch(["general", "text"], "نقاط الرسائل"),
        lvSwitch(["voice", "enabled"], "نقاط الصوت"), lvSwitch(["general", "reaction"], "نقاط التفاعلات"), lvSwitch(["general", "streak"], "مكافأة التواصل اليومي")),
      lvCard("إعدادات سريعة", "تُحفظ مع إعدادات النظام الحقيقية",
        lvGrid(lvNum(["points", "minXp"], "أقل نقاط للرسالة", 0, 1000, "", true), lvNum(["points", "maxXp"], "أعلى نقاط للرسالة", 0, 1000, "", true), lvNum(["points", "cooldown"], "فترة التهدئة بالثواني", 0, 3600))),
      lvCard("نشاط المستويات الفعلي", analytics ? "إجماليات من سجلات XP الحالية؛ لا توجد بيانات يومية تاريخية." : "تعذر تحميل إحصاءات المستويات.",
        analytics ? lvGrid(
          cell("أعضاء لديهم نقاط", lvFmt(Number(totals.participants) || 0)),
          cell("نقاط الرسائل", lvFmt(Number(totals.text_xp) || 0)),
          cell("نقاط الصوت", lvFmt(Number(totals.voice_xp) || 0)),
          cell("الرسائل المسجلة", lvFmt(Number(totals.total_messages) || 0)),
          cell("وقت الصوت المسجل", lvSeconds(totals.total_voice_seconds)),
          cell("نشطون خلال 7 أيام", lvFmt(Number(totals.active_members_7d) || 0)),
        ) : el("p", { class: "leveling-unavail", text: "حاول إعادة تحميل إعدادات هذا السيرفر." })));
  }
  function lvTabPublic() {
    const pub = lvState().draft.public;
    const shareUrl = pub.slug ? `${location.origin}${urlMountPrefix}/lb/${encodeURIComponent(pub.slug)}` : "";
    const url = el("code", { class: "leveling-public-url", dir: "ltr", text: shareUrl || "أدخل معرّفاً لإنشاء رابط المشاركة" });
    const copy = el("button", {
      type: "button", class: "leveling-btn leveling-public-copy", disabled: !shareUrl || !pub.enabled, text: "نسخ الرابط",
      onClick: async () => {
        if (!pub.slug || !pub.enabled) return;
        try {
          await navigator.clipboard.writeText(
            `${location.origin}${urlMountPrefix}/lb/${encodeURIComponent(pub.slug)}`,
          );
          toast("تم نسخ رابط لوحة الترتيب.", "success");
        } catch (_) {
          toast("تعذر النسخ تلقائياً. انسخ الرابط الظاهر يدوياً.", "warn");
        }
      },
    });
    const slug = el("input", {
      type: "text", dir: "ltr", autocomplete: "off", spellcheck: "false",
      maxlength: "40", placeholder: "prime-arena", value: pub.slug,
      "aria-label": "معرّف رابط لوحة الترتيب",
      onInput: (event) => {
        const clean = event.currentTarget.value.toLowerCase().replace(/[^a-z0-9-]/g, "");
        if (clean !== event.currentTarget.value) event.currentTarget.value = clean;
        pub.slug = clean;
        url.textContent = clean ? `${location.origin}${urlMountPrefix}/lb/${clean}` : "أدخل معرّفاً لإنشاء رابط المشاركة";
        copy.disabled = !clean || !pub.enabled;
        lvTouch();
      },
    });
    return el("div", { class: "leveling-stack" },
      lvCard("لوحة PRIME العامة", "انشر ترتيب الأعضاء برابط مستقل قابل للمشاركة. لا يتطلب الزوار تسجيل الدخول.",
        lvSwitch(["public", "enabled"], "إتاحة لوحة الترتيب للعامة", "يمكن للزوار مشاهدة ترتيب النص أو الصوت والملخص العام."),
        el("label", { class: "leveling-field leveling-public-field" },
          el("span", { class: "leveling-label", text: "معرّف الرابط" }),
          slug,
          el("small", { class: "leveling-hint", text: "من 3 إلى 40 حرفاً: أحرف إنجليزية صغيرة وأرقام وشرطة مفردة بين الكلمات." })),
        el("div", { class: "leveling-public-share" },
          el("span", { class: "leveling-label", text: "رابط المشاركة" }), url, copy),
      el("p", { class: "leveling-public-note", text: "يعمل الرابط بعد تفعيل اللوحة وحفظ الإعدادات. تعطيل اللوحة لا يمسح المعرّف." })));
  }
  function lvTabPoints() {
    return el("div", { class: "leveling-stack" },
      lvCard("نقاط الرسائل", "هذه القيم تتحكم في نقاط الرسائل المباشرة", lvGrid(lvNum(["points", "minXp"], "أقل نقاط", 0, 1000, "", true), lvNum(["points", "maxXp"], "أعلى نقاط", 0, 1000, "", true), lvNum(["points", "cooldown"], "التهدئة (ثانية)", 0, 3600)),
        lvSwitch(["general", "text"], "تفعيل نقاط الرسائل")),
      lvCard("المضاعفات", "عام وللرتب والقنوات", lvNum(["points", "xpMultiplier"], "المضاعف العام (xp_multiplier)", 0, 10, "من 0 إلى 10، الافتراضي 1 (يقبل كسوراً)", true), lvMultList(["points", "roleMult"], "مضاعفات الرتب", "role"), lvMultList(["points", "chanMult"], "مضاعفات القنوات", "channel"), lvBoosts()),
      lvCard("القنوات المسموحة", "اتركها فارغة للسماح بكل القنوات. عند التحديد تُحتسب النقاط في هذه القنوات فقط", lvPicker(["points", "allowedChannels"], "قنوات مسموحة", "channel")),
      lvCard("القائمة السوداء", "عناصر لا تكسب نقاطاً (منفصلة عن القنوات المسموحة)", lvGrid(lvPicker(["points", "bl", "channels"], "قنوات محظورة", "channel"), lvPicker(["points", "bl", "roles"], "رتب محظورة", "role"), lvUsers(["points", "bl", "users"], "أعضاء محظورون"))));
  }
  function lvTabVoice() {
    return el("div", { class: "leveling-stack" },
      lvCard("نقاط الصوت", "إعدادات نظام الصوت الفعلية", lvSwitch(["voice", "enabled"], "تفعيل نقاط الصوت"), lvSwitch(["voice", "separate"], "مستويات صوتية منفصلة", "مستوى الصوت مستقل عن مستوى الرسائل"),
        lvGrid(lvNum(["voice", "xpPerMin"], "نقاط في الدقيقة", 0, 500), lvNum(["voice", "minMembers"], "أقل عدد أعضاء بالغرفة", 1, 99))),
      lvCard("الحماية", "", lvSwitch(["voice", "muteBlock"], "منع النقاط عند الكتم"), lvSwitch(["voice", "deafBlock"], "منع النقاط عند الصمم")),
      lvCard("تناقص العائد", "تقليل النقاط بعد مدة طويلة", lvSwitch(["voice", "dimEnabled"], "تفعيل تناقص العائد"), lvGrid(lvNum(["voice", "dimThreshold"], "العتبة بالدقائق", 0, 1440), lvNum(["voice", "dimRate"], "نسبة النقاط بعد العتبة %", 0, 100))));
  }
  function lvTabRewards() {
    const wrap = el("div", { class: "leveling-stack" });
    const body = el("div", { class: "leveling-reward-list" });
    const render = () => {
      const cur = lvGet(["rewards", "list"]);
      body.replaceChildren(...cur.map((r, i) => {
        const lv = el("input", { type: "number", min: 1, max: 1000, value: r.level, "aria-label": "المستوى" });
        lv.addEventListener("input", () => { r.level = lv.value === "" ? "" : Number(lv.value); lvTouch(); });
        const role = el("select", { "aria-label": "الرتبة" });
        lvRoleOpts("اختر رتبة").forEach(([v, t]) => role.append(el("option", { value: v, text: t })));
        role.value = r.role;
        role.addEventListener("change", () => { r.role = role.value; lvTouch(); });
        const type = el("select", { "aria-label": "النوع" });
        [["text", "نصي"], ["voice", "صوتي"]].forEach(([v, t]) => type.append(el("option", { value: v, text: t })));
        type.value = r.type;
        type.addEventListener("change", () => { r.type = type.value; lvTouch(); });
        return el("div", { class: "leveling-reward" }, el("span", { class: "leveling-lvl", text: `المستوى` }), lv, role, type, el("button", { type: "button", class: "leveling-btn ghost", text: "حذف", onClick: () => { cur.splice(i, 1); lvTouch(); render(); } }));
      }));
      if (!cur.length) body.append(el("p", { class: "leveling-empty", text: "لا توجد مكافآت بعد. أضف أول مكافأة." }));
    };
    render();
     wrap.append(lvCard("مكافآت المستويات", "تُحفظ وتُطبّق على ترقيات الرتب", lvSwitch(["rewards", "highestOnly"], "الاحتفاظ بأعلى رتبة فقط", "تُزال الرتب الأقل عند الوصول لرتبة أعلى"),
      body, el("button", { type: "button", class: "leveling-btn", text: "إضافة مكافأة", onClick: () => { lvGet(["rewards", "list"]).push({ level: 5, role: "", type: "text" }); lvTouch(); render(); } })));
    if (!state.meta?.roles?.length) wrap.append(el("p", { class: "leveling-unavail", text: "قائمة الرتب غير متاحة حالياً، لذلك لا يمكن اختيار رتبة للمكافأة." }));
    return wrap;
  }
  const lvBgOk = (v) => { if (!v) return true; try { const u = new URL(v); return u.protocol === "https:" && v.length <= 400; } catch (_) { return false; } };
  function lvTabCard() {
    const cv = el("img", { class: "leveling-canvas", role: "img", alt: "معاينة بطاقة المستوى الحقيقية" });
    const color = el("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(lvGet(["card", "color"])) ? lvGet(["card", "color"]) : "#38bdf8" });
    color.addEventListener("input", () => lvSet(["card", "color"], color.value));
    const particleColor = el("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(lvGet(["card", "particleColor"])) ? lvGet(["card", "particleColor"]) : (lvGet(["card", "color"]) || "#38bdf8") });
    particleColor.addEventListener("input", () => lvSet(["card", "particleColor"], particleColor.value));
    const particleColorControl = el("div", { class: "lv-particle-color-control" }, lvField("لون الجزيئات المخصص", particleColor),
      el("button", { type: "button", class: "leveling-btn", text: "استخدام لون التمييز", onClick: () => lvSet(["card", "particleColor"], "accent") }));
    const bg = lvText(["card", "bg"], "رابط الخلفية (HTTPS فقط)", { type: "text", dir: "ltr", placeholder: "https://" }, "اختياري. يتحقق الخادم من الرابط ويحمّله عند إنشاء المعاينة.");
    bg.querySelector("input").addEventListener("input", (e) => e.target.setAttribute("aria-invalid", String(!lvBgOk(e.target.value.trim()))));
    const gallery = el("div", { class: "lv-template-gallery", role: "group", "aria-label": "قوالب بطاقات الرتب" },
      ...Object.entries(LV_LAYOUTS).map(([key, value], index) => el("button", {
        type: "button", class: `lv-template${lvGet(["card", "layout"]) === key ? " is-active" : ""}`,
      "aria-pressed": String(lvGet(["card", "layout"]) === key), onClick: (event) => {
        lvSet(["card", "layout"], key);
        gallery.querySelectorAll(".lv-template").forEach((button) => {
          const selected = button === event.currentTarget;
          button.classList.toggle("is-active", selected);
          button.setAttribute("aria-pressed", String(selected));
        });
      },
      }, el("span", { class: `lv-template-art lv-art-${key}`, "aria-hidden": "true" }, el("i"), el("b", { text: index < 4 ? "PR" : "P" }), el("em"), el("small", { text: "LEVEL 28" })),
      el("span", { class: "lv-template-copy" }, el("b", { text: value[2] }), el("small", { text: `${value[0]} × ${value[1]}` })))));
    const presetName = el("input", { type: "text", maxlength: 40, placeholder: "اسم الإعداد المحفوظ", "aria-label": "اسم الإعداد المحفوظ" });
    const presets = Array.isArray(lvGet(["card", "presets"])) ? lvGet(["card", "presets"]) : [];
    const presetList = presets.length ? el("div", { class: "lv-preset-list" }, ...presets.map((preset) => el("div", { class: "lv-preset-row" },
      el("button", { type: "button", class: "lv-preset-apply", text: preset.name, title: "تطبيق الإعداد", onClick: () => {
        Object.assign(lvState().draft.card, lvCardStyleSnapshot(lvMerge(lvDefaults().card, preset))); lvRender();
      } }),
      el("button", { type: "button", class: "lv-preset-delete", text: "حذف", "aria-label": `حذف الإعداد ${preset.name}`, onClick: () => {
        lvSet(["card", "presets"], lvState().draft.card.presets.filter((item) => item.id !== preset.id)); lvRender();
      } })))) : el("p", { class: "leveling-empty", text: "لا توجد إعدادات محفوظة بعد. احفظ توليفة مناسبة لتطبيقها لاحقاً." });
    const savePreset = el("button", { type: "button", class: "leveling-btn", text: "حفظ كإعداد", onClick: () => {
      const name = presetName.value.trim();
      if (!name) { presetName.focus(); toast("اكتب اسماً للإعداد المحفوظ", "warn"); return; }
      if (lvState().draft.card.presets.length >= 8) { toast("يمكن حفظ 8 إعدادات كحد أقصى. احذف إعداداً قبل إضافة آخر.", "warn"); return; }
      lvState().draft.card.presets.push({ id: `card-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`, name, ...lvCardStyleSnapshot(lvState().draft.card) });
      lvTouch(); lvRender();
    } });
    const gif = el("img", { class: "lv-gif-image", alt: "اختبار متحرك لانتقال المستوى", hidden: true });
    const gifStatus = el("p", { class: "lv-gif-status", role: "status", "aria-live": "polite", text: "اختبار مستقل لتأثير الإضاءة المتحرك؛ لا يحفظ الإعدادات ولا ينفّذ عمليات Discord." });
    const gifButton = el("button", { type: "button", class: "leveling-btn lv-gif-button", text: "اختبار ترقية متحرك · GIF", onClick: async () => {
      gifButton.disabled = true; gifStatus.textContent = "يجري إنشاء حركة الترقية من معاينة السيرفر…";
      try {
        const params = lvBuildCardPreviewParams(lvState().draft.card, "gif");
        const response = await api(`api/guild/${state.guild.id}/leveling/card-preview?${params.toString()}`);
        if (!response.ok) { const problem = await readJson(response, {}); throw Error(problem.error || "preview"); }
        const next = URL.createObjectURL(await response.blob()), previous = gif.dataset.objectUrl;
        gif.src = next; gif.dataset.objectUrl = next; gif.hidden = false;
        if (previous) URL.revokeObjectURL(previous);
        gifStatus.textContent = "هذه حركة ترقية فعلية من مولّد PRIME؛ المعاينة لا تحفظ ولا تغيّر إعدادات السيرفر.";
      } catch (error) {
        gifStatus.textContent = error.message === "unauth" ? "انتهت الجلسة؛ سجّل الدخول مجدداً." : "تعذر إنشاء حركة الترقية. أعد المحاولة بعد التحقق من إعدادات البطاقة.";
      } finally { gifButton.disabled = false; }
    } });
    return el("div", { class: "lv-card-editor" },
      lvCard("معرض القوالب", "ثمانية تخطيطات عملية؛ اختر القالب ثم اضبط تفاصيله أدناه.", gallery),
      el("div", { class: "leveling-split" },
        el("div", { class: "leveling-stack" },
          lvCard("الهوية والتكوين", "تُحفظ هذه القيم ضمن إعدادات المستوى الحالية.",
            lvGrid(lvSelect(["card", "particles"], "أثر الجزيئات", Object.entries(LV_PARTS)), lvSelect(["card", "barStyle"], "أسلوب شريط التقدم", [["gradient", "متدرّج"], ["solid", "مصمت"], ["segmented", "مقسّم"], ["neon", "مضيء"]])),
            el("div", { class: "lv-color-row" }, lvField("لون التمييز", color), particleColorControl),
            lvSelect(["card", "frame"], "إطار الرتبة والمستوى", [["auto", "تلقائي حسب المستوى"], ["none", "بلا إطار"], ["bronze", "برونزي"], ["silver", "فضي"], ["gold", "ذهبي"], ["diamond", "ماسي"]]),
            lvSwitch(["card", "showStats"], "عرض الإحصاءات"), lvSwitch(["card", "animated"], "لمعان شريط التقدم"), lvIdentity(), bg,
            el("p", { class: "leveling-bg-status", role: "status", "aria-live": "polite" }),
            lvGrid(lvNum(["card", "glowStrength"], "قوة التوهج", 0, 100, "0 هادئ · 100 قوي", true), lvNum(["card", "particleDensity"], "كثافة الجزيئات", 0, 100, "", true), lvNum(["card", "bgOverlay"], "طبقة تعتيم الخلفية", 0, 85, "تحافظ على وضوح النص.", true), lvNum(["card", "bgBlur"], "تمويه الخلفية", 0, 18, "بالبكسل.", true))),
          lvCard("الإحصاءات المعروضة", "تحكم مستقل بكل معلومة تظهر على البطاقة.", lvSwitch(["card", "stats", "messages"], "الرسائل"), lvSwitch(["card", "stats", "voice"], "الوقت الصوتي"), lvSwitch(["card", "stats", "streak"], "سلسلة النشاط"), lvSwitch(["card", "stats", "serverRank"], "ترتيب السيرفر")),
          lvCard("حركة الترقية", "تأثيرات المستوى على البطاقة، لا عمليات Discord.", lvSwitch(["card", "animationEnabled"], "تفعيل حركة الترقية"),
            lvGrid(lvSelect(["card", "animationStyle"], "نمط الحركة", [["beam", "شعاع"], ["aurora", "شفق"], ["burst", "اندفاع"]]), lvNum(["card", "animationIntensity"], "شدّة الحركة", 0, 100, "", true))),
          lvCard("إعدادات قابلة لإعادة الاستخدام", "تُحفظ ضمن المسودة وتُرسل مع الحفظ المعتاد.", el("div", { class: "lv-preset-create" }, presetName, savePreset), presetList)),
        el("div", { class: "leveling-stack lv-preview-column" },
           lvCard("معاينة بطاقة الرتبة", "تتحدّث مع الإعدادات وبيانات الحساب الحالية؛ وتعرض GIF عند استخدام خلفية متحركة.", el("div", { class: "leveling-canvas-wrap" }, cv), el("p", { class: "leveling-render-status", role: "status", "aria-live": "polite", text: "جارٍ تحميل بطاقة PRIME…" })),
          lvCard("اختبار حركة المستوى", "معاينة GIF مستقلة تعرض تأثير الإضاءة المتحرك على بطاقة المستوى؛ لا تحفظ الإعداد.", gifButton, el("div", { class: "lv-gif-wrap" }, gif), gifStatus))));
  }
  let lvBgImg = { url: "", img: null, failed: false };
  function lvBgStatus() {
    const n = $(".leveling-bg-status");
    if (!n) return;
    const u = String(lvState().draft.card.bg || "").trim();
    let t = "", warn = false;
    if (!u) t = "لا توجد خلفية مخصصة.";
    else if (!lvBgOk(u)) { t = "تحذير: الرابط غير صالح، يلزم رابط https://."; warn = true; }
    else t = "يحمّل الخادم الخلفية بعد التحقق من أن عنوانها عام وآمن.";
    n.textContent = t;
    n.classList.toggle("is-warn", warn);
  }
  // Artwork is generated offline by the original Phase 5 Pillow renderer.
  // Embedded base/mask assets avoid adding a preview endpoint in Phase 7.
  const LV_GEOMETRY = {
    vertical: { avatar: [164, 110, 232], name: [280, 372, 30, 475, "center"], handle: [280, 417, 465], bar: [56, 579, 448, 12], stats: [[30, 657, 242, 94], [288, 657, 242, 94], [30, 763, 242, 94]] },
    stats: { avatar: [45, 85, 190], name: [276, 75, 32, 470, "left"], handle: [278, 122, 430], bar: [278, 252, 685, 14], stats: [[32, 302, 302, 94], [349, 302, 302, 94], [666, 302, 302, 94]] },
    minimal: { avatar: [36, 48, 128], name: [195, 33, 26, 445, "left"], bar: [196, 164, 655, 10] },
    ring: { avatar: [185, 94, 250], name: [310, 401, 30, 530, "center"], handle: [310, 447, 530] },
    classic: { avatar: [58, 85, 165], name: [260, 89, 31, 490, "left"], handle: [261, 135, 455], bar: [262, 251, 675, 17] },
  };
  const lvImageCache = new Map();
  let lvDrawToken = 0, lvAnimation = 0;
  let lvAvatarImage = { url: "", image: null, failed: false };
  function lvAsset(cv, key) {
    const raw = getComputedStyle(cv).getPropertyValue(`--prime-card-${key}`).trim();
    const match = /^url\(["']?(data:image\/png;base64,[A-Za-z0-9+/=]+)["']?\)$/.exec(raw);
    if (!match) return Promise.reject(new Error(`Missing Phase 5 artwork: ${key}`));
    if (!lvImageCache.has(key)) {
      const promise = new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error("Unable to decode Phase 5 artwork"));
        image.src = match[1];
      });
      lvImageCache.set(key, promise);
      // Bound decoded-image memory on phones; the embedded URLs stay in CSS.
      while (lvImageCache.size > 12) lvImageCache.delete(lvImageCache.keys().next().value);
    }
    return lvImageCache.get(key);
  }
  function lvPixels(image, width, height) {
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, width, height);
    return context.getImageData(0, 0, width, height);
  }
  function lvLoadIdentity() {
    const raw = state.session?.avatar;
    let url = "";
    try {
      const parsed = new URL(raw);
      if (parsed.protocol === "https:" && ["cdn.discordapp.com", "media.discordapp.net", "cdn.discordapp.net"].includes(parsed.hostname)) url = parsed.href;
    } catch (_) { /* A missing Discord avatar is reported below. */ }
    if (url && lvAvatarImage.url !== url) {
      const image = new Image();
      lvAvatarImage = { url, image: null, failed: false };
      image.crossOrigin = "anonymous";
      image.referrerPolicy = "no-referrer";
      image.onload = () => { if (lvAvatarImage.url === url) { lvAvatarImage.image = image; drawLvCard(); } };
      image.onerror = () => { if (lvAvatarImage.url === url) { lvAvatarImage.failed = true; drawLvCard(); } };
      image.src = url;
    }
    const status = $(".leveling-avatar-status small");
    if (status) {
      const name = state.session?.username || "الحساب";
      status.textContent = url && lvAvatarImage.image ? `تُستخدم صورة Discord الفعلية لحساب ${name}.`
        : url && !lvAvatarImage.failed ? "جارٍ تحميل صورة حساب Discord الفعلية…"
        : `صورة Discord غير متاحة لحساب ${name}؛ تُعرض صورة مولد Phase 5 الافتراضية، وليست صورة عضو وهمي.`;
    }
    return url && lvAvatarImage.url === url ? lvAvatarImage.image : null;
  }
  async function drawLvCardLegacy() {
    cancelAnimationFrame(lvAnimation);
    const cv = $(".leveling-canvas");
    if (!cv) return;
    const token = ++lvDrawToken, d = { ...lvState().draft.card };
    const layout = LV_LAYOUTS[d.layout] ? d.layout : "vertical";
    const mode = LV_PARTS[d.particles] ? d.particles : "none";
    const [W, H] = LV_LAYOUTS[layout], geometry = LV_GEOMETRY[layout];
    const color = /^#[0-9a-f]{6}$/i.test(d.color) ? d.color : "#38bdf8";
    const rgb = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
    const avatarImage = lvLoadIdentity();
    const bgUrl = String(d.bg || "").trim();
    if (bgUrl && lvBgOk(bgUrl) && lvBgImg.url !== bgUrl) {
      const image = new Image();
      lvBgImg = { url: bgUrl, img: null, failed: false };
      image.referrerPolicy = "no-referrer";
      image.onload = () => { if (lvBgImg.url === bgUrl) { lvBgImg.img = image; drawLvCard(); } };
      image.onerror = () => { if (lvBgImg.url === bgUrl) { lvBgImg.failed = true; lvBgStatus(); } };
      image.src = bgUrl;
    }
    lvBgStatus();
    cv.dataset.ready = "false";
    try {
      const hidden = !d.showStats && geometry.stats;
      const sources = await Promise.all([
        lvAsset(cv, `${layout}-${mode}-base`), lvAsset(cv, `${layout}-${mode}-mask`),
        hidden ? lvAsset(cv, `${layout}-hidden-base`) : null,
        hidden ? lvAsset(cv, `${layout}-hidden-mask`) : null,
        bgUrl && lvBgImg.img && lvBgImg.url === bgUrl ? lvAsset(cv, `${layout}-background`) : null,
      ]);
      if (token !== lvDrawToken || !cv.isConnected) return;
      const base = lvPixels(sources[0], W, H), mask = lvPixels(sources[1], W, H);
      if (hidden) {
        const bare = lvPixels(sources[2], W, H), bareMask = lvPixels(sources[3], W, H);
        geometry.stats.forEach(([x, y, w, h]) => {
          for (let row = y; row < y + h; row++) {
            const start = (row * W + x) * 4, end = start + w * 4;
            base.data.set(bare.data.subarray(start, end), start);
            mask.data.set(bareMask.data.subarray(start, end), start);
          }
        });
      }
      // Reconstruct the original generator's accent pixels, not a new card design.
      for (let i = 0; i < base.data.length; i += 4) {
        for (let channel = 0; channel < 3; channel++) base.data[i + channel] += mask.data[i + channel] * rgb[channel] / 255;
      }
      const scene = document.createElement("canvas");
      scene.width = W; scene.height = H;
      const c = scene.getContext("2d");
      c.putImageData(base, 0, 0);
      if (sources[4]) {
        const matte = lvPixels(sources[4], W, H);
        for (let i = 0; i < matte.data.length; i += 4) {
          matte.data[i + 3] = Math.max(matte.data[i], matte.data[i + 1], matte.data[i + 2]);
          matte.data[i] = matte.data[i + 1] = matte.data[i + 2] = 255;
        }
        const alpha = document.createElement("canvas"), photo = document.createElement("canvas");
        alpha.width = photo.width = W; alpha.height = photo.height = H;
        alpha.getContext("2d").putImageData(matte, 0, 0);
        const pc = photo.getContext("2d"), image = lvBgImg.img;
        const scale = Math.max(W / image.width, H / image.height);
        pc.filter = "blur(8px) saturate(0.65)";
        pc.drawImage(image, (W - image.width * scale) / 2, (H - image.height * scale) / 2, image.width * scale, image.height * scale);
        pc.filter = "none"; pc.globalCompositeOperation = "destination-in"; pc.drawImage(alpha, 0, 0);
        c.drawImage(photo, 0, 0);
      }
      if (avatarImage) {
        const [x, y, diameter] = geometry.avatar;
        const crop = Math.min(avatarImage.width, avatarImage.height);
        c.save(); c.beginPath(); c.arc(x + diameter / 2, y + diameter / 2, diameter / 2, 0, Math.PI * 2); c.clip();
        c.drawImage(avatarImage, (avatarImage.width - crop) / 2, (avatarImage.height - crop) / 2, crop, crop, x, y, diameter, diameter); c.restore();
      }
      const [x, y, size, maxWidth, align] = geometry.name;
      c.direction = "ltr"; c.textAlign = align; c.textBaseline = "top";
      c.font = `700 ${size}px "DejaVu Sans", system-ui, sans-serif`; c.fillStyle = "#f8f5fc";
      c.fillText(String(state.session?.username || "حساب غير متاح").replace(/[\r\n]/g, " ").slice(0, 80), x, y, maxWidth);
      if (geometry.handle) {
        c.font = '15px "DejaVu Sans", system-ui, sans-serif'; c.fillStyle = "#aaa5ba";
        c.fillText(`@${String(state.session?.username || "member").replace(/[\r\n]/g, " ").slice(0, 80)}`, geometry.handle[0], geometry.handle[1], geometry.handle[2]);
      }
      cv.width = W; cv.height = H;
      const context = cv.getContext("2d");
      let lastFrame = -Infinity;
      const animate = Boolean(d.animated && geometry.bar && !matchMedia("(prefers-reduced-motion: reduce)").matches);
      const paint = (time) => {
        if (token !== lvDrawToken || !cv.isConnected) return;
        if (time - lastFrame >= 40) {
          context.clearRect(0, 0, W, H); context.drawImage(scene, 0, 0); lastFrame = time;
          if (animate) {
            const [bx, by, bw, bh] = geometry.bar, fill = bw * 640 / 1420;
            const offset = ((time % 2200) / 2200) * (fill + 70) - 35;
            context.save(); context.beginPath(); context.roundRect(bx, by, fill, bh, bh / 2); context.clip();
            context.fillStyle = "#fff0fb55"; context.beginPath();
            context.moveTo(bx + offset, by); context.lineTo(bx + offset + 22, by);
            context.lineTo(bx + offset + 5, by + bh); context.lineTo(bx + offset - 17, by + bh); context.fill(); context.restore();
          }
        }
        if (animate) lvAnimation = requestAnimationFrame(paint);
      };
      paint(performance.now());
      cv.dataset.renderer = "phase5"; cv.dataset.layout = layout; cv.dataset.particles = mode;
      cv.dataset.statistics = String(d.showStats); cv.dataset.ready = "true";
      const status = $(".leveling-render-status");
      if (status) status.textContent = "تم تحميل تصميم مولد PRIME Phase 5 الأصلي. الهوية من Discord، والنقاط والترتيب بيانات توضيحية.";
    } catch (error) {
      if (token !== lvDrawToken || !cv.isConnected) return;
      const status = $(".leveling-render-status");
      if (status) status.textContent = "تعذر تحميل أصول بطاقة Phase 5. حدّث الصفحة وحاول مجدداً؛ لم يُستخدم تصميم بديل.";
      cv.dataset.ready = "error";
    }
  }
  let lvDrawTimer = null, lvCardPreviewUrl = "";
  function lvBuildCardPreviewParams(card, format) {
    const params = new URLSearchParams({
      layout: card.layout, particles: card.particles, color: card.color, bg: String(card.bg || "").trim(),
      animated: String(Boolean(card.animated)), showStats: String(Boolean(card.showStats)),
      glowStrength: String(card.glowStrength ?? 54), particleDensity: String(card.particleDensity ?? 46),
      particleColor: String(card.particleColor || "accent"), barStyle: String(card.barStyle || "gradient"),
      frame: String(card.frame || "auto"), bgOverlay: String(card.bgOverlay ?? 28), bgBlur: String(card.bgBlur ?? 4),
      animationEnabled: String(Boolean(card.animationEnabled)), animationStyle: String(card.animationStyle || "beam"),
      animationIntensity: String(card.animationIntensity ?? 62),
      showMessages: String(Boolean(card.stats?.messages)), showVoice: String(Boolean(card.stats?.voice)),
      showStreak: String(Boolean(card.stats?.streak)), showServerRank: String(Boolean(card.stats?.serverRank)),
    });
    if (format) params.set("format", format);
    return params;
  }
  function drawLvCard() {
    clearTimeout(lvDrawTimer);
    lvDrawTimer = setTimeout(async () => {
      const image = $(".leveling-canvas");
      if (!image) return;
      const token = ++lvDrawToken, card = { ...lvState().draft.card };
      const status = $(".leveling-render-status");
      image.dataset.ready = "false";
      if (status) status.textContent = "ينشئ الخادم معاينة بطاقة بحسابك ونقاطك الحقيقية…";
      lvBgStatus();
      try {
        const params = lvBuildCardPreviewParams(card, "auto");
        const response = await api(
          `api/guild/${state.guild.id}/leveling/card-preview?${params.toString()}`,
        );
        if (!response.ok) {
          const problem = await readJson(response, {});
          throw Error(problem.error || "preview");
        }
        const nextUrl = URL.createObjectURL(await response.blob());
        if (token !== lvDrawToken || !image.isConnected) {
          URL.revokeObjectURL(nextUrl);
          return;
        }
        const previousUrl = lvCardPreviewUrl;
        lvCardPreviewUrl = nextUrl;
        image.src = nextUrl;
        image.dataset.ready = "true";
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        if (status) status.textContent = "المعاينة تعرض بيانات XP والترتيب الحقيقية للحساب المسجّل.";
      } catch (error) {
        if (token !== lvDrawToken || !image.isConnected) return;
        image.dataset.ready = "error";
        if (status) status.textContent = error.message === "unauth"
          ? "انتهت الجلسة؛ سجّل الدخول مجدداً."
          : "تعذر إنشاء المعاينة الحقيقية. تحقق من إعدادات البطاقة ثم أعد المحاولة.";
      }
    }, 300);
  }
  const LV_TEMPLATE_VARS = [
    "{user}", "{username}", "{mention}", "{level}", "{old_level}", "{xp}",
    "{required_xp}", "{progress}", "{rank}", "{total_members}", "{messages}",
    "{voice_time}", "{streak}", "{server}", "{period}", "{role}", "{passer}", "{passed}",
  ];
  const LV_DEFAULT_MESSAGE_TEXTS = {
    levelup: "مبروك {mention}! وصلت إلى المستوى {level} في {server}.",
    milestone: "{mention} حقق إنجازاً جديداً عند المستوى {level}.",
    overtake: "{mention} تجاوز {passed} وأصبح في المركز {rank}.",
    role_promotion: "مبروك {mention}! حصلت على رتبة {role}.",
  };
  const lvPrimeMessageConfig = (key) => {
    const s = lvState();
    s.draft.prime ||= {};
    const cfg = key === "levelup"
      ? (s.draft.prime.levelup ||= {})
      : ((s.draft.prime.notifications ||= {})[key] ||= {});
    const fallback = LV_DEFAULT_MESSAGE_TEXTS[key] || "{message}";
    cfg.message = typeof cfg.message === "string" ? cfg.message : fallback;
    cfg.messages = Array.isArray(cfg.messages) && cfg.messages.length
      ? cfg.messages : [{ id: "default", name: "الافتراضي", template: cfg.message }];
    cfg.activeMessageId = cfg.activeMessageId || cfg.messages[0]?.id || "default";
    return cfg;
  };
  const lvSyncActiveMessage = (key) => {
    const cfg = lvPrimeMessageConfig(key);
    let active = cfg.messages.find((item) => String(item.id) === String(cfg.activeMessageId));
    if (!active) {
      active = cfg.messages[0];
      cfg.activeMessageId = active.id;
    }
    cfg.message = String(active.template || "");
    const s = lvState();
    if (!s.draft.messages[key]) s.draft.messages[key] = { on: false, channel: "", tpl: "" };
    s.draft.messages[key].tpl = cfg.message;
  };
  const lvMessageSet = (key, field, value) => {
    const s = lvState();
    if (!s.draft.messages[key]) s.draft.messages[key] = { on: false, channel: "", tpl: "" };
    s.draft.messages[key][field] = value;
    const cfg = lvPrimeMessageConfig(key);
    if (field === "on") {
      if (key === "levelup") cfg.sendNotification = Boolean(value);
      else cfg.enabled = Boolean(value);
    }
    if (field === "channel") cfg.channel = String(value || "");
    if (field === "tpl") {
      cfg.message = String(value || "");
      const active = cfg.messages.find((item) => String(item.id) === String(cfg.activeMessageId));
      if (active) active.template = cfg.message;
    }
    lvTouch();
  };
  const lvMessageManager = (key) => {
    const cfg = lvPrimeMessageConfig(key);
    const currentId = String(cfg.activeMessageId || cfg.messages[0]?.id || "default");
    const select = el("select", {});
    cfg.messages.forEach((item) => {
      select.append(el("option", { value: String(item.id), text: String(item.name || item.id) }));
    });
    select.value = currentId;
    select.addEventListener("change", () => {
      cfg.activeMessageId = select.value;
      lvSyncActiveMessage(key);
      lvTouch();
    });
    const button = (label, handler, title) => el("button", {
      type: "button", class: "btn leveling-msg-manager-btn", text: label, title,
      onClick: handler,
    });
    return el("div", { class: "leveling-message-manager" },
      el("div", { class: "leveling-manager-row" },
        el("strong", { text: "مدير الرسائل" }),
        select,
        button("＋ إضافة", () => {
          const name = window.prompt("اسم الرسالة الجديدة:");
          if (!name?.trim()) return;
          const template = window.prompt("نص الرسالة:", cfg.message || LV_DEFAULT_MESSAGE_TEXTS[key]);
          if (template === null || !template.trim()) return;
          if (cfg.messages.length >= 20) {
            toast("الحد الأقصى 20 رسالة محفوظة.", "warn");
            return;
          }
          const id = `m-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
          cfg.messages.push({ id, name: name.trim().slice(0, 80), template: template.trim().slice(0, 500) });
          cfg.activeMessageId = id;
          lvSyncActiveMessage(key);
          lvTouch();
        }, "إضافة رسالة محفوظة"),
        button("✎ تعديل الاسم", () => {
          const active = cfg.messages.find((item) => String(item.id) === String(select.value));
          if (!active) return;
          const name = window.prompt("الاسم الجديد:", active.name || "");
          if (!name?.trim()) return;
          active.name = name.trim().slice(0, 80);
          lvTouch();
        }, "تعديل اسم الرسالة الحالية"),
        button("🗑 حذف", () => {
          if (select.value === "default") {
            toast("لا يمكن حذف الرسالة الافتراضية.", "warn");
            return;
          }
          if (!window.confirm("حذف الرسالة المحفوظة؟")) return;
          cfg.messages = cfg.messages.filter((item) => String(item.id) !== String(select.value));
          cfg.activeMessageId = cfg.messages[0]?.id || "default";
          lvSyncActiveMessage(key);
          lvTouch();
        }, "حذف الرسالة الحالية"),
        button("↩ الافتراضي", () => {
          const defaultText = LV_DEFAULT_MESSAGE_TEXTS[key];
          let active = cfg.messages.find((item) => String(item.id) === "default");
          if (!active) {
            active = { id: "default", name: "الافتراضي", template: defaultText };
            cfg.messages.unshift(active);
          }
          active.template = defaultText;
          cfg.activeMessageId = "default";
          lvSyncActiveMessage(key);
          lvTouch();
        }, "استعادة الرسالة الافتراضية"),
      ),
    );
  };
  function lvTabMessages() {
    const mk = (key, title, vars) => {
      const primePath = key === "levelup"
        ? ["prime", "levelup"]
        : ["prime", "notifications", key];
      const advanced = key === "levelup"
        ? null
        : el("div", { class: "leveling-stack" },
            lvSwitch([...primePath, "sendAsEmbed"], "إرسال كـ Embed"),
            lvSwitch([...primePath, "mentionUser"], "منشن العضو"),
            lvSelect([...primePath, "mentionRole"], "منشن رتبة إضافية", lvRoleOpts("بدون رتبة")),
            lvGrid(
              lvText([...primePath, "embedTitle"], "عنوان الإمبد"),
              lvText([...primePath, "embedColor"], "لون الإمبد", { type: "color" }),
            ),
            lvArea([...primePath, "embedDescription"], "وصف الإمبد", "يمكن استخدام {message} وباقي متغيرات PRIME"),
            lvGrid(
              lvText([...primePath, "embedFooter"], "تذييل الإمبد"),
              lvText([...primePath, "embedImage"], "رابط صورة الإمبد", { dir: "ltr", placeholder: "https://..." }),
            ),
            lvSwitch([...primePath, "timestamp"], "إضافة توقيت"),
          );
      return lvCard(
        title,
        `يدعم: ${vars}`,
        el("div", { class: "leveling-message-toolbar" },
          lvSwitch(["messages", key, "on"], "تفعيل الإشعار", "يتزامن مع إعداد PRIME الفعلي", (value) => lvMessageSet(key, "on", value)),
        ),
        lvMessageManager(key),
        lvSelect(["messages", key, "channel"], "القناة", lvChanOpts("القناة الحالية / غير محددة"),
          state.meta?.channels?.length ? "" : "غير متاح: قائمة القنوات لم تصل.", () => lvMessageSet(key, "channel", lvGet(["messages", key, "channel"]))),
        lvArea(["messages", key, "tpl"], "القالب", "الحد الأقصى 500 حرف", (value) => lvMessageSet(key, "tpl", value)),
        el("div", { class: "leveling-template-vars" }, ...LV_TEMPLATE_VARS.map((token) =>
          el("button", { type: "button", class: "leveling-chip", text: token, title: "نسخ المتغير",
            onClick: async () => {
              await navigator.clipboard?.writeText(token);
              toast(`تم نسخ ${token}`, "info", 1600);
            } }))),
        advanced,
        el("div", { class: "leveling-msg-preview", "data-msg-key": key }),
      );
    };
    return el(
      "div",
      { class: "leveling-stack" },
      lvDemoTag("الإعدادات هنا تُكتب إلى PRIME runtime وتبقى متوافقة مع الحقول القديمة"),
      mk("levelup", "إشعار رفع المستوى", "{user} {mention} {level} {old_level} {xp} {server}"),
      mk("milestone", "إشعار الإنجاز", "{user} {level} {xp} {progress}"),
      mk("overtake", "إشعار التجاوز", "{passer} {passed} {rank} {user}"),
      mk("role_promotion", "إشعار ترقية الرتبة", "{mention} {role} {level} {old_level}"),
    );
  }
  function lvTabPrime() {
    const periodicPanel = (period, title, defaults) => {
      const key = ["prime", "periodic", period];
      return lvCard(title, "إعداد مستقل؛ المدة تحسب من سجل XP الجديد فقط ولا تصفّر XP الدائم.",
        lvSwitch([...key, "enabled"], "تفعيل النشر الدوري"),
        lvGrid(
          lvSelect([...key, "channel"], "قناة النشر", lvChanOpts("اختر قناة")),
          lvSelect([...key, "rewardRole"], "رتبة الفائزين", lvRoleOpts("بدون مكافأة")),
          lvSelect([...key, "mode"], "مصدر XP للفترة", [["both", "النص + الصوت"], ["text", "النص فقط"], ["voice", "الصوت فقط"]]),
          lvText([...key, "time"], "وقت النشر", { type: "time" }),
          lvText([...key, "timezone"], "المنطقة الزمنية", { dir: "ltr", placeholder: "UTC" }),
          lvNum([...key, "winners"], "عدد الفائزين", 1, 20),
        ),
        period === "weekly" ? lvNum([...key, "weekday"], "يوم النشر (0 الاثنين، 6 الأحد)", 0, 6) : null,
        period === "monthly" ? lvNum([...key, "dayOfMonth"], "يوم النشر الشهري", 1, 28) : null,
        lvArea([...key, "message"], "رسالة الفائزين", "متغيرات: {user} {mention} {username} {xp} {rank} {period}"),
        lvSwitch([...key, "embed"], "إرسال كـ Embed"),
        lvGrid(
          lvText([...key, "embedTitle"], "عنوان الإمبد"),
          lvText([...key, "embedColor"], "لون الإمبد", { type: "color" }),
        ),
        lvArea([...key, "embedDescription"], "وصف الإمبد", "يمكن استخدام {message}"),
        lvGrid(
          lvSwitch([...key, "mentionWinners"], "منشن الفائزين"),
          lvSwitch([...key, "showXp"], "عرض XP المكتسب"),
          lvSwitch([...key, "showRank"], "عرض المركز"),
        ),
        el("div", { class: "leveling-prime-preview", "data-preview": `periodic-${period}` }),
      );
    };
    const p = ["prime"];
    return el("div", { class: "leveling-stack" },
      lvCard("إعدادات /rank", "الافتراضي صورة PNG واحدة فقط. إعدادات الرتبة اليدوية مستقلة عن إشعار الارتقاء.",
        lvSwitch([...p, "rank", "enabled"], "تفعيل أمر الرتبة"),
        lvPicker([...p, "rank", "channels"], "تقييد القنوات (فارغ = كل القنوات)", "channel"),
        lvSwitch([...p, "rank", "imageOnly"], "صورة فقط (تتجاوز النص والإمبد)"),
        lvSwitch([...p, "rank", "showCard"], "عرض بطاقة PRIME"),
        lvSwitch([...p, "rank", "showCustomMessage"], "عرض رسالة مخصصة"),
        lvArea([...p, "rank", "customMessage"], "الرسالة المخصصة", "متغيرات: {user} {username} {mention} {level} {xp} {required_xp} {progress} {rank} {total_members} {messages} {voice_time} {streak} {server}"),
        lvSwitch([...p, "rank", "sendEmbed"], "إرسال الرسالة داخل Embed"),
      ),
      lvCard("إشعار الارتقاء", "لا يتأثر هذا القسم بطريقة إرسال /rank.",
        lvSwitch([...p, "levelup", "sendNotification"], "إرسال إشعار الارتقاء"),
        lvSwitch([...p, "levelup", "sendAsEmbed"], "إرسال كـ Embed"),
        lvSwitch([...p, "levelup", "showRankCard"], "إرفاق بطاقة الرتبة"),
        lvSwitch([...p, "levelup", "mentionUser"], "منشن العضو"),
        lvSelect([...p, "levelup", "mentionRole"], "منشن رتبة إضافية", lvRoleOpts("بدون رتبة")),
        lvText([...p, "levelup", "embedTitle"], "عنوان الإمبد"),
        lvGrid(
          lvText([...p, "levelup", "embedColor"], "لون الإمبد", { type: "color" }),
          lvText([...p, "levelup", "embedFooter"], "تذييل الإمبد"),
        ),
        lvGrid(
          lvText([...p, "levelup", "embedThumbnail"], "رابط الصورة المصغرة", { dir: "ltr" }),
          lvText([...p, "levelup", "embedImage"], "رابط صورة الإمبد", { dir: "ltr" }),
        ),
        lvSwitch([...p, "levelup", "timestamp"], "إضافة توقيت"),
        el("div", { class: "leveling-prime-preview", "data-preview": "levelup" }),
      ),
      lvCard("إعدادات /top", "قائمة واحدة للنص والصوت والفترات، دون تعديل رصيد XP الدائم.",
        lvSwitch([...p, "top", "enabled"], "تفعيل /top"),
        lvGrid(
          lvSelect([...p, "top", "defaultMode"], "النمط الافتراضي", [["text", "نص"], ["voice", "صوت"]]),
          lvNum([...p, "top", "count"], "عدد النتائج (1–20)", 1, 20),
          lvText([...p, "top", "embedTitle"], "عنوان الترتيب"),
          lvText([...p, "top", "embedColor"], "لون الترتيب", { type: "color" }),
        ),
        lvArea([...p, "top", "embedMessage"], "رسالة الترتيب"),
        lvSwitch([...p, "top", "showAvatar"], "عرض صور الأعضاء"),
        lvSwitch([...p, "top", "showProgress"], "عرض شريط التقدم"),
        lvSwitch([...p, "top", "embed"], "إرسال كـ Embed"),
        el("div", { class: "leveling-prime-preview", "data-preview": "top" }),
      ),
      periodicPanel("daily", "TOP اليومي", {}),
      periodicPanel("weekly", "TOP الأسبوعي", {}),
      periodicPanel("monthly", "TOP الشهري", {}),
      el("p", { class: "leveling-unavail", text: "تُحفظ الإعدادات كوحدة واحدة عبر REST API، وتُطبق مباشرة بعد الحفظ." }),
    );
  }
  function lvTabStreak() {
    const streak = () => lvGet(["streak"]);
    const set = (path, value) => {
      const target = path.slice(0, -1).reduce((o, key) => o[key], streak());
      target[path[path.length - 1]] = value;
      lvTouch();
    };
    const field = (label, control, hint = "") => {
      const id = control.id || lvId();
      control.id = id;
      return el("div", { class: "leveling-field streak-field" },
        el("label", { for: id, text: label }), control,
        hint ? el("small", { text: hint }) : null);
    };
    const textInput = (label, path, attrs = {}, hint = "") => {
      const input = el("input", { type: "text", dir: "auto", ...attrs });
      input.value = path.reduce((o, key) => o?.[key], streak()) ?? "";
      input.addEventListener("input", () => set(path, input.value));
      return field(label, input, hint);
    };
    const numberInput = (label, path, min, max, hint = "") => {
      const input = el("input", { type: "number", min, max, step: "1", inputmode: "numeric" });
      const value = path.reduce((o, key) => o?.[key], streak());
      input.value = value ?? "";
      input.addEventListener("input", () => set(path, input.value === "" ? "" : Number(input.value)));
      return field(label, input, hint || `من ${min} إلى ${max}`);
    };
    const area = (label, path, hint = "") => {
      const input = el("textarea", { rows: 3, maxlength: 500, dir: "auto" });
      input.value = path.reduce((o, key) => o?.[key], streak()) ?? "";
      input.addEventListener("input", () => {
        const value = input.value;
        const target = path.slice(0, -1).reduce((o, key) => o[key], streak());
        target[path[path.length - 1]] = value;
        const preview = input.closest(".streak-message")?.querySelector(".streak-preview-copy");
        if (preview) preview.textContent = previewTemplate(value) || "لا يوجد نص للمعاينة.";
        lvTouch();
      });
      return field(label, input, hint);
    };
    const select = (label, path, options, hint = "") => {
      const input = el("select", {});
      options.forEach(([value, title]) => input.append(el("option", { value, text: title })));
      input.value = path.reduce((o, key) => o?.[key], streak()) ?? "";
      input.addEventListener("change", () => set(path, input.value));
      return field(label, input, hint);
    };
    const toggle = (label, path, hint = "") => {
      const id = lvId();
      const button = el("button", {
        class: "leveling-switch", type: "button", role: "switch",
        "aria-checked": String(Boolean(path.reduce((o, key) => o?.[key], streak()))),
        "aria-label": label,
      }, el("i"));
      button.addEventListener("click", () => {
        const value = !Boolean(path.reduce((o, key) => o?.[key], streak()));
        set(path, value);
        button.setAttribute("aria-checked", String(value));
      });
      return el("div", { class: "leveling-row streak-toggle-row" },
        el("span", { class: "leveling-row-copy" }, el("b", { id: `${id}-label`, text: label }), hint ? el("small", { text: hint }) : null),
        button);
    };
    const previewValues = {
      user: "عضو تجريبي", username: "عضو تجريبي", mention: "@عضو",
      streak: "7", current_streak: "7", best: "12", best_streak: "12",
      stage: "spark", stage_name: "شرارة", next_stage: "جمر",
      remaining: "2", progress: "71%", time_remaining: "12س 30د",
      server: "PRIME", server_rank: "8", global_rank: "120", threshold: "30",
    };
    const previewTemplate = (value) => String(value || "").replace(
      /\{(\w+)\}/g,
      (token, key) => (key in previewValues ? previewValues[key] : token),
    );
    const preview = (value) => el("div", { class: "streak-preview", role: "note" },
      el("span", { class: "streak-preview-label", text: "معاينة محلية · لا تُرسل إلى Discord" }),
      el("p", { class: "streak-preview-copy", text: previewTemplate(value) || "لا يوجد نص للمعاينة." }));
    const messageCard = (key, title, hint, toggleLabel = "تفعيل هذه الرسالة") => {
      const msg = streak().messages[key];
      return lvCard(title, hint,
        el("div", { class: "streak-message" },
          toggle(toggleLabel, ["messages", key, "enabled"]),
          area("نص الرسالة", ["messages", key, "message"], "تُعرض المعاينة محلياً فقط."),
          preview(msg.message)));
    };
    const archivedMessageCard = (key, title) => {
      const item = streak().messages[key] || {};
      return el("div", { class: "streak-archive-row", "data-testid": `streak-archived-message-${key}` },
        el("div", {},
          el("strong", { text: title }),
          el("p", { text: item.message || "لا يوجد قالب محفوظ." })),
        el("span", { class: "streak-archive-state", text: "محفوظ · لا يُرسل في التدفق الحالي" }));
    };
    const deliveryItem = (label, value, detail, testId) => el("div", {
      class: "streak-policy",
      "data-testid": testId,
    },
    el("span", { text: label }),
    el("strong", { text: value }),
    el("small", { text: detail }));
    const stageRows = el("div", { class: "streak-rows" });
    const renderStages = () => {
      const rows = Array.isArray(streak().stages) ? streak().stages : [];
      stageRows.replaceChildren(...(rows.length ? rows.map((stage, index) => {
        const basePath = ["stages", index];
        const card = el("article", { class: "streak-entity" });
        const title = el("h4", { text: stage.name || `مرحلة ${index + 1}` });
        const remove = el("button", { type: "button", class: "leveling-btn ghost streak-remove", text: "إزالة المرحلة", onClick: () => {
          streak().stages.splice(index, 1); lvTouch(); renderStages();
        } });
        const makeText = (label, key, attrs = {}) => textInput(label, [...basePath, key], attrs);
        const makeNumber = (label, key, min, max) => numberInput(label, [...basePath, key], min, max);
        const nullableText = (label, key, attrs = {}) => {
          const input = el("input", { type: "text", dir: "auto", ...attrs });
          input.value = stage[key] ?? "";
          input.addEventListener("input", () => set([...basePath, key], input.value || null));
          return field(label, input);
        };
        const color = el("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(stage.color || "") ? stage.color : "#5865f2", "aria-label": "لون المرحلة" });
        color.addEventListener("input", () => set([...basePath, "color"], color.value));
         const particleChoices = [["none", "بدون"], ["sparks", "شرارات"], ["shine", "لمعان"], ["embers", "جمر"], ["snow", "ثلج"], ["petals", "بتلات"], ["neon", "نيون"]];
        const particleSelect = select("تأثير الجزيئات", [...basePath, "particle"], particleChoices);
        const header = el("div", { class: "streak-entity-head" },
          el("div", {}, title, el("small", { text: `المعرّف الثابت: ${stage.stage_key}` })),
          toggle("تفعيل المرحلة", [...basePath, "enabled"]),
          remove);
        card.append(header,
          lvGrid(makeNumber("عتبة السلسلة", "threshold", 1, 2147483647), makeText("اسم المرحلة", "name", { maxlength: 80 })),
          lvGrid(makeText("الوصف", "description", { maxlength: 400 }), makeNumber("قوة التوهج", "glow", 0, 100)),
          lvGrid(field("اللون", color), particleSelect),
           lvGrid(makeText("التفاعل", "reaction", { maxlength: 100 }), nullableText("رابط الصورة", "image", { dir: "ltr", maxlength: 4096, placeholder: "https://" })),
           stage.message ? el("p", { class: "streak-archive-state", text: "قالب رسالة المرحلة محفوظ للتوافق، لكن لا يُرسل مع بطاقة PNG." }) : null);
        return card;
      }) : [el("p", { class: "leveling-empty", text: "لا توجد مراحل محفوظة حالياً. أضف مرحلة عند الحاجة." })]));
    };
    const milestoneRows = el("div", { class: "streak-rows" });
    const renderMilestones = () => {
      const rows = Array.isArray(streak().milestones) ? streak().milestones : [];
      milestoneRows.replaceChildren(...(rows.length ? rows.map((milestone, index) => {
        const savedState = milestone.enabled ? "مفعّل سابقاً" : "معطّل سابقاً";
        return el("div", {
          class: "streak-archive-row",
          "data-testid": `streak-archived-milestone-${index}`,
        },
        el("div", {},
          el("strong", { text: `إنجاز عند ${milestone.threshold} يوم` }),
          el("p", { text: `الحالة المحفوظة: ${savedState}` })),
        el("span", { class: "streak-archive-state", text: "محفوظ · لا ينتج عنه إشعار في التدفق الحالي" }));
      }) : [el("p", { class: "leveling-empty", text: "لا توجد إنجازات محفوظة." })]));
    };
    const channelOpts = lvChanOpts("بدون قناة محددة");
    const main = lvCard("إعدادات السلسلة", "تحكم في قناة التسجيل وحدود نقاط مكافأة الستريك ضمن إعدادات هذا السيرفر.",
      lvGrid(select("قناة تسجيل السلسلة", ["channel"], channelOpts, "يُحتسب النشاط هنا، وتُرسل ردود التكرار في القناة نفسها."),
         numberInput("نقاط النشاط اليومية", ["dailyXp"], 0, 100000),
         numberInput("الحد الأعلى للسلسلة", ["maxCap"], 0, 10000000)),
      lvGrid(
        el("div", { class: "streak-policy" }, el("span", { text: "المنطقة الزمنية" }), el("strong", { dir: "ltr", text: streak().timezone || "Asia/Riyadh" }), el("small", { text: "سياسة منصة ثابتة · للعرض فقط" })),
        el("div", { class: "streak-policy" }, el("span", { text: "وقت إعادة الضبط" }), el("strong", { dir: "ltr", text: streak().resetTime || "00:00" }), el("small", { text: "سياسة منصة ثابتة · للعرض فقط" })),
      ),
      textInput("تفاعل النجاح", ["successReaction"], { maxlength: 100, placeholder: "رمز التفاعل" }, "اسم أو رمز التفاعل المعتمد في بيانات السيرفر."));
    const delivery = lvCard("سلوك الإرسال الحالي", "يعرض هذا الملخص السلوك المنفّذ فعلياً بواسطة البوت.",
      el("div", { class: "streak-delivery-grid" },
        deliveryItem("التسجيل الناجح", "تفاعل ثم بطاقة PNG", "لا تُرسل رسالة نصية أو Embed؛ البطاقة مستقلة.", "streak-success-delivery"),
        deliveryItem("تكرار اليوم", "رد مع وقت إعادة الضبط", "يُحذف الرد والرسالة المكررة بعد 10 ثوانٍ.", "streak-duplicate-delivery"),
        deliveryItem("التذكير اليومي", "رسالة خاصة اختيارية", "يصل فقط لمن فعّل التذكير، وفق الوقت والقالب أدناه.", "streak-reminder-delivery")));
    const messages = lvCard("رسائل السلسلة", "يمكن تخصيص رد التكرار وتذكير المستخدمين الذين اختاروا الاشتراك. المعاينة محلية ولا ترسل إلى Discord.",
      el("div", { class: "streak-message-grid" },
        messageCard("duplicate", "نشاط مكرر", "الرد إلزامي؛ عند إيقاف القالب المخصص يستخدم البوت الرد القياسي مع العدّاد.", "استخدام القالب المخصص"),
        lvCard("تذكير النشاط", "رسالة خاصة يومية للمستخدمين الذين فعّلوا التذكير؛ التوقيت وفق المنطقة الزمنية الثابتة.",
          el("div", { class: "streak-message" },
            toggle("تفعيل التذكير", ["messages", "reminder", "enabled"]),
            lvGrid(textInput("وقت التذكير", ["messages", "reminder", "time"], { type: "time", dir: "ltr" }),
              area("نص التذكير", ["messages", "reminder", "message"])),
            preview(streak().messages.reminder.message))),
      ));
    const archivedMessages = lvCard("قوالب محفوظة للتوافق", "هذه القوالب تبقى محفوظة عند حفظ الإعدادات، لكنها لا تُرسل في مسار النشاط الذي يرسل بطاقة PNG فقط.",
      el("div", { class: "streak-archive-list" },
        archivedMessageCard("success", "قالب التسجيل الناجح"),
        archivedMessageCard("stageUp", "قالب الانتقال بين المراحل"),
        archivedMessageCard("milestone", "قالب الإنجاز")));
    const stages = lvCard("مراحل السلسلة", "تظهر هذه المراحل على بطاقة PNG. رسائل المراحل القديمة تبقى محفوظة ولا تُرسل.",
      el("div", { class: "streak-list-head" },
        el("span", { class: "streak-count", text: `${streak().stages.length} مرحلة` }),
        el("button", { type: "button", class: "leveling-btn", text: "إضافة مرحلة", onClick: () => {
          const used = new Set(streak().stages.map((item) => String(item.stage_key)));
          let key = `stage-${Date.now().toString(36)}`;
          while (used.has(key)) key += "-1";
          streak().stages.push({ stage_key: key, threshold: "", name: "", message: null, image: null, color: "#5865f2", reaction: null, description: "", glow: 0, particle: "none", enabled: true });
          lvTouch(); lvRender();
        } }), ),
      stageRows);
    const milestones = lvCard("إنجازات محفوظة", "تُعرض للرجوع إليها فقط؛ لا تُرسل إشعارات إنجاز في تدفق البطاقة الحالي.",
      el("div", { class: "streak-list-head" },
        el("span", { class: "streak-count", text: `${streak().milestones.length} إنجاز محفوظ` })),
      milestoneRows);
    renderStages(); renderMilestones();
    return el("div", { class: "leveling-stack streak-panel", dir: "rtl" },
      el("div", { class: "streak-intro" },
        el("span", { class: "leveling-kicker", text: "PRIME / STREAK CONTROL" }),
        el("h2", { text: "سلسلة النشاط" }),
        el("p", { text: "تُحفظ إعدادات القناة والتفاعل والبطاقة وقوالب التكرار والتذكير ضمن المسودة المشتركة حتى تستخدم حفظ إعدادات السيرفر." })),
      delivery, main, messages, archivedMessages, stages, milestones);
  }
  const lvMin = (m) => `${lvFmt(Math.floor(m / 60))} س ${lvFmt(Math.floor(m % 60))} د`;
  function lvTabData() {
    const s = lvState(), analytics = s.analytics, totals = analytics?.totals || {};
    const board = s.leaderboard, mode = s.dataMode;
    const voice = mode === "voice";
    const rows = (board?.rows || []).map((row) => el("tr", {},
      el("td", { text: lvFmt(row.rank) }),
      el("th", { scope: "row", text: row.name || `عضو ${String(row.user_id).slice(-4)}` }),
      el("td", { text: lvFmt(row.level) }),
      el("td", { text: lvFmt(row.xp) }),
      el("td", { text: voice ? lvSeconds(row.activity_total) : lvFmt(row.activity_total) }),
    ));
    const tableHead = ["#", "العضو", "المستوى", voice ? "نقاط الصوت" : "نقاط الرسائل", voice ? "وقت الصوت" : "الرسائل"];
    const modeSelect = el("select", { "aria-label": "ترتيب المتصدرين" },
      el("option", { value: "text", text: "ترتيب الرسائل" }),
      el("option", { value: "voice", text: "ترتيب الصوت" }));
    modeSelect.value = mode;
    modeSelect.addEventListener("change", () => lvLoadBoard(modeSelect.value, 0, false));
    const summary = analytics ? lvGrid(
      el("div", { class: "leveling-stat" }, el("small", { text: "الأعضاء المشاركون" }), el("b", { text: lvFmt(Number(totals.participants) || 0) })),
      el("div", { class: "leveling-stat" }, el("small", { text: "إجمالي نقاط الرسائل" }), el("b", { text: lvFmt(Number(totals.text_xp) || 0) })),
      el("div", { class: "leveling-stat" }, el("small", { text: "إجمالي نقاط الصوت" }), el("b", { text: lvFmt(Number(totals.voice_xp) || 0) })),
      el("div", { class: "leveling-stat" }, el("small", { text: "الرسائل المسجلة" }), el("b", { text: lvFmt(Number(totals.total_messages) || 0) })),
      el("div", { class: "leveling-stat" }, el("small", { text: "وقت الصوت المسجل" }), el("b", { text: lvSeconds(totals.total_voice_seconds) })),
      el("div", { class: "leveling-stat" }, el("small", { text: "أعضاء نشطون خلال 7 أيام" }), el("b", { text: lvFmt(Number(totals.active_members_7d) || 0) })),
    ) : el("p", { class: "leveling-unavail", text: "تعذر تحميل إحصاءات المستويات الحقيقية." });
    const table = el("div", { class: "leveling-table-wrap", tabindex: "0", role: "region", "aria-label": "ترتيب أعضاء السيرفر الحقيقي" },
      el("table", { class: "leveling-table" },
        el("thead", {}, el("tr", {}, ...tableHead.map((h) => el("th", { scope: "col", text: h })))),
        el("tbody", {}, ...rows)));
    const pagination = board?.nextOffset != null
      ? el("button", { type: "button", class: "leveling-btn", text: "تحميل المزيد", onClick: () => lvLoadBoard(mode, board.nextOffset, true) })
      : null;
    const resetCard = lvCard("إعادة ضبط بيانات التقدم",
      "تحذف مستويات ونقاط ونشاط أعضاء هذا السيرفر وسجل نقاطهم التاريخي فقط. تبقى إعدادات المستويات ومكافآتها كما هي، ولا تتغير الرتب الموجودة في Discord. لا يمكن التراجع عن الحذف.",
      state.session?.local_development
        ? el("p", { class: "leveling-unavail", role: "status", text: "إعادة الضبط معطلة في جلسة التطوير المحلية؛ استخدم حساب مسؤول من Discord في بيئة الإنتاج." })
        : null,
      el("button", {
        type: "button",
        class: "leveling-btn danger",
        text: "إعادة ضبط التقدم والسجل",
        disabled: Boolean(state.session?.local_development),
        onClick: async (event) => {
          if (!window.confirm(
            "تحذير: سيُحذف تقدم الرسائل والصوت، المستويات، النشاط، وسجل نقاط هذا السيرفر فقط. ستبقى الإعدادات ومكافآت الرتب محفوظة، ولن تتغير الرتب الممنوحة حالياً. لا يمكن التراجع. هل تريد المتابعة؟"
          )) return;
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const response = await writeApi(
              `api/guild/${s.gid}/leveling/reset-progress`,
              { confirmation: "RESET_LEVEL_PROGRESS" },
            );
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.ok) {
              const message = data.error === "level_admin_required"
                ? "إعادة الضبط متاحة لمالك السيرفر أو المسؤولين فقط."
                : data.error === "production_session_required"
                  ? "إعادة الضبط غير متاحة في جلسة التطوير المحلية."
                  : "تعذر إعادة ضبط تقدم المستويات.";
              toast(message, "error");
              return;
            }
            const analyticsResponse = await api(
              `api/guild/${s.gid}/leveling/analytics`,
            );
            s.analytics = await readJson(analyticsResponse, null);
            s.analyticsError = !analyticsResponse.ok;
            await lvLoadBoard(s.dataMode, 0, false);
            toast(
              `تمت إعادة ضبط التقدم. أُعيد ضبط ${lvFmt(data.members_reset || 0)} سجل عضو.`,
              "success",
              6000,
            );
          } catch (error) {
            if (error.message !== "unauth") toast("تعذر الاتصال بخادم إعدادات المستويات.", "error");
          } finally {
            button.disabled = Boolean(state.session?.local_development);
          }
        },
      }));
    return el("div", { class: "leveling-stack" },
      resetCard,
      lvCard("ترتيب الأعضاء الحقيقي", "مصدره سجلات نقاط السيرفر. الصفحات تستخدم ترتيباً مفهرساً للنص أو الصوت.", modeSelect,
        s.leaderboardError ? el("p", { class: "leveling-unavail", text: "تعذر تحميل ترتيب الأعضاء." }) : null,
        table, pagination),
      lvCard("ملخص النشاط الحقيقي", "إجماليات مباشرة من SQLite؛ نشاط آخر 7 أيام هو عدد الأعضاء ذوي آخر رسالة حديثة.", summary),
      lvCard("توزيع مستويات الرسائل", "أعلى 20 مستوى موجوداً في السجلات المحفوظة", lvDist(analytics?.distribution || [])),
      lvCard("السجل التاريخي", "لا يحتفظ النظام الحالي بتاريخ يومي أو بسجل أحداث مستوى قابل للرسم.",
        el("p", { class: "leveling-unavail", text: "الرسم اليومي والأحداث الأخيرة غير متاحين من قاعدة البيانات الحالية؛ لم تُنشأ بيانات تقديرية." })));
  }
  function lvValidate() {
    const d = lvState().draft, e = [];
    const int = (v, a, b) => typeof v === "number" && Number.isInteger(v) && v >= a && v <= b;
    const fin = (v, a, b) => typeof v === "number" && Number.isFinite(v) && v >= a && v <= b;
    if (!fin(d.points.xpMultiplier, 0, 10)) e.push("المضاعف العام يجب أن يكون رقماً بين 0 و10.");
    if (!int(d.points.minXp, 0, 1000) || !int(d.points.maxXp, 0, 1000)) e.push("نقاط الرسائل يجب أن تكون أعداداً صحيحة بين 0 و1000.");
    else if (d.points.minXp > d.points.maxXp) e.push("أقل نقاط للرسالة أكبر من الأعلى.");
    if (d.points.allowedChannels.some((c) => d.points.bl.channels.includes(c))) e.push("قناة موجودة في المسموحة وفي القائمة السوداء معاً.");
    if (!int(d.points.cooldown, 0, 3600)) e.push("فترة التهدئة بين 0 و3600 ثانية.");
    [...d.points.roleMult, ...d.points.chanMult].forEach((m) => { if (!m.id || !fin(m.mult, 0, 10)) e.push("كل مضاعف يحتاج عنصراً ومضاعفاً رقمياً بين 0 و10."); });
    d.points.boosts.forEach((b) => { if (!String(b.label).trim() || !fin(b.mult, 1, 10) || !int(b.hours, 1, 168)) e.push("التعزيز يحتاج اسماً ومضاعفاً 1-10 ومدة 1-168 ساعة."); });
    if (!int(d.voice.xpPerMin, 0, 500) || !int(d.voice.minMembers, 1, 99) || !int(d.voice.dimThreshold, 0, 1440) || !int(d.voice.dimRate, 0, 100)) e.push("قيم الصوت خارج النطاق المسموح.");
    const roles = state.meta?.roles || [];
    const seen = new Set();
    d.rewards.list.forEach((r) => {
      if (!int(r.level, 1, 1000)) e.push("مستوى المكافأة بين 1 و1000.");
      if (r.type !== "text" && r.type !== "voice") e.push("نوع المكافأة يجب أن يكون نصي أو صوتي.");
      if (!r.role || !lvAssignable(roles.find((x) => String(x.id) === r.role))) e.push("كل مكافأة تحتاج رتبة معروفة وقابلة للإسناد.");
      const k = `${r.level}:${r.type}`;
      if (seen.has(k)) e.push("لا يمكن تكرار المستوى والنوع نفسهما."); seen.add(k);
    });
    if (!/^#[0-9a-f]{6}$/i.test(d.card.color)) e.push("لون البطاقة غير صالح.");
    if (!LV_LAYOUTS[d.card.layout]) e.push("قالب بطاقة المستوى غير صالح.");
    if (!Number.isInteger(d.card.glowStrength) || d.card.glowStrength < 0 || d.card.glowStrength > 100) e.push("قوة التوهج يجب أن تكون عدداً صحيحاً من 0 إلى 100.");
    if (!Number.isInteger(d.card.particleDensity) || d.card.particleDensity < 0 || d.card.particleDensity > 100) e.push("كثافة الجزيئات يجب أن تكون عدداً صحيحاً من 0 إلى 100.");
    if (!Number.isInteger(d.card.bgOverlay) || d.card.bgOverlay < 0 || d.card.bgOverlay > 85) e.push("تعتيم الخلفية يجب أن يكون عدداً صحيحاً من 0 إلى 85.");
    if (!Number.isInteger(d.card.bgBlur) || d.card.bgBlur < 0 || d.card.bgBlur > 18) e.push("تمويه الخلفية يجب أن يكون عدداً صحيحاً من 0 إلى 18.");
    if (!Number.isInteger(d.card.animationIntensity) || d.card.animationIntensity < 0 || d.card.animationIntensity > 100) e.push("شدة الحركة يجب أن تكون عدداً صحيحاً من 0 إلى 100.");
    if (d.card.particleColor !== "accent" && !/^#[0-9a-f]{6}$/i.test(String(d.card.particleColor))) e.push("لون الجزيئات يجب أن يكون لون التمييز أو قيمة RGB سداسية.");
    if (!["gradient", "solid", "segmented", "neon"].includes(d.card.barStyle)) e.push("أسلوب شريط التقدم غير صالح.");
    if (!["auto", "none", "bronze", "silver", "gold", "diamond"].includes(d.card.frame)) e.push("إطار الرتبة غير صالح.");
    if (!["beam", "aurora", "burst"].includes(d.card.animationStyle)) e.push("نمط الحركة غير صالح.");
    if (d.public.slug && !PUBLIC_SLUG_RE.test(d.public.slug)) e.push("أدخل معرّف رابط من 3 إلى 40 حرفاً: أحرف إنجليزية صغيرة وأرقام وشرطة مفردة بين الكلمات.");
    if (d.public.enabled && !d.public.slug) e.push("أدخل معرّف الرابط قبل إتاحة اللوحة للعامة.");
    if (!lvBgOk(String(d.card.bg).trim())) e.push("رابط الخلفية يجب أن يبدأ بـ https://.");
    const allowed = new Set(LV_TEMPLATE_VARS.map((token) => token.slice(1, -1)));
    Object.entries(d.messages).forEach(([k, m]) => {
      if (!String(m.tpl).trim() || m.tpl.length > 500) e.push("قوالب الرسائل مطلوبة وبحد أقصى 500 حرف.");
      (String(m.tpl).match(/\{[^{}]*\}/g) || []).forEach((t) => { if (!allowed.has(t.slice(1, -1))) e.push(`متغير غير مدعوم ${t} في قالب ${k}.`); });
    });
    if (d.streak) {
      const streakInt = (value, min, max) => typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
      const validStreakTemplate = (value, key, label) => {
        const text = String(value ?? "");
        if (!text.trim() || text.length > 500) {
          e.push(`قالب ${label} مطلوب وبحد أقصى 500 حرف.`);
          return;
        }
        if ((text.match(/{/g) || []).length !== (text.match(/}/g) || []).length) {
          e.push(`الأقواس في قالب ${label} غير مكتملة.`);
        }
        const allowed = LV_STREAK_TEMPLATE_VARS[key] || new Set();
        (text.match(/\{[^{}]*\}/g) || []).forEach((token) => {
          if (!allowed.has(token.slice(1, -1))) e.push(`متغير غير مدعوم ${token} في قالب ${label}.`);
        });
      };
      if (!streakInt(d.streak.dailyXp, 0, 100000) || !streakInt(d.streak.maxCap, 0, 10000000)) e.push("إعدادات نقاط السلسلة يجب أن تكون أعداداً صحيحة ضمن النطاق المحدد.");
      if (d.streak.timezone !== "Asia/Riyadh" || d.streak.resetTime !== "00:00") e.push("المنطقة الزمنية ووقت إعادة الضبط ثابتان على توقيت الرياض ومنتصف الليل.");
      if (typeof d.streak.successReaction !== "string" || d.streak.successReaction.length > 100) e.push("تفاعل نجاح السلسلة مطلوب وبحد أقصى 100 حرف.");
      const stageKeys = new Set(), stageThresholds = new Set();
      (Array.isArray(d.streak.stages) ? d.streak.stages : []).forEach((stage) => {
        const label = stage.name || stage.stage_key;
        const key = String(stage.stage_key || "");
        if (!/^[a-z0-9][a-z0-9_-]{0,47}$/.test(key) || stageKeys.has(key)) e.push(`معرّف المرحلة ${label} غير صالح أو مكرر.`);
        stageKeys.add(key);
        if (!streakInt(stage.threshold, 1, 2147483647) || stageThresholds.has(stage.threshold)) e.push(`أدخل عتبة صحيحة وغير مكررة للمرحلة ${label}.`);
        stageThresholds.add(stage.threshold);
        if (!String(stage.name || "").trim() || stage.name.length > 80) e.push(`اسم المرحلة ${label} مطلوب وبحد أقصى 80 حرفاً.`);
        if (!streakInt(stage.glow, 0, 100) || !/^#[0-9a-f]{6}$/i.test(String(stage.color || ""))) e.push(`راجع قوة التوهج واللون للمرحلة ${label}.`);
        if (!Object.hasOwn(LV_PARTS, stage.particle)) e.push(`تأثير الجزيئات للمرحلة ${label} غير صالح.`);
        if (String(stage.description || "").length > 400 || String(stage.reaction || "").length > 100) e.push(`الوصف أو التفاعل في المرحلة ${label} أطول من المسموح.`);
        if (stage.image && !/^https:\/\//i.test(stage.image)) e.push(`رابط صورة المرحلة ${label} يجب أن يبدأ بـ https://.`);
        if (stage.message) validStreakTemplate(stage.message, "stageUp", `المرحلة ${label}`);
      });
      const milestoneThresholds = new Set();
      (Array.isArray(d.streak.milestones) ? d.streak.milestones : []).forEach((milestone, index) => {
        if (!streakInt(milestone.threshold, 1, 2147483647) || milestoneThresholds.has(milestone.threshold)) e.push(`أدخل عتبة صحيحة وغير مكررة للإنجاز ${index + 1} قبل الحفظ.`);
        milestoneThresholds.add(milestone.threshold);
        if (String(milestone.reaction || "").length > 100) e.push(`تفاعل الإنجاز ${index + 1} أطول من المسموح.`);
        if (milestone.image && !/^https:\/\//i.test(milestone.image)) e.push(`رابط صورة الإنجاز ${index + 1} يجب أن يبدأ بـ https://.`);
        if (milestone.message) validStreakTemplate(milestone.message, "milestone", `الإنجاز ${index + 1}`);
      });
      Object.entries(d.streak.messages || {}).forEach(([key, item]) => {
        if (!LV_STREAK_TEMPLATE_VARS[key]) return;
        validStreakTemplate(item?.message, key, key);
      });
      const reminderTime = String(d.streak.messages?.reminder?.time || "");
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(reminderTime)) e.push("وقت تذكير السلسلة غير صالح.");
    }
    return [...new Set(e)];
  }
  function lvActions() {
    const box = el("div", { class: "leveling-errors", role: "alert" });
    const show = (errs) => box.replaceChildren(...errs.map((x) => el("p", { text: x })));
    return el("div", { class: "leveling-actions" },
      el("button", { type: "button", class: "leveling-btn gold", text: "حفظ إعدادات السيرفر", onClick: async (event) => {
        const errs = lvValidate(); show(errs);
        if (errs.length) return toast("أصلح الأخطاء قبل حفظ إعدادات السيرفر", "warn");
        const s = lvState();
        if (!s.loaded) return toast("تعذر الحفظ قبل تحميل إعدادات السيرفر", "error");
        s.draft.card.bg = String(s.draft.card.bg).trim();
        const button = event.currentTarget;
        button.disabled = true;
        try {
          const response = await writeApi(
            `api/guild/${s.gid}/leveling/settings`,
            { revision: s.revision, draft: s.draft },
          );
          const data = await readJson(response, {});
          if (response.status === 409) {
            await loadLevelingData(s.gid);
            toast("تغيّرت إعدادات المستويات على السيرفر. حُمّلت النسخة الأحدث؛ راجع تعديلاتك ثم احفظ مجدداً.", "warn", 6000);
            lvRender();
          } else if (!response.ok || !data.draft) {
            toast(Object.values(data.fields || {})[0] || "تعذر حفظ إعدادات المستويات", "error");
          } else {
            s.revision = data.revision;
            s.configured = data.configured;
            s.saved = lvSan(lvMerge(lvDefaults(), data.draft));
            s.draft = clone(s.saved);
            s.loaded = true;
            s.loadError = false;
            lvStatus();
            lvPreviews();
            drawLvCard();
            toast("تم حفظ إعدادات المستويات وتحديث النظام الفعلي.", "success");
          }
        } catch (error) {
          if (error.message !== "unauth") toast("تعذر الاتصال بخادم إعدادات المستويات.", "error");
        } finally {
          button.disabled = false;
        }
      } }),
      el("button", { type: "button", class: "leveling-btn", text: "إعادة ضبط", onClick: () => {
        const s = lvState();
        s.draft = clone(s.saved); show([]); lvRender();
        toast("أُعيدت التعديلات إلى آخر إعدادات محمّلة من السيرفر", "info");
      } }), box);
  }
  const LV_PANELS = { general: lvTabGeneral, public: lvTabPublic, points: lvTabPoints, voice: lvTabVoice, rewards: lvTabRewards, card: lvTabCard, messages: lvTabMessages, prime: lvTabPrime, streak: lvTabStreak, data: lvTabData };
  function lvRender() {
    const root = $(".leveling-view");
    if (!root) return;
    const s = lvState();
    const tabs = $(".leveling-tabs", root), panel = $(".leveling-panel", root);
    [...tabs.children].forEach((b) => { const on = b.dataset.tab === s.tab; b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    panel.setAttribute("aria-labelledby", `leveling-tab-${s.tab}`);
    panel.replaceChildren(LV_PANELS[s.tab]());
    lvStatus();
    lvPreviews();
    drawLvCard();
  }
  function levelingView() {
    const s = lvState();
    const tabs = el("div", { class: "leveling-tabs", role: "tablist", "aria-label": "أقسام المستويات" }, ...LV_TABS.map(([k, t]) => el("button", {
      type: "button", role: "tab", id: `leveling-tab-${k}`, "data-tab": k, "data-leveling-tab": k, "aria-controls": "leveling-panel", "aria-selected": "false", tabindex: "-1", text: t,
      onClick: () => { s.tab = k; sessionStorage.setItem("leveling-tab", k); lvRender(); },
      onKeydown: (ev) => {
        const i = LV_TABS.findIndex((x) => x[0] === k), dir = ev.key === "ArrowLeft" ? 1 : ev.key === "ArrowRight" ? -1 : 0;
        if (!dir && ev.key !== "Home" && ev.key !== "End") return;
        ev.preventDefault();
        const n = ev.key === "Home" ? 0 : ev.key === "End" ? LV_TABS.length - 1 : (i + dir + LV_TABS.length) % LV_TABS.length;
        s.tab = LV_TABS[n][0]; sessionStorage.setItem("leveling-tab", s.tab); lvRender(); $(`#leveling-tab-${s.tab}`)?.focus();
      },
    })));
    const root = el("div", { class: "leveling-view" },
      el("header", { class: "leveling-hero" },
        el("div", {}, el("span", { class: "leveling-kicker", text: "PRIME / LEVELS" }), el("h1", { text: "المستويات" }), el("p", { text: `إدارة المستويات لسيرفر ${state.guild?.name || ""}. باقي أقسام البوت متاحة من القائمة الجانبية.` })),
        el("span", { class: "leveling-state-pill" })),
      s.loadError
        ? el("div", { class: "leveling-notice", role: "alert" },
          "تعذر تحميل إعدادات المستويات من السيرفر. لن تُعرض بيانات تجريبية مكانها. ",
          el("button", { type: "button", class: "leveling-btn", text: "إعادة المحاولة", onClick: async () => {
            await loadLevelingData(s.gid);
            renderPage();
          } }))
        : el("div", { class: "leveling-notice", role: "note", text: "تُحمّل الإعدادات والتحليلات من بيانات السيرفر، وتحفظ التغييرات إلى قاعدة البيانات مباشرةً." }),
      tabs, el("div", { class: "leveling-panel", role: "tabpanel", id: "leveling-panel" }), lvActions());
    queueMicrotask(lvRender);
    return root;
  }
  function syncThemeEditor() {
    const theme = state.themeDraft || systemDashboardTheme();
    THEME_COLOR_FIELDS.forEach(([key]) => {
      const input = $(`#theme-color-${key}`);
      if (input && theme[key]) input.value = theme[key];
      const value = $(`[data-theme-color-value="${key}"]`);
      if (value) value.textContent = theme[key] || "";
    });
    document.querySelectorAll("[data-theme-preset]").forEach((button) => {
      const selected = button.dataset.themePreset === theme.preset;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll("[data-theme-button-style]").forEach((button) => {
      const selected = button.dataset.themeButtonStyle === theme.buttonStyle;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    const save = $(".theme-save");
    if (save) {
      save.disabled = !state.themeDirty || state.themeSaving || !state.online;
      save.textContent = state.themeSaving ? "جارٍ الحفظ…" : "حفظ المظهر";
    }
    const status = $(".theme-save-status");
    if (status) {
      status.textContent = state.themeSaving
        ? "جارٍ حفظ المظهر في حسابك…"
        : state.themeDirty
          ? "معاينة مؤقتة — احفظ لتثبيت التغييرات لحسابك"
          : state.themeSaved
            ? "المظهر محفوظ لحساب Discord الخاص بك"
            : "المظهر الحالي هو الافتراضي؛ لن يتغير للمستخدمين الآخرين";
      status.classList.toggle("is-dirty", state.themeDirty);
    }
  }
  function setThemeDraft(nextTheme) {
    state.themeDraft = nextTheme;
    state.themeDirty = true;
    applyDashboardTheme(nextTheme);
    syncThemeEditor();
  }
  async function saveDashboardTheme() {
    if (!state.themeDirty || state.themeSaving || !state.themeDraft) return;
    state.themeSaving = true;
    syncThemeEditor();
    try {
      const response = await writeApi("api/user/theme", { theme: state.themeDraft });
      const data = await readJson(response, {});
      if (!response.ok || !data.ok || !data.theme) {
        toast(data.error === "validation" ? "تحقق من ألوان المظهر ثم حاول مجدداً" : "تعذر حفظ المظهر");
        return;
      }
      state.themeSaved = clone(data.theme);
      state.themeDraft = clone(data.theme);
      state.themeDirty = false;
      toast("تم حفظ المظهر لحسابك فقط", "success", 3200);
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال لحفظ المظهر");
    } finally {
      state.themeSaving = false;
      syncThemeEditor();
    }
  }
  async function resetDashboardTheme() {
    if (state.themeSaving) return;
    state.themeSaving = true;
    syncThemeEditor();
    try {
      const response = await writeApi("api/user/theme", { theme: null });
      const data = await readJson(response, {});
      if (!response.ok || !data.ok) {
        toast("تعذر استعادة المظهر الافتراضي");
        return;
      }
      state.themeSaved = null;
      state.themeDraft = systemDashboardTheme();
      state.themeDirty = false;
      applyDashboardTheme(null);
      toast("تمت استعادة مظهر PRIME الافتراضي", "success", 3200);
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال لاستعادة المظهر");
    } finally {
      state.themeSaving = false;
      syncThemeEditor();
    }
  }
  function appearanceView() {
    if (state.themeError || !state.themeTokens) {
      return el("section", { class: "theme-settings-view" },
        el("div", { class: "theme-settings-heading" },
          el("span", { class: "theme-kicker", text: "PRIME / PERSONAL STYLE" }),
          el("h1", { text: "المظهر الشخصي" })),
        el("div", { class: "theme-error", role: "alert" },
          el("strong", { text: "تعذر تحميل خيارات المظهر" }),
          el("p", { text: "تعذر قراءة رموز PRIME أو إعدادات حسابك. لم يتم تطبيق إعدادات بديلة." }),
          el("button", { type: "button", class: "btn primary", text: "إعادة المحاولة", onClick: async () => {
            state.themeError = "";
            try {
              await loadDashboardTheme();
              renderPage();
            } catch (_) {
              state.themeError = "theme_load_failed";
              renderPage();
            }
          } })));
    }
    const currentTheme = state.themeDraft || systemDashboardTheme();
    if (!state.themeDraft) state.themeDraft = currentTheme;
    const presets = dashboardThemePresets();
    const presetGrid = el("div", { class: "theme-preset-grid", "aria-label": "المظاهر الجاهزة" },
      ...presets.map(({ id, label, theme }) => el("button", {
        type: "button",
        class: "theme-preset",
        "data-theme-preset": id,
        "aria-pressed": String(currentTheme.preset === id),
        style: `--swatch-primary:${theme.primary};--swatch-secondary:${theme.secondary}`,
        onClick: () => setThemeDraft(clone(theme)),
      },
        el("span", { class: "theme-preset-swatches", "aria-hidden": "true" },
          el("i"), el("i"), el("i")),
        el("span", { class: "theme-preset-copy" },
          el("strong", { text: label }),
          el("small", { text: "PRIME · AMOLED" })))));
    const colorControls = el("div", { class: "theme-color-grid" },
      ...THEME_COLOR_FIELDS.map(([key, label]) => {
        const input = el("input", {
          id: `theme-color-${key}`,
          type: "color",
          value: currentTheme[key],
          "aria-label": label,
        });
        input.addEventListener("input", () => {
          const base = state.themeDraft || systemDashboardTheme();
          setThemeDraft({ ...base, preset: "custom", [key]: input.value });
        });
        return el("label", { class: "theme-color-control", for: `theme-color-${key}` },
          el("span", { class: "theme-color-chip", "aria-hidden": "true" }, input),
          el("span", { class: "theme-color-copy" },
            el("strong", { text: label }),
            el("code", { "data-theme-color-value": key, text: currentTheme[key] })));
      }));
    const buttonStyles = el("div", {
      class: "theme-button-options",
      role: "group",
      "aria-label": "نمط الأزرار",
    },
      ...[
        ["solid", "ممتلئ", "زر بارز"],
        ["soft", "هادئ", "زر بلون مخفف"],
        ["outline", "محدد", "زر بإطار"],
      ].map(([id, label, hint]) => el("button", {
        type: "button",
        class: "theme-button-option",
        "data-theme-button-style": id,
        "aria-pressed": String(currentTheme.buttonStyle === id),
        onClick: () => setThemeDraft({
          ...(state.themeDraft || systemDashboardTheme()),
          buttonStyle: id,
        }),
      },
        el("span", { class: `theme-button-sample theme-button-${id}`, text: "PRIME" }),
        el("strong", { text: label }),
        el("small", { text: hint }))));
    const preview = el("section", {
      class: "theme-preview-panel",
      "aria-label": "معاينة مباشرة للمظهر",
    },
      el("div", { class: "theme-preview-heading" },
        el("div", {}, el("span", { class: "theme-kicker", text: "LIVE PREVIEW" }),
          el("h2", { text: "معاينة لوحة PRIME" })),
        el("span", { class: "theme-live-pill", text: "مباشر" })),
      el("div", { class: "theme-preview-shell" },
        el("aside", { class: "theme-preview-sidebar" },
          el("strong", { text: "PRIME" }),
          el("span", { class: "theme-preview-nav is-active", text: "نظرة عامة" }),
          el("span", { class: "theme-preview-nav", text: "التذاكر" }),
          el("span", { class: "theme-preview-nav", text: "الأوامر" })),
        el("div", { class: "theme-preview-main" },
          el("div", { class: "theme-preview-topline" },
            el("span", { text: "مركز القيادة" }),
            el("span", { class: "theme-preview-avatar", text: "P" })),
          el("div", { class: "theme-preview-stats" },
            el("div", {}, el("small", { text: "الأعضاء" }), el("strong", { text: "1,284" })),
            el("div", {}, el("small", { text: "حالة البوت" }),
              el("strong", { class: "theme-preview-online", text: "متصل" }))),
          el("div", { class: "theme-preview-card" },
            el("span", { class: "theme-preview-accent" }),
            el("div", {}, el("strong", { text: "أدوات السيرفر" }),
              el("small", { text: "إدارة PRIME من مساحة واحدة" })),
            el("button", { type: "button", class: "theme-preview-action", text: "فتح الإعدادات" })))));
    const page = el("section", { class: "theme-settings-view" },
      el("header", { class: "theme-settings-heading" },
        el("div", {},
          el("span", { class: "theme-kicker", text: "PRIME / PERSONAL STYLE" }),
          el("h1", { text: "المظهر الشخصي" }),
          el("p", { text: "اختر مظهراً للوحة، عدّل الألوان والأزرار، وشاهد التغيير فوراً. تفضيلاتك مرتبطة بحسابك فقط." })),
        el("span", { class: "theme-account-badge", text: "خاص بحسابك" })),
      el("div", { class: "theme-settings-layout" },
        el("div", { class: "theme-settings-main" },
          el("section", { class: "theme-editor-panel" },
            el("div", { class: "theme-section-heading" },
              el("div", {}, el("h2", { text: "المظاهر الجاهزة" }),
                el("p", { text: "20 تركيبة مبنية على ألوان PRIME Design System." })),
              el("span", { class: "theme-count", text: "20" })),
            presetGrid),
          el("section", { class: "theme-editor-panel" },
            el("div", { class: "theme-section-heading" },
              el("div", {}, el("h2", { text: "تخصيص الألوان" }),
                el("p", { text: "غيّر الألوان التي تريدها؛ سيظهر التعديل مباشرة على اللوحة." }))),
            colorControls),
          el("section", { class: "theme-editor-panel" },
            el("div", { class: "theme-section-heading" },
              el("div", {}, el("h2", { text: "شكل الأزرار" }),
                el("p", { text: "اختر طريقة عرض الأزرار الأساسية." }))),
            buttonStyles)),
        el("div", { class: "theme-settings-aside" },
          preview,
          el("div", { class: "theme-save-panel" },
            el("div", { class: "theme-save-status", role: "status", "aria-live": "polite" }),
            el("div", { class: "theme-save-actions" },
              el("button", { type: "button", class: "theme-reset", text: "استعادة الافتراضي", onClick: resetDashboardTheme }),
              el("button", { type: "button", class: "theme-save btn primary", text: "حفظ المظهر", disabled: !state.themeDirty, onClick: saveDashboardTheme }))))));
    queueMicrotask(syncThemeEditor);
    return page;
  }
  function renderPage() {
    const main = $("#main");
    if (typeof state.aiControlCleanup === "function") {
      state.aiControlCleanup();
      state.aiControlCleanup = null;
    }
    main.replaceChildren();
    if (state.newer) {
      const n = el("div", {
        class: "notice",
        text: "توجد نسخة أحدث من الإعدادات. تعديلاتك ما زالت محفوظة محلياً. ",
      });
      n.append(
        el("button", {
          type: "button",
          text: "تحميل الإصدار الأحدث",
          onClick: () => {
            state.draft = clone(state.baseline);
            state.newer = false;
            renderPage();
          },
        }),
      );
      main.append(n);
    }
    const view = state.activeView;
    document.body.classList.toggle("leveling-route", view === "leveling");
    if (view === "overview") main.append(enhancedOverviewView());
    else if (view === "tickets") main.append(ticketsViewNextGen());
    else if (view === "commands") main.append(commandsView());
    else if (view === "gaming") main.append(gamingView());
    else if (view === "subscriptions") main.append(subscriptionDashboardView());
    else if (view === "clan") main.append(clanOpsView());
    else if (view === "broadcast") main.append(broadcastView());
    else if (view === "onboarding") main.append(onboardingView());
    else if (view === "security") main.append(securityView());
    else if (view === "analytics") main.append(analyticsView());
    else if (view === "economy") main.append(economyView());
    else if (view === "leveling") main.append(levelingView());
    else if (view === "ai" || view === "talk") {
      const panel = el("div", { class: "prime-ai-host" });
      main.append(panel);
      if (window.PrimeAIControl?.mount) {
        state.aiControlCleanup = window.PrimeAIControl.mount(panel, {
          guildId: state.guild.id,
          request: primeAIRequest,
          toast,
          initialDestination: view === "talk" ? "talk" : "overview",
          standalone: view === "talk",
        });
      } else {
        panel.append(
          el("div", {
            class: "notice",
            role: "alert",
            text: "تعذر تحميل وحدة PRIME AI. أعد تحميل لوحة التحكم.",
          }),
        );
      }
    }
    else if (view === "appearance") main.append(appearanceView());
    else if (["moderation", "community", "system"].includes(view)) main.append(operationsView(view));
    else main.append(settingsView());
    renderDock();
    renderDynamic();
    drawDashboardCharts();
  }
  function renderDynamic() {
    Object.keys(state.fields).forEach((k) => {
      const x = $(`#err-${k}`);
      if (x) x.textContent = state.fields[k];
    });
    const d = $(".dock");
    if (d) d.classList.toggle("show", dirty() || onboardingDirty());
    const onboardingSave = $(".onboarding-save");
    if (onboardingSave) onboardingSave.disabled = !onboardingDirty() || state.saving || !state.online;
    const onboardingTest = $(".onboarding-test");
    if (onboardingTest) onboardingTest.disabled = !state.online || state.saving;
    document.querySelectorAll("[data-role-matrix-key]").forEach((node) => {
      node.textContent = roleName(state.draft[node.dataset.roleMatrixKey]);
    });
    refreshEmbedPreview();
  }
  async function saveAll() {
    if (dirty()) await save();
    if (onboardingDirty()) await saveOnboarding();
  }
  function renderDock() {
    let d = $(".dock");
    if (d) d.remove();
    d = el(
      "div",
      { class: "dock" },
      el("div", { class: "dock-text", text: "⚠️ لديك تعديلات غير محفوظة" }),
      el("button", {
        class: "btn primary",
        type: "button",
        text: "حفظ التغييرات",
         onClick: saveAll,
      }),
      el("button", {
        class: "btn cancel",
        type: "button",
        text: "إلغاء",
        onClick: () => {
          state.draft = clone(state.baseline);
          state.fields = {};
          renderPage();
        },
      }),
    );
    document.body.append(d);
    renderDynamic();
  }
  // Saving and conflict handling
  async function save() {
    if (state.saving || !dirty() || !state.online) return;
    const guildId = state.guild.id,
      snap = changes(),
      sent = { ...clone(state.baseline), ...snap };
    state.saving = true;
    const b = $(".dock .primary");
    b.disabled = true;
    b.replaceChildren(el("span", { class: "spinner" }));
    try {
      const r = await writeApi(`api/guild/${guildId}/settings`, {
        revision: state.revision,
        changes: snap,
      });
      if (guildId !== state.guild.id) return;
      const data = await readJson(r, {});
      if (r.status === 200 && data.ok) {
        // تعديلات أُجريت أثناء الحفظ فقط هي التي تبقى غير محفوظة
        const later = Object.fromEntries(
          settingsKeys
            .filter((k) => !sameValue(state.draft[k], sent[k]))
            .map((k) => [k, state.draft[k]]),
        );
        if (data.revision >= state.revision) {
          state.baseline = clone(data.settings);
          state.revision = data.revision;
          state.updated = data.updated_at;
        }
        state.draft = { ...clone(state.baseline), ...later };
        state.newer = false;
        state.fields = {};
        navigator.vibrate?.([15, 30, 15]);
        toast("✅ تم تطبيق التحديثات فورياً على السيرفر", "success", 3500);
        renderPage();
      } else if (r.status === 400) {
        state.fields = data.fields || {};
        toast("يرجى مراجعة الحقول المعلّمة");
        renderDynamic();
      } else if (r.status === 403) {
        const reason = data.error === "csrf"
          ? "انتهت جلسة الحماية. أعد تحميل الصفحة ثم جرّب الحفظ."
          : "لا تملك صلاحية تعديل هذا السيرفر";
        toast(reason);
      }
      else if (r.status === 409) conflict(data);
      else if (r.status === 429)
        toast(`تم تجاوز الحد، حاول بعد ${data.retry_after} ثانية`, "warn", 5000);
      else toast("تعذر حفظ الإعدادات. حاول مجدداً");
    } catch (e) {
      if (e.message !== "unauth")
        toast("تعذر الاتصال بالخادم. احتفظنا بتعديلاتك");
    } finally {
      state.saving = false;
      if ($(".dock .primary")) {
        b.disabled = !state.online;
        b.textContent = "حفظ التغييرات";
      }
    }
  }
  async function saveOnboarding() {
    if (state.saving || !onboardingDirty() || !state.online) return false;
    const guildId = state.guild.id;
    const snap = onboardingChanges();
    const sent = { ...clone(state.baseline), ...snap };
    state.saving = true;
    const button = $(".onboarding-save");
    if (button) {
      button.disabled = true;
      button.replaceChildren(el("span", { class: "spinner" }), document.createTextNode(" جارٍ الحفظ…"));
    }
    try {
      const r = await writeApi(`api/guild/${guildId}/onboarding`, {
        revision: state.revision,
        changes: snap,
      });
      if (guildId !== state.guild.id) return false;
      const data = await readJson(r, {});
      if (r.ok && data.revision != null) {
        const later = Object.fromEntries(
          onboardingKeys
            .filter((key) => state.draft[key] !== sent[key])
            .map((key) => [key, state.draft[key]]),
        );
        state.baseline = { ...state.baseline, ...(data.settings || {}) };
        state.revision = data.revision;
        state.updated = data.updated_at;
        state.draft = { ...clone(state.baseline), ...later };
        state.onboarding = { ...state.onboarding, ...data, settings: data.settings || state.onboarding?.settings || {} };
        state.newer = false;
        state.fields = {};
        navigator.vibrate?.([15, 30, 15]);
        toast("✅ تم حفظ إعدادات الدخول وتطبيقها فورياً", "success", 3500);
        renderPage();
        return true;
      }
      if (r.status === 400) {
        state.fields = data.fields || {};
        toast("يرجى مراجعة حقول onboarding");
        renderDynamic();
      } else if (r.status === 403) {
        const reason = data.error === "csrf"
          ? "انتهت جلسة الحماية. أعد تحميل الصفحة ثم جرّب الحفظ."
          : "لا تملك صلاحية تعديل هذا السيرفر";
        toast(reason);
      }
      else if (r.status === 409) onboardingConflict(data);
      else if (r.status === 429) toast(`تم تجاوز الحد، حاول بعد ${data.retry_after || 5} ثانية`, "warn", 5000);
      else toast("تعذر حفظ إعدادات onboarding");
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال بالخادم. احتفظنا بتعديلاتك");
    } finally {
      state.saving = false;
      renderDynamic();
    }
    return false;
  }
  function onboardingConflict(data) {
    const back = el("div", { class: "modal-back", role: "dialog", "aria-modal": "true" });
    const modal = el(
      "div",
      { class: "modal" },
      el("h2", { text: "تعارض في إعدادات الدخول" }),
      el("p", { text: "تم تعديل استوديو onboarding من جلسة أخرى. اختر النسخة التي تريد اعتمادها." }),
    );
    const actions = el("div", { class: "modal-actions" });
    const adoptOnboarding = (keepLocal) => {
      const local = keepLocal ? onboardingChanges() : {};
      state.baseline = { ...state.baseline, ...(data.settings || {}) };
      state.revision = data.revision;
      state.updated = data.updated_at;
      state.draft = { ...clone(state.baseline), ...local };
      state.onboarding = { ...state.onboarding, ...data };
      state.newer = keepLocal && Object.keys(local).length > 0;
      back.remove();
      renderPage();
    };
    actions.append(
      el("button", { type: "button", text: "تحميل الأحدث", onClick: () => adoptOnboarding(false) }),
      el("button", { type: "button", text: "مراجعة تعديلي", onClick: () => adoptOnboarding(true) }),
    );
    modal.append(actions);
    back.append(modal);
    document.body.append(back);
  }
  async function sendTestWelcome() {
    if (!state.online || state.saving) return;
    if (onboardingDirty()) {
      const saved = await saveOnboarding();
      if (!saved) {
        toast("احفظ إعدادات onboarding أولاً لإرسال تجربة مطابقة لها", "warn");
        return;
      }
    }
    const channelId = state.draft?.welcome_channel_id;
    if (!channelId) {
      toast("اختر قناة الترحيب أولاً", "warn");
      return;
    }
    const button = $(".onboarding-test");
    if (button) {
      button.disabled = true;
      button.replaceChildren(el("span", { class: "spinner" }), document.createTextNode(" جارٍ الإرسال…"));
    }
    try {
      const r = await api(`api/guild/${state.guild.id}/onboarding/test-welcome`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": state.session.csrf,
        },
        body: JSON.stringify({
          target_channel_id: String(channelId),
          template_data: { username: "عضو تجريبي" },
        }),
      });
      const data = await readJson(r, {});
      if (r.ok && data.ok) toast("تم إرسال رسالة التجربة إلى Discord", "success", 3500);
      else toast(data.fields?.target_channel_id || "تعذر إرسال رسالة التجربة");
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال لإرسال رسالة التجربة");
    } finally {
      renderDynamic();
    }
  }
  async function deploySelfRoles() {
    const builder = ensureSelfRoleBuilder();
    if (!state.online || state.saving) return;
    if (!builder.target_channel_id) {
      toast("اختر قناة لوحة الرتب أولاً", "warn");
      return;
    }
    if (!builder.roles.length || builder.roles.length > 25) {
      toast("أضف من رتبة إلى 25 رتبة قابلة للإسناد", "warn");
      return;
    }
    const button = $(".builder-deploy");
    if (button) {
      button.disabled = true;
      button.replaceChildren(el("span", { class: "spinner" }), document.createTextNode(" جارٍ النشر…"));
    }
    try {
      const r = await api(`api/guild/${state.guild.id}/onboarding/self-roles`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": state.session.csrf,
        },
        body: JSON.stringify({
          target_channel_id: String(builder.target_channel_id),
          title: builder.title,
          description: builder.description,
          color: normalizePanelColor(builder.color),
          emoji: builder.emoji || "🏷️",
          roles: builder.roles.map((role) => ({
            id: String(role.id),
            label: String(role.label || roleName(role.id)).slice(0, 100),
            emoji: String(role.emoji || "").slice(0, 100),
          })),
        }),
      });
      const data = await readJson(r, {});
      if (r.ok && data.ok && data.panel) {
        state.onboarding = {
          ...state.onboarding,
          self_roles: [data.panel, ...(state.onboarding?.self_roles || [])],
        };
        navigator.vibrate?.([15, 30, 15]);
        toast("✅ نُشرت لوحة الرتب وحُفظت للاستعادة بعد إعادة التشغيل", "success", 4000);
        renderPage();
      } else toast(data.error === "role_not_assignable" ? "إحدى الرتب أعلى من رتبة البوت أو مُدارة" : "تعذر نشر لوحة الرتب");
    } catch (error) {
      if (error.message !== "unauth") toast("تعذر الاتصال لنشر لوحة الرتب");
    } finally {
      renderDynamic();
    }
  }
  function conflict(data) {
    const back = el("div", {
        class: "modal-back",
        role: "dialog",
        "aria-modal": "true",
      }),
      m = el(
        "div",
        { class: "modal" },
        el("h2", { text: "تعارض في الإعدادات" }),
        el("p", {
          text: "تم تعديل الإعدادات من جلسة أخرى. اختر كيف تريد المتابعة.",
        }),
      );
    const actions = el("div", { class: "modal-actions" });
    actions.append(
      el("button", {
        text: "تحميل الإصدار الأحدث",
        onClick: () => {
          adopt(data, false);
          back.remove();
          renderPage();
        },
      }),
      el("button", {
        text: "مراجعة",
        onClick: () => {
          adopt(data, true);
          back.remove();
          renderPage();
        },
      }),
    );
    m.append(actions);
    back.append(m);
    document.body.append(back);
  }
  // Guild loading and live events
  function stopIncidentRefresh() {
    if (state.incidentTimer) {
      clearInterval(state.incidentTimer);
      state.incidentTimer = null;
    }
  }
  async function refreshIncidents(id, redraw = false) {
    if (state.guild?.id !== id) return;
    try {
      const r = await api(`api/guild/${id}/security/incidents`);
      const data = await readJson(r, {});
      if (!r.ok || !data) return;
      if (state.guild?.id !== id) return;
      const lockChanged = state.lockdown !== Boolean(data.locked);
      state.incidents = data.incidents || [];
      state.whitelist = data.whitelist || [];
      state.lockdown = Boolean(data.locked);
      state.protectedChannels = data.protected_channels || [];
      if (redraw) {
        if (lockChanged) {
          const view = $("#view-security");
          if (view) view.replaceWith(securityView());
        } else {
          refreshIncidentBody();
        }
      }
    } catch (error) {
      if (error.message !== "unauth") updatePing("wait");
    }
  }
  async function refreshDashboardStats(id, redraw = false) {
    if (state.guild?.id !== id) return;
    const results = await Promise.allSettled([
      optionalJson(`api/guild/${id}/stats`, { counts: {}, series: [] }),
      optionalJson(`api/guild/${id}/actions`, { actions: [] }),
      optionalJson(
        `api/guilds/${id}/analytics?range=${encodeURIComponent(state.analyticsRange || "7d")}`,
        {},
      ),
    ]);
    try {
      const authFailure = results.find(
        (result) => result.status === "rejected" && result.reason?.message === "unauth",
      );
      if (authFailure) throw authFailure.reason;
      if (state.guild?.id !== id) return;
      if (results[0].status === "fulfilled") state.stats = results[0].value;
      if (results[1].status === "fulfilled") state.actions = results[1].value.actions || [];
      if (results[2].status === "fulfilled" && results[2].value?.summary) {
        state.analytics = results[2].value;
      }
      if (redraw && state.activeView !== "settings") renderPage();
    } catch (error) {
      if (error.message !== "unauth") updatePing("wait");
    }
  }
  async function fetchGuildAnalytics(id, range = state.analyticsRange, redraw = false) {
    if (state.guild?.id !== id) return null;
    const safeRange = ["today", "7d", "30d", "3m", "90d", "year"].includes(range)
      ? range
      : "7d";
    state.analyticsLoading = true;
    if (redraw && state.activeView === "overview") renderPage();
    try {
      const payload = await optionalJson(
        `api/guilds/${id}/analytics?range=${encodeURIComponent(safeRange)}`,
        {},
      );
      if (state.guild?.id !== id) return null;
      state.analyticsRange = safeRange;
      if (payload?.summary) state.analytics = payload;
      return payload;
    } finally {
      state.analyticsLoading = false;
      if (redraw && state.activeView === "overview") renderPage();
    }
  }
  function startIncidentRefresh(id) {
    stopIncidentRefresh();
    state.incidentTimer = setInterval(() => refreshIncidents(id, true), 15000);
  }
  async function fetchGuildMeta(id) {
    const meta = await optionalJson(`api/guild/${id}/meta`, {
      guild: state.guild || { id, name: "السيرفر" },
      channels: [],
      categories: [],
      roles: [],
      members: [],
      stickers: [],
      emojis: [],
    });
    if (state.guild?.id !== id) return meta;
    applyGuildMeta(meta);
    if (meta.stats) state.stats = meta.stats;
    state.commandStudio = {
      ...(state.commandStudio || {}),
      roles: meta.roles || [],
      channels: meta.channels || [],
    };
    state.autoResponderMeta = {
      roles: meta.roles || [],
      emojis: meta.emojis || [],
      members: meta.members || [],
    };
    return meta;
  }
  async function loadGuild(id) {
    const g = state.session.guilds.find((x) => x.id === id);
    if (!g) return;
    state.guild = g;
    sessionStorage.setItem("dashboard-guild", id);
    state.meta = state.baseline = state.draft = null;
    state.stats = null;
    state.analytics = null;
    state.analyticsLoading = false;
    state.actions = [];
    state.drawerOpen = false;
    state.onboarding = null;
    state.commandStudio = { commands: [], roles: [], channels: [] };
    state.commandRegistry = { categories: [], commands: [], policies: {}, byKey: {} };
    state.autoResponses = [];
    state.commandSearch = "";
    state.tickets = { active: [], archive: [], kpis: [], canned: [] };
    state.gaming = [];
    state.clanOps = { applications: [], roster: [], scrims: [], dropdown: { config: {}, categories: [] } };
    state.broadcast = {
      history: [],
      draft: {
        ...state.broadcast.draft,
        channel_id: "",
        content: "",
        title: "",
        description: "",
        thumbnail_url: "",
        image_url: "",
      },
    };
    state.ticketDropdown = {
      config: {
        embed_title: "مركز الدعم والتذاكر",
        embed_description: "",
        embed_color: "#5865F2",
        footer_text: "",
        channel_id: null,
        message_id: null,
      },
      categories: [],
    };
    state.economy = { wealth: [], settings: null, multipliers: {} };
    state.subscriptionDashboard = {
      ...state.subscriptionDashboard,
      data: null,
      loading: false,
      error: "",
      guildId: id,
      query: "",
      status: "all",
    };
    state.ticketSearch = "";
    state.ticketStatusFilter = "all";
    state.selfRoleBuilder = null;
    state.fields = {};
    renderShell();
    closeSSE();
    stopIncidentRefresh();
    try {
      const fallbacks = [
        {
          guild: state.guild,
          channels: [],
          categories: [],
          roles: [],
          members: [],
          stickers: [],
          emojis: [],
        },
        { revision: 0, updated_at: null, settings: {} },
        { incidents: [], whitelist: [], locked: false },
        { revision: 0, updated_at: null, settings: {} },
        { commands: [], roles: [], channels: [] },
        { categories: [], commands: [], policies: {} },
        { rules: [], channels: [], roles: [], emojis: [], members: [] },
        { tickets: [] },
        { tickets: [] },
        { kpis: [] },
        { responses: [] },
        { counts: {}, series: [] },
        { actions: [] },
        { scrims: [] },
        { wealth: [], settings: { settings: {} }, multipliers: {} },
        { channels: {} },
        { applications: [] },
        { roster: [] },
        { scrims: [] },
        { config: {}, categories: [] },
        { history: [] },
      ];
      const urls = [
        null,
        `api/guild/${id}/settings`,
        `api/guild/${id}/security/incidents`,
        `api/guild/${id}/onboarding`,
        `api/guild/${id}/commands`,
        `api/guild/${id}/commands/registry`,
        `api/guild/${id}/auto-responses`,
        `api/guild/${id}/tickets/active`,
        `api/guild/${id}/tickets/archive`,
        `api/guild/${id}/tickets/kpis`,
        `api/guild/${id}/tickets/canned`,
        `api/guild/${id}/stats`,
        `api/guild/${id}/actions`,
        `api/guild/${id}/gaming`,
        `api/guild/${id}/economy`,
        `api/guild/${id}/logs/channels`,
        `api/guild/${id}/clan/applications?status=all`,
        `api/guild/${id}/clan/roster`,
        `api/guild/${id}/clan/scrims`,
        `api/guild/${id}/tickets/dropdown-config`,
        `api/guild/${id}/broadcast/history`,
      ];
      const results = await Promise.allSettled(
        urls.map((url, index) =>
          index === 0
            ? fetchGuildMeta(id)
            : optionalJson(url, fallbacks[index]),
        ),
      );
      const authFailure = results.find(
        (result) => result.status === "rejected" && result.reason?.message === "unauth",
      );
      if (authFailure) throw authFailure.reason;
      const payload = (index) =>
        results[index].status === "fulfilled"
          ? results[index].value
          : fallbacks[index];
      if (state.guild.id !== id) return;
      const [
        meta,
        settings,
        incidents,
        onboarding,
        commands,
        registry,
        autoResponses,
        activeTickets,
        archiveTickets,
        ticketKpis,
        canned,
        stats,
        actions,
        gaming,
        economy,
        logRouting,
        clanApplications,
        clanRoster,
        clanScrims,
        ticketDropdown,
        broadcastHistory,
      ] = urls.map((_, index) => payload(index));
      if (state.guild.id !== id) return;
      applyGuildMeta(meta);
      state.broadcast.history = broadcastHistory.history || [];
      state.commandStudio = {
        commands: commands.commands || [],
        roles: commands.roles || meta.roles || [],
        channels: commands.channels || meta.channels || [],
      };
      state.commandRegistry = {
        categories: registry.categories || [],
        commands: registry.commands || [],
        policies: registry.policies || {},
        byKey: Object.fromEntries((registry.commands || []).map((item) => [String(item.key).toLowerCase(), item])),
      };
      state.selectedCommandIds = [];
      state.commandSearch = "";
      state.commandCogFilter = "all";
      state.commandStatusFilter = "all";
      state.commandRoleFilter = "all";
      state.commandDetail = null;
       state.autoResponses = autoResponses.rules || [];
      state.autoResponderMeta = {
        roles: autoResponses.roles || meta.roles || [],
        emojis: autoResponses.emojis || meta.emojis || [],
        members: autoResponses.members || meta.members || [],
      };
      state.tickets = {
        active: activeTickets.tickets || [],
        archive: archiveTickets.tickets || [],
        kpis: ticketKpis.kpis || [],
        canned: canned.responses || [],
      };
      state.incidents = incidents.incidents || [];
      state.whitelist = incidents.whitelist || [];
      state.lockdown = Boolean(incidents.locked);
      state.protectedChannels = incidents.protected_channels || [];
      state.stats = stats?.counts ? stats : meta.stats || stats;
      state.actions = actions.actions || [];
      state.gaming = gaming.scrims || [];
      state.clanOps = {
        applications: clanApplications.applications || [],
        roster: clanRoster.roster || [],
        scrims: clanScrims.scrims || [],
        dropdown: {
          config: ticketDropdown.config || {},
          categories: ticketDropdown.categories || [],
        },
      };
      state.economy = {
        wealth: economy.wealth || [],
        settings: economy.settings || { settings: {} },
        multipliers: economy.multipliers || {},
      };
      state.logRouting = logRouting || { channels: {} };
      state.baseline = clone(settings.settings);
      state.onboarding = onboarding;
      state.baseline = { ...state.baseline, ...(onboarding.settings || {}) };
      state.draft = clone(state.baseline);
      state.revision = onboarding.revision ?? settings.revision;
      state.updated = onboarding.updated_at ?? settings.updated_at;
      const ticketConfigData = await optionalJson(
        `api/guild/${id}/tickets/config`,
        { config: {}, categories: [] },
      );
      state.ticketConfig = { ...state.ticketConfig, ...(ticketConfigData.config || {}) };
      if (Array.isArray(ticketConfigData.categories) && ticketConfigData.categories.length) {
        state.ticketCategories = ticketConfigData.categories;
      }
      state.ticketDropdown = {
        config: { ...state.ticketDropdown.config, ...(ticketDropdown.config || {}) },
        categories: Array.isArray(ticketDropdown.categories) && ticketDropdown.categories.length
          ? ticketDropdown.categories
          : state.ticketCategories.map((item) => ({ ...item })),
      };
      await fetchGuildAnalytics(id, state.analyticsRange, false);
      await loadLevelingData(id);
      renderPage();
      await loadAndHydratePermissions(id);
      openSSE(id);
      startIncidentRefresh(id);
    } catch (e) {
      if (e.message !== "unauth" && state.guild.id === id) {
        $("#main").replaceChildren(
          el(
            "div",
            { class: "empty" },
            el("strong", { text: "تعذر تحميل الإعدادات" }),
            el("button", {
              class: "btn primary",
              text: "إعادة المحاولة",
              onClick: () => loadGuild(id),
            }),
          ),
        );
      }
    }
  }
  function chooseGuild(id) {
    if (id === state.guild.id) return;
    if (dirty()) {
      const back = el("div", {
          class: "modal-back",
          role: "dialog",
          "aria-modal": "true",
        }),
        m = el(
          "div",
          { class: "modal" },
          el("h2", { text: "لديك تعديلات غير محفوظة" }),
          el("p", { text: "ماذا تريد قبل الانتقال إلى سيرفر آخر؟" }),
        ),
        a = el("div", { class: "modal-actions" });
      a.append(
        el("button", {
          class: "save",
          text: "حفظ",
          onClick: async () => {
            await save();
            if (!dirty()) {
              back.remove();
              loadGuild(id);
            }
          },
        }),
        el("button", {
          text: "تجاهل",
          onClick: () => {
            back.remove();
            loadGuild(id);
          },
        }),
        el("button", { text: "البقاء", onClick: () => back.remove() }),
      );
      m.append(a);
      back.append(m);
      document.body.append(back);
    } else loadGuild(id);
  }
  function openSSE(id) {
    state.source = new EventSource(`api/guild/${id}/events`);
    state.source.addEventListener("settings", (e) => {
      if (state.guild?.id !== id) return;
      const d = JSON.parse(e.data);
      if (state.revision != null && d.revision <= state.revision) return;
      const wasDirty = dirty();
      adopt(d, true);
      renderPage();
      if (!wasDirty) toast("تم تحديث الإعدادات من جلسة أخرى", "info", 2600);
    });
    state.source.addEventListener("ping", (e) => {
      const d = JSON.parse(e.data);
      updatePing(d.online ? "online" : "offline", d.latency_ms);
    });
    state.source.addEventListener("expired", redirect);
    state.source.addEventListener("action", (e) => {
      if (state.guild?.id !== id) return;
      const payload = JSON.parse(e.data);
      state.actions = [{ ...payload, timestamp: new Date().toISOString() }, ...(state.actions || [])].slice(0, 100);
      if (state.activeView !== "settings") renderPage();
    });
    state.source.onerror = () => {
      updatePing("wait");
      setOffline(true);
    };
  }
  // Network and lifecycle events
  function closeSSE() {
    state.source?.close();
    state.source = null;
  }
  function setOffline(check = false) {
    if (!navigator.onLine || check) {
      state.online = false;
      let x = $(".net-banner");
      if (!x) {
        x = el("div", {
          class: "net-banner",
          text: "⚠️ انقطع الاتصال بالشبكة — الحفظ معطّل مؤقتاً",
        });
        document.body.append(x);
      }
      const b = $(".dock .primary");
      if (b) b.disabled = true;
    }
  }
  async function health() {
    if (!navigator.onLine) {
      setOffline(true);
      return;
    }
    try {
      const r = await api("api/health");
      if (!r.ok) throw Error();
      state.failures = 0;
      state.online = true;
      $(".net-banner")?.remove();
      const b = $(".dock .primary");
      if (b) b.disabled = false;
      await refreshDashboardStats(state.guild?.id, false);
    } catch (e) {
      state.failures++;
      if (state.failures >= 2) setOffline(true);
    }
  }
  async function start() {
    try {
      const r = await api("api/me"),
        me = await readJson(r, {});
      if (!me.auth) return redirect();
      state.session = me.session;
      try {
        await loadDashboardTheme();
      } catch (error) {
        if (error.message === "unauth") throw error;
        state.themeError = "theme_load_failed";
      }
      if (!state.session.guilds?.length) {
        const inviteUrl = state.session.invite_url;
         const botReady = state.session.bot_ready === true;
        app.replaceChildren(
          el(
            "main",
            { class: "page" },
            el(
              "div",
              { class: "empty access-empty" },
              el("strong", { text: "لا توجد سيرفرات مصرّح بها" }),
              el("span", {
                text: botReady
                  ? "البوت متصل، لكن لا توجد صلاحية إدارة في السيرفرات التي تملكها أو أن البوت غير مضاف إليها."
                  : "البوت غير متصل حالياً بـ Discord، لذلك لا تستطيع اللوحة رؤية السيرفرات التي أُضيف إليها.",
              }),
              inviteUrl
                ? el(
                    "div",
                    { class: "empty-actions" },
                    el(
                      "a",
                      {
                        class: "invite-button",
                        href: inviteUrl,
                        target: "_blank",
                        rel: "noopener noreferrer",
                      },
                      "دعوة البوت إلى سيرفر",
                    ),
                     el("small", {
                       class: "empty-hint",
                       text: botReady
                         ? "اختر السيرفر من صفحة Discord ثم وافق على الدعوة، وبعدها أعد تحميل الداشبورد."
                         : "شغّل البوت وانتظر ظهور رسالة الاتصال بـ Discord، ثم سجّل الخروج وأعد تسجيل الدخول.",
                     }),
                  )
                : el("span", {
                    class: "empty-hint",
                    text: "رابط دعوة البوت غير متاح حالياً. تحقق من إعداد CLIENT_ID ثم أعد المحاولة.",
                  }),
            ),
          ),
        );
        return;
      }
      const id = sessionStorage.getItem("dashboard-guild");
      loadGuild(
        state.session.guilds.some((g) => g.id === id)
          ? id
          : state.session.guilds[0].id,
      );
    } catch (e) {
      if (e.message !== "unauth")
        app.replaceChildren(
          el(
            "main",
            { class: "page" },
            el("div", {
              class: "empty",
              text: "تعذر التحقق من الجلسة. أعد تحميل الصفحة.",
            }),
          ),
        );
    }
  }
  const publicLeaderboardPath = /^\/(?:api\/)?lb\/([^/]+)\/?$/.exec(location.pathname);
  async function renderPublicLeaderboard() {
    document.body.classList.add("public-lb-route");
    const appRoot = $("#app");
    const slug = publicLeaderboardPath ? decodeURIComponent(publicLeaderboardPath[1]) : "";
    const view = { mode: "text", page: 1, userId: "", data: null, loading: true, error: "", request: 0 };
    const fmt = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat("ar").format(Number(value)) : "—";
    const initials = (name) => String(name || "؟").trim().slice(0, 1) || "؟";
    const safeDate = (value) => {
      if (!value) return "غير متاح";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("ar", { dateStyle: "medium", timeStyle: "short" }).format(date);
    };
    const avatarNode = (url, name) => {
      const fallback = el("span", { class: "public-lb-avatar-fallback", text: initials(name), "aria-hidden": "true" });
      if (!url) return el("span", { class: "public-lb-avatar" }, fallback);
      const img = el("img", { src: url, alt: "", loading: "lazy" });
      img.addEventListener("error", () => img.replaceWith(fallback), { once: true });
      return el("span", { class: "public-lb-avatar" }, img);
    };
    const heading = () => el("header", { class: "public-lb-top" },
      el("a", { class: "public-lb-brand", href: "/", "aria-label": "PRIME" }, el("span", { class: "public-lb-mark", text: "P" }), el("span", { text: "PRIME" })),
      el("span", { class: "public-lb-tag", text: "COMMUNITY RANKING" }));
    const request = async () => {
      const requestId = ++view.request;
      view.loading = true;
      view.error = "";
      draw();
      const query = new URLSearchParams({ mode: view.mode, page: String(view.page) });
      if (view.userId) query.set("user_id", view.userId);
      try {
        if (!PUBLIC_SLUG_RE.test(slug)) throw Object.assign(new Error("invalid-slug"), { status: 400 });
        const response = await fetch(`${urlMountPrefix}/lb/${encodeURIComponent(slug)}/data?${query}`, {
          method: "GET", headers: { Accept: "application/json" }, credentials: "omit", cache: "no-store",
        });
        let data = null;
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) throw Object.assign(new Error("unavailable"), { status: response.status, data });
        if (!data || !Array.isArray(data.rows)) throw Object.assign(new Error("invalid-response"), { status: 502 });
        if (requestId !== view.request) return;
        view.data = data;
      } catch (error) {
        if (requestId !== view.request) return;
        view.data = null;
        view.error = error.status === 404 || error.status === 400 || error.message === "invalid-slug"
          ? "تعذر العثور على لوحة عامة بهذا الرابط. قد يكون الرابط غير صالح أو أن اللوحة غير متاحة."
          : "تعذر تحميل الترتيب الآن. تحقق من اتصالك وحاول مرة أخرى.";
      } finally {
        if (requestId === view.request) {
          view.loading = false;
          draw();
        }
      }
    };
    const draw = () => {
      if (!appRoot) return;
      const data = view.data;
      const title = data?.server?.name || "لوحة الترتيب";
      const icon = data?.server?.iconUrl
        ? el("img", { src: data.server.iconUrl, alt: "", class: "public-lb-server-icon" })
        : el("span", { class: "public-lb-server-icon public-lb-server-fallback", text: initials(title) });
      const hero = el("section", { class: "public-lb-hero" },
        el("div", { class: "public-lb-orbit public-lb-orbit-one", "aria-hidden": "true" }),
        el("div", { class: "public-lb-orbit public-lb-orbit-two", "aria-hidden": "true" }),
        el("div", { class: "public-lb-server" }, icon,
          el("div", {}, el("span", { class: "public-lb-eyebrow", text: "PRIME / LEADERBOARD" }),
            el("h1", { text: title }), el("p", { text: "الترتيب يُحسم بالنقاط. ابدأ التحدي." }))),
        el("div", { class: "public-lb-hero-index" }, el("span", { text: "RANK" }), el("b", { text: "01—10" })));
      const mode = el("div", { class: "public-lb-modes", role: "tablist", "aria-label": "نوع الترتيب" },
        ...[["text", "النص"], ["voice", "الصوت"]].map(([value, label]) => el("button", {
          type: "button", role: "tab", "aria-selected": String(view.mode === value),
          class: view.mode === value ? "is-active" : "",
          text: label,
          onClick: () => { if (view.mode !== value) { view.mode = value; view.page = 1; request(); } },
        })));
      const summary = data ? el("section", { class: "public-lb-summary", "aria-label": "ملخص السيرفر" },
        ...[
          ["الأعضاء", fmt(data.server?.memberCount)],
          ["حسابات لديها نقاط", fmt(data.summary?.activeMembers)],
          ["إجمالي XP", fmt(data.summary?.totalXp)],
          ["آخر تحديث", safeDate(data.summary?.lastUpdated)],
        ].map(([label, value], i) => el("div", { class: `public-lb-stat public-lb-stat-${i + 1}` },
          el("span", { text: label }), el("b", { text: value })))) : null;
      const controls = el("div", { class: "public-lb-controls" },
        mode,
        el("span", { class: "public-lb-page-caption", text: data ? `صفحة ${fmt(data.page)} من ${fmt(data.totalPages)}` : "أفضل اللاعبين" }));
      const body = view.loading
        ? el("div", { class: "public-lb-loading", role: "status", "aria-label": "جارٍ تحميل الترتيب" },
          ...Array.from({ length: 5 }, (_, i) => el("div", { class: "public-lb-skeleton", style: `--row:${i}` })))
        : view.error
          ? el("div", { class: "public-lb-state public-lb-error", role: "alert" },
            el("span", { class: "public-lb-state-mark", text: "!" }),
            el("h2", { text: "الترتيب غير متاح" }),
            el("p", { text: view.error }),
            el("button", { type: "button", class: "public-lb-retry", text: "إعادة المحاولة", onClick: request }))
          : !data?.rows?.length
            ? el("div", { class: "public-lb-state", role: "status" },
              el("span", { class: "public-lb-state-mark", text: "—" }),
              el("h2", { text: "لا يوجد ترتيب بعد" }),
              el("p", { text: "ستظهر هنا أسماء الأعضاء عند بدء اكتساب النقاط." }))
            : el("div", { class: "public-lb-table-wrap" },
              el("table", { class: "public-lb-table" },
                el("thead", {}, el("tr", {},
                  ...["المركز", "العضو", "المستوى", "XP", "التقدم", view.mode === "text" ? "الرسائل" : "وقت الصوت", "التواصل"].map((label) => el("th", { scope: "col", text: label })))),
                el("tbody", {}, ...data.rows.map((row) => {
                  const removed = row.removed === true;
                  const userName = removed ? "عضو غير متاح" : (row.name || "عضو");
                  const rankTone = Number(row.rank) <= 3 ? ` rank-${row.rank}` : "";
                  const progress = row.progress || {};
                  const percent = Math.max(0, Math.min(100, Number(progress.percentage) || 0));
                  return el("tr", { class: `${removed ? "is-removed" : ""}${rankTone}` },
                    el("td", {}, el("span", { class: "public-lb-rank", text: `#${fmt(row.rank)}` })),
                    el("td", {}, el("div", { class: "public-lb-member" }, avatarNode(removed ? null : row.avatarUrl, userName),
                      el("span", { class: "public-lb-member-name", text: userName }))),
                    el("td", { class: "public-lb-level", "data-label": "المستوى" }, removed ? "—" : fmt(row.level)),
                    el("td", { class: "public-lb-xp", "data-label": "XP" }, removed ? "—" : fmt(row.xp)),
                    el("td", { class: "public-lb-progress-cell", "data-label": "التقدم" },
                      removed ? "—" : el("div", { class: "public-lb-progress" },
                        el("span", { class: "public-lb-progress-track" }, el("i", { style: `width:${percent}%` })),
                        el("small", { text: `${fmt(progress.current)} / ${fmt(progress.required)}` }))),
                    el("td", { class: "public-lb-activity", "data-label": view.mode === "text" ? "الرسائل" : "وقت الصوت" }, removed ? "—" : fmt(row.activityTotal)),
                    el("td", { class: "public-lb-streak", "data-label": "التواصل" }, removed ? "—" : `${fmt(row.streak)} يوم`));
                }))));
      const pages = data && data.totalPages > 1 ? el("nav", { class: "public-lb-pagination", "aria-label": "صفحات الترتيب" },
        el("button", { type: "button", disabled: view.page <= 1 || view.loading, text: "السابق", onClick: () => { view.page--; request(); } }),
        el("span", { text: `${fmt(view.page)} / ${fmt(data.totalPages)}` }),
        el("button", { type: "button", disabled: view.page >= data.totalPages || view.loading, text: "التالي", onClick: () => { view.page++; request(); } })) : null;
      const viewer = el("form", {
        class: "public-lb-lookup",
        onSubmit: (event) => {
          event.preventDefault();
          const input = $("input", event.currentTarget);
          const id = input.value.trim();
          if (!/^\d{17,20}$/.test(id)) {
            input.setAttribute("aria-invalid", "true");
            $(".public-lb-lookup-message", event.currentTarget).textContent = "أدخل معرّف Discord رقميّاً صالحاً.";
            return;
          }
          input.removeAttribute("aria-invalid");
          $(".public-lb-lookup-message", event.currentTarget).textContent = "";
          view.userId = id;
          view.page = 1;
          request();
        },
      },
        el("div", {}, el("span", { class: "public-lb-eyebrow", text: "YOUR POSITION" }), el("h2", { text: "أين ترتيبك؟" }), el("p", { text: "أدخل معرّف Discord الخاص بك للبحث عن مركزك." })),
        el("div", { class: "public-lb-lookup-input" },
          el("input", { type: "text", inputmode: "numeric", dir: "ltr", autocomplete: "off", maxlength: "20", placeholder: "معرّف Discord", value: view.userId, "aria-label": "معرّف Discord الخاص بك" }),
          el("button", { type: "submit", disabled: view.loading, text: "اعثر على ترتيبي" }),
          el("small", { class: "public-lb-lookup-message", role: "status", "aria-live": "polite" })),
        data?.viewerRank ? el("div", { class: "public-lb-viewer-result" },
          avatarNode(data.viewerRank.avatarUrl, data.viewerRank.name),
          el("span", { text: data.viewerRank.name || "عضو" }),
          el("b", { text: `#${fmt(data.viewerRank.rank)}` }),
          el("small", { text: `المستوى ${fmt(data.viewerRank.level)} · ${fmt(data.viewerRank.xp)} XP` })) : null,
        view.userId && data && !data.viewerRank ? el("p", { class: "public-lb-lookup-message", text: "لم يظهر هذا الحساب في ترتيب السيرفر." }) : null);
      appRoot.replaceChildren(el("main", { class: "public-lb-page", dir: "rtl" },
        heading(),
        hero,
        summary,
        el("section", { class: "public-lb-board" },
          el("div", { class: "public-lb-board-heading" },
            el("div", {}, el("span", { class: "public-lb-eyebrow", text: "THE LEADERS" }), el("h2", { text: "المتصدرون" })),
            controls),
          body,
          pages),
        viewer,
        el("footer", { class: "public-lb-footer" }, el("span", {}, "PRIME", el("i", { text: " / " }), "الطموح يبدأ من هنا"), el("span", { text: "لوحة ترتيب مجتمعية" }))));
    };
    await request();
  }
  addEventListener("keydown", (e) => {
    if (publicLeaderboardPath) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openCommandPalette();
      return;
    }
    if (e.key === "Escape") {
      $(".command-palette-back")?.remove();
      toggleDrawer(false);
    }
    if (e.key !== "Escape") return;
    document.querySelectorAll(".popover:not([hidden])").forEach((p) => {
      p.hidden = true;
      const trigger = p.parentElement?.querySelector("button");
      trigger?.setAttribute("aria-expanded", "false");
      trigger?.focus();
    });
  });
  addEventListener("pointerdown", (e) => {
    if (publicLeaderboardPath) return;
    document.querySelectorAll(".popover:not([hidden])").forEach((p) => {
      if (!p.parentElement.contains(e.target)) {
        p.hidden = true;
        p.parentElement.querySelector("button")?.setAttribute("aria-expanded", "false");
      }
    });
  });
  if (!publicLeaderboardPath) {
    addEventListener("online", health);
    addEventListener("offline", () => setOffline(true));
    addEventListener("beforeunload", (e) => {
      closeSSE();
      if (dirty()) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }
  if (publicLeaderboardPath) {
    renderPublicLeaderboard().catch(() => {
      document.body.classList.add("public-lb-route");
      app?.replaceChildren(el("main", { class: "public-lb-page public-lb-state", role: "alert" },
        el("h1", { text: "تعذر فتح لوحة الترتيب" }),
        el("p", { text: "أعد تحميل الصفحة أو تحقق من الرابط." })));
    });
  } else {
    setInterval(health, 15000);
    setupPwa();
    start();
  }
})();
