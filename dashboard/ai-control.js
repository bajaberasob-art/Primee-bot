(function () {
  "use strict";

  function mount(container, options) {
    if (!container || typeof container.replaceChildren !== "function") {
      throw new TypeError("PrimeAIControl.mount requires a DOM container.");
    }
    const config = options || {};
    if (!config.guildId) throw new TypeError("PrimeAIControl.mount requires guildId.");
    if (typeof config.request !== "function") {
      throw new TypeError("PrimeAIControl.mount requires a request adapter.");
    }

    const guildPath = `api/guild/${encodeURIComponent(String(config.guildId))}/ai`;
    const state = {
      disposed: false,
      settingsLoading: true,
      settingsError: "",
      auditLoading: true,
      auditError: "",
      settings: null,
      settingsConflict: null,
      settingsSaveError: "",
      channels: [],
      memories: [],
      memoryError: "",
      events: [],
      enabled: false,
      systemPrompt: "",
      allowedChannels: [],
      settingsSaving: false,
      memoryContent: "",
      memorySaving: false,
      deletingMemoryId: null,
      testPrompt: "",
      testing: false,
      testResult: null,
      sandboxPrompt: "",
      sandboxChannelId: "",
      sandboxRunning: false,
      sandboxResult: null,
      sandboxError: "",
      controlLoading: true,
      controlError: "",
      controlSnapshot: null,
      controlDraft: null,
      controlLoaded: false,
      controlSaving: false,
      controlConflict: null,
      controlSaveError: "",
      controlChannels: [],
      actionRegistry: [],
      skills: [],
      skillDrafts: {},
      skillSaving: null,
      skillErrors: {},
      operations: [],
      operationsLoading: true,
      operationsError: "",
      analytics: null,
      analyticsLoading: true,
      analyticsError: "",
      isBotOwner: false,
      roles: [],
      providerStatus: "unknown",
      memoryScope: "SERVER",
      memoryScopeId: "",
      memoryExpiresInDays: null,
      memoryExpiryOverridden: false,
      memoryEnabled: true,
      editingMemory: null,
      selectedPersonaChannel: "",
      selectedOverrideRole: "",
      activeDestination: config.initialDestination || "overview",
    };
    const navigationStorageKey = `prime-ai:${String(config.guildId)}:${config.standalone ? "talk" : "control"}:destination`;
    if (!config.standalone) {
      try {
        const savedDestination = sessionStorage.getItem(navigationStorageKey);
        if (savedDestination) state.activeDestination = savedDestination;
      } catch (_) {
        // Navigation still works when browser storage is unavailable.
      }
    }

    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    };
    const button = (label, action, className, disabled) => {
      const node = el("button", className || "prime-ai-button", label);
      node.type = "button";
      node.dataset.action = action;
      node.disabled = Boolean(disabled);
      return node;
    };
    const sectionHeader = (title, description, titleId) => {
      const head = el("div", "prime-ai-section-head");
      const text = el("div");
      const heading = el("h2", "", title);
      if (titleId) heading.id = titleId;
      text.append(heading);
      if (description) text.append(el("p", "", description));
      head.append(text);
      return head;
    };
    const setToast = (message, type) => {
      if (typeof config.toast === "function") {
        try {
          config.toast(message, type || "error");
        } catch (_) {
          // Feedback remains available in the screen when a host toast fails.
        }
      }
    };
    const request = (method, path, body) => config.request(method, path, body);
    const ensureOk = (response) => {
      if (!response || !response.ok) {
        throw new Error(`request_failed_${response ? response.status : "network"}`);
      }
      return response;
    };
    const responseJson = async (response) => {
      const contentType = response.headers && response.headers.get
        ? response.headers.get("content-type") || ""
        : "";
      if (!contentType.toLowerCase().includes("application/json")) return {};
      try {
        return await response.json();
      } catch (_) {
        throw new Error("invalid_response");
      }
    };
    const readableDate = (value) => {
      if (!value) return "غير متوفر";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat("ar", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
    };
    const errorText = () => "تعذر تحميل البيانات. تحقق من الاتصال ثم أعد المحاولة.";
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const controlConfig = () => state.controlDraft || {};
    const controlHasChanges = () => Boolean(
      state.controlSnapshot &&
      state.controlDraft &&
      JSON.stringify(state.controlDraft) !== JSON.stringify(state.controlSnapshot.config),
    );
    const syncSaveDock = () => {
      const dock = container.querySelector(".prime-ai-save-dock");
      if (!dock) return;
      const dirty = controlHasChanges();
      const saveButton = dock.querySelector('[data-action="save-control"]');
      const discardButton = dock.querySelector('[data-action="discard-control"]');
      const conflictButton = dock.querySelector('[data-action="apply-control-conflict"]');
      const status = dock.querySelector(".prime-ai-save-status");
      const conflicted = Boolean(state.controlConflict);
      dock.classList.toggle("has-conflict", conflicted);
      dock.hidden = !dirty && !conflicted && !state.controlSaving;
      if (status) {
        status.textContent = conflicted
          ? "تغيّرت نسخة الخادم. حمّلها لمراجعة الإعدادات قبل الحفظ."
          : state.controlSaving
            ? "جارٍ حفظ التغييرات..."
            : state.controlSaveError
              ? "تعذر حفظ التغييرات. راجع التنبيه وحاول مرة أخرى."
              : "لديك تغييرات غير محفوظة.";
      }
      if (saveButton) {
        saveButton.disabled = !dirty || state.controlSaving || conflicted;
        saveButton.textContent = state.controlSaving ? "جارٍ الحفظ..." : "حفظ التغييرات";
      }
      if (discardButton) {
        discardButton.hidden = !dirty || conflicted || state.controlSaving;
      }
      if (conflictButton) conflictButton.hidden = !conflicted;
    };
    const configuredMemoryExpirationDays = () => {
      const value = state.controlSnapshot?.config?.memory?.default_expiration_days;
      return Number.isInteger(value) && value >= 0 && value <= 3650 ? value : 90;
    };
    const effectiveMemoryExpirationDays = () => (
      state.editingMemory || state.memoryExpiryOverridden
        ? state.memoryExpiresInDays
        : configuredMemoryExpirationDays()
    );
    const readPath = (object, path) => path.split(".").reduce(
      (value, key) => value && value[key] !== undefined ? value[key] : undefined,
      object,
    );
    const writePath = (object, path, value) => {
      const parts = path.split(".");
      let target = object;
      parts.slice(0, -1).forEach((key) => {
        if (!target[key] || typeof target[key] !== "object") target[key] = {};
        target = target[key];
      });
      target[parts[parts.length - 1]] = value;
    };
    const availableChannels = () => state.controlChannels.length
      ? state.controlChannels
      : state.channels;
    const scopeLabel = (memory) => {
      const scope = String(memory.scope || "SERVER").toUpperCase();
      if (scope === "GLOBAL") return "ذاكرة عامة — مالك البوت";
      if (scope === "CHANNEL") {
        const channel = availableChannels().find((item) => String(item.id) === String(memory.scope_id));
        return `قناة: ${channel ? `#${channel.name}` : memory.scope_id}`;
      }
      if (scope === "ROLE") {
        const role = state.roles.find((item) => String(item.id) === String(memory.scope_id));
        return `رتبة: ${role ? role.name : memory.scope_id}`;
      }
      if (scope === "USER") return `عضو: ${memory.scope_id}`;
      return "الخادم";
    };
    const daysUntil = (value) => {
      if (!value) return 0;
      const remaining = Date.parse(value) - Date.now();
      return Number.isFinite(remaining) && remaining > 0
        ? Math.ceil(remaining / 86400000)
        : 0;
    };
    const fieldValue = (target) => {
      if (target.type === "checkbox") return Boolean(target.checked);
      if (target.multiple) {
        return Array.from(target.selectedOptions || [])
          .map((option) => String(option.value));
      }
      if (target.type === "number" || target.type === "range") {
        return target.value === "" ? null : Number(target.value);
      }
      return target.value;
    };
    const updateDraft = (path, value) => {
      if (path.startsWith("skill.")) {
        const parts = path.split(".");
        const key = parts[1];
        const skillPath = parts.slice(2).join(".");
        const current = state.skillDrafts[key];
        if (current && skillPath) writePath(current, skillPath, value);
        return;
      }
      if (state.controlDraft) writePath(state.controlDraft, path, value);
    };
    const applyControlSnapshot = (snapshot, preserveDraft) => {
      if (!snapshot || !snapshot.config || !Number.isInteger(snapshot.revision)) {
        throw new Error("invalid_response");
      }
      state.controlSnapshot = snapshot;
      const allowlist = snapshot.config.access && snapshot.config.access.allowed_channels;
      if (Array.isArray(allowlist)) state.allowedChannels = allowlist.map(String);
      if (!preserveDraft || !state.controlDraft) {
        state.controlDraft = clone(snapshot.config);
      }
    };
    const installSkills = (skills, preserveDraft) => {
      if (!Array.isArray(skills)) throw new Error("invalid_response");
      state.skills = skills;
      if (!preserveDraft) {
        state.skillDrafts = Object.fromEntries(
          skills.map((skill) => [skill.key, clone(skill)]),
        );
      }
    };
    function controlField(label, input, help) {
      const wrap = el("label", "prime-ai-control-field");
      wrap.append(el("span", "prime-ai-control-label", label), input);
      if (help) wrap.append(el("small", "prime-ai-help", help));
      return wrap;
    }
    function controlDisclosure(title, description, open) {
      const details = el("details", "prime-ai-disclosure");
      details.open = Boolean(open);
      const summary = el("summary", "prime-ai-disclosure-summary");
      const copy = el("span", "prime-ai-disclosure-copy");
      copy.append(el("strong", "", title));
      if (description) copy.append(el("small", "", description));
      summary.append(copy, el("span", "prime-ai-disclosure-indicator", "⌄"));
      const content = el("div", "prime-ai-disclosure-content");
      details.append(summary, content);
      return { details, content };
    }
    function controlInput(path, label, value, options) {
      const opts = options || {};
      const input = el(opts.multiline ? "textarea" : "input", opts.multiline ? "prime-ai-textarea" : "prime-ai-control-input");
      if (opts.multiline) input.rows = opts.rows || 3;
      else input.type = opts.type || "text";
      input.value = value === undefined || value === null ? "" : String(value);
      input.dataset.controlPath = path;
      if (opts.type === "number" || opts.type === "range") {
        if (opts.min !== undefined) input.min = String(opts.min);
        if (opts.max !== undefined) input.max = String(opts.max);
        if (opts.step !== undefined) input.step = String(opts.step);
      }
      if (opts.maxLength) input.maxLength = opts.maxLength;
      input.disabled = state.controlSaving;
      return controlField(label, input, opts.help);
    }
    function controlSelect(path, label, value, choices, multiple, help) {
      const select = el("select", "prime-ai-control-input");
      select.dataset.controlPath = path;
      select.multiple = Boolean(multiple);
      if (multiple) select.size = Math.min(5, Math.max(3, choices.length));
      choices.forEach((choice) => {
        const option = el("option", "", choice.label);
        option.value = String(choice.value);
        option.selected = multiple
          ? (Array.isArray(value) && value.map(String).includes(String(choice.value)))
          : String(value) === String(choice.value);
        select.append(option);
      });
      select.disabled = state.controlSaving;
      return controlField(
        label,
        select,
        help || (multiple ? "يمكن اختيار أكثر من عنصر." : ""),
      );
    }
    function controlToggle(path, label, checked, help) {
      const wrap = el("label", "prime-ai-control-toggle");
      const copy = el("span", "prime-ai-control-toggle-copy");
      copy.append(el("strong", "", label));
      if (help) copy.append(el("small", "prime-ai-help", help));
      const input = el("input");
      input.type = "checkbox";
      input.checked = Boolean(checked);
      input.dataset.controlPath = path;
      input.disabled = state.controlSaving;
      wrap.append(copy, input);
      return wrap;
    }

    function loadingCard(label) {
      const card = el("section", "prime-ai-card prime-ai-loading");
      card.setAttribute("aria-label", label);
      card.setAttribute("aria-busy", "true");
      card.append(el("span", "prime-ai-skeleton prime-ai-skeleton-title"));
      card.append(el("span", "prime-ai-skeleton"));
      card.append(el("span", "prime-ai-skeleton prime-ai-skeleton-short"));
      return card;
    }

    function loadError(message, action) {
      const box = el("div", "prime-ai-error");
      box.setAttribute("role", "alert");
      box.append(el("p", "", message || errorText()));
      box.append(button("إعادة المحاولة", action, "prime-ai-button prime-ai-button-secondary"));
      return box;
    }

    function renderSettingsCard() {
      const card = el("section", "prime-ai-card prime-ai-settings-card");
      card.setAttribute("aria-labelledby", "prime-ai-settings-title");
      card.append(sectionHeader("إعدادات المساعد", "تحكم في نطاق عمل PRIME AI داخل هذا الخادم.", "prime-ai-settings-title"));
      if (state.settingsLoading) {
        card.append(loadingCard("جارٍ تحميل الإعدادات"));
        return card;
      }
      if (state.settingsError) {
        card.append(loadError(state.settingsError, "reload-settings"));
        return card;
      }
      if (state.settingsConflict) {
        const conflict = el("div", "prime-ai-conflict");
        conflict.setAttribute("role", "alert");
        conflict.append(el("strong", "", "تغيّرت الإعدادات على الخادم"));
        conflict.append(el("p", "", "احتفظنا بتعديلاتك الحالية. حمّل النسخة الحالية من الخادم قبل إجراء تغييرات جديدة."));
        conflict.append(el("small", "", `المراجعة الحالية على الخادم: ${state.settingsConflict.revision}`));
        conflict.append(button(
          "تحميل الإعدادات الحالية",
          "apply-conflict-settings",
          "prime-ai-button prime-ai-button-secondary",
          state.settingsSaving,
        ));
        card.append(conflict);
      }
      if (state.settingsSaveError) {
        const error = el("div", "prime-ai-error");
        error.setAttribute("role", "alert");
        error.append(el("p", "", state.settingsSaveError));
        error.append(button("إعادة تحميل الإعدادات", "reload-settings", "prime-ai-button prime-ai-button-secondary"));
        card.append(error);
      }

      const statusRow = el("div", "prime-ai-status-row");
      const statusCopy = el("div", "prime-ai-status-copy");
      statusCopy.append(el("strong", "", "حالة المساعد"));
      statusCopy.append(el("span", "", state.enabled ? "مفعّل لهذا الخادم" : "متوقف لهذا الخادم"));
      const toggleLabel = el("label", "prime-ai-switch");
      const toggle = el("input");
      toggle.type = "checkbox";
      toggle.checked = state.enabled;
      toggle.dataset.field = "enabled";
      toggle.setAttribute("role", "switch");
      toggle.setAttribute("aria-label", "تفعيل PRIME AI");
      toggle.disabled = state.settingsSaving;
      const switchTrack = el("span", "prime-ai-switch-track");
      toggleLabel.append(toggle, switchTrack);
      statusRow.append(statusCopy, toggleLabel);
      card.append(statusRow);

      const promptField = el("div", "prime-ai-field");
      const promptLabel = el("label", "", "تعليمات النظام");
      promptLabel.htmlFor = "prime-ai-system-prompt";
      const prompt = el("textarea", "prime-ai-textarea");
      prompt.id = "prime-ai-system-prompt";
      prompt.rows = 7;
      prompt.maxLength = 1500;
      prompt.value = state.systemPrompt;
      prompt.placeholder = "اكتب أسلوب المساعد وحدود إجاباته لهذا الخادم.";
      prompt.dataset.field = "system-prompt";
      prompt.disabled = state.settingsSaving;
      promptField.append(promptLabel, prompt);
      promptField.append(el("small", "prime-ai-help", "تضيف هذه التعليمات سياقاً للمحادثة ولا تستبدل أنظمة الخبرة أو السلاسل أو الاشتراكات."));
      card.append(promptField);

      const settingsMeta = el("div", "prime-ai-meta");
      const provider = state.settings && state.settings.provider
        ? state.settings.provider
        : "غير محدد";
      settingsMeta.append(el("span", "", `المزوّد: ${provider}`));
      settingsMeta.append(el("span", "", `آخر تحديث: ${readableDate(state.settings && state.settings.updated_at)}`));
      card.append(settingsMeta);
      const actions = el("div", "prime-ai-actions");
      actions.append(button(
        state.settingsSaving ? "جارٍ الحفظ..." : "حفظ الإعدادات",
        "save-settings",
        "prime-ai-button prime-ai-button-primary",
        state.settingsSaving || Boolean(state.settingsConflict) || !state.controlLoaded,
      ));
      if (!state.controlLoaded) {
        actions.append(el(
          "small",
          "prime-ai-help",
          state.controlError
            ? "حمّل مركز التحكم قبل الحفظ حتى لا تُستبدل سياسة القنوات بنسخة قديمة."
            : "جارٍ تحميل سياسة الوصول الموحدة...",
        ));
      }
      card.append(actions);
      return card;
    }

    function renderMemoriesCard() {
      const card = el("section", "prime-ai-card");
      const memoryPolicy = controlConfig().memory || {};
      const canCreateMemory = memoryPolicy.enabled !== false &&
        memoryPolicy.creation_enabled !== false;
      const contentLimit = Math.max(
        100,
        Math.min(1000, Number(memoryPolicy.maximum_content_length) || 1000),
      );
      card.setAttribute("aria-labelledby", "prime-ai-memories-title");
      card.append(sectionHeader(
        "ذاكرة PRIME AI",
        "تُدار هنا ملاحظات الخادم فقط. ذكريات المستخدم خاصة بصاحبها ولا تظهر للمشرفين؛ طلب التذكّر يتطلب موافقته في الخاص.",
        "prime-ai-memories-title",
      ));
      if (!state.controlLoaded && state.controlLoading) {
        card.append(loadingCard("جارٍ تحميل ذاكرة الخادم"));
        return card;
      }
      if (!state.controlLoaded) {
        card.append(loadError(state.controlError, "reload-control"));
        return card;
      }

      const form = el("form", "prime-ai-memory-form");
      form.dataset.form = "memory";
      const input = el("textarea", "prime-ai-textarea");
      input.id = "prime-ai-memory-content";
      input.rows = 3;
      input.maxLength = contentLimit;
      input.placeholder = "أضف ملاحظة إدارية يحتاجها المساعد عند الرد.";
      input.value = state.memoryContent;
      input.dataset.field = "memory-content";
      input.disabled = state.memorySaving || (!canCreateMemory && !state.editingMemory);

      const scope = el("select", "prime-ai-control-input");
      scope.dataset.localField = "memory-scope";
      const scopes = [
        { value: "SERVER", label: "الخادم" },
        { value: "CHANNEL", label: "قناة محددة" },
        { value: "ROLE", label: "رتبة محددة" },
      ];
      if (state.isBotOwner) scopes.push({ value: "GLOBAL", label: "عام — جميع الخوادم" });
      scopes.forEach((item) => {
        const option = el("option", "", item.label);
        option.value = item.value;
        option.selected = item.value === state.memoryScope;
        scope.append(option);
      });
      scope.disabled = state.memorySaving || (!canCreateMemory && !state.editingMemory);
      const scopeField = controlField("نطاق الذاكرة", scope);

      let scopeIdField = null;
      if (state.memoryScope === "CHANNEL" || state.memoryScope === "ROLE") {
        const values = state.memoryScope === "CHANNEL"
          ? availableChannels().map((item) => ({ value: item.id, label: `#${item.name || item.id}` }))
          : state.roles.map((item) => ({ value: item.id, label: item.name || item.id }));
        const scopeId = el("select", "prime-ai-control-input");
        scopeId.dataset.localField = "memory-scope-id";
        const placeholder = el("option", "", "اختر العنصر");
        placeholder.value = "";
        scopeId.append(placeholder);
        values.forEach((item) => {
          const option = el("option", "", item.label);
          option.value = String(item.value);
          option.selected = String(item.value) === state.memoryScopeId;
          scopeId.append(option);
        });
        scopeId.disabled = state.memorySaving || !values.length ||
          (!canCreateMemory && !state.editingMemory);
        scopeIdField = controlField(
          state.memoryScope === "CHANNEL" ? "القناة" : "الرتبة",
          scopeId,
          values.length ? "" : "لا توجد عناصر متاحة في هذا الخادم.",
        );
      }

      const expiry = el("input", "prime-ai-control-input");
      expiry.type = "number";
      expiry.min = "0";
      expiry.max = "3650";
      expiry.step = "1";
      const expiryDays = effectiveMemoryExpirationDays();
      expiry.value = expiryDays === null || expiryDays === undefined ? "" : String(expiryDays);
      expiry.dataset.localField = "memory-expiry";
      expiry.disabled = state.memorySaving || (!canCreateMemory && !state.editingMemory);
      const expiryField = controlField(
        "انتهاء بعد أيام",
        expiry,
        "استخدم 0 لتثبيتها دون تاريخ انتهاء؛ الذاكرة المثبّتة مستثناة من الحذف الآلي بسبب العمر.",
      );

      const enabledLabel = el("label", "prime-ai-control-toggle");
      const enabledCopy = el("span", "prime-ai-control-toggle-copy");
      enabledCopy.append(el("strong", "", "استخدام هذه الذاكرة"));
      enabledCopy.append(el("small", "prime-ai-help", "يمكن إيقافها مؤقتاً دون حذفها."));
      const enabledInput = el("input");
      enabledInput.type = "checkbox";
      enabledInput.checked = state.memoryEnabled;
      enabledInput.dataset.localField = "memory-enabled";
      enabledInput.disabled = state.memorySaving || (!canCreateMemory && !state.editingMemory);
      enabledLabel.append(enabledCopy, enabledInput);

      const details = el("div", "prime-ai-memory-editor-grid");
      details.append(scopeField);
      if (scopeIdField) details.append(scopeIdField);
      details.append(expiryField);
      if (state.editingMemory) {
        details.append(enabledLabel);
      } else {
        details.append(controlField(
          "الحالة عند الإضافة",
          el("span", "prime-ai-muted", "تُنشأ الذاكرة مفعّلة؛ يمكنك إيقافها من التعديل."),
        ));
      }
      const formActions = el("div", "prime-ai-actions");
      formActions.append(button(
        state.memorySaving
          ? "جارٍ الحفظ..."
          : state.editingMemory ? "حفظ تعديل الذاكرة" : "إضافة إلى الذاكرة",
        "submit-memory",
        "prime-ai-button prime-ai-button-primary",
        state.memorySaving || (!canCreateMemory && !state.editingMemory),
      ));
      if (state.editingMemory) {
        formActions.append(button(
          "إلغاء التعديل",
          "cancel-memory-edit",
          "prime-ai-button prime-ai-button-secondary",
          state.memorySaving,
        ));
      }
      form.append(
        controlField(state.editingMemory ? "تعديل الملاحظة" : "معلومة جديدة", input),
        details,
        formActions,
      );
      card.append(form);
      if (!canCreateMemory && !state.editingMemory) {
        card.append(el(
          "p",
          "prime-ai-warning",
          "إضافة ذكريات جديدة متوقفة في سياسة الذاكرة. يمكن متابعة مراجعة الذكريات الموجودة.",
        ));
      }
      if (state.memoryError) {
        const error = el("div", "prime-ai-error");
        error.setAttribute("role", "alert");
        error.append(el("p", "", state.memoryError));
        card.append(error);
      }

      const list = el("div", "prime-ai-memory-list");
      if (!state.memories.length) {
        const empty = el("div", "prime-ai-empty");
        empty.append(el("strong", "", "لا توجد ذكريات محفوظة"));
        empty.append(el("span", "", "أضف أول معلومة لتظهر هنا."));
        list.append(empty);
      } else {
        state.memories.forEach((memory) => {
          const item = el("article", "prime-ai-memory-item");
          const content = el("p", "prime-ai-memory-content", memory.content || "");
          const meta = el("div", "prime-ai-memory-meta");
          meta.append(el("span", "", scopeLabel(memory)));
          if (memory.source) {
            meta.append(el(
              "span",
              "",
              memory.source === "AI_CANDIDATE"
                ? "مصدرها اقتراح وافق عليه المستخدم"
                : "أضيفت إدارياً",
            ));
          }
          if (memory.pinned) {
            meta.append(el("span", "", "مثبّتة — لا تنتهي"));
          }
          meta.append(el("span", "", memory.enabled === false || memory.enabled === 0 ? "متوقفة" : "مفعّلة"));
          if (memory.expires_at) meta.append(el("span", "", `تنتهي ${readableDate(memory.expires_at)}`));
          meta.append(el("span", "", `أضافها ${memory.created_by || "غير معروف"}`));
          meta.append(el("time", "", readableDate(memory.created_at)));
          const edit = button(
            "تعديل",
            "edit-memory",
            "prime-ai-button prime-ai-button-secondary",
            state.deletingMemoryId !== null || state.memorySaving,
          );
          edit.dataset.memoryId = String(memory.id);
          const remove = button(
            String(state.deletingMemoryId) === String(memory.id) ? "جارٍ الحذف..." : "حذف",
            "delete-memory",
            "prime-ai-button prime-ai-button-danger",
            state.deletingMemoryId !== null,
          );
          remove.dataset.memoryId = String(memory.id);
          const actions = el("div", "prime-ai-memory-actions");
          actions.append(edit, remove);
          item.append(content, meta, actions);
          list.append(item);
        });
      }
      card.append(list);
      return card;
    }

    function renderTestCard() {
      const card = el("section", "prime-ai-card prime-ai-test-card");
      card.setAttribute("aria-labelledby", "prime-ai-test-title");
      card.append(sectionHeader(
        "اختبار لمرة واحدة",
        "أرسل رسالة إلى PRIME AI باستخدام الإعدادات المحفوظة حالياً. لا تُحفظ رسالة الاختبار كإعداد.",
        "prime-ai-test-title",
      ));
      const form = el("form", "prime-ai-test-form");
      form.dataset.form = "test";
      const label = el("label", "", "رسالة الاختبار");
      label.htmlFor = "prime-ai-test-prompt";
      const prompt = el("textarea", "prime-ai-textarea");
      prompt.id = "prime-ai-test-prompt";
      prompt.rows = 3;
      prompt.maxLength = 1200;
      prompt.placeholder = "اكتب سؤالاً لاختبار سلوك المساعد.";
      prompt.value = state.testPrompt;
      prompt.dataset.field = "test-prompt";
      const actions = el("div", "prime-ai-actions");
      actions.append(button(
        state.testing ? "جارٍ الاختبار..." : "تشغيل الاختبار",
        "submit-test",
        "prime-ai-button prime-ai-button-primary",
        state.testing,
      ));
      form.append(label, prompt, actions);
      card.append(form);

      if (state.testResult) {
        const result = el("div", state.testResult.ok ? "prime-ai-test-result" : "prime-ai-error");
        result.setAttribute("role", state.testResult.ok ? "status" : "alert");
        result.append(el("strong", "", state.testResult.ok ? "نتيجة الاختبار" : "تعذر إكمال الاختبار"));
        result.append(el("p", "", state.testResult.text));
        card.append(result);
      }
      return card;
    }

    function renderAuditCard() {
      const card = el("section", "prime-ai-card");
      card.setAttribute("aria-labelledby", "prime-ai-audit-title");
      const heading = sectionHeader("سجل نشاط الذكاء الاصطناعي", "أحدث أحداث الاستخدام والإدارة المسجلة لهذا الخادم.", "prime-ai-audit-title");
      heading.append(button(
        state.auditLoading ? "جارٍ التحديث..." : "تحديث السجل",
        "reload-audit",
        "prime-ai-button prime-ai-button-secondary",
        state.auditLoading,
      ));
      card.append(heading);
      if (state.auditLoading) {
        card.append(loadingCard("جارٍ تحميل سجل النشاط"));
        return card;
      }
      if (state.auditError) {
        card.append(loadError(state.auditError, "reload-audit"));
        return card;
      }
      card.append(el(
        "p",
        "prime-ai-help prime-ai-audit-availability",
        "المتاح في سجل الخادم: المنفّذ (معرّف Discord)، الإجراء، النتيجة، التفاصيل، والوقت. لا يوفّر المصدر حقول أداة أو هدف أو اسم مستخدم أو حالة منفصلة.",
      ));
      if (!state.events.length) {
        const empty = el("div", "prime-ai-empty");
        empty.append(el("strong", "", "لا توجد أحداث حديثة"));
        empty.append(el("span", "", "سيظهر سجل المساعد هنا عند توفر أحداث."));
        card.append(empty);
        return card;
      }

      const list = el("div", "prime-ai-audit-list");
      state.events.forEach((event) => {
        const row = el("article", "prime-ai-audit-item");
        const top = el("div", "prime-ai-audit-top");
        top.append(el("strong", "", event.action || "نشاط"));
        top.append(el("time", "", readableDate(event.created_at)));
        const meta = el("div", "prime-ai-audit-meta");
        meta.append(el("span", "", `المنفّذ: ${event.actor_id || "غير معروف"}`));
        meta.append(el("span", "prime-ai-audit-result", event.result || "غير محدد"));
        row.append(top, el("p", "", event.detail || "لا توجد تفاصيل إضافية."), meta);
        list.append(row);
      });
      card.append(list);
      return card;
    }

    function renderControlCard(destination) {
      const card = el("section", "prime-ai-card prime-ai-control-panel");
      const controlSections = [];
      card.append(sectionHeader(
        "مركز التحكم",
        "إعدادات هذا القسم محفوظة ضمن مراجعة مركز التحكم الحالية.",
        "prime-ai-control-title",
      ));
      if (state.controlLoading && !state.controlSnapshot) {
        card.append(loadingCard("جارٍ تحميل مركز التحكم"));
        return card;
      }
      if (state.controlError && !state.controlSnapshot) {
        card.append(loadError(state.controlError, "reload-control"));
        return card;
      }
      const cfg = controlConfig();
      const addControlSection = (key, section) => {
        controlSections.push({ key, details: section.details });
      };
      if (state.controlError) {
        const warning = el("p", "prime-ai-error-text", state.controlError);
        warning.setAttribute("role", "alert");
        card.append(warning);
      }
      if (state.controlConflict) {
        const conflict = el("div", "prime-ai-conflict");
        conflict.setAttribute("role", "alert");
        conflict.append(el("strong", "", "تغيّرت إعدادات التحكم على الخادم"));
        conflict.append(el("p", "", "احتفظنا بتعديلاتك الحالية؛ حمّل النسخة الأحدث قبل متابعة الحفظ."));
        conflict.append(button("تحميل النسخة الأحدث", "apply-control-conflict", "prime-ai-button prime-ai-button-secondary"));
        card.append(conflict);
      }
      if (state.controlSaveError) {
        const error = el("div", "prime-ai-error");
        error.setAttribute("role", "alert");
        error.append(el("p", "", state.controlSaveError));
        card.append(error);
      }
      const modesSection = controlDisclosure(
        "أنماط PRIME AI",
        "فعّل إجراء محمياً فقط إذا احتجت إليه؛ كل تغيير يخضع لفحوص السيرفر.",
        true,
      );
      modesSection.content.append(controlSelect("mode", "النمط الافتراضي", cfg.mode, [
        { value: "CHAT", label: "محادثة" },
        { value: "ASSISTANT", label: "مساعد PRIME للقراءة" },
      ]));
      const modes = el("div", "prime-ai-control-grid");
      [
        ["chat", "المحادثة"],
        ["assistant", "مساعد PRIME"],
      ].forEach(([key, label]) => modes.append(
        controlToggle(`modes.${key}`, label, cfg.modes && cfg.modes[key]),
      ));
      modes.append(el(
        "p",
        "prime-ai-help",
        "يمكن طلب الإجراءات مباشرة باللغة الطبيعية في المحادثة؛ لا يلزم تبديل نمط أو استخدام /ask_ai. تخضع كل خطوة لسياسة الإجراء وصلاحيات Discord.",
      ));
      modesSection.content.append(modes);
      modesSection.content.append(controlToggle(
        "activation.command",
        "السماح بأوامر Slash",
        cfg.activation && cfg.activation.command,
      ));
      addControlSection("modes", modesSection);

      const channels = availableChannels()
        .filter((item) => item.text_based !== false)
        .map((item) => ({ value: item.id, label: `#${item.name || item.id}` }));
      const roles = (state.roles || []).map((item) => ({ value: item.id, label: item.name || item.id }));
      const permissions = [
        ["everyone", "الجميع"], ["manage_messages", "إدارة الرسائل"],
        ["moderate_members", "إشراف الأعضاء"], ["manage_roles", "إدارة الرتب"],
        ["manage_channels", "إدارة القنوات"], ["manage_guild", "إدارة الخادم"],
        ["administrator", "مسؤول"], ["owner", "مالك البوت"],
      ].map(([value, label]) => ({ value, label }));
      const actionSection = controlDisclosure(
        "صلاحيات الإجراءات عبر Talk",
        "حدد إجراءات Discord المتاحة عبر المحادثة والقنوات والرتب المسموحة لكل إجراء. يعاد فحص الصلاحيات الحقيقية عند التنفيذ، وتبقى الحمايات الإلزامية فعالة.",
        false,
      );
      const safety = cfg.safety || {};
      const safetyGrid = el("div", "prime-ai-control-grid");
      safetyGrid.append(
        controlToggle("safety.enabled", "تفعيل محرك الإجراءات", safety.enabled),
        controlToggle(
          "safety.dry_run",
          "وضع المعاينة — لا تغييرات على Discord",
          safety.dry_run,
          "اتركه مفعّلاً أثناء اختبار الخطة. تعطيله يسمح بالتنفيذ الفعلي بعد استيفاء الشروط.",
        ),
        controlToggle(
          "safety.prompt_injection_protection",
          "الحماية من تعليمات المستخدم المضمّنة",
          safety.prompt_injection_protection,
          "حماية إلزامية.",
        ),
        controlToggle(
          "safety.mass_action_protection",
          "منع الإجراءات الجماعية",
          safety.mass_action_protection,
          "حماية إلزامية.",
        ),
        controlInput(
          "safety.max_action_count",
          "أقصى عدد خطوات في الطلب",
          safety.max_action_count,
          { type: "number", min: 1, max: 5, step: 1 },
        ),
      );
      [
        "safety.prompt_injection_protection",
        "safety.mass_action_protection",
      ].forEach((path) => {
        const input = safetyGrid.querySelector(`[data-control-path="${path}"]`);
        if (input) input.disabled = true;
      });
      actionSection.content.append(safetyGrid);
      const actionCards = el("div", "prime-ai-audit-list");
      (state.actionRegistry || []).forEach((entry) => {
        const id = String(entry.id || "");
        const policy = (cfg.actions || {})[id] || {};
        if (!id) return;
        const item = el("article", "prime-ai-skill-card");
        const categoryLabel = {
          MESSAGE: "رسائل",
          CHANNEL: "قنوات",
          ROLE: "رتب",
          MEMBER: "أعضاء",
          MODERATION: "إشراف",
        }[entry.category] || entry.category;
        const discordPermissions = [
          entry.discord_permission,
          ...(entry.additional_discord_permissions || []),
        ].filter(Boolean).join(", ");
        const riskLabel = {
          LOW: "منخفض",
          MEDIUM: "متوسط",
          HIGH: "مرتفع",
          CRITICAL: "حرج",
        }[entry.risk] || entry.risk;
        const primePermission = permissions.find(
          (permission) => permission.value === (cfg.access && cfg.access.minimum_permission),
        );
        const primePermissionLabel = primePermission ? primePermission.label : "الجميع";
        item.append(el("strong", "prime-ai-skill-title", entry.name || id));
        item.append(el("p", "prime-ai-help", entry.description || ""));
        item.append(el(
          "small",
          "prime-ai-help",
          `الفئة: ${categoryLabel} · الخطورة: ${riskLabel} · صلاحيات Discord: ${discordPermissions}`,
        ));
        item.append(el(
          "small",
          "prime-ai-help",
          `قيد الوصول في PRIME: ${primePermissionLabel} · تأكيد إضافي: ${entry.confirmation_required ? "إلزامي بسبب الخطورة" : "غير مطلوب للإجراء المفرد"} · تدقيق التنفيذ: ${entry.audit_required ? "إلزامي" : "غير مطلوب"}`,
        ));
        const grid = el("div", "prime-ai-control-grid");
        const minimumRoleChoices = [
          { value: "", label: "لا يوجد حد رتبة إضافي" },
          ...roles.filter((role) => /^\d{15,22}$/.test(String(role.value))),
        ];
        if (
          policy.minimum_role_id &&
          !minimumRoleChoices.some(
            (role) => String(role.value) === String(policy.minimum_role_id),
          )
        ) {
          minimumRoleChoices.splice(1, 0, {
            value: String(policy.minimum_role_id),
            label: `رتبة غير موجودة (${String(policy.minimum_role_id).slice(-4)})`,
          });
        }
        grid.append(
          controlToggle(`actions.${id}.enabled`, "السماح بهذا الإجراء", policy.enabled),
          controlSelect(
            `actions.${id}.allowed_channels`,
            "القنوات المسموح بها",
            policy.allowed_channels || [],
            channels,
            true,
          ),
          controlSelect(
            `actions.${id}.allowed_roles`,
            "الرتب المسموح بها",
            policy.allowed_roles || [],
            roles,
            true,
          ),
          controlSelect(
            `actions.${id}.minimum_role_id`,
            "الحد الأدنى للرتبة",
            policy.minimum_role_id || "",
            minimumRoleChoices,
            false,
            "يجب أن تكون رتبتك أعلى أو مساوية لهذا الحد. مالك الخادم وAdministrator يتجاوزان هذا الحد فقط، ولا يتجاوزان قوائم الرتب أو صلاحيات Discord المطلوبة.",
          ),
        );
        item.append(grid);
        const preview = document.createElement("details");
        preview.className = "prime-ai-control-grid";
        const previewSummary = el(
          "summary",
          "prime-ai-help",
          "معاينة سياسة الإجراء — لا تنفّذ أي تغيير",
        );
        preview.append(previewSummary);
        const previewRows = el("div", "prime-ai-control-grid");
        const selectedChannelNames = (policy.allowed_channels || []).map((channelId) => {
          const match = channels.find((channel) => String(channel.value) === String(channelId));
          return match ? match.label : String(channelId);
        });
        const selectedRoleNames = (policy.allowed_roles || []).map((roleId) => {
          const match = roles.find((role) => String(role.value) === String(roleId));
          return match ? match.label : String(roleId);
        });
        const rateLimits = cfg.rate_limits || {};
        const actionRate = rateLimits.action || {};
        const dangerousRate = rateLimits.dangerous_action || {};
        [
          `صلاحيات Discord: ${discordPermissions}`,
          `قيد الوصول في PRIME: ${primePermissionLabel} · الحد الأدنى للرتبة: ${policy.minimum_role_id ? minimumRoleChoices.find((role) => String(role.value) === String(policy.minimum_role_id))?.label || "رتبة محددة" : "لا يوجد"}`,
          `التنفيذ: ${entry.confirmation_required ? "بعد تأكيد صاحب الطلب ثم إعادة فحص الصلاحيات" : "مباشر بعد فحوص السياسة والصلاحيات"}`,
          `القنوات: ${selectedChannelNames.length ? selectedChannelNames.join("، ") : "لا توجد قيود إضافية على هذا الإجراء"}`,
          `الرتب: ${selectedRoleNames.length ? selectedRoleNames.join("، ") : "لا توجد قيود إضافية على هذا الإجراء"}`,
          `حد الإجراءات: ${actionRate.limit || "—"} لكل ${actionRate.window_seconds || "—"} ثانية`,
          `حد الإجراءات الخطرة: ${dangerousRate.limit || "—"} لكل ${dangerousRate.window_seconds || "—"} ثانية`,
          `التدقيق: ${entry.audit_required ? "مفعّل وإلزامي" : "غير مطلوب"}`,
        ].forEach((line) => previewRows.append(el("p", "prime-ai-help", line)));
        preview.append(previewRows);
        item.append(preview);
        actionCards.append(item);
      });
      actionSection.content.append(actionCards);
      actionSection.content.append(el(
        "p",
        "prime-ai-help",
        "PRIME لا يمنح أي صلاحية بنفسه. تُفحص صلاحيات Discord الحقيقية، وتُفحص رتبة المستخدم والـbot وتسلسل الرتب والقيود لكل طلب. الطلب الملتبس فقط يتوقف ويطلب توضيحاً؛ الإجراءات الخطرة تبقى خلف تأكيد واحد فقط.",
      ));
      addControlSection("talk", actionSection);

      const moderation = cfg.moderation || {};
      const categoryLabels = {
        spam: "الرسائل المزعجة",
        harassment: "المضايقة",
        suspicious_behavior: "السلوك المشبوه",
        prohibited_content: "المحتوى المحظور",
        repeated_violations: "المخالفات المتكررة",
      };
      const moderationSection = controlDisclosure(
        "الإشراف المدعوم بالذكاء الاصطناعي",
        "التصنيف لا يعاقب المستخدم. التنبيهات والتوصيات لا تغيّر رسائل الخادم.",
        false,
      );
      const moderationGrid = el("div", "prime-ai-control-grid");
      moderationGrid.append(
        controlSelect("moderation.mode", "وضع الإشراف", moderation.mode, [
          { value: "OFF", label: "متوقف" },
          { value: "LOG_ONLY", label: "تسجيل فقط" },
          { value: "ALERT", label: "تنبيه المشرفين" },
          { value: "RECOMMEND", label: "توصية للمراجعة" },
          { value: "AUTO_WITH_CONFIRMATION", label: "مراجعة ثم تأكيد بشري" },
        ]),
        controlSelect(
          "moderation.channel_ids",
          "القنوات التي تخضع للتصنيف",
          moderation.channel_ids || [],
          channels,
          true,
        ),
        controlSelect(
          "moderation.categories",
          "فئات المخالفات",
          moderation.categories || [],
          Object.entries(categoryLabels).map(([value, label]) => ({ value, label })),
          true,
        ),
        controlInput(
          "moderation.confidence_threshold",
          "حد الثقة الأدنى",
          moderation.confidence_threshold,
          { type: "number", min: 0.5, max: 1, step: 0.01 },
        ),
        controlToggle(
          "moderation.log_findings",
          "حفظ نتائج التصنيف للمدة المحددة",
          moderation.log_findings,
          "يتضمن السجل نص الرسالة ويُحذف وفق مدة احتفاظ الإشراف.",
        ),
        controlSelect(
          "moderation.alert_channel_id",
          "قناة تنبيهات الإشراف",
          moderation.alert_channel_id || "",
          [{ value: "", label: "اختر قناة" }, ...channels],
        ),
        controlSelect(
          "moderation.auto_action_policy",
          "إجراء ينتظر موافقة بشرية",
          moderation.auto_action_policy || "NONE",
          [
            { value: "NONE", label: "لا إجراء" },
            { value: "TIMEOUT_MEMBER", label: "اقتراح مهلة للعضو" },
          ],
        ),
        controlInput(
          "moderation.timeout_minutes",
          "مدة المهلة المقترحة بالدقائق",
          moderation.timeout_minutes,
          { type: "number", min: 1, max: 10080, step: 1 },
        ),
      );
      moderationSection.content.append(moderationGrid);
      moderationSection.content.append(el(
        "p",
        "prime-ai-help",
        "وضع «مراجعة ثم تأكيد بشري» يتطلب سجلّاً محفوظاً وقناة تنبيه. ضغط زر المراجعة يرسل طلباً خاصاً للمشرف؛ لا تنفّذ المهلة إلا بعد تأكيد منشئ الطلب، ثم إعادة فحص الصلاحيات والهدف.",
      ));
      addControlSection("moderation", moderationSection);

      const natural = cfg.natural_commands || {};
      const naturalSection = controlDisclosure(
        "التفعيل وقناة Talk",
        "تحكم بمتى يرد PRIME وأين يرد تلقائياً. تُحفظ محادثات PRIME المختارة لاستعادة السياق مع فصلها حسب الخادم والقناة والعضو والموضوع؛ مدة الحفظ قابلة للضبط أدناه.",
        true,
      );
      const naturalGrid = el("div", "prime-ai-control-grid");
      const talkChannel = cfg.talk_channel || {};
      naturalGrid.append(
        controlToggle("natural_commands.enabled", "تفعيل الأوامر الطبيعية", natural.enabled),
        controlToggle("activation.mention", "التفعيل عند منشن PRIME", cfg.activation && cfg.activation.mention),
        controlToggle("activation.reply", "التفعيل عند الرد على PRIME", cfg.activation && cfg.activation.reply),
        controlToggle(
          "activation.wake_word",
          "الاستجابة عند النداء «يا برايم» أو Hey Prime",
          !cfg.activation || cfg.activation.wake_word !== false,
        ),
        controlToggle(
          "talk_channel.enabled",
          "الرد تلقائياً في قناة Talk المحددة فقط",
          talkChannel.enabled,
        ),
        controlSelect(
          "talk_channel.channel_id",
          "قناة Talk",
          talkChannel.channel_id || "",
          channels,
        ),
        controlSelect("natural_commands.clarification_behavior", "التعامل مع الأسماء المتشابهة", natural.clarification_behavior, [
          { value: "ask", label: "اطلب التحديد" },
          { value: "show_matches", label: "اعرض النتائج واطلب الاختيار" },
        ]),
        controlSelect("natural_commands.unknown_command_behavior", "الطلب غير المعروف", natural.unknown_command_behavior, [
          { value: "respond", label: "أجب كمحادثة عامة" },
          { value: "ignore", label: "تجاهل الطلب غير المعروف" },
        ]),
      );
      naturalSection.content.append(naturalGrid);
      naturalSection.content.append(el(
        "p",
        "prime-ai-help",
        "قناة Talk تقيّد رسائل المحادثة الطبيعية بها فقط؛ لا تفعّل الرد على الرسائل غير الموجّهة إلى PRIME، وتبقى أوامر Slash خاضعة لقائمة قنوات PRIME AI.",
      ));
      addControlSection("talk", naturalSection);

      const accessSection = controlDisclosure(
        "الصلاحيات والوصول",
        "حدد من يستطيع استخدام Talk: الصلاحية المطلوبة، القنوات والرتب المسموحة أو المحظورة.",
        true,
      );
      accessSection.content.append(controlSelect("access.minimum_permission", "أقل صلاحية مطلوبة", cfg.access && cfg.access.minimum_permission, permissions));
      const accessGrid = el("div", "prime-ai-control-grid");
      accessGrid.append(
        controlSelect("access.allowed_channels", "قنوات PRIME AI المسموحة", cfg.access && cfg.access.allowed_channels, channels, true),
        controlSelect("access.blocked_channels", "القنوات المحظورة", cfg.access && cfg.access.blocked_channels, channels, true),
        controlSelect("access.allowed_roles", "رتب PRIME AI المسموحة", cfg.access && cfg.access.allowed_roles, roles, true),
        controlSelect("access.blocked_roles", "الرتب المحظورة", cfg.access && cfg.access.blocked_roles, roles, true),
      );
      if (cfg.access && cfg.access.legacy_allowlist_conflict) {
        const conflictMessage = cfg.access.allowed_channels && cfg.access.allowed_channels.length
          ? "تم العثور على قائمتين قديمتين مختلفتين. يقتصر الوصول حالياً على التقاطع الآمن؛ احفظ القائمة الظاهرة لتوحيدها."
          : "تعارض القوائم القديمة لا ينتج عنه أي قناة مشتركة. الوصول متوقف حتى تختار قناة واحدة أو أكثر وتحفظها.";
        const warning = el("p", "prime-ai-warning", conflictMessage);
        warning.setAttribute("role", "alert");
        accessSection.content.append(warning);
      }
      accessSection.content.append(accessGrid);
      addControlSection("talk", accessSection);

      const contextSection = controlDisclosure(
        "سياق المحادثة",
        "حدد مقدار المحادثة الأخيرة وما إذا كانت الرسالة المقتبسة تُرسل مع السؤال.",
        true,
      );
      const contextGrid = el("div", "prime-ai-control-grid");
      contextGrid.append(
        controlInput("context.max_messages", "رسائل السياق المؤقت", cfg.context && cfg.context.max_messages, { type: "number", min: 0, max: 30, step: 1 }),
        controlToggle(
          "context.include_reply_context",
          "تضمين الرسالة المقتبسة",
          cfg.context && cfg.context.include_reply_context,
          "عند الإيقاف، يظل PRIME قادراً على الرد على رسائله لكن لا يرسل نص الرسالة المرجعية إلى المزوّد.",
        ),
      );
      contextSection.content.append(contextGrid);
      addControlSection("talk", contextSection);

      const personalitySection = controlDisclosure(
        "الشخصية",
        "اضبط طابع الإجابة وطولها ولهجتها من دون تغيير صلاحيات المساعد.",
        false,
      );
      const personality = cfg.personality || {};
      const personalityGrid = el("div", "prime-ai-control-grid");
      personalityGrid.append(
        controlSelect("personality.preset", "الطابع", personality.preset, [
          "Practical", "Formal", "Friendly", "Romantic", "Sarcastic", "Firm",
          "Mysterious", "Funny", "Smart", "Gaming", "Aggressive", "Custom",
        ].map((value) => ({ value, label: value }))),
        controlSelect("personality.response_length", "طول الإجابة", personality.response_length, [
          { value: "short", label: "قصير" }, { value: "medium", label: "متوسط" }, { value: "long", label: "مفصل" },
        ]),
        controlInput("personality.tone", "النبرة", personality.tone, { maxLength: 80 }),
        controlInput("personality.language", "اللغة", personality.language, { maxLength: 30 }),
        controlInput("personality.arabic_dialect", "اللهجة العربية", personality.arabic_dialect, { maxLength: 40 }),
        controlInput("personality.formality", "الرسمية", personality.formality, { type: "range", min: 0, max: 100, step: 1 }),
        controlInput("personality.humor", "الفكاهة", personality.humor, { type: "range", min: 0, max: 100, step: 1 }),
        controlInput("personality.emoji_usage", "استخدام الإيموجي", personality.emoji_usage, { type: "range", min: 0, max: 100, step: 1 }),
        controlInput("personality.toughness", "الحزم", personality.toughness, { type: "range", min: 0, max: 100, step: 1 }),
        controlInput("personality.directness", "المباشرة", personality.directness, { type: "range", min: 0, max: 100, step: 1 }),
        controlInput("personality.friendliness", "الودّية", personality.friendliness, { type: "range", min: 0, max: 100, step: 1 }),
        controlInput("personality.seriousness", "الجدية", personality.seriousness, { type: "range", min: 0, max: 100, step: 1 }),
        controlSelect("personality.emotional_style", "الأسلوب العاطفي", personality.emotional_style, [
          { value: "balanced", label: "متوازن" },
          { value: "warm", label: "ودود" },
          { value: "direct", label: "مباشر" },
          { value: "neutral", label: "محايد" },
        ]),
        controlInput("personality.greeting_style", "أسلوب الترحيب", personality.greeting_style, { maxLength: 80 }),
        controlInput("personality.reply_style", "أسلوب الرد", personality.reply_style, { maxLength: 80 }),
        controlInput("personality.custom_instructions", "تعليمات إضافية", personality.custom_instructions, { multiline: true, maxLength: 1000 }),
      );
      personalitySection.content.append(personalityGrid);
      addControlSection("talk", personalitySection);

      const provider = cfg.provider || {};
      const providerSection = controlDisclosure(
        "المزوّد",
        "يستخدم PRIME AI Google Gemini؛ لا تُحفظ مفاتيح المزوّد في اللوحة.",
        false,
      );
      providerSection.content.append(el(
        "p",
        "prime-ai-help",
        `المزوّد ثابت على Google Gemini؛ الحالة: ${
          state.providerStatus === "configured" ? "مهيّأ" : "غير مهيّأ"
        }. لا يتم تبديله تلقائياً ولا تحفظ اللوحة أي مفاتيح مزوّد.`,
      ));
      const providerGrid = el("div", "prime-ai-control-grid");
      providerGrid.append(
        controlInput("provider.model", "اسم النموذج", provider.model, { maxLength: 80 }),
        controlInput("provider.temperature", "درجة الإبداع", provider.temperature, { type: "number", min: 0, max: 2, step: 0.1 }),
        controlInput("provider.max_tokens", "حد الرموز", provider.max_tokens, { type: "number", min: 64, max: 8192, step: 1 }),
        controlInput("provider.timeout_seconds", "مهلة المزوّد بالثواني", provider.timeout_seconds, { type: "number", min: 3, max: 120, step: 1 }),
        controlInput("provider.retry_count", "عدد إعادات المحاولة", provider.retry_count, { type: "number", min: 0, max: 3, step: 1 }),
      );
      providerSection.content.append(providerGrid);
      addControlSection("providers", providerSection);

      const response = cfg.response || {};
      const responseSection = controlDisclosure(
        "الردود والخصوصية",
        "حدّد تنسيق رد Discord وسياسة الإشارات والحذف.",
        false,
      );
      const responseGrid = el("div", "prime-ai-control-grid");
      responseGrid.append(
        controlInput("response.maximum_length", "أقصى طول للإجابة", response.maximum_length, { type: "number", min: 100, max: 3500, step: 50 }),
        controlToggle("response.reply_behavior", "الرد مع اقتباس الرسالة", response.reply_behavior),
        controlSelect("response.mention_behavior", "السماح بالإشارات", response.mention_behavior, [
          { value: "none", label: "منع الإشارات" }, { value: "user", label: "صاحب الطلب فقط" }, { value: "roles", label: "رتب صاحب الطلب فقط" },
        ]),
        controlInput("response.auto_delete_seconds", "حذف الرد بعد (ثوانٍ، 0 للإبقاء)", response.auto_delete_seconds, { type: "number", min: 0, max: 86400, step: 1 }),
      );
      [
        ["streaming", "استخدام استجابة متدفقة"],
        ["typing_indicator", "إظهار مؤشر الكتابة"],
        ["embed_behavior", "عرض الرد داخل بطاقة"],
        ["markdown", "السماح بتنسيق Markdown"],
        ["emoji", "السماح بالإيموجي"],
      ].forEach(([key, label]) => responseGrid.append(controlToggle(`response.${key}`, label, response[key])));
      responseSection.content.append(responseGrid);
      addControlSection("talk", responseSection);

      const memory = cfg.memory || {};
      const memorySection = controlDisclosure(
        "سياسة الذاكرة",
        "PRIME يحفظ فقط سياقاً موجزاً ومحدوداً للمحادثة الموجّهة لهذا المستخدم والقناة لمدة قصيرة لاستمرار الحوار بعد مرور ساعات أو إعادة التشغيل؛ أما الذاكرة الشخصية الدائمة فتظل مرتبطة بالمستخدم وتتطلب موافقته.",
        false,
      );
      const memoryGrid = el("div", "prime-ai-control-grid");
      memoryGrid.append(
        controlToggle("memory.enabled", "تفعيل الذاكرة", memory.enabled),
        controlToggle("memory.user_memory_enabled", "السماح بذاكرة المستخدم الخاصة", memory.user_memory_enabled),
        controlToggle("memory.server_memory_enabled", "السماح بذاكرة الخادم", memory.server_memory_enabled),
        controlToggle("memory.creation_enabled", "السماح بإنشاء ذكريات", memory.creation_enabled),
        controlToggle("memory.retrieval_enabled", "استرجاع الذكريات إلى الردود", memory.retrieval_enabled),
        controlInput("memory.context_limit", "عدد الملاحظات لكل طلب", memory.context_limit, { type: "number", min: 0, max: 30, step: 1 }),
        controlInput("memory.maximum_count", "الحد الأقصى للذكريات", memory.maximum_count, { type: "number", min: 1, max: 500, step: 1 }),
        controlInput("memory.maximum_content_length", "أقصى طول للملاحظة", memory.maximum_content_length, { type: "number", min: 100, max: 1000, step: 25 }),
        controlInput("memory.default_expiration_days", "انتهاء الملاحظة بعد أيام (0 بلا انتهاء)", memory.default_expiration_days, { type: "number", min: 0, max: 3650, step: 1 }),
      );
      memorySection.content.append(memoryGrid);
      addControlSection("memory", memorySection);

      const limitsSection = controlDisclosure(
        "حدود إجراءات PRIME وسجلاته",
        "حدود الإجراءات والإشراف ومدة الاحتفاظ بالذكريات والتدقيق.",
        false,
      );
      const rates = cfg.rate_limits || {};
      const rateGrid = el("div", "prime-ai-control-grid");
      ["action", "dangerous_action", "moderation"].forEach((key) => {
        const policy = rates[key] || {};
        rateGrid.append(
          controlInput(`rate_limits.${key}.limit`, `حد ${key}`, policy.limit, { type: "number", min: 1, max: 10000, step: 1 }),
          controlInput(`rate_limits.${key}.window_seconds`, `نافذة ${key} بالثواني`, policy.window_seconds, { type: "number", min: 1, max: 86400, step: 1 }),
        );
      });
      const retentionGrid = el("div", "prime-ai-control-grid");
      const retentionLabels = {
        memory_days: "حفظ ذكريات PRIME",
        audit_days: "سجلات التدقيق",
        moderation_days: "سجلات الإشراف",
      };
      Object.entries(cfg.retention || {}).forEach(([key, value]) => {
        if (key === "conversation_days") return;
        retentionGrid.append(controlInput(`retention.${key}`, `${retentionLabels[key] || `الاحتفاظ: ${key}`} (أيام)`, value, { type: "number", min: 0, max: 3650, step: 1 }));
      });
      limitsSection.content.append(rateGrid, retentionGrid);
      addControlSection("limits", limitsSection);

      const talkLimitsSection = controlDisclosure(
        "حدود Talk والاحتفاظ",
        "حدود الطلبات الحوارية بحسب المستخدم والرتبة والقناة والخادم، ومدة حفظ محادثات PRIME المختارة.",
        false,
      );
      const talkRateGrid = el("div", "prime-ai-control-grid");
      ["user", "role", "channel", "guild"].forEach((key) => {
        const policy = rates[key] || {};
        talkRateGrid.append(
          controlInput(`rate_limits.${key}.limit`, `حد ${key}`, policy.limit, { type: "number", min: 1, max: 10000, step: 1 }),
          controlInput(`rate_limits.${key}.window_seconds`, `نافذة ${key} بالثواني`, policy.window_seconds, { type: "number", min: 1, max: 86400, step: 1 }),
        );
      });
      const conversationRetention = controlInput(
        "retention.conversation_days",
        "مدة حفظ محادثات PRIME المختارة (أيام، 0 للإيقاف)",
        (cfg.retention || {}).conversation_days,
        { type: "number", min: 0, max: 3650, step: 1 },
      );
      talkLimitsSection.content.append(talkRateGrid, conversationRetention);
      addControlSection("talk", talkLimitsSection);

      const personaSection = controlDisclosure(
        "استثناءات القنوات والرتب",
        "خصص شخصية مختلفة لقناة أو رتبة محددة.",
        false,
      );
      const personaGrid = el("div", "prime-ai-control-grid");
      const personaChannel = state.selectedPersonaChannel || (state.channels[0] && String(state.channels[0].id)) || "";
      const channelChoice = el("select", "prime-ai-control-input");
      channelChoice.dataset.localField = "persona-channel";
      state.channels.forEach((item) => {
        const option = el("option", "", `#${item.name || item.id}`);
        option.value = String(item.id);
        option.selected = String(item.id) === String(personaChannel);
        channelChoice.append(option);
      });
      personaGrid.append(controlField("شخصية قناة محددة", channelChoice));
      const currentChannelPersona = (cfg.channel_personas || {})[personaChannel] || {};
      const hasChannelPersona = Object.prototype.hasOwnProperty.call(
        cfg.channel_personas || {},
        personaChannel,
      );
      if (personaChannel) {
        personaGrid.append(
          controlToggle(
            `channel_personas.${personaChannel}.enabled`,
            "تفعيل شخصية القناة",
            currentChannelPersona.enabled === undefined
              ? hasChannelPersona
              : currentChannelPersona.enabled,
            "عند الإيقاف تعود القناة إلى الشخصية العامة أو استثناء الرتبة.",
          ),
          controlSelect(`channel_personas.${personaChannel}.preset`, "طابع القناة", currentChannelPersona.preset || personality.preset, [
            "Practical", "Formal", "Friendly", "Romantic", "Sarcastic", "Firm",
            "Mysterious", "Funny", "Smart", "Gaming", "Aggressive", "Custom",
          ].map((value) => ({ value, label: value }))),
          controlSelect(`channel_personas.${personaChannel}.response_length`, "طول الرد للقناة", currentChannelPersona.response_length || personality.response_length, [
            { value: "short", label: "قصير" }, { value: "medium", label: "متوسط" }, { value: "long", label: "مفصل" },
          ]),
          controlInput(`channel_personas.${personaChannel}.tone`, "نبرة القناة", currentChannelPersona.tone || "", { maxLength: 80 }),
          controlInput(`channel_personas.${personaChannel}.formality`, "الرسمية", currentChannelPersona.formality === undefined ? personality.formality : currentChannelPersona.formality, { type: "range", min: 0, max: 100, step: 1 }),
          controlInput(`channel_personas.${personaChannel}.humor`, "الفكاهة", currentChannelPersona.humor === undefined ? personality.humor : currentChannelPersona.humor, { type: "range", min: 0, max: 100, step: 1 }),
          controlInput(`channel_personas.${personaChannel}.emoji_usage`, "استخدام الإيموجي", currentChannelPersona.emoji_usage === undefined ? personality.emoji_usage : currentChannelPersona.emoji_usage, { type: "range", min: 0, max: 100, step: 1 }),
          controlInput(`channel_personas.${personaChannel}.directness`, "مباشرة الرد", currentChannelPersona.directness === undefined ? personality.directness : currentChannelPersona.directness, { type: "range", min: 0, max: 100, step: 1 }),
          controlInput(`channel_personas.${personaChannel}.friendliness`, "ودّية الرد", currentChannelPersona.friendliness === undefined ? personality.friendliness : currentChannelPersona.friendliness, { type: "range", min: 0, max: 100, step: 1 }),
          controlInput(`channel_personas.${personaChannel}.seriousness`, "جدية الرد", currentChannelPersona.seriousness === undefined ? personality.seriousness : currentChannelPersona.seriousness, { type: "range", min: 0, max: 100, step: 1 }),
          controlInput(`channel_personas.${personaChannel}.custom_instructions`, "تعليمات القناة", currentChannelPersona.custom_instructions || "", { maxLength: 500 }),
        );
      } else {
        personaGrid.append(el("p", "prime-ai-muted", "لا توجد قناة متاحة لإعداد شخصية خاصة."));
      }
      const roleChoice = el("select", "prime-ai-control-input");
      roleChoice.dataset.localField = "override-role";
      roles.forEach((item) => {
        const option = el("option", "", item.label);
        option.value = String(item.value);
        option.selected = String(item.value) === String(state.selectedOverrideRole || "");
        roleChoice.append(option);
      });
      personaGrid.append(controlField("رتبة لها استثناء", roleChoice));
      const roleId = state.selectedOverrideRole || (roles[0] && String(roles[0].value)) || "";
      const currentRoleOverride = (cfg.role_overrides || {})[roleId] || {};
      if (roleId) {
        personaGrid.append(
          controlSelect(`role_overrides.${roleId}.preset`, "طابع الرتبة", currentRoleOverride.preset || personality.preset, [
            "Practical", "Formal", "Friendly", "Firm", "Funny", "Gaming", "Smart", "Custom",
          ].map((value) => ({ value, label: value }))),
          controlInput(`role_overrides.${roleId}.tone`, "نبرة الرتبة", currentRoleOverride.tone || "", { maxLength: 80 }),
        );
      }
      personaSection.content.append(personaGrid);
      const personaActions = el("div", "prime-ai-actions");
      personaActions.append(
        button("حذف استثناء القناة", "remove-channel-persona", "prime-ai-button prime-ai-button-secondary", !personaChannel || !hasChannelPersona),
        button("حذف استثناء الرتبة", "remove-role-override", "prime-ai-button prime-ai-button-secondary", !roleId),
      );
      personaSection.content.append(personaActions);
      addControlSection("talk", personaSection);

      controlSections
        .filter((section) => section.key === destination)
        .forEach((section) => {
          section.details.open = true;
          card.append(section.details);
        });
      return card;
    }

    function renderSkillCards() {
      const card = el("section", "prime-ai-card");
      card.append(sectionHeader("AI → Skills", "إدارة المهارات المسجلة للقراءة فقط حسب الفئة والصلاحيات وحدود الاستخدام.", "prime-ai-skills-title"));
      if (state.controlLoading && !state.controlLoaded) {
        card.append(loadingCard("جارٍ تحميل المهارات"));
        return card;
      }
      if (state.controlError && !state.controlLoaded) {
        card.append(loadError(state.controlError, "reload-control"));
        return card;
      }
      if (!state.skills.length) {
        card.append(el("p", "prime-ai-muted", "لا توجد مهارات متاحة."));
        return card;
      }
      const channels = availableChannels().map((item) => ({ value: item.id, label: `#${item.name || item.id}` }));
      const roles = (state.roles || []).map((item) => ({ value: item.id, label: item.name || item.id }));
      const permissions = ["everyone", "manage_messages", "moderate_members", "manage_roles", "manage_channels", "manage_guild", "administrator", "owner"]
        .map((value) => ({ value, label: value }));
      const categoryLabels = {
        General: "عام",
        Leveling: "المستويات",
        Streak: "السلاسل",
        Subscription: "الاشتراكات",
        Server: "الخادم",
        Analytics: "التحليلات",
      };
      const categories = ["General", "Leveling", "Streak", "Subscription", "Server", "Analytics"];
      categories.forEach((category) => {
        const skills = state.skills.filter((skill) => skill.category === category);
        if (!skills.length) return;
        const group = el("section", "prime-ai-skill-group");
        group.append(sectionHeader(categoryLabels[category] || category, "", `prime-ai-skills-${category.toLowerCase()}`));
        skills.forEach((skill) => {
          const draft = state.skillDrafts[skill.key] || skill;
          const section = el("article", "prime-ai-skill-card");
          section.append(el("div", "prime-ai-skill-title", skill.name));
          section.append(el(
            "p",
            "prime-ai-help",
            skill.description || "",
          ));
          const permission = permissions.find((item) => item.value === draft.required_permission);
          section.append(el(
            "small",
            "prime-ai-help",
            `${skill.available ? "متاحة" : "غير متاحة"} · الصلاحية: ${permission ? permission.label : draft.required_permission || "غير محددة"}`,
          ));
          const grid = el("div", "prime-ai-control-grid");
          const enabled = controlToggle(`skill.${skill.key}.enabled`, "تفعيل المهارة", draft.enabled);
          if (!skill.available) {
            const toggle = enabled.querySelector("input");
            if (toggle) toggle.disabled = true;
          }
          grid.append(
            enabled,
            controlSelect(`skill.${skill.key}.required_permission`, "أقل صلاحية", draft.required_permission, permissions),
            controlSelect(`skill.${skill.key}.allowed_channels`, "قنوات المهارة", draft.allowed_channels, channels, true),
            controlSelect(`skill.${skill.key}.allowed_roles`, "رتب المهارة", draft.allowed_roles, roles, true),
            controlInput(`skill.${skill.key}.rate_limit.limit`, "حد الطلبات", draft.rate_limit && draft.rate_limit.limit, { type: "number", min: 1, max: 10000, step: 1 }),
            controlInput(`skill.${skill.key}.rate_limit.window_seconds`, "نافذة الحد بالثواني", draft.rate_limit && draft.rate_limit.window_seconds, { type: "number", min: 1, max: 86400, step: 1 }),
          );
          section.append(grid);
          if (state.skillErrors[skill.key]) {
            const error = el("p", "prime-ai-error-text", state.skillErrors[skill.key]);
            error.setAttribute("role", "alert");
            section.append(error);
          }
          section.append(button("حفظ المهارة", "save-skill", "prime-ai-button prime-ai-button-secondary", state.skillSaving === skill.key || !skill.available));
          section.querySelector("[data-action='save-skill']").dataset.skillKey = skill.key;
          group.append(section);
        });
        card.append(group);
      });
      return card;
    }

    function renderRuntimeCards() {
      const wrapper = el("div", "prime-ai-runtime-grid");
      const stats = el("section", "prime-ai-card");
      const statsHeader = sectionHeader("تحليلات PRIME AI", "بيانات الاستخدام المسجلة خلال آخر 30 يوماً.", "prime-ai-analytics-title");
      statsHeader.append(button(
        state.analyticsLoading ? "جارٍ التحديث..." : "تحديث المؤشرات",
        "reload-analytics",
        "prime-ai-button prime-ai-button-secondary",
        state.analyticsLoading,
      ));
      stats.append(statsHeader);
      if (state.analyticsError) {
        stats.append(loadError(state.analyticsError, "reload-analytics"));
      }
      const totals = (state.analytics && state.analytics.totals) || {};
      const statGrid = el("div", "prime-ai-stat-grid");
      [
        ["الطلبات", totals.requests || 0],
        ["الناجحة", totals.successful || 0],
        ["الفاشلة", totals.failed || 0],
        ["متوسط التأخير", `${totals.avg_latency_ms || 0} ms`],
        ["الرموز", totals.tokens || 0],
        ["طلبات الإجراءات", state.analytics && state.analytics.actions || 0],
        ["أحداث الإشراف", state.analytics && state.analytics.moderation_events || 0],
      ].forEach(([label, value]) => {
        const item = el("div", "prime-ai-stat");
        item.append(el("span", "", label), el("strong", "", value));
        statGrid.append(item);
      });
      stats.append(statGrid);
      const breakdowns = [
        ["الطلبات حسب المهارة", state.analytics && state.analytics.skills, "skill"],
        ["الطلبات حسب القناة", state.analytics && state.analytics.channels, "channel_id"],
        ["الطلبات حسب العضو", state.analytics && state.analytics.users, "user_id"],
      ];
      breakdowns.forEach(([title, rows, nameKey]) => {
        if (!Array.isArray(rows) || !rows.length) return;
        const breakdown = el("div", "prime-ai-analytics-breakdown");
        breakdown.append(el("h3", "", title));
        rows.forEach((item) => {
          const row = el("div", "prime-ai-analytics-row");
          row.append(el("span", "", item[nameKey] || "غير محدد"));
          row.append(el("strong", "", item.requests || 0));
          breakdown.append(row);
        });
        stats.append(breakdown);
      });
      wrapper.append(stats);
      return wrapper;
    }

    function renderOverviewCard() {
      const card = el("section", "prime-ai-card");
      const heading = sectionHeader(
        "لوحة حالة PRIME AI",
        "من هنا تبدأ إعداد المساعد وتتابع أهم حالاته من مكان واحد.",
        "prime-ai-overview-title",
      );
      const primaryActions = el("div", "prime-ai-actions prime-ai-overview-actions");
      [
        ["إعداد Talk", "talk"],
        ["اختبار آمن", "sandbox"],
      ].forEach(([label, destination], index) => {
        const action = button(
          label,
          "navigate-destination",
          `prime-ai-button ${index === 0 ? "prime-ai-button-primary" : "prime-ai-button-secondary"}`,
        );
        action.dataset.destination = destination;
        primaryActions.append(action);
      });
      heading.append(primaryActions);
      card.append(heading);
      const cfg = state.controlSnapshot && state.controlSnapshot.config
        ? state.controlSnapshot.config
        : {};
      const memory = cfg.memory || {};
      const safety = cfg.safety || {};
      const actions = Object.values(cfg.actions || {});
      const talkChannel = cfg.talk_channel || {};
      const analytics = state.analytics;
      const analyticsText = state.analyticsError
        ? "تعذر تحميل المؤشرات"
        : state.analyticsLoading
          ? "جارٍ التحميل"
          : `${analytics && analytics.totals ? analytics.totals.requests || 0 : 0} طلب خلال 30 يوماً`;
      const values = [
        ["حالة المساعد", state.settingsLoading ? "جارٍ التحميل" : state.settings ? state.enabled ? "مفعّل" : "متوقف" : "غير معروف"],
        ["مزوّد الذكاء", state.controlLoaded ? state.providerStatus === "configured" ? "متصل بالإعداد" : "غير مهيّأ" : "جارٍ التحقق"],
        ["Talk", state.controlLoaded
          ? talkChannel.enabled && talkChannel.channel_id ? "مفعّل" : "غير مفعّل"
          : "جارٍ التحقق"],
        ["المهارات", state.controlLoaded
          ? `${state.skills.filter((item) => item.available && item.enabled).length} مفعّلة`
          : "جارٍ التحميل"],
        ["إجراءات Discord", state.controlLoaded
          ? `${actions.filter((item) => item.enabled).length} مسموح بها`
          : "جارٍ التحميل"],
        ["حماية التنفيذ", state.controlLoaded
          ? safety.dry_run ? "معاينة آمنة" : "تنفيذ بعد فحوص السياسة"
          : "جارٍ التحميل"],
        ["الذاكرة", state.controlLoaded ? memory.enabled ? "مفعّلة" : "متوقفة" : "جارٍ التحميل"],
        ["نشاط آخر 30 يوماً", analyticsText],
      ];
      const grid = el("div", "prime-ai-stat-grid");
      values.forEach(([label, value]) => {
        const item = el("div", "prime-ai-stat");
        item.append(el("span", "", label), el("strong", "", value));
        grid.append(item);
      });
      card.append(grid);

      const readiness = el("section", "prime-ai-readiness");
      readiness.append(el("h3", "", "الإعداد الأساسي"));
      readiness.append(el("p", "prime-ai-help", "تحقق من هذه النقاط قبل تفعيل PRIME AI في خادمك."));
      const readinessItems = [
        {
          label: "المساعد",
          detail: state.settingsLoading ? "جارٍ التحقق" : state.enabled ? "مفعّل" : "متوقف",
          ready: Boolean(state.settings && state.enabled),
          destination: "general",
          action: "مراجعة الإعداد",
        },
        {
          label: "مزود الخدمة",
          detail: !state.controlLoaded ? "جارٍ التحقق" : state.providerStatus === "configured" ? "مهيّأ" : "يحتاج إعداداً",
          ready: state.controlLoaded && state.providerStatus === "configured",
          destination: "providers",
          action: "عرض المزوّد",
        },
        {
          label: "قناة Talk",
          detail: !state.controlLoaded
            ? "جارٍ التحقق"
            : talkChannel.enabled && talkChannel.channel_id ? "مهيّأة" : "اختيارية — غير مفعّلة",
          ready: state.controlLoaded && Boolean(talkChannel.enabled && talkChannel.channel_id),
          destination: "talk",
          action: "إعداد Talk",
        },
        {
          label: "المهارات",
          detail: !state.controlLoaded
            ? "جارٍ التحقق"
            : state.skills.some((item) => item.available && item.enabled) ? "متاحة" : "لا توجد مهارات مفعّلة",
          ready: state.controlLoaded && state.skills.some((item) => item.available && item.enabled),
          destination: "skills",
          action: "إدارة المهارات",
        },
      ];
      const readinessList = el("div", "prime-ai-readiness-list");
      readinessItems.forEach((item) => {
        const row = el("article", `prime-ai-readiness-item${item.ready ? " is-ready" : ""}`);
        const status = el("span", "prime-ai-readiness-status", item.detail);
        status.setAttribute("role", "status");
        const text = el("div", "prime-ai-readiness-copy");
        text.append(el("strong", "", item.label), status);
        const link = button(item.action, "navigate-destination", "prime-ai-button prime-ai-button-secondary");
        link.dataset.destination = item.destination;
        row.append(text, link);
        readinessList.append(row);
      });
      readiness.append(readinessList);
      card.append(readiness);

      const quick = el("section", "prime-ai-quick-config");
      quick.append(el("h3", "", "إدارة PRIME AI"));
      quick.append(el("p", "prime-ai-help", "انتقل إلى المحادثة أو القدرات أو سياسات الحماية."));
      const shortcuts = el("div", "prime-ai-quick-links");
      [
        ["Talk والسياق", "talk"],
        ["الشخصية والردود", "personality"],
        ["الوصول والصلاحيات", "permissions"],
        ["المهارات", "skills"],
        ["الإشراف", "moderation"],
        ["سجل الطلبات", "actions"],
      ].forEach(([label, destination]) => {
        const link = button(label, "navigate-destination", "prime-ai-button prime-ai-quick-link");
        link.dataset.destination = destination;
        shortcuts.append(link);
      });
      quick.append(shortcuts);
      card.append(quick);

      const recent = el("div", "prime-ai-overview-recent");
      const recentHeader = el("div", "prime-ai-overview-recent-head");
      recentHeader.append(el("strong", "", "آخر نشاط مسجل"));
      const auditLink = button("سجل التدقيق", "navigate-destination", "prime-ai-button prime-ai-button-secondary");
      auditLink.dataset.destination = "audit";
      recentHeader.append(auditLink);
      recent.append(recentHeader);
      if (state.auditError) {
        recent.append(el("p", "prime-ai-error-text", "تعذر تحميل سجل النشاط."));
      } else if (state.auditLoading) {
        recent.append(el("p", "prime-ai-muted", "جارٍ تحميل سجل النشاط."));
      } else if (state.events.length) {
        const event = state.events[0];
        recent.append(el(
          "p",
          "",
          `${event.action || "نشاط"} · ${event.result || "غير محدد"} · ${readableDate(event.created_at)}`,
        ));
      } else {
        recent.append(el("p", "prime-ai-muted", "لا توجد أحداث مسجلة."));
      }
      card.append(recent);
      return card;
    }

    function renderSandboxCard() {
      const card = el("section", "prime-ai-card prime-ai-sandbox-card");
      card.append(sectionHeader(
        "معمل اختبار آمن",
        "عاين فهم PRIME AI والسياق وفحوص الصلاحيات باستخدام الإعدادات الحالية.",
        "prime-ai-sandbox-title",
      ));
      const safetyNote = el("div", "prime-ai-warning");
      safetyNote.append(el("strong", "", "معاينة فقط — لا تغييرات على Discord"));
      safetyNote.append(el(
        "p",
        "",
        "المعمل ينشئ خطة ويفحص الصلاحيات، لكنه لا يستدعي منفّذ الإجراءات. لا تُحفظ رسالة الاختبار.",
      ));
      card.append(safetyNote);

      const channels = (state.controlChannels || []).filter(
        (item) => item.text_based !== false,
      );
      if (!state.sandboxChannelId && channels.length) {
        state.sandboxChannelId = String(channels[0].id);
      }
      const form = el("form", "prime-ai-test-form");
      form.dataset.form = "sandbox";
      const channelLabel = el("label", "", "قناة السياق");
      channelLabel.htmlFor = "prime-ai-sandbox-channel";
      const channel = el("select", "prime-ai-select");
      channel.id = "prime-ai-sandbox-channel";
      channel.dataset.localField = "sandbox-channel";
      channel.disabled = state.sandboxRunning || channels.length === 0;
      const emptyOption = el(
        "option",
        "",
        channels.length ? "اختر قناة" : "لا توجد قنوات نصية",
      );
      emptyOption.value = "";
      channel.append(emptyOption);
      channels.forEach((item) => {
        const option = el("option", "", `#${item.name || item.id}`);
        option.value = String(item.id);
        option.selected = String(item.id) === state.sandboxChannelId;
        channel.append(option);
      });

      const promptLabel = el("label", "", "طلب المعاينة");
      promptLabel.htmlFor = "prime-ai-sandbox-prompt";
      const prompt = el("textarea", "prime-ai-textarea");
      prompt.id = "prime-ai-sandbox-prompt";
      prompt.rows = 3;
      prompt.maxLength = 1200;
      prompt.placeholder = "مثال: أنشئ قناة باسم الإعلانات.";
      prompt.value = state.sandboxPrompt;
      prompt.dataset.localField = "sandbox-prompt";
      const actions = el("div", "prime-ai-actions");
      actions.append(button(
        state.sandboxRunning ? "جارٍ إنشاء المعاينة..." : "إنشاء معاينة",
        "run-sandbox",
        "prime-ai-button prime-ai-button-primary",
        state.sandboxRunning || channels.length === 0,
      ));
      form.append(channelLabel, channel, promptLabel, prompt, actions);
      card.append(form);

      if (state.sandboxError) {
        const error = el("p", "prime-ai-error-text", state.sandboxError);
        error.setAttribute("role", "alert");
        card.append(error);
      }
      if (state.sandboxResult) {
        const result = el("div", "prime-ai-test-result");
        result.setAttribute("role", "status");
        result.append(el("strong", "", "نتيجة المعاينة — لم يُنفّذ أي إجراء"));
        const context = state.sandboxResult.context_preview || {};
        result.append(el(
          "p",
          "",
          `النية: ${state.sandboxResult.intent || "غير محددة"} · القناة: #${context.channel || "غير محددة"} · الأهداف المطابقة: ${Number(context.target_candidates) || 0}`,
        ));
        if (state.sandboxResult.clarification) {
          result.append(el("p", "", `التوضيح المطلوب: ${state.sandboxResult.clarification}`));
        }
        const steps = Array.isArray(state.sandboxResult.steps)
          ? state.sandboxResult.steps
          : [];
        const decisions = Array.isArray(state.sandboxResult.permission_decisions)
          ? state.sandboxResult.permission_decisions
          : [];
        if (steps.length) {
          const list = el("div", "prime-ai-audit-list");
          steps.forEach((step, index) => {
            const item = el("article", "prime-ai-audit-item");
            item.append(el(
              "strong",
              "",
              `${index + 1}. ${step.name || step.action_id || "إجراء"}`,
            ));
            const targetNames = (step.targets || [])
              .map((target) => target.name)
              .filter(Boolean);
            if (targetNames.length) {
              item.append(el("p", "", `الهدف: ${targetNames.join("، ")}`));
            }
            const args = step.arguments && Object.keys(step.arguments).length
              ? JSON.stringify(step.arguments)
              : "";
            if (args) item.append(el("p", "", `التفاصيل: ${args}`));
            const decision = decisions[index];
            item.append(el(
              "p",
              "",
              decision && decision.allowed
                ? "فحوص الصلاحية: مسموح حالياً — لم يُنفّذ."
                : `فحوص الصلاحية: مرفوض — ${decision && decision.reason || "تعذر التحقق"}`,
            ));
            list.append(item);
          });
          result.append(list);
        } else {
          result.append(el("p", "", "لم تُنشأ خطوات إجراء. راجع التوضيح أو الإعدادات المحفوظة."));
        }
        card.append(result);
      }
      return card;
    }

    function renderOperationCard() {
      const card = el("section", "prime-ai-card");
      const heading = sectionHeader("طلبات الإجراءات", "طلبات موثقة؛ تنفذ إجراءات PRIME المفعّلة مباشرة بعد إعادة فحص صلاحية Administrator والهدف وصلاحيات البوت.", "prime-ai-operations-title");
      heading.append(button(
        state.operationsLoading ? "جارٍ التحديث..." : "تحديث الطلبات",
        "reload-operations",
        "prime-ai-button prime-ai-button-secondary",
        state.operationsLoading,
      ));
      card.append(heading);
      if (state.operationsError) card.append(loadError(state.operationsError, "reload-operations"));
      const list = el("div", "prime-ai-audit-list");
      if (state.operationsLoading && !state.operations.length) {
        list.append(loadingCard("جارٍ تحميل طلبات الإجراءات"));
      } else if (!state.operations.length) {
        list.append(el("p", "prime-ai-muted", "لا توجد طلبات إجراءات."));
      }
      state.operations.forEach((item) => {
        const row = el("article", "prime-ai-audit-item");
        row.append(el("strong", "", `${item.status} · ${item.skill || "PRIME AI"}`));
        row.append(el("p", "", item.request || "طلب بلا نص."));
        row.append(el("small", "prime-ai-help", `${(item.tools || []).join("، ")} · ${readableDate(item.created_at)}`));
        if (item.execution_result) row.append(el("p", "", `النتيجة: ${item.execution_result}`));
        if (item.error) row.append(el("p", "prime-ai-error-text", item.error));
        list.append(row);
      });
      card.append(list);
      return card;
    }

    function render() {
      if (state.disposed) return;
      const page = el("div", "prime-ai-control");
      page.dir = "rtl";
      const groups = config.standalone
        ? [
          {
            id: "talk",
            label: "Talk",
            destinations: [
              { id: "talk", label: "إعدادات Talk", description: "القناة والصلاحيات وسلوك محادثة PRIME." },
            ],
          },
        ]
        : [
        {
          id: "home",
          label: "الرئيسية",
          destinations: [
            { id: "overview", label: "لوحة الحالة", description: "حالة النظام، أساسيات الإعداد، وآخر نشاط." },
            { id: "general", label: "التشغيل الأساسي", description: "تفعيل المساعد وتعليمات الخادم." },
          ],
        },
        {
          id: "conversation",
          label: "المحادثة",
          destinations: [
            { id: "talk", label: "Talk", description: "القناة والصلاحيات وسلوك محادثة PRIME." },
            { id: "context", label: "السياق", description: "السياق المؤقت والرسائل المقتبسة." },
            { id: "personality", label: "الشخصية", description: "طابع PRIME ولغته وطول ردوده." },
            { id: "personas", label: "استثناءات القنوات والرتب", description: "شخصيات مخصصة لقناة أو رتبة." },
            { id: "responses", label: "تنسيق الردود", description: "الذكر والتنسيق والحذف التلقائي." },
          ],
        },
        {
          id: "capabilities",
          label: "القدرات",
          destinations: [
            { id: "skills", label: "المهارات", description: "تفعيل المهارات وصلاحياتها." },
            { id: "modes", label: "الأنماط", description: "أنماط PRIME AI والتفعيل." },
            { id: "memory", label: "الذاكرة", description: "سياسة الذاكرة وملاحظات الخادم." },
          ],
        },
        {
          id: "safety",
          label: "السلامة",
          destinations: [
            { id: "permissions", label: "الوصول والصلاحيات", description: "القنوات والرتب المسموح بها." },
            { id: "moderation", label: "الإشراف", description: "تصنيف التنبيهات ومراجعتها." },
            { id: "sandbox", label: "Sandbox", description: "معاينة الإجراءات دون تنفيذها." },
          ],
        },
        {
          id: "engine",
          label: "محرك الذكاء الاصطناعي",
          destinations: [
            { id: "providers", label: "المزوّد", description: "إعدادات مزوّد Gemini الحالية." },
            { id: "limits", label: "الحدود والاحتفاظ", description: "حدود الاستخدام ومدد الاحتفاظ." },
          ],
        },
        {
          id: "activity",
          label: "النشاط",
          destinations: [
            { id: "actions", label: "سجل الطلبات", description: "طلبات PRIME ونتائج الإجراءات المسجلة." },
            { id: "audit", label: "سجل التدقيق", description: "الأحداث الإدارية المسجلة." },
            { id: "analytics", label: "التحليلات", description: "ملخص الاستخدام خلال 30 يوماً." },
            { id: "testing", label: "اختبار PRIME AI", description: "اختبار الاتصال والرد على رسالة لمرة واحدة." },
          ],
        },
      ];
      const destinations = groups.flatMap((group) => group.destinations);
      const active = destinations.find((item) => item.id === state.activeDestination) ||
        destinations[0];
      state.activeDestination = active.id;
      const activeGroup = groups.find((group) => group.destinations.some((item) => item.id === active.id));
      const intro = el("header", "prime-ai-hero");
      const identity = el("div", "prime-ai-identity");
      identity.append(el("span", "prime-ai-eyebrow", "PRIME AI CONTROL CENTER"));
      identity.append(el("h1", "", "PRIME AI"));
      identity.append(el("p", "", "مركز تحكم الذكاء الاصطناعي لخادمك."));
      const badgeStatus = state.settingsLoading
        ? "جارٍ التحقق"
        : state.settings
          ? state.enabled ? "مفعّل" : "متوقف"
          : "الحالة غير معروفة";
      const badge = el(
        "span",
        `prime-ai-status-badge ${state.settings && state.enabled ? "is-enabled" : "is-disabled"}`,
      );
      badge.append(el("span", "prime-ai-status-dot"));
      badge.append(document.createTextNode(badgeStatus));
      intro.append(identity, badge);
      page.append(intro);

      const notice = el("aside", "prime-ai-notice");
      notice.append(el("strong", "", "مساعد محادثاتي ضمن أنظمة PRIME"));
      const conversationDays = state.controlSnapshot?.config?.retention?.conversation_days;
      const retentionCopy = Number.isInteger(conversationDays)
        ? conversationDays === 0
          ? "حفظ سياق المحادثات متوقف."
          : `مدة حفظ سياق المحادثات الموجّهة: ${conversationDays} ${conversationDays === 1 ? "يوم" : "أيام"}.`
        : "مدة حفظ سياق المحادثات قابلة للضبط من قسم الحدود والاحتفاظ.";
      notice.append(el(
        "p",
        "",
        `يعمل المساعد بجانب أنظمة PRIME الأخرى. تُرسل الرسائل الموجّهة إلى PRIME والسياق المحدود المسموح به إلى Google Gemini. ${retentionCopy} لا يجمع PRIME سجل القناة بالكامل، والذاكرة المنفصلة تخضع لإعداداتها الخاصة.`,
      ));
      page.append(notice);

      const workspace = el(
        "div",
        `prime-ai-workspace${config.standalone ? " prime-ai-workspace-standalone" : ""}`,
      );
      const nav = el("nav", "prime-ai-sidebar");
      nav.setAttribute("aria-label", "أقسام PRIME AI");
      nav.append(el("span", "prime-ai-sidebar-title", "مركز التحكم"));
      groups.forEach((group) => {
        const groupOpen = group.id === activeGroup.id;
        const section = el("details", "prime-ai-nav-group");
        section.open = groupOpen;
        const summary = el("summary", "prime-ai-nav-group-title");
        summary.append(el("span", "", group.label));
        summary.append(el("span", "prime-ai-nav-caret", "⌄"));
        section.append(summary);
        const links = el("div", "prime-ai-nav-links");
        group.destinations.forEach((destination) => {
          const link = button(
            destination.label,
            "navigate-destination",
            `prime-ai-nav-button${destination.id === active.id ? " is-active" : ""}`,
          );
          link.dataset.destination = destination.id;
          link.setAttribute("aria-current", destination.id === active.id ? "page" : "false");
          links.append(link);
        });
        section.append(links);
        nav.append(section);
      });
      const content = el("main", "prime-ai-content");
      const pageHeading = el("header", "prime-ai-page-title");
      pageHeading.append(el("h2", "", active.label));
      pageHeading.append(el("p", "", active.description));
      content.append(pageHeading);
      switch (active.id) {
        case "overview":
          content.append(renderOverviewCard());
          break;
        case "general":
          content.append(renderSettingsCard());
          break;
        case "context":
        case "permissions":
        case "personality":
        case "personas":
        case "talk":
        case "moderation":
        case "modes":
        case "providers":
        case "responses":
        case "limits":
          content.append(renderControlCard(active.id));
          break;
        case "actions":
          content.append(renderOperationCard());
          break;
        case "memory":
          content.append(renderControlCard("memory"), renderMemoriesCard());
          break;
        case "skills":
          content.append(renderSkillCards());
          break;
        case "sandbox":
          content.append(renderSandboxCard());
          break;
        case "audit":
          content.append(renderAuditCard());
          break;
        case "analytics":
          content.append(renderRuntimeCards());
          break;
        case "testing":
          content.append(renderTestCard());
          break;
        default:
          content.append(renderOverviewCard());
      }
      if (!config.standalone) workspace.append(nav);
      workspace.append(content);
      page.append(workspace);
      const saveDock = el("aside", "prime-ai-save-dock");
      saveDock.setAttribute("aria-label", "حفظ تغييرات مركز التحكم");
      const saveStatus = el("p", "prime-ai-save-status");
      saveStatus.setAttribute("aria-live", "polite");
      const saveActions = el("div", "prime-ai-save-actions");
      saveActions.append(
        button("تحميل النسخة الأحدث", "apply-control-conflict", "prime-ai-button prime-ai-button-secondary"),
        button("تجاهل التغييرات", "discard-control", "prime-ai-button prime-ai-button-secondary"),
        button("حفظ التغييرات", "save-control", "prime-ai-button prime-ai-button-primary"),
      );
      saveDock.append(saveStatus, saveActions);
      page.append(saveDock);
      container.replaceChildren(page);
      syncSaveDock();
    }

    async function loadSettings(preserveDraft) {
      state.settingsLoading = true;
      state.settingsError = "";
      render();
      try {
        const response = ensureOk(await request("GET", guildPath));
        const data = await responseJson(response);
        if (!data.settings || !Number.isInteger(data.settings.revision) ||
          !Array.isArray(data.channels) || !Array.isArray(data.memories)) {
          throw new Error("invalid_response");
        }
        if (state.disposed) return;
        state.settings = data.settings;
        if (preserveDraft && state.settingsConflict) {
          state.settingsConflict = data.settings;
        } else if (!preserveDraft) {
          state.settingsConflict = null;
          state.settingsSaveError = "";
        }
        state.channels = data.channels;
        if (!state.controlLoaded) state.memories = data.memories;
        if (!preserveDraft) {
          state.enabled = Boolean(data.settings.enabled);
          state.systemPrompt = typeof data.settings.system_prompt === "string" ? data.settings.system_prompt : "";
          if (!state.controlLoaded) {
            state.allowedChannels = Array.isArray(data.settings.allowed_channel_ids)
              ? data.settings.allowed_channel_ids.map(String)
              : [];
          }
        }
      } catch (_) {
        if (state.disposed) return;
        state.settingsError = errorText();
      } finally {
        if (!state.disposed) {
          state.settingsLoading = false;
          render();
        }
      }
    }

    async function loadAudit() {
      state.auditLoading = true;
      state.auditError = "";
      render();
      try {
        const response = ensureOk(await request("GET", `${guildPath}/audit`));
        const data = await responseJson(response);
        if (!Array.isArray(data.events)) throw new Error("invalid_response");
        if (state.disposed) return;
        state.events = data.events;
      } catch (_) {
        if (state.disposed) return;
        state.auditError = errorText();
      } finally {
        if (!state.disposed) {
          state.auditLoading = false;
          render();
        }
      }
    }

    async function loadControl() {
      state.controlLoading = true;
      state.controlError = "";
      render();
      try {
        const response = ensureOk(await request("GET", `${guildPath}/control`));
        const data = await responseJson(response);
        if (
          !data.control ||
          !Array.isArray(data.skills) ||
          !Array.isArray(data.memories) ||
        !Array.isArray(data.channels) ||
          !Array.isArray(data.action_registry) ||
          !Array.isArray(data.roles)
        ) {
          throw new Error("invalid_response");
        }
        if (state.disposed) return;
        applyControlSnapshot(data.control, false);
        installSkills(data.skills, false);
        state.memories = data.memories;
        state.controlChannels = data.channels;
        state.actionRegistry = data.action_registry;
        state.roles = data.roles;
        state.isBotOwner = Boolean(data.is_bot_owner);
        state.providerStatus = String(data.provider_status || "unknown");
        state.controlLoaded = true;
        if (Array.isArray(data.operations)) {
          state.operations = data.operations;
          state.operationsError = "";
        } else {
          state.operationsError = "تعذر تحميل سجل الطلبات.";
        }
        state.operationsLoading = false;
        if (data.analytics && typeof data.analytics === "object" && data.analytics.totals) {
          state.analytics = data.analytics;
          state.analyticsError = "";
        } else {
          state.analyticsError = "تعذر تحميل المؤشرات.";
        }
        state.analyticsLoading = false;
      } catch (_) {
        if (state.disposed) return;
        state.controlError = errorText();
        state.operationsError = state.controlError;
        state.operationsLoading = false;
        state.analyticsError = state.controlError;
        state.analyticsLoading = false;
      } finally {
        if (!state.disposed) {
          state.controlLoading = false;
          render();
        }
      }
    }

    async function loadOperations() {
      state.operationsLoading = true;
      state.operationsError = "";
      render();
      try {
        const response = ensureOk(await request("GET", `${guildPath}/operations`));
        const data = await responseJson(response);
        if (!Array.isArray(data.operations)) throw new Error("invalid_response");
        if (!state.disposed) state.operations = data.operations;
      } catch (_) {
        if (!state.disposed) state.operationsError = errorText();
      } finally {
        if (!state.disposed) {
          state.operationsLoading = false;
          render();
        }
      }
    }

    async function loadAnalytics() {
      state.analyticsLoading = true;
      state.analyticsError = "";
      render();
      try {
        const response = ensureOk(await request("GET", `${guildPath}/analytics`));
        const data = await responseJson(response);
        if (!data.totals || typeof data.totals !== "object") throw new Error("invalid_response");
        if (!state.disposed) state.analytics = data;
      } catch (_) {
        if (!state.disposed) state.analyticsError = errorText();
      } finally {
        if (!state.disposed) {
          state.analyticsLoading = false;
          render();
        }
      }
    }

    async function saveSettings() {
      if (
        state.settingsSaving ||
        state.settingsLoading ||
        state.settingsError ||
        !state.controlLoaded
      ) return;
      state.settingsSaving = true;
      state.settingsSaveError = "";
      render();
      try {
        const response = await request("POST", `${guildPath}/settings`, {
          enabled: state.enabled,
          system_prompt: state.systemPrompt,
          allowed_channel_ids: state.allowedChannels.slice(),
          revision: state.settings.revision,
        });
        if (response && response.status === 409) {
          const conflictBody = await responseJson(response);
          const current = conflictBody.settings || conflictBody;
          if (current && Number.isInteger(current.revision)) {
            state.settingsConflict = current;
            state.settingsSaveError = "";
          } else {
            state.settingsSaveError = "تعارض في الإعدادات. احتفظنا بتعديلاتك؛ أعد تحميل الإعدادات الحالية قبل الحفظ مجدداً.";
          }
          return;
        }
        ensureOk(response);
        if (state.disposed) return;
        setToast("تم حفظ إعدادات PRIME AI.", "success");
        await loadSettings(true);
      } catch (_) {
        if (!state.disposed) {
          state.settingsSaveError = "تعذر حفظ الإعدادات. تحقق من الاتصال ثم أعد المحاولة.";
          setToast("تعذر حفظ الإعدادات. تحقق من الاتصال وحاول مجدداً.", "error");
        }
      } finally {
        if (!state.disposed) {
          state.settingsSaving = false;
          render();
        }
      }
    }

    async function saveControl() {
      if (state.controlSaving || !state.controlSnapshot || !state.controlDraft) return;
      state.controlSaving = true;
      state.controlSaveError = "";
      state.controlConflict = null;
      render();
      try {
        const response = await request("POST", `${guildPath}/control`, {
          config: clone(state.controlDraft),
          revision: state.controlSnapshot.revision,
        });
        const data = await responseJson(response);
        if (response && response.status === 409 && data.control) {
          state.controlConflict = data.control;
          state.controlSaveError = "";
          return;
        }
        if (!response || !response.ok || !data.control) {
          throw new Error("save_failed");
        }
        applyControlSnapshot(data.control, false);
        state.controlConflict = null;
        setToast("تم حفظ إعدادات مركز التحكم.", "success");
      } catch (_) {
        if (!state.disposed) {
          state.controlSaveError = "تعذر حفظ إعدادات مركز التحكم. راجع القيم ثم أعد المحاولة.";
          setToast("تعذر حفظ إعدادات مركز التحكم.", "error");
        }
      } finally {
        if (!state.disposed) {
          state.controlSaving = false;
          render();
        }
      }
    }

    async function saveSkill(skillKey) {
      const skill = state.skills.find((item) => item.key === skillKey);
      const draft = state.skillDrafts[skillKey];
      if (!skill || !draft || state.skillSaving) return;
      state.skillSaving = skillKey;
      state.skillErrors[skillKey] = "";
      render();
      try {
        const payload = {
          enabled: Boolean(draft.enabled),
          required_permission: String(draft.required_permission || "manage_guild"),
          allowed_channels: Array.isArray(draft.allowed_channels) ? draft.allowed_channels.map(String) : [],
          allowed_roles: Array.isArray(draft.allowed_roles) ? draft.allowed_roles.map(String) : [],
          rate_limit: {
            limit: Number(draft.rate_limit && draft.rate_limit.limit),
            window_seconds: Number(draft.rate_limit && draft.rate_limit.window_seconds),
          },
        };
        const response = await request(
          "POST",
          `${guildPath}/skills/${encodeURIComponent(skillKey)}`,
          { revision: skill.revision, settings: payload },
        );
        const data = await responseJson(response);
        if (response && response.status === 409 && data.skill) {
          const latest = data.skill.skill || data.skill;
          const revision = data.skill.revision || latest.revision;
          if (latest && latest.key) {
            const updated = { ...latest, revision };
            state.skills = state.skills.map((item) => item.key === skillKey ? updated : item);
            state.skillDrafts[skillKey] = clone(updated);
          }
          state.skillErrors[skillKey] = "تغيّرت هذه المهارة على الخادم؛ حمّلنا النسخة الأحدث. راجعها ثم احفظ مجدداً.";
          return;
        }
        if (!response || !response.ok || !data.skill) throw new Error("save_failed");
        state.skills = state.skills.map((item) => item.key === skillKey ? data.skill : item);
        state.skillDrafts[skillKey] = clone(data.skill);
        state.skillErrors[skillKey] = "";
        setToast("تم حفظ إعدادات المهارة.", "success");
      } catch (_) {
        if (!state.disposed) {
          state.skillErrors[skillKey] = "تعذر حفظ المهارة. تحقق من القيم والصلاحيات ثم أعد المحاولة.";
          setToast("تعذر حفظ إعدادات المهارة.", "error");
        }
      } finally {
        if (!state.disposed) {
          state.skillSaving = null;
          render();
        }
      }
    }

    async function addMemory() {
      const content = state.memoryContent.trim();
      const expiresInDays = effectiveMemoryExpirationDays();
      if (!content) {
        setToast("اكتب محتوى الذاكرة قبل الحفظ.", "error");
        return;
      }
      if (!Number.isInteger(expiresInDays) ||
        expiresInDays < 0 || expiresInDays > 3650) {
        state.memoryError = "أدخل مدة انتهاء صحيحة بين 0 و3650 يوماً.";
        render();
        return;
      }
      const scope = String(state.memoryScope || "SERVER").toUpperCase();
      const scopeId = ["CHANNEL", "ROLE", "USER"].includes(scope)
        ? String(state.memoryScopeId || "").trim()
        : "";
      if (["CHANNEL", "ROLE", "USER"].includes(scope) && !scopeId) {
        state.memoryError = "اختر القناة أو الرتبة أو أدخل معرّف العضو.";
        render();
        return;
      }
      if (scope === "USER" && !/^\d{1,22}$/.test(scopeId)) {
        state.memoryError = "معرّف العضو يجب أن يتكون من أرقام فقط.";
        render();
        return;
      }
      if (state.memorySaving) return;
      state.memorySaving = true;
      state.memoryError = "";
      render();
      try {
        const editing = state.editingMemory;
        const path = editing
          ? `${guildPath}/memories/${encodeURIComponent(String(editing.id))}/edit`
          : `${guildPath}/memories`;
        const response = await request("POST", path, {
          content,
          scope,
          scope_id: scopeId,
          expires_in_days: expiresInDays,
          enabled: Boolean(state.memoryEnabled),
        });
        const data = await responseJson(response);
        if (!response || !response.ok || !data.memory) {
          if (response && response.status === 403) throw new Error("forbidden");
          throw new Error("save_failed");
        }
        if (state.disposed) return;
        state.memoryContent = "";
        state.editingMemory = null;
        state.memoryScope = "SERVER";
        state.memoryScopeId = "";
        state.memoryExpiresInDays = null;
        state.memoryExpiryOverridden = false;
        state.memoryEnabled = true;
        state.memories = [data.memory, ...state.memories.filter(
          (item) => String(item.id) !== String(data.memory.id),
        )];
        setToast(editing ? "تم تحديث الذاكرة." : "تمت إضافة الذاكرة.", "success");
        loadAudit();
      } catch (_) {
        if (!state.disposed) {
          state.memoryError = "تعذر حفظ الذاكرة. تحقق من النطاق والصلاحيات ثم أعد المحاولة.";
          setToast("تعذر حفظ الذاكرة.", "error");
        }
      } finally {
        if (!state.disposed) {
          state.memorySaving = false;
          render();
        }
      }
    }

    async function deleteMemory(memoryId) {
      if (state.deletingMemoryId !== null) return;
      const memory = state.memories.find((item) => String(item.id) === String(memoryId));
      if (!window.confirm(`هل تريد حذف هذه الذاكرة (${memory ? scopeLabel(memory) : memoryId})؟`)) return;
      state.deletingMemoryId = memoryId;
      state.memoryError = "";
      render();
      try {
        ensureOk(await request("POST", `${guildPath}/memories/${encodeURIComponent(String(memoryId))}/delete`, {}));
        if (state.disposed) return;
        state.memories = state.memories.filter((memory) => String(memory.id) !== String(memoryId));
        if (state.editingMemory && String(state.editingMemory.id) === String(memoryId)) {
          state.editingMemory = null;
          state.memoryContent = "";
        }
        setToast("تم حذف الذاكرة.", "success");
        loadAudit();
      } catch (_) {
        if (!state.disposed) {
          state.memoryError = "تعذر حذف الذاكرة. تحقق من الاتصال ثم أعد المحاولة.";
          setToast("تعذر حذف الذاكرة. حاول مجدداً.", "error");
        }
      } finally {
        if (!state.disposed) {
          state.deletingMemoryId = null;
          render();
        }
      }
    }

    async function runTest() {
      const prompt = state.testPrompt.trim();
      if (!prompt) {
        state.testResult = { ok: false, text: "اكتب رسالة الاختبار أولاً." };
        render();
        return;
      }
      if (state.testing) return;
      state.testing = true;
      state.testResult = null;
      render();
      try {
        const response = ensureOk(await request("POST", `${guildPath}/test`, { prompt }));
        const result = await responseJson(response);
        if (state.disposed) return;
        if (result.ok) {
          state.testResult = {
            ok: true,
            text: typeof result.answer === "string" && result.answer
              ? result.answer
              : "لم تُرجع الاستجابة إجابة نصية.",
          };
          loadAudit();
        } else {
          state.testResult = {
            ok: false,
            text: typeof result.error === "string" && result.error
              ? result.error
              : "تعذر إكمال الاختبار.",
          };
        }
      } catch (_) {
        if (!state.disposed) state.testResult = { ok: false, text: "تعذر الاتصال بخدمة الاختبار. حاول مجدداً." };
      } finally {
        if (!state.disposed) {
          state.testing = false;
          render();
        }
      }
    }

    async function runSandbox() {
      const prompt = state.sandboxPrompt.trim();
      if (!prompt) {
        state.sandboxError = "اكتب طلباً لمعاينته أولاً.";
        render();
        return;
      }
      if (!state.sandboxChannelId) {
        state.sandboxError = "اختر قناة نصية لعرض سياقها.";
        render();
        return;
      }
      if (state.sandboxRunning) return;
      state.sandboxRunning = true;
      state.sandboxError = "";
      state.sandboxResult = null;
      render();
      try {
        const response = ensureOk(await request(
          "POST",
          `${guildPath}/sandbox`,
          { prompt, channel_id: state.sandboxChannelId },
        ));
        const result = await responseJson(response);
        if (!result || result.preview_only !== true) {
          throw new Error("unsafe_preview_response");
        }
        if (state.disposed) return;
        state.sandboxResult = result;
        loadAudit();
      } catch (_) {
        if (!state.disposed) {
          state.sandboxError = "تعذر إنشاء المعاينة الآمنة. تحقق من الاتصال والصلاحيات ثم أعد المحاولة.";
        }
      } finally {
        if (!state.disposed) {
          state.sandboxRunning = false;
          render();
        }
      }
    }

    const onInput = (event) => {
      const target = event.target;
      const dataset = target && target.dataset ? target.dataset : {};
      if (dataset.controlPath) {
        updateDraft(dataset.controlPath, fieldValue(target));
      } else if (dataset.localField === "memory-scope-id") {
        state.memoryScopeId = target.value;
      } else if (dataset.localField === "memory-expiry") {
        state.memoryExpiresInDays = fieldValue(target);
        state.memoryExpiryOverridden = true;
      } else if (dataset.localField === "sandbox-prompt") state.sandboxPrompt = target.value;
      else if (dataset.field === "system-prompt") state.systemPrompt = target.value;
      else if (dataset.field === "memory-content") state.memoryContent = target.value;
      else if (dataset.field === "test-prompt") state.testPrompt = target.value;
      syncSaveDock();
    };
    const onChange = (event) => {
      const target = event.target;
      const dataset = target && target.dataset ? target.dataset : {};
      if (dataset.controlPath) {
        updateDraft(dataset.controlPath, fieldValue(target));
        syncSaveDock();
        if (
          dataset.controlPath === "memory.default_expiration_days" &&
          !state.editingMemory &&
          !state.memoryExpiryOverridden
        ) {
          const expiry = container.querySelector('[data-local-field="memory-expiry"]');
          if (expiry) expiry.value = target.value;
        }
        return;
      }
      if (dataset.localField === "persona-channel") {
        state.selectedPersonaChannel = String(target.value);
        render();
        return;
      }
      if (dataset.localField === "sandbox-channel") {
        state.sandboxChannelId = String(target.value);
        return;
      }
      if (dataset.localField === "override-role") {
        state.selectedOverrideRole = String(target.value);
        render();
        return;
      }
      if (dataset.localField === "memory-scope") {
        state.memoryScope = String(target.value).toUpperCase();
        state.memoryScopeId = "";
        render();
        return;
      }
      if (dataset.localField === "memory-scope-id") {
        state.memoryScopeId = String(target.value);
        return;
      }
      if (dataset.localField === "memory-expiry") {
        state.memoryExpiresInDays = fieldValue(target);
        state.memoryExpiryOverridden = true;
        return;
      }
      if (dataset.localField === "memory-enabled") {
        state.memoryEnabled = Boolean(target.checked);
        return;
      }
      const field = dataset.field || "";
      if (field === "enabled") {
        state.enabled = target.checked;
        render();
      }
    };
    const onClick = (event) => {
      const target = event.target.closest && event.target.closest("[data-action]");
      if (!target || !container.contains(target)) return;
      const action = target.dataset.action;
      if (action === "navigate-destination") {
        const destination = target.dataset.destination;
        if (!destination) return;
        state.activeDestination = destination;
        if (!config.standalone) {
          try {
            sessionStorage.setItem(navigationStorageKey, destination);
          } catch (_) {
            // The selected section still changes for this page session.
          }
        }
        render();
        const heading = container.querySelector(".prime-ai-page-title h2");
        if (heading) {
          heading.tabIndex = -1;
          heading.focus({ preventScroll: true });
        }
      } else if (action === "save-settings") saveSettings();
      else if (action === "reload-settings") loadSettings();
      else if (action === "reload-audit") loadAudit();
      else if (action === "delete-memory") deleteMemory(target.dataset.memoryId);
      else if (action === "submit-memory") addMemory();
      else if (action === "edit-memory") {
        const memory = state.memories.find((item) => String(item.id) === String(target.dataset.memoryId));
        if (!memory) return;
        state.editingMemory = clone(memory);
        state.memoryContent = String(memory.content || "");
        state.memoryScope = String(memory.scope || "SERVER").toUpperCase();
        state.memoryScopeId = String(memory.scope_id || "");
        state.memoryExpiresInDays = daysUntil(memory.expires_at);
        state.memoryExpiryOverridden = true;
        state.memoryEnabled = memory.enabled !== false && memory.enabled !== 0;
        state.memoryError = "";
        render();
      } else if (action === "cancel-memory-edit") {
        state.editingMemory = null;
        state.memoryContent = "";
        state.memoryScope = "SERVER";
        state.memoryScopeId = "";
        state.memoryExpiresInDays = null;
        state.memoryExpiryOverridden = false;
        state.memoryEnabled = true;
        state.memoryError = "";
        render();
      }
      else if (action === "submit-test") runTest();
      else if (action === "run-sandbox") runSandbox();
      else if (action === "save-control") saveControl();
      else if (action === "discard-control" && state.controlSnapshot) {
        applyControlSnapshot(state.controlSnapshot, false);
        state.controlConflict = null;
        state.controlSaveError = "";
        render();
      }
      else if (action === "reload-control") loadControl();
      else if (action === "apply-control-conflict" && state.controlConflict) {
        try {
          applyControlSnapshot(state.controlConflict, false);
          state.controlConflict = null;
          state.controlSaveError = "";
        } catch (_) {
          state.controlSaveError = errorText();
        }
        render();
      } else if (action === "save-skill") saveSkill(target.dataset.skillKey);
      else if (action === "reload-operations") loadOperations();
      else if (action === "reload-analytics") loadAnalytics();
      else if (action === "remove-channel-persona" && state.selectedPersonaChannel && state.controlDraft) {
        if (state.controlDraft.channel_personas) {
          delete state.controlDraft.channel_personas[state.selectedPersonaChannel];
        }
        render();
      } else if (action === "remove-role-override" && state.selectedOverrideRole && state.controlDraft) {
        if (state.controlDraft.role_overrides) {
          delete state.controlDraft.role_overrides[state.selectedOverrideRole];
        }
        render();
      }
      else if (action === "apply-conflict-settings" && state.settingsConflict) {
        state.settings = state.settingsConflict;
        state.enabled = Boolean(state.settingsConflict.enabled);
        state.systemPrompt = typeof state.settingsConflict.system_prompt === "string"
          ? state.settingsConflict.system_prompt
          : "";
        state.settingsConflict = null;
        state.settingsSaveError = "";
        state.settingsError = "";
        render();
      }
    };
    const onSubmit = (event) => {
      const form = event.target.closest && event.target.closest("form[data-form]");
      if (!form || !container.contains(form)) return;
      event.preventDefault();
      if (form.dataset.form === "memory") addMemory();
      else if (form.dataset.form === "test") runTest();
      else if (form.dataset.form === "sandbox") runSandbox();
    };

    container.addEventListener("input", onInput);
    container.addEventListener("change", onChange);
    container.addEventListener("click", onClick);
    container.addEventListener("submit", onSubmit);
    render();
    loadSettings();
    loadControl();
    loadAudit();

    return function cleanup() {
      state.disposed = true;
      container.removeEventListener("input", onInput);
      container.removeEventListener("change", onChange);
      container.removeEventListener("click", onClick);
      container.removeEventListener("submit", onSubmit);
      container.replaceChildren();
    };
  }

  window.PrimeAIControl = Object.assign(window.PrimeAIControl || {}, { mount });
})();