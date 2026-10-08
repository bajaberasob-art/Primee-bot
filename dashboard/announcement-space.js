/* PRIME announcement space: vanilla DOM, mount(host, config) -> cleanup. */
(function () {
  "use strict";
  var MIN = 4, MAX = 5;

  function el(tag, attrs) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === undefined || v === null || v === false) return;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else if (k === "disabled" || k === "hidden") n[k] = !!v;
      else n.setAttribute(k, v === true ? "" : String(v));
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c !== null && c !== undefined && c !== false) n.append(c);
    }
    return n;
  }

  var uid = 0;
  var ERR = {
    csrf: "انتهت صلاحية الجلسة. أعد تحميل اللوحة ثم حاول مجدداً.",
    forbidden: "ليست لديك صلاحية تعديل هذه الإعدادات.",
  };
  var PERM = {
    view_channel: "عرض القناة",
    read_message_history: "قراءة سجل الرسائل",
    add_reactions: "إضافة التفاعلات",
    use_external_emojis: "استخدام الإيموجي الخارجي",
  };

  function loadMagic() {
    if (window.PrimeAIMagic) return Promise.resolve(window.PrimeAIMagic);
    return new Promise(function (resolve, reject) {
      var s = document.querySelector("script[data-prime-ai-island]") ||
        document.querySelector('script[src*="ai-magic-island.js"]');
      var created = !s;
      if (!s) {
        var own = document.querySelector('script[src*="ai-control.js"]');
        s = document.createElement("script");
        s.src = own ? own.src.replace(/ai-control\.js/, "ai-magic-island.js") : "static/ai-magic-island.js";
        s.async = true;
        s.dataset.primeAiIsland = "1";
      }
      s.addEventListener("load", function () {
        window.PrimeAIMagic ? resolve(window.PrimeAIMagic) : reject(new Error("magic"));
      }, { once: true });
      s.addEventListener("error", function () { if (created) s.remove(); reject(new Error("magic")); }, { once: true });
      if (created) document.head.append(s);
    });
  }

  function mount(host, config) {
    config = config || {};
    var guildId = config.guildId;
    var url = "api/guild/" + guildId + "/announcement-reactions";
    var disposed = false;
    var token = 0;
    var id = "as" + (++uid);
    var st = {
      loading: true, loadError: "", snap: null, ids: [], channel: "", enabled: false,
      saving: false, conflict: null, msg: null, query: "", touched: false,
    };
    var magicHost = null;

    var root = el("section", { class: "announcement-space", dir: "rtl", "aria-labelledby": id + "-t" });
    host.replaceChildren(root);

    var search = el("input", {
      type: "search", id: id + "-q", autocomplete: "off", placeholder: "ابحث باسم الإيموجي",
      oninput: function () { st.query = search.value.trim().toLowerCase(); renderGrid(); },
      onkeydown: function (e) { if (e.key === "Enter") e.preventDefault(); },
    });

    function toast(m, t) { try { config.toast && config.toast(m, t); } catch (_) {} }

    function applySnapshot(data, keepDraft) {
      st.snap = data;
      st.conflict = null;
      if (!keepDraft) {
        var c = data.config || {};
        st.channel = c.channel_id || "";
        st.ids = (c.emoji_ids || []).slice();
        st.enabled = !!c.enabled;
        st.touched = false;
      }
    }
    function dirty() {
      if (!st.snap) return false;
      var c = st.snap.config || {};
      return (c.channel_id || "") !== st.channel || !!c.enabled !== st.enabled ||
        (c.emoji_ids || []).join(",") !== st.ids.join(",");
    }
    function emojiMap() {
      var m = {};
      ((st.snap && st.snap.emojis) || []).forEach(function (e) { m[e.id] = e; });
      return m;
    }
    function usable(e) { return !!(e && e.available && e.usable); }
    function usableCount() { return ((st.snap && st.snap.emojis) || []).filter(usable).length; }
    function validate() {
      if (!st.enabled) return "";
      if (!st.channel) return "اختر قناة قبل التفعيل.";
      var m = emojiMap();
      if (st.ids.length < MIN || st.ids.length > MAX) return "اختر من 4 إلى 5 إيموجي قبل التفعيل.";
      if (st.ids.some(function (i) { return !usable(m[i]); })) return "بعض الإيموجي المحددة غير متاحة حالياً. أزلها أو استبدلها.";
      return "";
    }

    async function load(opts) {
      opts = opts || {};
      var my = ++token;
      st.loading = !st.snap; st.loadError = "";
      render();
      try {
        var r = await config.api(url, { cache: "no-store" });
        if (r.status === 401) throw new Error("unauth");
        var data = null;
        try { data = await r.json(); } catch (_) {}
        if (disposed || my !== token) return;
        if (!r.ok || !data || !data.config) {
          st.loadError = r.status === 403 ? ERR.forbidden : "تعذر تحميل إعدادات المساحة الإعلانية.";
        } else {
          applySnapshot(data, opts.keepDraft && st.touched);
        }
      } catch (e) {
        if (disposed || my !== token) return;
        st.loadError = "تعذر الاتصال بالخادم. تحقق من الاتصال وحاول مجدداً.";
      }
      st.loading = false;
      render();
    }

    async function save() {
      if (st.saving || !st.snap) return;
      var bad = validate();
      if (bad) { st.msg = { kind: "error", text: bad }; render(); return; }
      var my = ++token;
      st.saving = true; st.msg = null; render();
      var body = {
        channel_id: st.channel || null,
        emoji_ids: st.ids.slice(),
        enabled: st.enabled,
        revision: st.snap.config.revision,
      };
      try {
        var r = await config.writeApi(url, body);
        var data = null;
        try { data = await r.json(); } catch (_) {}
        if (disposed || my !== token) return;
        st.saving = false;
        if (r.ok && data && data.config) {
          applySnapshot(data, false);
          st.msg = { kind: "ok", text: "تم حفظ الإعدادات. ستطبق على الرسائل الجديدة فقط." };
          toast("تم حفظ المساحة الإعلانية", "success");
        } else if (r.status === 409) {
          st.conflict = (data && data.config) || {};
          st.msg = null;
        } else {
          var text = (data && data.message) || ERR[data && data.error] ||
            (r.status === 403 ? ERR.forbidden : "تعذر الحفظ. راجع الحقول وحاول مجدداً.");
          st.msg = { kind: "error", text: text };
        }
      } catch (e) {
        if (disposed || my !== token) return;
        st.saving = false;
        st.msg = { kind: "error", text: "تعذر الاتصال بالخادم. لم يتم الحفظ، وما زالت مسودتك محفوظة هنا." };
      }
      render();
    }

    function loadServerState() {
      if (!window.confirm || window.confirm("سيتم استبدال مسودتك بالحالة المحفوظة على الخادم. متابعة؟")) {
        st.touched = false; st.conflict = null; load({ keepDraft: false });
      }
    }

    function toggle(eid) {
      var i = st.ids.indexOf(eid);
      if (i >= 0) st.ids.splice(i, 1);
      else if (st.ids.length < MAX) st.ids.push(eid);
      else { st.msg = { kind: "error", text: "الحد الأقصى 5 إيموجي. أزل واحداً أولاً." }; render(); return; }
      st.touched = true; st.msg = null;
      render();
    }

    function pill(text, kind) { return el("span", { class: "as-pill as-" + kind, text: text }); }

    function emojiImg(e, cls) {
      return el("img", { class: cls || "as-emoji", src: e.url, alt: e.name, loading: "lazy", width: 40, height: 40 });
    }

    function statusBlock() {
      var s = st.snap.status || {};
      var ok = s.code === "ready", stopped = s.code === "disabled";
      var box = el("div", { class: "as-status " + (ok ? "is-ok" : stopped ? "is-stopped" : "is-warn"), role: ok || stopped ? "status" : "alert" });
      box.append(el("strong", { text: ok ? "الجاهزية: سليمة" : stopped ? "النظام متوقف" : "تنبيه الجاهزية" }));
      if (s.message) box.append(el("p", { text: s.message }));
      var miss = s.missing_permissions || [];
      if (miss.length) box.append(el("p", { text: "صلاحيات ناقصة: " + miss.map(function (m) { return PERM[m] || m; }).join("، ") }));
      if ((s.invalid_emoji_ids || []).length) box.append(el("p", { text: "إيموجي محفوظة لم تعد صالحة: " + s.invalid_emoji_ids.length }));
      if (s.worker_ready === false) box.append(el("p", { text: "عامل التفاعلات غير جاهز حالياً." }));
      box.append(el("p", { class: "as-hint", text: "الإيموجيات من هذا السيرفر فقط؛ صلاحية استخدام الإيموجيات الخارجية ليست مطلوبة لهذا الاختيار." }));
      var c = st.snap.config || {};
      if (c.last_error) box.append(el("p", { text: "آخر خطأ: " + c.last_error }));
      box.append(el("button", { class: "btn as-btn", type: "button", onclick: function () { load({ keepDraft: true }); }, text: "تحديث الحالة" }));
      return box;
    }

    function persistedBlock() {
      var c = st.snap.config || {}, m = emojiMap();
      var ch = (st.snap.channels || []).find(function (x) { return x.id === c.channel_id; });
      var row = el("div", { class: "as-chips" });
      (c.emoji_ids || []).forEach(function (i) {
        var e = m[i];
        row.append(e ? el("span", { class: "as-chip" }, emojiImg(e, "as-emoji-sm"), el("span", { text: e.name }))
          : el("span", { class: "as-chip as-muted", text: "غير متاح" }));
      });
      if (!row.children.length) row.append(el("span", { class: "as-muted", text: "لا توجد إيموجي محفوظة" }));
      var rt = st.snap.runtime || {};
      return el("div", { class: "as-card" },
        el("h3", { text: "الحالة المحفوظة" }),
        el("p", null, pill(c.enabled ? "مفعّلة" : "متوقفة", c.enabled ? "on" : "off"), " ",
          el("span", { class: "as-muted", text: "المراجعة " + c.revision })),
        el("p", { text: "القناة: " + (c.channel_id ? (ch ? "#" + ch.name : "قناة غير معروفة") : "غير محددة") }),
        row,
        el("p", { class: "as-muted", text: "قائمة التشغيل: " + (rt.queue_size || 0) + " · المتجاهلة: " + (rt.dropped || 0) }));
    }

    function draftPanel() {
      var snap = st.snap, n = usableCount(), canEnable = n >= MIN;
      var wrap = el("div", { class: "as-card" });
      wrap.append(el("h3", { text: "المسودة (غير محفوظة)" }));
      if (dirty()) wrap.append(pill("تغييرات غير محفوظة", "warn"));

      var sw = el("label", { class: "as-switch" },
        el("input", { type: "checkbox", role: "switch", checked: st.enabled ? "checked" : null,
          disabled: st.saving || (!canEnable && !st.enabled),
          onchange: function (e) { st.enabled = e.target.checked; st.touched = true; st.msg = null; render(); } }),
        el("span", { text: st.enabled ? "التفاعل التلقائي: مفعّل" : "التفاعل التلقائي: متوقف" }));
      if (st.enabled) sw.querySelector("input").checked = true;
      wrap.append(sw);
      if (!canEnable) wrap.append(el("p", { class: "as-hint", text: "لا يمكن التفعيل: يوجد " + n + " إيموجي صالحة فقط في السيرفر، والمطلوب 4 على الأقل. أضف إيموجي مخصصة للسيرفر ثم حدّث." }));

      var sel = el("select", { id: id + "-ch", disabled: st.saving,
        onchange: function (e) { st.channel = e.target.value; st.touched = true; st.msg = null; render(); } });
      sel.append(el("option", { value: "", text: "— بدون قناة —" }));
      (snap.channels || []).forEach(function (c) {
        var o = el("option", { value: c.id, text: "# " + c.name });
        if (c.id === st.channel) o.selected = true;
        sel.append(o);
      });
      if (st.channel && !(snap.channels || []).some(function (c) { return c.id === st.channel; })) {
        var o = el("option", { value: st.channel, text: "قناة غير متاحة" }); o.selected = true; sel.append(o);
      }
      wrap.append(el("div", { class: "as-field" }, el("label", { for: id + "-ch", text: "قناة الإعلانات" }), sel));
      return wrap;
    }

    var gridHost = el("div", { class: "as-grid-host" });
    function renderGrid() {
      gridHost.replaceChildren();
      if (!st.snap) return;
      var list = (st.snap.emojis || []);
      var shown = list.filter(function (e) { return !st.query || e.name.toLowerCase().indexOf(st.query) >= 0; });
      if (!list.length) {
        gridHost.append(el("div", { class: "as-empty" }, el("strong", { text: "لا توجد إيموجي مخصصة في السيرفر" }),
          el("p", { text: "أضف 4 إيموجي مخصصة على الأقل في Discord ثم اضغط تحديث." })));
        return;
      }
      if (!shown.length) { gridHost.append(el("p", { class: "as-muted", text: "لا نتائج مطابقة للبحث." })); return; }
      var grid = el("div", { class: "as-grid", role: "group", "aria-label": "الإيموجي المخصصة" });
      shown.forEach(function (e) {
        var on = st.ids.indexOf(e.id) >= 0, ok = usable(e);
        grid.append(el("button", {
          type: "button", class: "as-emoji-card" + (on ? " is-on" : ""), "aria-pressed": on ? "true" : "false",
          disabled: st.saving || (!ok && !on),
          onclick: function () { toggle(e.id); },
        }, emojiImg(e), el("span", { class: "as-name", text: e.name }),
          el("small", { text: !ok ? "غير متاحة" : on ? "محددة" : e.animated ? "متحركة" : "ثابتة" })));
      });
      gridHost.append(grid);
    }

    function previewBlock() {
      var m = emojiMap();
      var sel = st.ids.map(function (i) { return m[i]; }).filter(Boolean);
      var wrap = el("div", { class: "as-card as-preview" },
        el("h3", { text: "معاينة توضيحية" }),
        el("p", { class: "as-muted", text: "معاينة محلية فقط — لا تُرسل أي رسالة إلى Discord." }));
      var bubble = el("div", { class: "as-bubble" }, el("p", { text: "مثال على رسالة إعلان جديدة" }));
      var rx = el("div", { class: "as-reacts" });
      sel.forEach(function (e) { rx.append(el("span", { class: "as-react" }, emojiImg(e, "as-emoji-sm"), el("b", { text: "1" }))); });
      if (!sel.length) rx.append(el("span", { class: "as-muted", text: "لم تُحدَّد إيموجي" }));
      bubble.append(rx);
      wrap.append(bubble, el("p", { class: "as-muted", text: "تُضاف التفاعلات إلى الرسائل الجديدة فقط ولا يوجد تعبئة للرسائل القديمة." }));
      return wrap;
    }

    function render() {
      if (disposed) return;
      var focusId = document.activeElement && root.contains(document.activeElement) ? document.activeElement.id : "";
      var keep = st.query;
      if (magicHost && window.PrimeAIMagic) { try { window.PrimeAIMagic.dispose(magicHost); } catch (_) {} }
      magicHost = null;
      root.replaceChildren();
      root.setAttribute("aria-busy", st.loading || st.saving ? "true" : "false");
      root.append(el("header", { class: "as-head" },
        el("h2", { id: id + "-t", text: "📢 المساحة الإعلانية" }),
        el("p", { text: "اختر قناة الإعلانات و4–5 إيموجي من إيموجي السيرفر لتُضاف تلقائياً على الإعلانات الجديدة." })));

      if (st.loading) {
        root.append(el("div", { class: "as-skel", role: "status", "aria-label": "جارٍ التحميل" }, el("i"), el("i"), el("i")));
        return;
      }
      if (st.loadError && !st.snap) {
        root.append(el("div", { class: "as-status is-warn", role: "alert" }, el("p", { text: st.loadError }),
          el("button", { class: "btn as-btn", type: "button", onclick: function () { load(); }, text: "إعادة المحاولة" })));
        return;
      }
      if (st.loadError) root.append(el("div", { class: "as-status is-warn", role: "alert" }, el("p", { text: st.loadError + " مسودتك ما زالت محفوظة." })));

      var n = usableCount(), sel = st.ids.length;
      magicHost = el("div", { class: "as-metrics" });
      var fb = el("div", { class: "as-metric-fallback" },
        el("span", { text: "المحدد: " + sel + " من " + MAX }),
        el("span", { text: "الصالح في السيرفر: " + n }));
      magicHost.append(fb);
      root.append(magicHost);

      if (st.conflict) {
        root.append(el("div", { class: "as-status is-warn", role: "alert" },
          el("strong", { text: "تعارض في الحفظ" }),
          el("p", { text: "تم تغيير الإعدادات من مكان آخر (المراجعة " + (st.conflict.revision !== undefined ? st.conflict.revision : "؟") + "). مسودتك لم تُفقد ولم يتم الكتابة فوق أي شيء." }),
          el("button", { class: "btn as-btn", type: "button", onclick: loadServerState, text: "تحميل حالة الخادم (يستبدل المسودة)" })));
      }
      root.append(statusBlock());
      var cols = el("div", { class: "as-cols" }, persistedBlock(), draftPanel());
      root.append(cols);

      var pick = el("div", { class: "as-card" },
        el("h3", { text: "إيموجي السيرفر (" + sel + "/" + MAX + ")" }),
        el("div", { class: "as-field" }, el("label", { for: id + "-q", text: "بحث" }), search));
      search.value = keep;
      pick.append(gridHost);
      renderGrid();
      root.append(pick, previewBlock());

      var err = validate();
      var actions = el("div", { class: "as-actions" });
      if (st.msg) actions.append(el("p", { class: "as-msg as-" + st.msg.kind, role: st.msg.kind === "error" ? "alert" : "status", text: st.msg.text }));
      else if (err) actions.append(el("p", { class: "as-msg as-error", text: err }));
      actions.append(
        el("button", { class: "btn primary as-btn", type: "button", disabled: st.saving || !dirty(), onclick: save,
          text: st.saving ? "جارٍ الحفظ…" : "حفظ التغييرات" }),
        el("button", { class: "btn as-btn", type: "button", disabled: st.saving || !dirty(),
          onclick: function () { applySnapshot(st.snap, false); st.msg = null; render(); }, text: "تجاهل المسودة" }));
      root.append(actions);

      if (focusId) { var f = document.getElementById(focusId); if (f) f.focus({ preventScroll: true }); }

      var host2 = magicHost;
      loadMagic().then(function (bridge) {
        if (disposed || magicHost !== host2 || !host2.isConnected) return;
        bridge.mount(host2, { active: true, stats: [
          { id: "as-selected-" + guildId, label: "المحدد من 5", value: sel },
          { id: "as-usable-" + guildId, label: "إيموجي صالحة", value: n },
        ] });
        fb.hidden = true;
      }).catch(function () {});
    }

    load();

    return function cleanup() {
      disposed = true; token++;
      if (magicHost && window.PrimeAIMagic) { try { window.PrimeAIMagic.dispose(magicHost); } catch (_) {} }
      magicHost = null;
      root.remove();
    };
  }

  window.PrimeAnnouncements = { mount: mount, version: 1 };
})();
