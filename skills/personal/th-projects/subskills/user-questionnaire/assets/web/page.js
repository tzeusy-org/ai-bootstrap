// Owner questionnaire review page. The server renders all content; this
// script paints answer state and records changes.
//
// State model: `confirmed` is the last answers object the server
// acknowledged. `pending` overlays answers sent but not yet acknowledged, so
// the page responds instantly; a failed save drops its overlay (the page
// falls back to what the server holds) and keeps a visible error with a
// retry until a later save of that item succeeds. Typed note/edit text lives
// in `texts`, so it survives failed saves and switching answer kinds.
"use strict";

(() => {
  const boot = JSON.parse(document.getElementById("q-boot").textContent);
  const items = boot.items;
  let confirmed = boot.answers;
  const pending = new Map(); // id -> { seq, answer | null }
  const failed = new Map(); // id -> { message, next }
  const texts = new Map(); // id -> { note, edit }
  const typing = new Map(); // id -> debounce timer
  let seq = 0;
  let current = -1; // index of the card in the reading band, or -1

  const NOTE_LABEL = {
    pick: "Note (optional)",
    hold: "What would change your answer? (re-ask trigger)",
    reject: "Why reject every option?",
  };
  const TALLY = [
    ["pick", "chosen"],
    ["edit", "edited"],
    ["hold", "held"],
    ["reject", "rejected"],
  ];
  const ANCHOR_Y = 80; // matches html { scroll-padding-top }
  const coarse = matchMedia("(pointer: coarse)"); // touch screen as the main pointer
  const REQUEST_MS = 15000; // a request still unanswered by then counts as lost

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const card = (id) => document.getElementById("item-" + id);
  const byData = (attr, id) => $$(`[${attr}]`).filter((el) => el.getAttribute(attr) === id);
  const recordedAt = (id) => confirmed.items[id]?.recorded_at ?? null;
  const answerOf = (id) => (pending.has(id) ? pending.get(id).answer : confirmed.items[id]) || null;
  const isDone = () => Boolean(confirmed.completed_at);
  const isReadonly = () => !boot.live || isDone();
  const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  // Adopt the server's note/edit text for items the owner is not working on
  // here, so another tab's saves show up. A cleared answer keeps the local
  // text, so re-picking restores it.
  function refreshTexts() {
    for (const item of items) {
      const text = texts.get(item.id) || { note: "", edit: "" };
      texts.set(item.id, text);
      const answer = confirmed.items[item.id];
      const busy = typing.has(item.id) || pending.has(item.id) || card(item.id).contains(document.activeElement);
      if (!answer || busy) continue;
      text.note = answer.note || "";
      if (answer.kind === "edit") text.edit = answer.choice;
    }
  }
  refreshTexts();

  // ── persistence ────────────────────────────────────────────────────────

  // Every request goes through one queue, so replies apply in the order the
  // requests were made and an older state never lands over a newer one. A
  // timeout keeps one stalled request from holding up every later save.
  let queue = Promise.resolve();

  function send(path, init) {
    const run = async () => {
      // fetch rejects only when the request never got an answer; browsers
      // word that differently ("Failed to fetch", "Load failed"), so name it.
      const signal = AbortSignal.timeout?.(REQUEST_MS);
      const response = await fetch(boot.api + path, { ...init, signal }).catch(() => {
        throw new Error("no connection");
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      return data;
    };
    const result = queue.then(run, run);
    queue = result.catch(() => {});
    return result;
  }

  function post(path, body) {
    return send(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true, // lets the save that runs when the tab is hidden finish
    });
  }

  function accept(data) {
    confirmed = data.answers;
    refreshTexts();
    $("#revised").hidden = data.packet_sha256 === boot.packetSha256;
  }

  function resync() {
    return send("/state", { cache: "no-store" })
      .then(accept)
      .catch(() => {}) // offline: keep the last confirmed state; failed items show their error
      .then(paintAll);
  }

  // A phone drops its connection when it sleeps or changes network. Once the
  // connection or the page is back, pick up what the server holds, then
  // resend a failed save only where the answer it meant to replace is still
  // there: a newer answer from another device wins, and the page shows it.
  function catchUp() {
    if (!boot.live) return;
    resync().then(() => {
      for (const [id, failure] of [...failed]) {
        if (recordedAt(id) === failure.base) setAnswer(id, failure.next);
        else failed.delete(id);
      }
      paintAll();
    });
  }

  function setAnswer(id, next) {
    if (isReadonly()) return;
    const mine = ++seq;
    pending.set(id, { seq: mine, answer: next ? { ...next, recorded_at: null } : null });
    failed.delete(id);
    paintItem(id);
    paintSummary();
    const settle = () => {
      if (pending.has(id) && pending.get(id).seq === mine) pending.delete(id);
    };
    post("/answer", { id, ...(next || { kind: null }) })
      .then((data) => {
        settle();
        accept(data);
        paintItem(id);
        paintSummary();
      })
      .catch((error) => {
        settle();
        // base: the server's answer as this page last knew it, which the
        // save was meant to replace (replies apply in order, so it is current).
        if (!pending.has(id)) failed.set(id, { message: error.message, next, base: recordedAt(id) });
        resync();
      });
  }

  function complete(body, statusText) {
    flushAllText();
    const status = $("#final-status");
    status.textContent = statusText;
    status.className = "save";
    return post("/complete", body)
      .then((data) => {
        accept(data);
        status.textContent = "";
        paintAll();
      })
      .catch((error) => {
        status.textContent = error.message;
        status.className = "save is-err";
        resync();
      });
  }

  // ── painting ───────────────────────────────────────────────────────────

  function statusText(item, answer) {
    if (!answer) return "Unanswered";
    const text = boot.statusText[answer.kind].replace("{choice}", answer.choice);
    const offRecommendation = answer.kind === "pick" && item.recommended && answer.choice !== item.recommended;
    return offRecommendation ? `${text} (rec. ${item.recommended})` : text;
  }

  function syncField(textarea, value) {
    if (document.activeElement !== textarea) textarea.value = value;
  }

  function paintSave(id, el, answer) {
    const save = $(".save", el);
    const retry = $(".retry", el);
    const failure = failed.get(id);
    retry.hidden = !failure;
    if (failure) {
      save.textContent = `Not saved: ${failure.message}`;
      save.className = "save is-err";
    } else if (pending.has(id)) {
      save.textContent = "Saving…";
      save.className = "save";
    } else if (typing.has(id)) {
      save.textContent = "Unsaved changes…";
      save.className = "save";
    } else if (answer && answer.recorded_at) {
      save.textContent = `Saved ${clock(answer.recorded_at)}`;
      save.className = "save is-ok";
    } else {
      save.textContent = "";
      save.className = "save";
    }
  }

  function paintItem(id) {
    const el = card(id);
    const answer = answerOf(id);
    const kind = answer ? answer.kind : null;
    const text = texts.get(id);

    for (const opt of $$(".opt", el)) {
      opt.setAttribute("aria-pressed", String(kind === "pick" && answer.choice === opt.dataset.label));
    }
    for (const button of $$("[data-kind]", el)) {
      button.setAttribute("aria-pressed", String(kind === button.dataset.kind));
    }

    const edit = $(".fld-edit", el);
    edit.hidden = kind !== "edit";
    syncField($("textarea", edit), text.edit);

    const note = $(".fld-note", el);
    note.hidden = !kind || kind === "edit" || (isReadonly() && !text.note);
    if (kind && kind !== "edit") $("label", note).textContent = NOTE_LABEL[kind];
    syncField($("textarea", note), text.note);

    paintSave(id, el, answer);

    const item = items.find((candidate) => candidate.id === id);
    const kindClass = kind ? "k-" + kind : "";
    for (const pill of byData("data-status-for", id)) {
      pill.textContent = statusText(item, answer);
      pill.className = "pill " + kindClass;
    }
    for (const dot of byData("data-dot-for", id)) dot.className = "dot " + kindClass;
    for (const segment of byData("data-seg-for", id)) segment.className = kindClass;
  }

  function paintSummary() {
    const answered = items.filter((item) => answerOf(item.id));
    const unanswered = items.filter((item) => !answerOf(item.id));
    const emptyEdits = answered.filter((item) => answerOf(item.id).kind === "edit" && !answerOf(item.id).choice);
    $("#progress-text").textContent = `${answered.length} of ${items.length} answered`;

    $("#tally").replaceChildren(
      ...TALLY.map(([kind, label]) => {
        const n = answered.filter((item) => answerOf(item.id).kind === kind).length;
        const li = document.createElement("li");
        li.className = "pill" + (n ? " k-" + kind : "");
        li.textContent = `${n} ${label}`;
        return li;
      }),
    );

    const todo = $("#todo");
    todo.replaceChildren();
    const missing = unanswered.length ? unanswered : emptyEdits;
    if (missing.length) {
      const head = document.createElement("p");
      head.className = "todo-h";
      head.textContent = unanswered.length ? "Still to answer" : "Describe your edit for";
      const list = document.createElement("ol");
      list.className = "todo-list";
      for (const item of missing) {
        const link = document.createElement("a");
        link.href = "#item-" + item.id;
        const num = document.createElement("span");
        num.className = "ov-num";
        num.textContent = items.indexOf(item) + 1;
        const title = document.createElement("span");
        title.textContent = item.title;
        link.append(num, title);
        const li = document.createElement("li");
        li.append(link);
        list.append(li);
      }
      todo.append(head, list);
    }

    const next = $("#next-btn");
    next.hidden = isDone();
    next.textContent = unanswered.length ? "Next ↓" : "Submit ↓";
    next.setAttribute("aria-label", unanswered.length ? "Next unanswered decision" : "Go to submit");

    for (const radio of $$('input[name="apply"]')) {
      radio.checked = typeof confirmed.apply_now === "boolean" && String(confirmed.apply_now) === radio.value;
    }
    const applyChosen = typeof confirmed.apply_now === "boolean";
    // Saves in flight do not block submit: the request queue sends the
    // submit after them. Only a failed save does.
    const done = isDone();
    const submit = $("#submit");
    submit.disabled = !boot.live || done || missing.length > 0 || !applyChosen || failed.size > 0;
    submit.textContent = done ? "Submitted" : "Submit review";
    $("#reopen").hidden = !done || !boot.live;
    $("#final").classList.toggle("is-done", done);

    const status = $("#final-status");
    if (done) {
      status.textContent = `Submitted ${clock(confirmed.completed_at)}. You can close this page.`;
      status.className = "save is-ok";
    } else if (failed.size) {
      status.textContent = "Some answers did not save; retry them first.";
      status.className = "save is-err";
    } else if (!missing.length && !applyChosen) {
      status.textContent = "Choose whether to apply, then submit.";
      status.className = "save";
    } else if (!status.classList.contains("is-err")) {
      status.textContent = "";
    }

    const readonly = isReadonly();
    document.body.classList.toggle("is-readonly", readonly);
    for (const control of $$(".opt, .chip-btn[data-kind], .answer textarea, input[name='apply']")) {
      control.disabled = readonly;
    }
  }

  function paintAll() {
    items.forEach((item) => paintItem(item.id));
    paintSummary();
  }

  function paintCurrent() {
    items.forEach((item, index) => {
      card(item.id).classList.toggle("is-current", index === current);
      for (const link of byData("data-nav-for", item.id)) {
        if (index === current) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
      }
    });
  }

  // ── actions ────────────────────────────────────────────────────────────

  function fieldFor(id, kind) {
    return $(kind === "edit" ? ".fld-edit textarea" : ".fld-note textarea", card(id));
  }

  function choose(id, label) {
    const answer = answerOf(id);
    if (answer && answer.kind === "pick" && answer.choice === label) return setAnswer(id, null);
    setAnswer(id, { kind: "pick", choice: label, note: texts.get(id).note });
  }

  function toggleKind(id, kind) {
    const answer = answerOf(id);
    if (answer && answer.kind === kind) return setAnswer(id, null);
    const text = texts.get(id);
    setAnswer(id, { kind, choice: kind === "edit" ? text.edit : "", note: text.note });
    // The field is visible now (optimistic paint); focus it before any reply
    // arrives, so typing never lands in another item's field. On touch,
    // focus opens the on-screen keyboard, so only the edit text (required)
    // takes it; hold and reject notes are optional and wait for a tap.
    if (kind === "edit" || !coarse.matches) fieldFor(id, kind).focus({ preventScroll: true });
  }

  function flushText(id) {
    if (!typing.has(id)) return;
    clearTimeout(typing.get(id));
    typing.delete(id);
    const answer = answerOf(id);
    if (!answer) return;
    const text = texts.get(id);
    setAnswer(id, {
      kind: answer.kind,
      choice: answer.kind === "edit" ? text.edit.trim() : answer.choice,
      note: text.note.trim(),
    });
  }

  function flushAllText() {
    [...typing.keys()].forEach(flushText);
  }

  // Jumps are instant, so the target is in place at once: it becomes current
  // now rather than when the reading band reports it, and a quick second
  // tap continues from it. Focus follows (the heading, so no on-screen
  // keyboard opens) and screen readers continue where the page now is.
  function scrollToCard(index) {
    const el = card(items[index].id);
    el.scrollIntoView({ block: "start", behavior: "instant" });
    $("h2", el).focus({ preventScroll: true });
    current = index;
    paintCurrent();
  }

  function scrollToFinal() {
    $("#final").scrollIntoView({ block: "start", behavior: "instant" });
    $("#final-h").focus({ preventScroll: true });
    current = -1;
    paintCurrent();
  }

  // Next/previous card: from the current card when there is one, else
  // relative to the scroll anchor (e.g. from the submit section).
  function step(direction) {
    let index;
    if (current >= 0) {
      index = current + direction;
    } else {
      const tops = items.map((item) => card(item.id).getBoundingClientRect().top);
      index = direction > 0 ? tops.findIndex((top) => top > ANCHOR_Y + 8) : tops.findLastIndex((top) => top < ANCHOR_Y - 8);
    }
    if (index >= 0 && index < items.length) scrollToCard(index);
  }

  // Index of the card being read: the current one, else the last card whose
  // top has scrolled past the anchor (-1 above the first card).
  function position() {
    if (current >= 0) return current;
    return items.findLastIndex((item) => card(item.id).getBoundingClientRect().top <= ANCHOR_Y + 8);
  }

  // The first unanswered card after the one being read, wrapping to the top;
  // the submit section once every card is answered.
  function nextUnanswered() {
    const open = items.map((item, index) => index).filter((index) => !answerOf(items[index].id));
    if (!open.length) return scrollToFinal();
    const here = position();
    scrollToCard(open.find((index) => index > here) ?? open[0]);
  }

  // ── events ─────────────────────────────────────────────────────────────

  // Safari does not focus a tapped or clicked button, so a note still focused
  // elsewhere would keep the keyboard and the typing. Let it go (it saves).
  function releaseField() {
    if (document.activeElement.matches("textarea")) document.activeElement.blur();
  }

  document.addEventListener("click", (event) => {
    const opt = event.target.closest(".opt");
    const kindButton = event.target.closest("[data-kind]");
    if (opt || kindButton) releaseField();
    if (opt) return choose(opt.closest(".item").dataset.id, opt.dataset.label);
    if (kindButton) return toggleKind(kindButton.closest(".item").dataset.id, kindButton.dataset.kind);
    const retry = event.target.closest(".retry");
    if (retry) {
      const id = retry.closest(".item").dataset.id;
      return setAnswer(id, failed.get(id).next);
    }
    if (event.target.id === "submit") return complete({ completed: true }, "Submitting…");
    if (event.target.id === "reopen") return complete({ completed: false }, "Reopening…");
    if (event.target.id === "help-btn") return $("#help").showModal();
    if (event.target.id === "next-btn") return nextUnanswered();
  });

  document.addEventListener("change", (event) => {
    if (event.target.name === "apply") complete({ apply_now: event.target.value === "true" }, "Saving…");
  });

  document.addEventListener("input", (event) => {
    const textarea = event.target.closest(".answer textarea");
    if (!textarea) return;
    const id = textarea.closest(".item").dataset.id;
    texts.get(id)[textarea.closest(".fld-edit") ? "edit" : "note"] = textarea.value;
    clearTimeout(typing.get(id));
    typing.set(id, setTimeout(() => flushText(id), 600));
    paintSave(id, card(id), answerOf(id));
  });

  document.addEventListener("focusout", (event) => {
    const textarea = event.target.closest(".answer textarea");
    if (textarea) flushText(textarea.closest(".item").dataset.id);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushAllText();
    else catchUp();
  });
  window.addEventListener("online", catchUp);

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey || $("#help").open) return;
    if (event.target.closest("textarea, input, select, [contenteditable]")) return;
    const key = event.key;
    const item = items[current]; // only the card in the reading band takes answer keys
    if (key === "j") step(1);
    else if (key === "k") step(-1);
    else if (key === "s") scrollToFinal();
    else if (key === "?") $("#help").showModal();
    else if (!item) return;
    else if (/^[a-d]$/.test(key) && item.options.includes(key.toUpperCase())) choose(item.id, key.toUpperCase());
    else if (key === "e") toggleKind(item.id, "edit");
    else if (key === "h") toggleKind(item.id, "hold");
    else if (key === "r") toggleKind(item.id, "reject");
    else if (key === "n" && answerOf(item.id)) fieldFor(item.id, answerOf(item.id).kind).focus();
    else return;
    event.preventDefault();
  });

  // The reading band is a strip 30-40% down the viewport. The card crossing
  // it is current; when the submit section reaches it, no card is.
  const inBand = new Set();
  const final = $("#final");
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const index = entry.target === final ? "final" : items.findIndex((item) => card(item.id) === entry.target);
        if (entry.isIntersecting) inBand.add(index);
        else inBand.delete(index);
      }
      const cards = [...inBand].filter((index) => index !== "final");
      current = inBand.has("final") || !cards.length ? -1 : Math.min(...cards);
      paintCurrent();
    },
    { rootMargin: "-30% 0px -60% 0px" },
  );
  items.forEach((item) => observer.observe(card(item.id)));
  observer.observe(final);

  paintAll();
})();
