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
    send_messages: "إرسال الرسائل",
    attach_files: "إرفاق الملفات",
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
      second: "", lineOn: false, lineCh: [], imageId: "", image: null, localUrl: "", uploading: false, upMsg: null,
    };
    function revokeLocal() { if (st.localUrl) { try { URL.revokeObjectURL(st.localUrl); } catch (_) {} st.localUrl = ""; } }
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
      var stale = keepDraft && st.snap && data.config && st.snap.config &&
        data.config.revision !== st.snap.config.revision;
      if (stale) {
        // Keep the old base revision so the draft is never silently rebased.
        st.snap = Object.assign({}, data, { config: st.snap.config, line_image: st.snap.line_image });
        st.conflict = data.config;
      } else { st.snap = data; st.conflict = null; }
      if (!keepDraft) {
        revokeLocal(); st.upMsg = null;
        var c = data.config || {};
        st.second = c.second_channel_id || "";
        st.lineOn = !!c.line_enabled;
        st.lineCh = (c.line_channel_ids || []).slice(0, 2);
        st.imageId = c.line_image_id || "";
        st.image = data.line_image || null;
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
        (c.second_channel_id || "") !== st.second || !!c.line_enabled !== st.lineOn ||
        (c.line_channel_ids || []).join(",") !== st.lineCh.join(",") || (c.line_image_id || "") !== st.imageId ||
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
      if (st.second && !st.channel) return "اختر القناة الأولى قبل إضافة قناة تفاعلات ثانية.";
      if (st.second && st.second === st.channel) return "القناة الثانية يجب أن تختلف عن القناة الأولى.";
      if (st.lineOn) {
        if (st.lineCh.length < 1 || st.lineCh.length > 2) return "اختر قناة أو قناتين للاوتو لاين قبل التفعيل.";
        if (st.lineCh.length === 2 && st.lineCh[0] === st.lineCh[1]) return "قناتا اللاين يجب أن تكونا مختلفتين.";
        if (!st.imageId) return "ارفع صورة الفاصل قبل تفعيل الاوتو لاين.";
      }
      if (!st.enabled) return "";
      if (!st.channel) return "اختر قناة قبل التفعيل.";
      var m = emojiMap();
      if (st.ids.length < MIN || st.ids.length > MAX) return "اختر من 4 إلى 5 إيموجي قبل التفعيل.";
      if (st.ids.some(function (i) { return !usable(m[i]); })) return "بعض الإيموجي المحددة غير متاحة حالياً. أزلها أو استبدلها.";
      return "";
    }

    async function load(opts) {
      if (st.saving || st.uploading) return;
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
      if (st.saving || st.uploading || !st.snap) return;
      var bad = validate();
      if (bad) { st.msg = { kind: "error", text: bad }; render(); return; }
      var my = ++token;
      st.saving = true; st.msg = null; render();
      var body = {
        channel_id: st.channel || null,
        emoji_ids: st.ids.slice(),
        enabled: st.enabled,
        second_channel_id: st.second || null,
        line_enabled: st.lineOn,
        line_channel_ids: st.lineCh.slice(),
        line_image_id: st.imageId || null,
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

    async function upload(file) {
      if (!file || st.uploading || st.saving) return;
      var okType = ["image/png", "image/jpeg", "image/gif"].indexOf(file.type) >= 0;
      if (!okType) { st.upMsg = { kind: "error", text: "الصيغ المسموحة: PNG أو JPG أو GIF فقط." }; render(); return; }
      if (file.size > 2 * 1024 * 1024) { st.upMsg = { kind: "error", text: "حجم الصورة يتجاوز 2 ميغابايت." }; render(); return; }
      var my = ++token;
      st.uploading = true; st.upMsg = null; render();
      function send() {
        return config.api(url + "/image", { method: "POST", cache: "no-store",
          headers: { "Content-Type": file.type || "application/octet-stream", "X-CSRF-Token": config.getCsrf() }, body: file });
      }
      try {
        var r = await send();
        if (r.status === 403 && config.refreshSession) {
          var d0 = null; try { d0 = await r.clone().json(); } catch (_) {}
          if (!d0 || /csrf/i.test(String(d0.error || ""))) { await config.refreshSession(); r = await send(); }
        }
        var data = null; try { data = await r.json(); } catch (_) {}
        if (disposed || my !== token) return;
        st.uploading = false;
        if (r.ok && data && data.image && data.image.id) {
          revokeLocal();
          try { st.localUrl = URL.createObjectURL(file); } catch (_) {}
          st.image = data.image; st.imageId = data.image.id; st.touched = true;
          st.upMsg = { kind: "ok", text: "تم رفع الصورة كمسودة. اضغط حفظ التغييرات لتفعيلها." };
        } else {
          st.upMsg = { kind: "error", text: (data && data.message) || ERR[data && data.error] ||
            (r.status === 403 ? ERR.forbidden : "تعذر رفع الصورة. تم الاحتفاظ بالاختيار السابق.") };
        }
      } catch (e) {
        if (disposed || my !== token) return;
        st.uploading = false;
        st.upMsg = { kind: "error", text: "تعذر الاتصال أثناء الرفع. تم الاحتفاظ بالاختيار السابق." };
      }
      render();
    }

    function chanSelect(sid, label, value, optional, onpick) {
      var snap = st.snap, sel = el("select", { id: id + "-" + sid, disabled: st.saving,
        onchange: function (e) { onpick(e.target.value); st.touched = true; st.msg = null; render(); } });
      sel.append(el("option", { value: "", text: optional ? "— بدون —" : "— اختر قناة —" }));
      (snap.channels || []).forEach(function (c) {
        var o = el("option", { value: c.id, text: "# " + c.name }); if (c.id === value) o.selected = true; sel.append(o);
      });
      if (value && !(snap.channels || []).some(function (c) { return c.id === value; })) {
        var o2 = el("option", { value: value, text: "قناة غير متاحة" }); o2.selected = true; sel.append(o2);
      }
      return el("div", { class: "as-field" }, el("label", { for: id + "-" + sid, text: label }), sel);
    }

    function chanStatus(list, title) {
      if (!list || !list.length) return null;
      var names = {}; ((st.snap.channels) || []).forEach(function (c) { names[c.id] = c.name; });
      var box = el("ul", { class: "as-chstat", "aria-label": title });
      list.forEach(function (x) {
        var good = x.code === "ready", stopped = x.code === "disabled";
        var miss = (x.missing_permissions || []).map(function (m) { return PERM[m] || m; }).join("، ");
        box.append(el("li", { class: good ? "is-ok" : stopped ? "is-stopped" : "is-bad" },
          el("b", { text: "# " + (names[x.channel_id] || "قناة") }),
          el("span", { text: " " + (x.message || (good ? "جاهزة" : x.code || "")) }),
          miss ? el("small", { text: " — ناقص: " + miss }) : null));
      });
      return box;
    }

    function linePanel() {
      var wrap = el("div", { class: "as-card as-line" });
      wrap.append(el("h3", { text: "الاوتو لاين (فاصل بالصورة)" }),
        el("p", { class: "as-muted", text: "بعد كل رسالة نصية جديدة مؤهلة ترسل صورة الفاصل تلقائياً في القنوات المختارة. مستقل عن التفاعلات." }));
      wrap.append(el("label", { class: "as-switch" },
        el("input", { type: "checkbox", role: "switch", id: id + "-lon", checked: st.lineOn ? "checked" : null, disabled: st.saving || st.uploading,
          onchange: function (e) { st.lineOn = e.target.checked; st.touched = true; st.msg = null; render(); } }),
        el("span", { text: st.lineOn ? "الاوتو لاين: مفعّل" : "الاوتو لاين: متوقف" })));
      if (st.lineOn) { var sw = wrap.querySelector("input"); sw.checked = true; }
      wrap.append(chanSelect("l1", "قناة اللاين الأولى", st.lineCh[0] || "", false, function (v) {
        st.lineCh = v ? [v].concat(st.lineCh.slice(1, 2)) : st.lineCh.slice(1, 2);
      }));
      wrap.append(chanSelect("l2", "قناة اللاين الثانية (اختيارية)", st.lineCh[1] || "", true, function (v) {
        st.lineCh = v ? [st.lineCh[0] || "", v].filter(Boolean) : st.lineCh.slice(0, 1);
      }));
      var img = st.image, src = st.localUrl || (img && img.url) || "";
      var prev = el("div", { class: "as-line-prev" });
      if (src) prev.append(el("img", { src: src, alt: "معاينة صورة الفاصل", class: "as-line-img" }));
      else prev.append(el("span", { class: "as-muted", text: "لا توجد صورة محددة" }));
      wrap.append(prev);
      if (img) wrap.append(el("p", { class: "as-muted", text: img.mime + " · " + img.width + "×" + img.height + " · " + Math.ceil(img.size / 1024) + " ك.ب" +
        (st.imageId && st.snap.config.line_image_id !== st.imageId ? " · مسودة غير محفوظة" : "") }));
      var fi = el("input", { type: "file", id: id + "-file", accept: "image/png,image/jpeg,image/gif", hidden: true,
        onchange: function (e) { var f = e.target.files && e.target.files[0]; e.target.value = ""; upload(f); } });
      wrap.append(fi, el("div", { class: "as-row" },
        el("button", { class: "btn as-btn", type: "button", id: id + "-up", disabled: st.saving || st.uploading,
          onclick: function () { fi.click(); }, text: st.uploading ? "جارٍ الرفع…" : "رفع صورة (PNG/JPG/GIF، حتى 2MB)" }),
        el("button", { class: "btn as-btn", type: "button", disabled: st.saving || st.uploading || !st.imageId,
          onclick: function () { revokeLocal(); st.imageId = ""; st.image = null; st.touched = true; st.upMsg = null; render(); }, text: "إزالة من المسودة" })));
      if (st.upMsg) wrap.append(el("p", { class: "as-msg as-" + st.upMsg.kind, role: st.upMsg.kind === "error" ? "alert" : "status", text: st.upMsg.text }));
      var ls = st.snap.line_status;
      if (ls) {
        if (ls.message) wrap.append(el("p", { class: "as-muted", text: "حالة اللاين: " + ls.message }));
        if ((ls.missing_permissions || []).length) wrap.append(el("p", { class: "as-muted", text: "صلاحيات ناقصة: " + ls.missing_permissions.map(function (m) { return PERM[m] || m; }).join("، ") }));
        var cs = chanStatus(ls.channels, "حالة قنوات اللاين"); if (cs) wrap.append(cs);
      }
      wrap.append(el("p", { class: "as-hint", text: "الصلاحيات المطلوبة للاوتو لاين: إرسال الرسائل وإرفاق الملفات. الصورة الجديدة لا تُفعَّل إلا بعد الحفظ، وإزالتها تتطلب إيقاف اللاين أو رفع صورة بديلة." }));
      return wrap;
    }

    function rulesBlock() {
      return el("div", { class: "as-card" },
        el("h3", { text: "قواعد الرسائل المؤهلة" }),
        el("ul", { class: "as-rules" },
          el("li", { text: "نص فقط وغير فارغ: أي رسالة تحتوي ملفاً أو صورة أو ملصقاً أو Embed فقط أو رسالة نظام تُتجاهل، حتى مع وجود نص مرافق." }),
          el("li", { text: "رسائل البوت الخاصة بالفاصل تُتجاهل ولا تُنتج فاصلاً جديداً." }),
          el("li", { text: "الرسائل الجديدة فقط، بدون إعادة تشغيل على السجل القديم." }),
          el("li", { text: "قناة التفاعلات الثانية اختيارية وتستخدم نفس الإيموجي." })));
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
      var rc = chanStatus(s.channels, "حالة قنوات التفاعل"); if (rc) box.append(rc);
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
      function nm(i) { var x = (st.snap.channels || []).find(function (y) { return y.id === i; }); return x ? "#" + x.name : "قناة غير معروفة"; }
      return el("div", { class: "as-card" },
        el("h3", { text: "الحالة المحفوظة" }),
        el("p", null, pill(c.enabled ? "مفعّلة" : "متوقفة", c.enabled ? "on" : "off"), " ",
          el("span", { class: "as-muted", text: "المراجعة " + c.revision })),
        el("p", { text: "القناة: " + (c.channel_id ? (ch ? "#" + ch.name : "قناة غير معروفة") : "غير محددة") }),
        el("p", { text: "القناة الثانية: " + (c.second_channel_id ? nm(c.second_channel_id) : "غير محددة") }),
        el("p", null, pill(c.line_enabled ? "لاين مفعّل" : "لاين متوقف", c.line_enabled ? "on" : "off"), " ",
          el("span", { class: "as-muted", text: (c.line_channel_ids || []).map(nm).join("، ") })),
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
      wrap.append(chanSelect("ch2", "قناة ثانية (اختيارية، نفس التفاعلات)", st.second, true, function (v) { st.second = v; }));
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
          type: "button", id: id + "-emoji-" + e.id,
          class: "as-emoji-card" + (on ? " is-on" : ""), "aria-pressed": on ? "true" : "false",
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
      var gridScroll = gridHost.scrollTop;
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
      root.append(cols, linePanel(), rulesBlock());

      var pick = el("div", { class: "as-card" },
        el("h3", { text: "إيموجي السيرفر (" + sel + "/" + MAX + ")" }),
        el("div", { class: "as-field" }, el("label", { for: id + "-q", text: "بحث" }), search));
      search.value = keep;
      pick.append(gridHost);
      renderGrid();
      root.append(pick, previewBlock());
      gridHost.scrollTop = gridScroll;

      var err = validate();
      var actions = el("div", { class: "as-actions" });
      if (st.msg) actions.append(el("p", { class: "as-msg as-" + st.msg.kind, role: st.msg.kind === "error" ? "alert" : "status", text: st.msg.text }));
      else if (err) actions.append(el("p", { class: "as-msg as-error", text: err }));
      actions.append(
        el("button", { class: "btn primary as-btn", type: "button", disabled: st.saving || st.uploading || !dirty(), onclick: save,
          text: st.saving ? "جارٍ الحفظ…" : "حفظ التغييرات" }),
        el("button", { class: "btn as-btn", type: "button", disabled: st.saving || st.uploading || !dirty(),
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
      disposed = true; token++; revokeLocal();
      if (magicHost && window.PrimeAIMagic) { try { window.PrimeAIMagic.dispose(magicHost); } catch (_) {} }
      magicHost = null;
      root.remove();
    };
  }

  window.PrimeAnnouncements = { mount: mount, version: 1 };
})();
