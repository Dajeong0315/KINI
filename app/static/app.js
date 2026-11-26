/* Tablet screens. Network requests and writes live in api_client.js. */
(() => {
  const api = KINIAPI.createClient(),
    s = api.state,
    $ = (q, r = document) => r.querySelector(q);
  const views = [...document.querySelectorAll("[data-view]")];
  const intakeDraft = {};
  const screenNames = { home: "홈", inventory: "식재료", recommend: "메뉴 추천", report: "영양 관리", profile: "가족 관리" };
  const inventoryView = { query: "", filter: "all", page: 1 };
  let fieldId = 0, busy = false,
    errorMessage = "",
    errorStatus = 0,
    current = "home",
    lastAnswer = "",
    inventoryMessage = "",
    familyMessage = "",
    reportMember = "all",
    recommendationOptionsOpen = false,
    profileOptionsOpen = false,
    dialogTrigger = null,
    existingOutflows = "";
  const conditions = {
    meal_type: "dinner",
    cook_time_limit_minutes: 30,
    purchase_allowed: false,
    excluded_ingredients: [],
  };
  const preferenceLabels = {
    protein_management: "단백질 관리",
    weight_management: "체중 관리",
    inventory_efficiency: "재고 우선",
    nutrition_balance: "영양 균형",
    child_custom_diet: "어린이 식사",
    senior_healthy_diet: "어르신 식사",
    sodium_management: "나트륨 관리",
  };
  const messages = {
    no_safe_recommendation:
      "조건을 만족하는 추천이 없습니다. 참여자와 조건을 확인해 주세요.",
    privacy_blocked:
      "개인정보 보호로 추천을 중단했습니다. 입력 조건을 확인해 주세요.",
    api_error:
      "AI 연결 오류로 추천을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
    stale_unvalidated: "기준 데이터가 변경되었습니다. 다시 추천해 주세요.",
    recommendations_available:
      "서버가 반환한 후보입니다. 재료와 데이터 품질을 확인해 주세요.",
  };
  function node(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  const note = (t) => node("p", t, "data-note");
  const connectionLabels = {
    pending: "연동 예정", insufficient: "데이터 부족", empty: "데이터 없음",
    loading: "조회 중", error: "조회 실패",
  };
  function badge(text, tone = "neutral") {
    const label = node("span", text, "badge");
    label.dataset.tone = tone;
    return label;
  }
  function connectionState(kind, description) {
    const state = node("div", undefined, "connection-state");
    state.dataset.status = kind;
    state.append(badge(connectionLabels[kind], kind === "error" ? "danger" : "neutral"), note(description));
    return state;
  }
  function openDialog(title, ...children) {
    const dialog = $("#scenario-dialog");
    if (!dialog.open) dialogTrigger = document.activeElement;
    $("#dialog-title").textContent = title;
    $("#dialog-body").replaceChildren(...children);
    $("#dialog-error").hidden = true;
    if (!dialog.open) dialog.showModal();
  }
  function closeDialog() {
    $("#scenario-dialog").close();
  }
  function button(text, action, cls = "outline") {
    const b = node("button", text, cls);
    b.type = "button";
    b.disabled = busy;
    b.onclick = action;
    return b;
  }
  function heading(root, title, description, action) {
    const h = node("div", undefined, "heading-row"),
      copy = node("div");
    copy.append(node("h1", title));
    if (description) copy.append(note(description));
    h.append(copy);
    if (action) h.append(action);
    root.append(h);
  }
  function field(
    label,
    { type = "text", value = "", options, min, max, step } = {},
  ) {
    const wrap = node("label", undefined, "scenario-field"),
      caption = node("span", label),
      input = node(options ? "select" : "input");
    caption.id = `field-label-${++fieldId}`;
    input.setAttribute("aria-labelledby", caption.id);
    if (options) {
      for (const [v, t] of options) {
        const o = node("option", t);
        o.value = v;
        input.append(o);
      }
    } else input.type = type;
    input.value = value;
    for (const [k, v] of Object.entries({ min, max, step }))
      if (v !== undefined) input[k] = v;
    wrap.append(caption, input);
    return { wrap, input };
  }
  function checkbox(label) {
    const f = field(label, { type: "checkbox" });
    return f;
  }
  function quality(root, value) {
    const row = node("div", undefined, "quality-labels");
    const qualities = { demo_data: "예시 데이터", verified: "확인된 데이터", confirmation_required: "추가 확인 필요", unverified: "미검증" };
    row.append(badge(qualities[value.data_quality] || "데이터 품질 미확인"));
    if (value.requires_confirmation === true) row.append(badge("사용자 확인 필요", "warning"));
    root.append(row);
    for (const w of value.warnings || []) root.append(node("p", w, "inline-warning"));
    if (value.data_quality === "demo_data")
      root.append(
        note(
          "서버의 예시 데이터입니다. 실제 제품·영양 판단에 사용하지 마세요.",
        ),
      );
  }
  function nutrition(root, n) {
    if (!n) {
      root.append(note("영양 정보 미제공"));
      return;
    }
    const grid = node("dl", undefined, "nutrient-grid");
    for (const [k, label, unit] of [
      ["energy_kcal", "열량", "kcal"],
      ["protein_g", "단백질", "g"],
      ["sodium_mg", "나트륨", "mg"],
    ]) {
      const metric = node("div");
      metric.append(node("dt", label), node("dd", n[k] == null ? "미제공" : `${n[k]} ${unit}`));
      grid.append(metric);
    }
    root.append(grid);
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    errorMessage = "";
    errorStatus = 0;
    $("#dialog-error").hidden = true;
    document.body.setAttribute("aria-busy", "true");
    const controls = [...document.querySelectorAll("button,input,select,textarea")];
    const disabled = controls.map((n) => n.disabled);
    controls.forEach((n) => (n.disabled = true));
    try {
      await action();
    } catch (e) {
      errorStatus = e.status || 0;
      errorMessage =
        e.status === 403
          ? "이 작업을 수행할 가구 권한을 확인해 주세요. 세션이 만료됐다면 다시 로그인해 주세요."
          : e.status === 409 && e.code === "inventory_confirmation_required"
            ? `출고 확인 필요 · ${e.message}`
            : e.message;
      if ($("#scenario-dialog").open && !s.session) closeDialog();
    } finally {
      busy = false;
      controls.forEach((n, i) => (n.disabled = disabled[i]));
      document.body.setAttribute("aria-busy", "false");
      render();
    }
  }
  function go(name, { historyEntry = true } = {}) {
    const next = views.some((v) => v.dataset.view === name) ? name : "home";
    if (current !== next) {
      if ($("#scenario-dialog").open) closeDialog();
      current = next;
      errorMessage = "";
      errorStatus = 0;
      if (historyEntry) history.pushState(null, "", `/${$("#app-shell").dataset.device}#${current}`);
    }
    render();
    $("#main-content").focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
    if (name === "inventory" && s.session) run(() => api.inventory());
  }
  function loginView(root) {
    heading(
      root,
      "로컬 가구 로그인",
      "가구 계정으로 로그인해 식사와 재고를 확인하세요.",
    );
    const p = panel(root, "가구 계정"),
      form = node("form");
    const name = field("로그인 이름"),
      password = field("비밀번호", { type: "password" });
    const household = field("가구 ID", {
      type: "number",
      value: s.household?.id || 1,
      min: 1,
      step: 1,
    });
    name.input.autocomplete = "username";
    password.input.autocomplete = "current-password";
    name.input.required =
      password.input.required =
      household.input.required =
        true;
    household.input.readOnly = active();
    const submit = button("로그인", () => {}, "primary");
    submit.type = "submit";
    form.append(name.wrap, password.wrap, household.wrap, submit);
    form.onsubmit = (e) => {
      e.preventDefault();
      run(async () => {
        try {
          const previousHousehold = s.household?.id;
          await api.login(
            name.input.value,
            password.input.value,
            Number(household.input.value),
          );
          if (previousHousehold !== s.household.id) {
            resetScreenContext();
            existingOutflows = "";
            for (const key of Object.keys(intakeDraft)) delete intakeDraft[key];
          }
        } finally {
          password.input.value = "";
        }
        await Promise.all([api.inventory(), api.refreshData()]);
      });
    };
    p.append(form);
  }
  function selectedRecipe() {
    return s.result?.status === "recommendations_available"
      ? s.result.recommendations.find((r) => r.recipe_id === s.picked)
      : null;
  }
  function active() {
    return s.cooking?.status === "active";
  }
  function panel(root, title) {
    const p = node("section", undefined, "card scenario-section");
    p.append(node("h2", title));
    root.append(p);
    return p;
  }
  function memberPicker(withAll = false) {
    const group = node("div", undefined, "member-buttons");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "식사 대상 선택");
    const members = s.household.members;
    const toggle = (name, selected, next) => {
      const choice = button(name, () => run(() => api.selectMembers(next)), "member");
      choice.setAttribute("aria-pressed", String(selected));
      choice.disabled = busy || active();
      group.append(choice);
    };
    if (withAll && members.length) {
      const all = s.selected.length === members.length;
      toggle("전체 선택", all, all ? [] : members.map(member => member.id));
    }
    for (const member of members) {
      const selected = s.selected.includes(member.id);
      toggle(member.name, selected, selected
        ? s.selected.filter(id => id !== member.id)
        : [...s.selected, member.id]);
    }
    if (!members.length) group.append(note("가족 관리에서 구성원을 추가해 주세요."));
    return group;
  }
  function daysToExpiry(date) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "")) return null;
    const value = Date.parse(date + "T00:00:00Z");
    if (!Number.isFinite(value) || new Date(value).toISOString().slice(0, 10) !== date) return null;
    const day = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" });
    return (value - Date.parse(day + "T00:00:00Z")) / 86400000;
  }
  function expiryKind(date) {
    const days = daysToExpiry(date);
    if (days === null) return "undated";
    if (days < 0) return "expired";
    return days <= 3 ? "soon" : "regular";
  }
  function expiryBadge(date) {
    const days = daysToExpiry(date);
    if (days === null) return badge("미등록");
    if (days < 0) return badge("기한 지남", "danger");
    if (days === 0) return badge("오늘까지", "warning");
    if (days <= 3) return badge(`D-${days} · 임박`, "warning");
    return badge("기한 등록", "positive");
  }
  function inventoryState() {
    if (s.inventoryStatus === "error") return connectionState("error", "재고를 불러오지 못했습니다. 식재료 화면에서 다시 시도해 주세요.");
    if (s.inventoryStatus !== "ready" || !s.inventory) return connectionState("loading", "재고를 확인하고 있어요.");
    if (!s.inventory.items.length) return connectionState("empty", "등록된 식재료가 없어요.");
    return null;
  }
  function expiryOverview(root) {
    const state = inventoryState();
    if (state) {
      root.append(state);
      return;
    }
    const stock = s.inventory.items.filter(item => item.quantity_g !== 0);
    const soon = stock.filter(item => expiryKind(item.expiry_date) === "soon");
    const expired = stock.filter(item => expiryKind(item.expiry_date) === "expired");
    const undated = stock.filter(item => expiryKind(item.expiry_date) === "undated");
    const counts = node("div", undefined, "quality-labels");
    counts.append(badge(`임박 ${soon.length}개`, soon.length ? "warning" : "neutral"), badge(`기한 지남 ${expired.length}개`, expired.length ? "danger" : "neutral"));
    root.append(counts);
    const due = [...expired, ...soon].sort((a, b) => a.expiry_date.localeCompare(b.expiry_date));
    if (due.length) {
      const rows = node("ul", undefined, "expiry-list");
      for (const item of due.slice(0, 4)) {
        const row = node("li");
        row.append(node("span", item.name), expiryBadge(item.expiry_date));
        rows.append(row);
      }
      root.append(rows);
    } else if (!stock.length) {
      root.append(connectionState("empty", "보유 중인 식재료가 없어요."));
    } else if (stock.length === undated.length) {
      root.append(connectionState("insufficient", "소비기한을 등록한 식재료가 없어요."));
    } else {
      root.append(note("등록된 기한 중 임박하거나 지난 식재료가 없어요."));
    }
    root.append(note(`오늘부터 3일 이내를 임박으로 표시해요.${undated.length ? ` · 기한 미등록 ${undated.length}개` : ""}`));
  }
  function home(root) {
    heading(root, "오늘은 무엇을 먹을까요?", "식사할 가족을 선택하고 냉장고 상태를 확인해 보세요.");
    // The question area stays hidden until the server supplies a question.
    const questions = node("section", undefined, "question-card");
    questions.hidden = true;
    questions.dataset.status = "pending";
    questions.append(node("h2", "KINI가 궁금해요"), connectionState("pending", "질문을 받으면 여기에 표시합니다."));
    root.append(questions);

    const grid = node("div", undefined, "grid-two home-summary");
    root.append(grid);
    const family = panel(grid, "오늘은 누가 식사하나요?");
    family.append(note("함께 식사할 가족을 모두 선택해 주세요."), memberPicker());
    const start = button("맞춤 메뉴 추천받기", () => {
      go("recommend");
      requestRecommendation(false);
    }, "primary");
    start.disabled = busy || active() || !s.selected.length;
    family.append(note(`${s.selected.length}명 선택`), start);
    if (active()) family.append(button("진행 중인 조리 보기", () => go("cook")));
    const expiry = panel(grid, "냉장고 현황");
    expiryOverview(expiry);
    expiry.append(button("식재료 확인", () => go("inventory")));

    const purchases = panel(root, "KINI 맞춤 구매 제안");
    purchases.append(connectionState("pending", "평소 자주 사용한 식재료와 현재 잔량을 바탕으로 제안할 영역이에요."));
    purchases.append(button("구매 제안 자세히 보기", () => {
      const details = node("dl", undefined, "readout");
      for (const label of ["사용 빈도", "최근 사용일", "현재 잔량", "제안 이유"]) {
        details.append(node("dt", label), node("dd", "—"));
      }
      openDialog("맞춤 구매 제안", connectionState("pending", "구매 제안 서버가 연동되면 품목별로 확인할 수 있어요."), details);
    }));
  }
  function requestRecommendation(more) {
    return run(() => {
      if (!Number.isInteger(conditions.cook_time_limit_minutes) || conditions.cook_time_limit_minutes < 1)
        throw Error("조리 시간을 확인해 주세요.");
      const pending = api.recommend(conditions, more);
      render();
      return pending;
    });
  }
  function recommendation(root) {
    heading(root, "메뉴 추천", "식사할 가족의 알레르기, 식단 조건, 취향을 반영해요.");
    const picker = panel(root, "식사 대상");
    const actions = node("div", undefined, "picker-actions");
    const submit = button(s.result?.status === "recommendations_available" ? "다시 추천받기" : "맞춤 메뉴 추천받기",
      () => requestRecommendation(false), "primary");
    submit.disabled = busy || active() || !s.selected.length;
    actions.append(note(`${s.selected.length}명 선택`), submit);
    if (active()) actions.append(button("진행 중인 조리 보기", () => go("cook")));
    picker.append(memberPicker(true), actions);

    const options = node("details", undefined, "options");
    options.id = "recommendation-options";
    options.open = recommendationOptionsOpen;
    options.append(node("summary", "추천 조건"));
    const controls = node("div", undefined, "scenario-controls");
    controls.id = "recommendation-controls";
    const meal = field("끼니", {
      value: conditions.meal_type,
      options: [["breakfast", "아침"], ["lunch", "점심"], ["dinner", "저녁"]],
    });
    const time = field("조리 제한 (분)", { type: "number", value: conditions.cook_time_limit_minutes, min: 1, max: 240 });
    const purchase = checkbox("부족한 재료 구매 가능");
    const exclude = field("제외 재료 (쉼표 구분)", { value: conditions.excluded_ingredients.join(", ") });
    purchase.input.checked = conditions.purchase_allowed;
    const results = node("div", undefined, "recommendation-results");
    for (const f of [meal, time, exclude, purchase]) {
      f.input.disabled = busy || active();
      f.input.oninput = () => {
        conditions.meal_type = meal.input.value;
        conditions.cook_time_limit_minutes = Number(time.input.value);
        conditions.purchase_allowed = purchase.input.checked;
        conditions.excluded_ingredients = exclude.input.value.split(",").map(value => value.trim()).filter(Boolean);
        if (!s.result) return;
        api.invalidate();
        submit.textContent = "맞춤 메뉴 추천받기";
        recommendationResult(results);
      };
      controls.append(f.wrap);
    }
    options.append(controls);
    picker.append(options);
    root.append(results);
    recommendationResult(results);
  }

  function recommendationResult(root) {
    root.replaceChildren();
    const notice = node("div", undefined, "recommendation-notice");
    root.append(notice);
    const status = node("div", undefined, "scenario-status");
    status.id = "recommendation-status";
    status.setAttribute("role", "status");
    status.dataset.state = s.result?.status || (busy ? "loading" : "neutral");
    status.textContent = s.result?.message || messages[s.result?.status] ||
      (busy ? "추천 요청을 처리하는 중입니다…" : "가족을 선택하면 맞춤 메뉴를 추천받을 수 있어요.");
    notice.append(status);
    if (!s.selected.length) {
      const general = panel(root, "냉장고 속 재료로 만들 수 있는 메뉴");
      general.append(connectionState("pending", "식사 대상 선택 없이 보는 일반 메뉴는 연동 예정이에요."));
      return;
    }
    if (!s.result) return;
    quality(notice, s.result);
    if (s.result.status !== "recommendations_available") {
      status.append(button("다시 요청", () => requestRecommendation(false)));
      return;
    }
    const names = s.household.members.filter(member => s.selected.includes(member.id)).map(member => member.name);
    root.append(node("h2", `맞춤 메뉴 · ${names.join(", ")}`, "section-title"));
    const layout = node("div", undefined, "menu-grid");
    for (const r of s.result.recommendations) {
      const card = node("article", undefined, "card recipe-option");
      const reason = s.result.ai_explanation?.candidate_explanations?.find(item => item.recipe_id === r.recipe_id)?.reason;
      card.append(badge(`${r.cook_time_minutes}분`, "positive"), node("h3", r.name));
      if (reason) card.append(note(reason));
      nutrition(card, r.nutrition_per_serving);
      if (r.requires_confirmation) card.append(badge("확인 필요", "warning"));
      const detail = button("조리법 확인", () => {
        s.picked = r.recipe_id;
        const detailContent = node("div", undefined, "recipe-detail");
        recipe(detailContent, true);
        openDialog(r.name, detailContent);
      });
      detail.setAttribute("aria-label", `${r.name} 조리법 확인`);
      detail.disabled = busy || active();
      card.append(detail);
      layout.append(card);
    }
    root.append(layout);
    const info = node("details", undefined, "options reason-card");
    info.append(node("summary", "추천 근거와 제외된 후보"), note(s.result.ai_explanation?.summary || s.result.explanation || "설명 미제공"));
    for (const x of s.result.excluded || []) info.append(note(`${x.name}: ${(x.reasons || x.reason_codes || []).join(", ")}`));
    root.append(info);
    const more = button("같은 조건으로 더 보기", () => requestRecommendation(true));
    more.disabled = busy || active();
    root.append(more);
  }

  function recipe(root, inDialog = false) {
    const r = selectedRecipe();
    if (!inDialog) heading(root, r?.name || "추천 메뉴를 먼저 선택하세요", "조리 시작 전 재료·품질을 확인하세요.");
    if (!r) {
      root.append(button("추천으로 이동", () => go("recommend")));
      return;
    }
    const p = panel(root, "이번 참여자 기준 필요량");
    for (const i of r.inventory_usage || [])
      p.append(
        note(`${i.ingredient}: 필요 ${i.needed_g}g / 보유 ${i.available_g}g`),
      );
    p.append(
      note(
        "이번 식사에 필요한 재료량입니다. 조리 중 재료를 변경하면 총 조리량을 다시 확인할 수 있어요.",
      ),
    );
    nutrition(p, r.nutrition_per_serving);
    quality(p, r);
    p.append(connectionState("pending", "이 메뉴의 상세 조리 순서는 연동 예정이에요."));
    const confirm = checkbox("실제 재료·분량 및 데이터 품질을 확인했습니다");
    const start = button(
      "조리 시작",
      () =>
        run(async () => {
          try {
            await api.startCooking(r.recipe_id, confirm.input.checked, conditions);
          } catch (error) {
            if (inDialog && s.result?.status === "stale_unvalidated") closeDialog();
            throw error;
          }
          if (inDialog) closeDialog();
          existingOutflows = lastAnswer = "";
          go("cook");
        }),
      "primary",
    );
    start.disabled = busy || active();
    p.append(confirm.wrap, start);
  }
  function cook(root) {
    const c = s.cooking;
    heading(
      root,
      "조리 기록",
      "이번에 조리할 재료와 총 사용량을 확인하세요.",
    );
    if (!c) {
      root.append(
        note("진행 중인 조리가 없습니다."),
        button("추천으로 이동", () => go("recommend")),
      );
      return;
    }
    const r = selectedRecipe();
    const p = panel(
      root,
      r?.recipe_id === c.recipe_id ? r.name : `조리 세션 ${c.id}`,
    );
    p.append(badge(c.planned_servings == null ? "계획 인분 미제공" : `계획 ${c.planned_servings}인분`, "positive"), note("서버 계산 1인분 영양"));
    quality(p, c);
    nutrition(p, c.nutrition_per_serving);
    p.append(node("h3", "이번 조리에 사용할 총량"));
    for (const i of c.ingredients)
      p.append(
        note(
          `${s.ingredientNames[i.ingredient_id] || `재료 ID ${i.ingredient_id}`}: ${i.quantity_g}g`,
        ),
      );
    if (active()) {
      const form = node("form"),
        name = field("변경할 정확한 재료명"),
        qty = field("이번 조리 총 사용량 (g) · 0은 제거", {
          type: "number",
          value: 0,
          min: 0,
          max: 10000,
          step: 0.1,
        }),
        save = button("변경 및 영양 재검증", () => {}, "primary");
      save.type = "submit";
      form.append(name.wrap, qty.wrap, save);
      form.onsubmit = (e) => {
        e.preventDefault();
        run(() => api.changeIngredient(name.input.value, qty.input.value));
      };
      if (s.outflows?.started)
        p.append(
          note(
            "출고를 시작해 재료 변경이 잠겼습니다. 섭취량 기록 화면에서 이어서 진행하세요.",
          ),
        );
      else
        p.append(
          form,
          note("최종 재료를 확인한 뒤 재고 출고와 섭취 기록으로 진행합니다."),
        );
      const question = field("조리 질문"),
        answer = node("div", undefined, "ai-answer"),
        qform = node("form"),
        send = button("질문 보내기", () => {}, "outline");
      send.type = "submit";
      qform.append(question.wrap, send);
      qform.onsubmit = (e) => {
        e.preventDefault();
        run(async () => {
          const v = await api.question(question.input.value);
          lastAnswer =
            v.answer + (v.requires_confirmation ? " (사용자 확인 필요)" : "");
        });
      };
      answer.textContent = lastAnswer;
      const help = node("details", undefined, "options");
      help.open = !!lastAnswer;
      help.append(node("summary", "조리 도움말"), qform, answer);
      const deferredRecord = button("조리만 기록하기", () => openDialog("조리만 기록하기", connectionState("pending", "조리 기록과 실제 식사 확인을 따로 저장하는 기능은 연동 예정이에요.")));
      p.append(
        help,
        deferredRecord,
        button("조리 완료 · 섭취량 확인", () => go("record"), "primary"),
      );
    } else p.append(button("저장 확인 보기", () => go("report")));
  }
  function record(root) {
    heading(
      root,
      "실제 섭취량 기록",
      "변경된 조리 세션을 기준으로 서버에 저장합니다.",
    );
    if (!active()) {
      root.append(
        note(
          s.cooking?.status === "completed"
            ? "저장 완료. 중복 저장할 수 없습니다."
            : "진행 중인 조리가 없습니다.",
        ),
      );
      return;
    }
    const p = panel(root, "사용량과 섭취량 확인");
    p.append(badge(s.cooking.planned_servings == null ? "계획 인분 미제공" : `계획 ${s.cooking.planned_servings}인분`, "positive"), node("h3", "이번 조리에 사용한 총량"));
    for (const i of s.cooking.ingredients)
      p.append(
        note(
          `${s.ingredientNames[i.ingredient_id] || `재료 ID ${i.ingredient_id}`}: ${i.quantity_g}g`,
        ),
      );
    p.append(note("서버 계산 1인분 영양"));
    nutrition(p, s.cooking.nutrition_per_serving);
    quality(p, s.cooking);
    const stock = panel(root, "재료 출고 확인");
    const batch = s.outflows;
    if (!batch?.started) {
      stock.append(
        note(
          "이미 확정된 출고가 있으면 아래 식사 기록에 ID를 입력하세요. 해당 재료를 다시 출고하지 마세요.",
        ),
      );
      stock.append(
        button("수동 출고량 확인", () =>
          run(() => {
            if (existingOutflows.trim())
              throw Error("입력한 확정 출고 ID를 먼저 확인해 주세요.");
            return api.prepareOutflows();
          }),
        ),
      );
    }
    if (batch) {
      const list = node("ul", undefined, "outflow-list");
      for (const entry of batch.entries) {
        list.append(
          node(
            "li",
            `${entry.name} ${entry.body.quantity_g}g · 재고 #${entry.body.inventory_item_id} · ${entry.expiry || "기한 미기재"} · ${entry.applied ? `출고 확정 #${entry.eventId}` : "확정 전"}`,
          ),
        );
      }
      stock.append(list);
      if (batch.entries.some((entry) => !entry.applied)) {
        const confirmation = checkbox(
          "아직 출고하지 않은 재료이며 위 재고와 중량을 확인했습니다",
        );
        stock.append(
          confirmation.wrap,
          button(
            "수동 출고 확정",
            () =>
              run(async () => {
                if (!confirmation.input.checked || existingOutflows.trim())
                  throw Error(
                    "출고할 재료와 중량, 기존 출고 여부를 확인해 주세요.",
                  );
                await api.confirmOutflows();
                await api.inventory();
              }),
            "primary",
          ),
        );
      } else
        stock.append(
          node("p", "출고가 재고에 반영됐습니다. 이제 섭취량을 저장하세요."),
        );
    }
    const intake = panel(root, "구성원별 섭취량");
    const form = node("form"),
      entries = s.household.members
        .filter((m) => s.selected.includes(m.id))
        .map((m) => {
          const f = field(`${m.name} 실제 섭취량 (인분)`, {
            type: "number",
            value: intakeDraft[`${s.cooking.id}:${m.id}`] ?? "",
            min: 0,
            max: 10,
            step: "any",
          });
          f.input.oninput = () => {
            intakeDraft[`${s.cooking.id}:${m.id}`] = f.input.value;
          };
          form.append(f.wrap);
          return { member: m, input: f.input };
        }),
      confirmed = checkbox("실제 사용량과 개인별 섭취량을 확인했습니다");
    const save = button("서버에 식사 기록 저장", () => {}, "primary");
    save.type = "submit";
    if (!batch?.started) {
      const ids = field("기존 확정 출고 ID (쉼표 구분)", {
        value: existingOutflows,
      });
      ids.input.oninput = () => {
        existingOutflows = ids.input.value;
      };
      form.append(ids.wrap);
    }
    form.append(
      note("직접 확인한 인분을 입력하세요. 이번 식사에 참여하지 않은 구성원은 0인분으로 입력하세요."),
      confirmed.wrap,
      save,
    );
    form.onsubmit = (e) => {
      e.preventDefault();
      run(async () => {
        await api.complete(
          entries.map((x) => ({
            member_id: x.member.id,
            servings: Number(x.input.value || NaN),
          })),
          confirmed.input.checked,
          !!batch?.started,
          existingOutflows
            .split(",")
            .map((id) => id.trim())
            .filter(Boolean)
            .map((id) => (/^\d+$/.test(id) ? Number(id) : NaN)),
        );
        go("report");
        await api.inventory();
      });
    };
    intake.append(form);
  }
  function inventoryEdit(item, remove = false) {
    const action = button(remove ? "재고에서 삭제" : "변경 저장", () => {}, "primary");
    action.disabled = true;
    const footer = node("div", undefined, "dialog-actions");
    footer.append(button("닫기", closeDialog), action);
    const details = node("dl", undefined, "readout");
    details.append(
      node("dt", "식재료"), node("dd", item.name),
      node("dt", "보유 수량"), node("dd", item.quantity_g == null ? "중량 미확인" : `${item.quantity_g} g`),
      node("dt", "소비기한"), node("dd", item.expiry_date || "미등록"),
    );
    const editor = node("div", undefined, "scenario-controls");
    if (!remove) {
      for (const control of [
        field("식재료명", { value: item.name }),
        field("수량 (g)", { type: "number", value: item.quantity_g ?? "" }),
        field("소비기한", { type: "date", value: item.expiry_date || "" }),
      ]) {
        control.input.readOnly = true;
        editor.append(control.wrap);
      }
    }
    openDialog(remove ? "식재료 삭제 확인" : "식재료 수정", remove ? details : editor,
      connectionState("pending", remove ? "이 식재료를 삭제하는 기능은 연동 예정이에요." : "기존 재고의 수량·단위·소비기한 수정은 연동 예정이에요."), footer);
  }
  function showStockQuestion() {
    const q = field("질문"), form = node("form"), answer = node("p", "", "data-note");
    const send = button("질문 보내기", () => {}, "primary");
    send.type = "submit";
    form.append(q.wrap, send);
    form.onsubmit = (e) => {
      e.preventDefault();
      run(async () => { answer.textContent = (await api.chat(q.input.value)).answer; });
    };
    openDialog("재고 질문", note("재고 질문 도우미는 별도의 예시 재고를 사용합니다."), form, answer);
  }
  function openInventoryForm() {
    const form = node("form"),
      name = field("등록할 정확한 재료명"),
      qty = field("측정 중량", { type: "number", min: 0.001, max: 1000000, step: "any" }),
      unit = field("단위", { value: "g", options: [["g", "g"], ["kg", "kg"]] }),
      expiry = field("표시 소비기한 (선택)", { type: "date" }),
      confirm = checkbox("재료명·실제 중량을 확인했습니다"),
      save = button("입고 확정", () => {}, "primary");
    save.type = "submit";
    name.input.required = qty.input.required = confirm.input.required = true;
    form.append(name.wrap, qty.wrap, unit.wrap, expiry.wrap, confirm.wrap, save);
    form.onsubmit = (e) => {
      e.preventDefault();
      run(async () => {
        const saved = await api.addInventory(name.input.value.trim(), Number(qty.input.value), expiry.input.value, unit.input.value);
        inventoryMessage = `재고 항목 ${saved.inventory_item_id}을 등록했습니다.`;
        inventoryView.query = "";
        inventoryView.filter = "all";
        inventoryView.page = 1;
        closeDialog();
        await api.inventory();
      });
    };
    openDialog("식재료 추가", form);
  }
  function stockRows(root) {
    root.replaceChildren();
    const state = inventoryState();
    if (state) {
      root.append(state);
      return;
    }
    const items = s.inventory.items.filter((item) =>
      item.name.toLocaleLowerCase("ko-KR").includes(inventoryView.query.trim().toLocaleLowerCase("ko-KR")) &&
      (inventoryView.filter === "all" || item.category === inventoryView.filter),
    );
    const pages = Math.max(1, Math.ceil(items.length / 5));
    inventoryView.page = Math.min(inventoryView.page, pages);
    const start = (inventoryView.page - 1) * 5;
    if (!items.length) {
      root.append(connectionState("empty", "검색 조건에 맞는 식재료가 없어요."));
      return;
    }
    const table = node("table", undefined, "inventory-table");
    const thead = node("thead"), labels = node("tr"), tbody = node("tbody");
    const columns = ["식재료명", "분류", "수량", "소비기한", "관리"];
    for (const label of columns) {
      const th = node("th", label);
      th.scope = "col";
      labels.append(th);
    }
    const caption = node("caption", "보유 식재료 목록", "sr-only");
    table.append(caption, thead, tbody);
    thead.append(labels);
    for (const item of items.slice(start, start + 5)) {
      const row = node("tr");
      const name = node("td", item.name, "item-name");
      const category = node("td", item.category || "미제공");
      const quantity = node("td", item.quantity_g == null ? "중량 미확인" : `${item.quantity_g} g`);
      const expiry = node("td");
      if (item.expiry_date && daysToExpiry(item.expiry_date) != null) {
        const date = node("time", item.expiry_date);
        date.dateTime = item.expiry_date;
        expiry.append(date);
      }
      expiry.append(expiryBadge(item.expiry_date));
      const actions = node("td");
      const manage = node("div", undefined, "table-actions");
      for (const [label, action] of [
        ["수정", () => inventoryEdit(item)],
        ["삭제", () => inventoryEdit(item, true)],
        ["영양 조회", () => showNutrition(item.name)],
      ]) {
        const control = button(label, action, "text-button");
        if (label !== "영양 조회") control.setAttribute("aria-label", `${item.name} ${label}`);
        manage.append(control);
      }
      actions.append(manage);
      const cells = [name, category, quantity, expiry, actions];
      cells.forEach((cell, i) => { cell.dataset.label = columns[i]; });
      row.append(...cells);
      tbody.append(row);
    }
    root.append(table);
    const pagination = node("div", undefined, "pagination");
    const range = node("span", `${start + 1}–${Math.min(start + 5, items.length)} / ${items.length}개`, "data-note");
    const change = (label, step, disabled) => {
      const control = button(label, () => {
        inventoryView.page += step;
        stockRows(root);
        root.querySelector(".pagination button:not(:disabled)")?.focus();
      });
      control.disabled = busy || disabled;
      return control;
    };
    pagination.append(range, change("이전", -1, inventoryView.page <= 1), change("다음", 1, inventoryView.page >= pages));
    root.append(pagination);
  }
  function inventory(root) {
    const add = button("+ 식재료 추가", openInventoryForm, "primary");
    add.disabled = busy || active() || s.session.role === "viewer";
    heading(root, "식재료 관리", "냉장고 속 식재료의 수량과 소비기한을 확인하세요.", add);
    if (inventoryMessage) {
      const confirmation = node("p", inventoryMessage, "inline-success");
      confirmation.setAttribute("role", "status");
      root.append(confirmation);
    }
    const card = panel(root, "보유 식재료");
    const toolbar = node("div", undefined, "inventory-toolbar");
    const search = field("식재료 검색", { type: "search", value: inventoryView.query });
    search.input.placeholder = "재료 이름";
    const categories = node("div", undefined, "button-row filters");
    categories.setAttribute("role", "group");
    categories.setAttribute("aria-label", "식재료 분류");
    const available = [...new Set(s.inventory?.items.map(item => item.category).filter(Boolean) || [])];
    const list = node("div", undefined, "inventory-list");
    list.setAttribute("aria-live", "polite");
    for (const category of ["all", ...new Set(["채소", "육류", "유제품", "기타", ...available])]) {
      const filter = button(category === "all" ? "전체" : category, () => {
        inventoryView.filter = category;
        inventoryView.page = 1;
        for (const choice of categories.children) choice.setAttribute("aria-pressed", String(choice === filter));
        stockRows(list);
      }, "filter");
      filter.disabled = busy || (category !== "all" && !available.length);
      filter.setAttribute("aria-pressed", String(inventoryView.filter === category));
      categories.append(filter);
    }
    search.input.oninput = () => {
      inventoryView.query = search.input.value;
      inventoryView.page = 1;
      stockRows(list);
    };
    toolbar.append(search.wrap, categories);
    card.append(toolbar);
    if (!available.length && s.inventory?.items.length) card.append(note("분류 정보는 연동 예정이에요. 재료 이름으로 검색할 수 있습니다."));
    card.append(list);
    stockRows(list);
    const actions = node("div", undefined, "button-row");
    actions.append(button("재고 새로고침", () => run(() => api.inventory())));
    const tools = node("details", undefined, "options");
    tools.append(node("summary", "기타 식재료 도구"), button("재고에 대해 질문", showStockQuestion));
    actions.append(tools);
    card.append(actions);
  }
  function showNutrition(name) {
    run(async () => {
      const v = await api.nutrition(name);
      const content = node("div");
      content.append(node("h3", v.food_name));
      nutrition(content, v);
      content.append(note(`출처: ${v.source || "미제공"}`), note(v.match_note || ""), note(`기준량: ${v.serving_size || "미제공"}`));
      openDialog("영양 조회", content);
    });
  }
  function feedbackDialog(receipt) {
    const group = node("div", undefined, "feedback-preview");
    const names = receipt.items.map(item => s.household.members.find(member => member.id === item.member_id)?.name).filter(Boolean);
    for (const [label, choices] of [
      ["맛", ["별로예요", "보통이에요", "맛있어요"]],
      ["간", ["싱거워요", "적당해요", "짜요"]],
      ["난이도", ["쉬웠어요", "보통이에요", "어려웠어요"]],
    ]) {
      const row = node("section");
      const actions = node("div", undefined, "button-row");
      for (const value of [...choices, "이 항목 건너뛰기"]) {
        const option = button(value, () => {});
        option.disabled = true;
        actions.append(option);
      }
      row.append(node("h3", label), actions);
      group.append(row);
    }
    const actions = node("div", undefined, "dialog-actions");
    for (const label of ["나중에 평가", "평가 건너뛰기", "평가 저장"]) {
      const control = button(label, () => {});
      control.disabled = true;
      actions.append(control);
    }
    openDialog("식후 평가", note(`평가 대상 · ${receipt.name || "식사"} / ${names.join(", ") || "식사 구성원 미확인"}`),
      note("평가자는 저장 시 별도로 확인합니다."),
      connectionState("pending", "평가 저장과 다음 추천 반영은 서버 연동 후 제공돼요."), group, actions);
  }
  function report(root) {
    heading(root, "영양 관리", "가족 구성원별 영양과 조리·식사 기록을 확인하세요.");
    const filter = node("div", undefined, "button-row report-picker");
    filter.setAttribute("role", "group");
    filter.setAttribute("aria-label", "영양 현황 대상");
    for (const [key, label] of [["all", "우리 가족"], ...s.household.members.map(member => [member.id, member.name])]) {
      const choice = button(label, () => { reportMember = key; render(); }, "filter");
      choice.setAttribute("aria-pressed", String(reportMember === key));
      filter.append(choice);
    }
    root.append(filter);
    const person = s.household.members.find(member => member.id === reportMember);
    const chart = panel(root, person ? `${person.name}의 최근 영양 현황` : "가족 영양 현황");
    const metrics = node("div", undefined, "nutrition-rows");
    for (const nutrient of ["탄수화물", "단백질", "지방", "식이섬유"]) {
      const row = node("div", undefined, "nutrition-row");
      const track = node("span", undefined, "nutrition-track");
      track.setAttribute("aria-hidden", "true");
      const value = node("span", "—");
      value.setAttribute("aria-label", "아직 계산되지 않음");
      row.append(node("span", nutrient), track, value);
      metrics.append(row);
    }
    chart.append(metrics, connectionState("pending", "기간별 영양 분석은 연동 예정이에요. 실제 섭취량을 확인한 기록부터 표시합니다."));
    const bottom = node("div", undefined, "grid-two nutrition-bottom");
    root.append(bottom);
    const supplement = panel(bottom, "보완이 필요한 영양소");
    supplement.append(connectionState("pending", "영양 분석이 연결되면 보완 영양소와 메뉴를 확인할 수 있어요."));
    const suggestion = button("보완 메뉴 추천", () => {});
    suggestion.disabled = true;
    supplement.append(suggestion);

    const p = panel(bottom, "식사 기록 저장 확인");
    p.append(note("이번 화면에서 서버 저장을 확인한 기록입니다. 과거 기록 조회는 연동 예정이에요."));
    const receipts = s.receipts.filter(r => reportMember === "all" || r.items.some(item => item.member_id === reportMember));
    if (!receipts.length) p.append(connectionState("insufficient", "이번 탭에서 확인한 기록이 없습니다. 과거 기록이 없다는 의미는 아닙니다."));
    for (const r of receipts.slice().reverse()) {
      const row = node("div", undefined, "scenario-log");
      row.append(node("h3", `${r.name || "식사"} · 기록 ID ${r.record_id}`),
        badge("식사 확인 완료", "positive"), badge("식후 평가 연동 예정"));
      for (const item of r.items) {
        if (reportMember !== "all" && reportMember !== item.member_id) continue;
        const name = s.household.members.find(member => member.id === item.member_id)?.name || "구성원";
        row.append(note(`${name} · 입력한 실제 섭취량 ${item.servings}인분`));
      }
      row.append(note("서버 계산 1인분 영양"));
      nutrition(row, r.nutrition_per_serving);
      row.append(button("식후 평가", () => feedbackDialog(r)));
      p.append(row);
    }
  }
  function openMemberForm() {
    const form = node("form");
    const name = field("구성원 이름"), age = field("나이", { type: "number", min: 0, max: 130, step: 1 });
    const allergies = field("알레르기 (쉼표 구분)"), dislikes = field("비선호 음식 (쉼표 구분)");
    name.input.required = age.input.required = true;
    name.input.maxLength = 100;
    const save = button("구성원 추가", () => {}, "primary");
    save.type = "submit";
    form.append(name.wrap, age.wrap, allergies.wrap, dislikes.wrap,
      connectionState("pending", "건강·식단 조건과 선호 음식 입력은 연동 예정이에요."), save);
    const values = input => [...new Set(input.value.split(",").map(value => value.trim()).filter(Boolean))];
    form.onsubmit = e => {
      e.preventDefault();
      run(async () => {
        const member = await api.addMember({
          name: name.input.value.trim(),
          age: Number(age.input.value),
          allergies: values(allergies.input),
          dislikes: values(dislikes.input),
        });
        familyMessage = `${member.name} 구성원을 추가했습니다.`;
        closeDialog();
      });
    };
    openDialog("가족 구성원 추가", form);
  }
  function familyAction(member, remove = false) {
    const controls = node("div", undefined, "dialog-actions");
    const action = button(remove ? "삭제 확인" : "저장", () => {}, "primary");
    action.disabled = true;
    controls.append(button("닫기", closeDialog), action);
    const editor = node("div", undefined, "scenario-controls");
    if (!remove) {
      const dislikes = (member.preferences || []).filter(pref => pref.kind === "dislike").map(pref => pref.value);
      for (const control of [
        field("구성원 이름", { value: member.name }),
        field("나이", { type: "number", value: member.age }),
        field("알레르기", { value: member.allergies.join(", ") }),
        field("비선호 음식", { value: dislikes.join(", ") }),
      ]) {
        control.input.readOnly = true;
        editor.append(control.wrap);
      }
    }
    openDialog(remove ? "가족 구성원 삭제 확인" : "가족 정보 수정", remove ? note(`${member.name} 구성원`) : editor,
      connectionState("pending", remove
        ? "구성원 삭제 및 기존 기록 보존은 서버 연동 후 제공돼요."
        : "등록된 알레르기, 건강·식단과 음식 취향 변경은 연동 예정이에요."), controls);
  }
  function profile(root) {
    const add = button("+ 구성원 추가", openMemberForm, "primary");
    add.disabled = busy || active() || s.session.role === "viewer";
    heading(root, "가족 관리", `${s.household.name}의 구성원과 식사 조건을 확인하세요.`, add);
    if (familyMessage) {
      const message = node("p", familyMessage, "inline-success");
      message.setAttribute("role", "status");
      root.append(message);
    }
    const grid = node("div", undefined, "family-grid");
    root.append(grid);
    for (const member of s.household.members) {
      const card = panel(grid, member.name);
      const details = node("dl", undefined, "readout");
      const preferences = member.preferences || [];
      const constraints = member.constraints || [];
      details.append(
        node("dt", "알레르기"), node("dd", member.allergies.join(", ") || "미설정"),
        node("dt", "건강 및 식단"), node("dd", constraints.map(condition => condition.kind === "meal_sodium_max_mg"
          ? `한 끼 나트륨 최대 ${condition.value} mg` : condition.kind === "excluded_ingredient" ? `제외 재료 ${condition.value}` : "설정 있음").join(" · ") || "미설정"),
        node("dt", "음식 취향"), node("dd", preferences.map(option => `${option.kind === "dislike" ? "비선호 " : ""}${preferenceLabels[option.value] || option.value}`).join(" · ") || "미설정"),
      );
      const actions = node("div", undefined, "button-row");
      const edit = button("정보 수정", () => familyAction(member));
      const remove = button("삭제", () => familyAction(member, true));
      edit.setAttribute("aria-label", `${member.name} 정보 수정`);
      remove.setAttribute("aria-label", `${member.name} 삭제`);
      actions.append(edit, remove);
      card.append(note(`나이 ${member.age}세`), details, actions);
    }
    if (!s.household.members.length) root.append(connectionState("empty", "등록된 가족 구성원이 없어요."));
    const options = node("details", undefined, "options");
    options.id = "profile-options";
    options.append(node("summary", "가구 계정 및 데이터 출처"));
    options.open = profileOptionsOpen;
    options.append(note(`가구 권한 · ${{ owner: "소유자", editor: "편집", viewer: "조회" }[s.session.role] || s.session.role}`));
    const login = button("다시 로그인", () => run(() => {
      api.clearSession();
      resetScreenContext();
    }));
    const refresh = button("데이터 상태 새로고침", () => run(() => api.refreshData()));
    refresh.id = "demo-tools";
    const modes = node("div", undefined, "button-row");
    for (const [key, label] of [["fridge", "냉장고"], ["mobile", "모바일 앱"]]) {
      const switcher = button(label, () => { setDevice(key); go("home"); }, "filter");
      switcher.dataset.deviceChoice = key;
      switcher.setAttribute("aria-pressed", String($("#app-shell").dataset.device === key));
      modes.append(switcher);
    }
    options.append(login, refresh, modes);
    for (const e of s.dataErrors) options.append(note(e));
    const releases = s.data?.releases, q = s.data?.quality;
    if (releases) {
      options.append(note(`영양 release: ${releases.current?.nutrition_release_id ?? "published 없음"} · 알레르겐 기준: ${releases.current?.allergen_reference_version_id ?? "published 없음"}`));
      for (const release of releases.nutrition_releases || []) options.append(note(`${release.provider_name} / ${release.dataset_name} · ${release.status} · 승인 ${release.accepted_count} / 격리 ${release.quarantined_count}`));
    }
    if (q) {
      options.append(note(`공식 식품 ${q.official_food_items}개 · 활성 매핑 ${q.active_mappings}개`));
      for (const source of q.ingredients_by_source || []) options.append(note(`${source.source_type}: ${source.count}개`));
    }
    options.append(note("ThinQ 동기화 상태는 이 화면에서 제공하지 않습니다."));
    root.append(options);
  }

  function resetScreenContext() {
    $("#dialog-body").replaceChildren();
    lastAnswer = inventoryMessage = familyMessage = "";
    reportMember = "all";
    Object.assign(inventoryView, { query: "", filter: "all", page: 1 });
  }

  function render() {
    recommendationOptionsOpen = $("#recommendation-options")?.open ?? recommendationOptionsOpen;
    profileOptionsOpen = $("#profile-options")?.open ?? profileOptionsOpen;
    const shell = $("#app-shell");
    const main = $("#main-content");
    const root = views.find(view => view.dataset.view === current);
    const section = root.dataset.parentView || current;
    history.replaceState(null, "", `/${shell.dataset.device}#${current}`);
    document.title = `KINI · ${screenNames[section]}`;
    main.setAttribute("aria-label", screenNames[section]);
    for (const view of views) {
      view.replaceChildren();
      view.hidden = view !== root;
      view.classList.toggle("active", view === root);
      view.classList.toggle("login-view", view === root && !s.session);
    }
    document.querySelectorAll("button[data-view-link]").forEach(button => {
      button.disabled = busy || !s.session;
      const selected = button.dataset.viewLink === section;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    $("#dataset-badge").hidden = !(s.session && s.data?.datasetMode === "demo");
    const dialogError = $("#dialog-error");
    const errorInDialog = Boolean(errorMessage && $("#scenario-dialog").open);
    dialogError.hidden = !errorInDialog;
    if (errorInDialog) {
      dialogError.textContent = errorMessage;
      dialogError.dataset.httpStatus = String(errorStatus);
    }
    if (errorMessage && !errorInDialog) {
      const alert = node("p", errorMessage, "scenario-warning");
      alert.setAttribute("role", "alert");
      alert.dataset.httpStatus = String(errorStatus);
      root.append(alert);
      if (errorStatus === 403)
        root.append(button("다시 로그인", () => run(() => api.clearSession())));
    }
    if (!s.session) {
      loginView(root);
      return;
    }
    ({ home, inventory, recommend: recommendation, recipe, cook, record, report, profile })[current](root);
  }

  document.addEventListener("click", e => {
    const link = e.target.closest("[data-view-link]");
    if (!link) return;
    e.preventDefault();
    if (!busy && s.session) go(link.dataset.viewLink);
  });
  $(".skip-link").onclick = e => {
    e.preventDefault();
    $("#main-content").focus();
  };
  const handleLocation = () => {
    if (location.pathname === "/mobile" || location.pathname === "/fridge") {
      setDevice(location.pathname.slice(1));
    }
    if (location.hash.slice(1) !== current) go(location.hash.slice(1), { historyEntry: false });
  };
  window.addEventListener("hashchange", handleLocation);
  window.addEventListener("popstate", handleLocation);
  const dialog = $("#scenario-dialog");
  $("#dialog-close").onclick = closeDialog;
  dialog.addEventListener("cancel", event => {
    if (busy) event.preventDefault();
  });
  dialog.addEventListener("close", () => {
    const trigger = dialogTrigger;
    if (dialog.open) return;
    dialogTrigger = null;
    $("#dialog-body").replaceChildren();
    if (document.activeElement === document.body) {
      const target = trigger?.isConnected && !trigger.disabled ? trigger : $("#main-content");
      target.focus({ preventScroll: true });
    }
  });
  function setDevice(device) {
    $("#app-shell").dataset.device = device;
    document.documentElement.dataset.device = device;
  }
  const explicit = ["/fridge", "/mobile"].includes(location.pathname)
    ? location.pathname.slice(1)
    : null;
  setDevice(explicit || (window.matchMedia("(max-width:660px)").matches ? "mobile" : "fridge"));
  current = location.hash.slice(1);
  if (!views.some(view => view.dataset.view === current)) current = "home";
  render();
})();
