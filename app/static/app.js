/* Six scenario screens backed by the local Flask API. */
(() => {
  const api = KINIAPI.createClient(),
    s = api.state,
    $ = (q, r = document) => r.querySelector(q);
  const views = [...document.querySelectorAll("[data-view]")];
  let busy = false,
    ready = false,
    errorMessage = "",
    current = "home",
    lastAnswer = "",
    inventoryError = "",
    inventoryMessage = "";
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
  function button(text, action, cls = "outline") {
    const b = node("button", text, cls);
    b.type = "button";
    b.disabled = busy;
    b.onclick = action;
    return b;
  }
  function heading(root, title, description) {
    const h = node("div", undefined, "heading-row"),
      copy = node("div");
    copy.append(node("h1", title), note(description));
    h.append(copy);
    root.append(h);
  }
  function field(
    label,
    { type = "text", value = "", options, min, max, step } = {},
  ) {
    const wrap = node("label", label, "scenario-field"),
      input = node(options ? "select" : "input");
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
    wrap.append(input);
    return { wrap, input };
  }
  function checkbox(label) {
    const f = field(label, { type: "checkbox" });
    return f;
  }
  function quality(root, value) {
    root.append(
      note(
        `데이터 품질: ${value.data_quality || "미제공"} · 사용자 확인: ${value.requires_confirmation === false ? "서버상 추가 확인 없음" : "필요"}`,
      ),
    );
    for (const w of value.warnings || []) root.append(note(w));
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
    for (const [k, label, unit] of [
      ["energy_kcal", "열량", "kcal"],
      ["protein_g", "단백질", "g"],
      ["sodium_mg", "나트륨", "mg"],
    ])
      root.append(note(`${label}: ${n[k] ?? "미제공"} ${unit}`));
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    errorMessage = "";
    document.body.setAttribute("aria-busy", "true");
    const controls = [...document.querySelectorAll("button,input,select")];
    const disabled = controls.map((n) => n.disabled);
    controls.forEach((n) => (n.disabled = true));
    try {
      await action();
    } catch (e) {
      errorMessage = e.message;
    } finally {
      busy = false;
      controls.forEach((n, i) => (n.disabled = disabled[i]));
      document.body.setAttribute("aria-busy", "false");
      render();
    }
  }
  function go(name) {
    current = views.some((v) => v.dataset.view === name) ? name : "home";
    history.replaceState(null, "", `#${current}`);
    render();
    window.scrollTo({ top: 0 });
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
  function home(root) {
    const fridge = $("#app-shell").dataset.device === "fridge";
    heading(
      root,
      fridge ? "오늘의 식탁" : "오늘의 식사",
      "서버에 등록된 가족 중 이번 식사의 참여자를 선택하세요.",
    );
    const stock = node("section", undefined, "stock-overview");
    stock.append(
      node("p", fridge ? "냉장고에서 바로 시작" : "우리 집 냉장고", "eyebrow"),
      node(
        "h2",
        s.legacyInventory
          ? `등록된 재료 ${s.legacyInventory.items.length}가지`
          : "재고 연결을 확인해 주세요",
      ),
      note(
        s.legacyInventory
          ? s.legacyInventory.items.map((i) => i.name).join(" · ")
          : "재고 조회 후 표시합니다.",
      ),
      button("재고 살펴보기", () => go("inventory"), "outline"),
    );
    root.append(stock);
    const dashboard = node("div", undefined, "home-dashboard");
    root.append(dashboard);
    const p = panel(dashboard, "누구와 드시나요?");
    const list = node("div", undefined, "members");
    for (const m of s.household.members) {
      const chosen = s.selected.includes(m.id),
        b = button(
          `${chosen ? "✓ " : ""}${m.name}`,
          () =>
            run(() =>
              api.selectMembers(
                chosen
                  ? s.selected.filter((id) => id !== m.id)
                  : [...s.selected, m.id],
              ),
            ),
          "member",
        );
      b.classList.toggle("selected", chosen);
      b.setAttribute("aria-pressed", chosen);
      b.disabled = busy || active();
      list.append(b);
    }
    p.append(
      list,
      note("조리 중에는 참여자를 변경할 수 없습니다."),
      button("메뉴 추천 조건 선택", () => go("recommend"), "primary"),
      button("진행 중인 조리 보기", () => go("cook")),
    );
    const chat = panel(dashboard, "재고에 대해 물어보기");
    const q = field("질문", { value: "" });
    const answer = node("div", undefined, "ai-answer");
    answer.textContent = lastAnswer;
    answer.hidden = !lastAnswer;
    const form = node("form");
    const send = button("질문 보내기", () => {}, "primary");
    send.type = "submit";
    form.append(q.wrap, send);
    form.onsubmit = (e) => {
      e.preventDefault();
      run(async () => {
        lastAnswer = (await api.chat(q.input.value)).answer;
      });
    };
    chat.append(
      form,
      answer,
      note(
        "질문 답변과 메뉴 추천에 사용하는 재고 출처가 다릅니다. 실제 재료를 함께 확인해 주세요.",
      ),
    );
  }
  function recommendation(root) {
    heading(root, "메뉴 추천", "가족 선택 → 추천 확인 → 조리 변경 → 섭취 기록");
    const controls = node("div", undefined, "scenario-controls");
    controls.id = "recommendation-controls";
    const meal = field("끼니", {
        value: conditions.meal_type,
        options: [
          ["breakfast", "아침"],
          ["lunch", "점심"],
          ["dinner", "저녁"],
        ],
      }),
      time = field("조리 제한 (분)", {
        type: "number",
        value: conditions.cook_time_limit_minutes,
        min: 1,
        max: 240,
      }),
      purchase = checkbox("부족한 재료 구매 가능"),
      exclude = field("제외 재료 (쉼표 구분)", {
        value: conditions.excluded_ingredients.join(", "),
      });
    purchase.input.checked = conditions.purchase_allowed;
    for (const f of [meal, time, purchase, exclude]) {
      f.input.disabled = busy || active();
      f.input.onchange = () =>
        run(() => {
          api.invalidate();
          conditions.meal_type = meal.input.value;
          conditions.cook_time_limit_minutes = Number(time.input.value);
          conditions.purchase_allowed = purchase.input.checked;
          conditions.excluded_ingredients = exclude.input.value
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean);
        });
      controls.append(f.wrap);
    }
    const request = (more) =>
      run(() => {
        if (
          !Number.isInteger(conditions.cook_time_limit_minutes) ||
          conditions.cook_time_limit_minutes < 1
        )
          throw Error("조리 시간을 확인해 주세요.");
        const pending = api.recommend(conditions, more);
        render();
        return pending;
      });
    const submit = button("추천 요청", () => request(false), "primary");
    submit.disabled = busy || active();
    controls.append(submit);
    const more = button("같은 조건으로 더 보기", () => request(true));
    more.disabled = busy || active() || !s.result;
    controls.append(more);
    root.append(controls);
    const status = node("div", undefined, "scenario-status");
    status.id = "recommendation-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.dataset.state = s.result?.status || (busy ? "loading" : "neutral");
    status.textContent =
      s.result?.message ||
      messages[s.result?.status] ||
      (busy
        ? "추천 요청을 처리하는 중입니다…"
        : "참여자와 조건을 선택한 뒤 추천을 요청하세요.");
    root.append(status);
    if (!s.result) return;
    quality(root, s.result);
    if (s.result.status !== "recommendations_available") {
      status.append(button("다시 요청", () => request(false)));
      return;
    }
    const layout = node("div", undefined, "recommend-layout"),
      list = node("section", undefined, "recipe-list"),
      reason = node("aside", undefined, "reason-card");
    for (const r of s.result.recommendations) {
      const b = button(
        "",
        () => {
          s.picked = r.recipe_id;
          go("recipe");
        },
        "recipe-option",
      );
      b.append(node("h2", r.name), note(`${r.cook_time_minutes}분`));
      nutrition(b, r.nutrition_per_serving);
      b.disabled = busy || active();
      list.append(b);
    }
    reason.append(
      node("h2", "추천 근거"),
      note(
        s.result.ai_explanation?.summary ||
          s.result.explanation ||
          "설명 미제공",
      ),
    );
    if ((s.result.excluded || []).length) {
      const details = node("details");
      details.append(node("summary", "제외된 후보"));
      for (const x of s.result.excluded)
        details.append(
          note(`${x.name}: ${(x.reasons || x.reason_codes || []).join(", ")}`),
        );
      reason.append(details);
    }
    layout.append(list, reason);
    root.append(layout);
  }
  function recipe(root) {
    const r = selectedRecipe();
    heading(
      root,
      r?.name || "추천 메뉴를 먼저 선택하세요",
      "조리 시작 전 재료·품질을 확인하세요.",
    );
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
        "위 수량은 참여자 기준입니다. 조리 시작 후 수량은 서버의 레시피 전체 g이므로 별도로 확인하세요.",
      ),
    );
    nutrition(p, r.nutrition_per_serving);
    quality(p, r);
    p.append(
      note("레시피 상세·조리 단계 API가 없어 순서 안내는 제공하지 않습니다."),
    );
    const confirm = checkbox("실제 재료·분량 및 데이터 품질을 확인했습니다");
    const start = button(
      "조리 시작",
      () =>
        run(async () => {
          await api.startCooking(
            r.recipe_id,
            confirm.input.checked,
            conditions,
          );
          current = "cook";
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
      "조리 변경 및 질문",
      "서버가 검증한 조리 세션의 재료 전체 g을 수정합니다.",
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
    p.append(note(`세션 ${c.id} · ${c.status} · 전체 레시피 기준 g`));
    quality(p, c);
    nutrition(p, c.nutrition_per_serving);
    for (const i of c.ingredients)
      p.append(
        note(
          `${s.ingredientNames[i.ingredient_id] || `재료 ID ${i.ingredient_id}`}: ${i.quantity_g}g`,
        ),
      );
    if (active()) {
      const form = node("form"),
        name = field("변경할 정확한 재료명"),
        qty = field("레시피 전체 사용량 (g) · 0은 제거", {
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
      p.append(
        form,
        note("레시피 분량 자동 조정·재고 차감·취소 API는 없습니다."),
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
      p.append(
        qform,
        answer,
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
    for (const i of s.cooking.ingredients)
      p.append(
        note(
          `${s.ingredientNames[i.ingredient_id] || `재료 ID ${i.ingredient_id}`}: ${i.quantity_g}g`,
        ),
      );
    nutrition(p, s.cooking.nutrition_per_serving);
    quality(p, s.cooking);
    const form = node("form"),
      entries = s.household.members
        .filter((m) => s.selected.includes(m.id))
        .map((m) => {
          const f = field(`${m.name} 실제 섭취량 (인분)`, {
            type: "number",
            value: 1,
            min: 0,
            max: 10,
            step: 0.25,
          });
          form.append(f.wrap);
          return { member: m, input: f.input };
        }),
      confirmed = checkbox("실제 사용량과 개인별 섭취량을 확인했습니다");
    const save = button("서버에 식사 기록 저장", () => {}, "primary");
    save.type = "submit";
    form.append(
      note(
        "먹지 않은 사람은 0인분으로 입력하세요. 서버에는 실제 식사한 사람만 전송합니다.",
      ),
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
        );
        current = "report";
      });
    };
    p.append(form);
  }
  function inventory(root) {
    heading(
      root,
      "냉장고 재고",
      "기존 JSON 재고와 추천용 SQLite 재고는 서로 동기화되지 않습니다.",
    );
    const p = panel(root, "기존 재고 조회 · /api/inventory");
    p.append(button("재고 새로고침", () => run(loadInventory)));
    if (inventoryError) p.append(note(inventoryError));
    if (s.legacyInventory)
      p.append(
        note(`재고 원본 갱신일: ${s.legacyInventory.updated_at || "미제공"}`),
      );
    for (const i of s.legacyInventory?.items || []) {
      const row = node("article", undefined, "inventory-item");
      row.append(
        node("h2", i.name),
        note(
          `${i.quantity} ${i.unit} · ${i.location || "위치 미제공"} · 소비기한 ${i.use_by || "미입력"}`,
        ),
        button("영양 조회", () => showNutrition(i.name)),
      );
      p.append(row);
    }
    if (!s.legacyInventory)
      p.append(note("재고를 조회하지 못했습니다. 새로고침해 주세요."));
    const sqlite = panel(root, "추천용 SQLite 재고");
    if (inventoryMessage) {
      const confirmation = note(inventoryMessage);
      confirmation.setAttribute("role", "status");
      sqlite.append(confirmation);
    }
    sqlite.append(
      note("독립 재고 목록 API가 없습니다. 마지막 추천 응답의 스냅샷입니다."),
    );
    for (const i of s.result?.inventory_snapshot || [])
      sqlite.append(
        note(
          `${i.name}: ${i.quantity_g ?? "미확인"}g · ${i.expiry_date || i.use_by || "소비기한 미입력"}`,
        ),
      );
    const form = node("form"),
      name = field("등록할 정확한 재료명"),
      qty = field("측정 중량 (g)", {
        type: "number",
        min: 0.1,
        max: 1000000,
        step: 0.1,
      }),
      expiry = field("표시 소비기한 (선택)", { type: "date" }),
      confirm = checkbox("재료명·실제 중량을 확인했습니다"),
      save = button("SQLite 재고 등록", () => {}, "primary");
    save.type = "submit";
    form.append(name.wrap, qty.wrap, expiry.wrap, confirm.wrap, save);
    form.onsubmit = (e) => {
      e.preventDefault();
      run(async () => {
        if (
          !confirm.input.checked ||
          !Number.isFinite(Number(qty.input.value)) ||
          Number(qty.input.value) <= 0
        )
          throw Error("재료와 양을 확인해 주세요.");
        inventoryMessage = "";
        const saved = await api.addInventory(
          name.input.value,
          Number(qty.input.value),
          expiry.input.value,
        );
        inventoryMessage = `재고 항목 ${saved.id}을 서버에 등록했습니다. 최신 재고는 추천을 다시 요청해 확인하세요.`;
        current = "inventory";
      });
    };
    sqlite.append(
      form,
      note(
        "등록 후 추천을 다시 요청해 최신 스냅샷을 확인하세요. 카메라 인식·재고 수정/삭제 API는 없습니다.",
      ),
    );
  }
  async function loadInventory() {
    try {
      await api.inventory();
      inventoryError = "";
    } catch (e) {
      inventoryError = e.message;
    }
  }
  function showNutrition(name) {
    run(async () => {
      const v = await api.nutrition(name);
      const body = $("#dialog-body");
      body.replaceChildren(node("h3", v.food_name));
      nutrition(body, v);
      body.append(
        note(`출처: ${v.source || "미제공"}`),
        note(v.match_note || ""),
        note(`기준량: ${v.serving_size || "미제공"}`),
      );
      $("#dialog-title").textContent = "영양 조회";
      $("#scenario-dialog").showModal();
    });
  }
  function report(root) {
    heading(
      root,
      "식사 기록 저장 확인",
      "과거 기록 조회·기간별 리포트 API는 아직 없습니다. 아래는 이 탭에서 서버 저장이 확인된 응답입니다.",
    );
    const p = panel(root, "이번 연결의 저장 결과");
    if (!s.receipts.length)
      p.append(
        note(
          "이 탭에서 확인한 저장 응답이 없습니다. 과거 기록이 없다는 의미는 아닙니다.",
        ),
      );
    for (const r of s.receipts) {
      const row = node("div", undefined, "scenario-log");
      row.append(node("h3", `${r.name || "식사"} · 기록 ID ${r.record_id}`));
      for (const i of r.items)
        row.append(
          note(
            `${s.household.members.find((m) => m.id === i.member_id)?.name}: ${i.servings}인분`,
          ),
        );
      row.append(note("서버 계산 1인분 영양"));
      nutrition(row, r.nutrition_per_serving);
      p.append(row);
    }
    p.append(
      note(
        "새로고침하면 저장 확인 목록은 사라지지만 서버의 기록은 유지됩니다. 보조식품 일정·목표 변경·하루 마감 API는 없습니다.",
      ),
    );
  }
  function profile(root) {
    heading(
      root,
      "가족 및 데이터 상태",
      "서버가 제공하는 가족을 조회합니다. 가입·초대·프로필 수정 API는 아직 없습니다.",
    );
    const p = panel(root, s.household.name);
    for (const m of s.household.members) {
      p.append(
        node("h3", m.name),
        note(`알레르기: ${m.allergies.join(", ") || "미설정"}`),
        note(
          `선호 설정: ${m.preferences.map((x) => preferenceLabels[x.value] || x.value).join(", ") || "미설정"}`,
        ),
      );
    }
    const d = panel(root, "데이터 출처 상태");
    d.append(button("상태 새로고침", () => run(() => api.refreshData())));
    for (const e of s.dataErrors) d.append(note(e));
    const releases = s.data?.releases,
      q = s.data?.quality;
    if (releases) {
      d.append(
        note(
          `영양 release: ${releases.current?.nutrition_release_id ?? "published 없음"} · 알레르겐 기준: ${releases.current?.allergen_reference_version_id ?? "published 없음"}`,
        ),
      );
      for (const r of releases.nutrition_releases || [])
        d.append(
          note(
            `${r.provider_name} / ${r.dataset_name} · ${r.status} · 승인 ${r.accepted_count} / 격리 ${r.quarantined_count}`,
          ),
        );
    }
    if (q) {
      d.append(
        note(
          `공식 식품 ${q.official_food_items}개 · 활성 매핑 ${q.active_mappings}개`,
        ),
      );
      for (const x of q.ingredients_by_source || [])
        d.append(note(`${x.source_type}: ${x.count}개`));
    }
    d.append(
      note(
        "ThinQ 동기화 상태·스냅샷 시각 API는 아직 없습니다. release 상태를 재고 동기화 상태로 해석하지 않습니다.",
      ),
    );
  }
  function render() {
    history.replaceState(
      null,
      "",
      `/${$("#app-shell").dataset.device}#${current}`,
    );
    for (const v of views) {
      v.replaceChildren();
      v.classList.toggle("active", v.dataset.view === current);
    }
    document.querySelectorAll("[data-view-link]").forEach((b) => {
      b.disabled = busy;
      b.classList.toggle("active", b.dataset.viewLink === current);
    });
    $("#demo-tools").disabled = busy;
    $("#household-settings").textContent = s.household?.name || "가족 조회";
    const root = views.find((v) => v.dataset.view === current);
    if (errorMessage) {
      const e = node("p", errorMessage, "scenario-warning");
      e.setAttribute("role", "alert");
      root.append(e);
    }
    if (!ready) {
      heading(
        root,
        "로컬 API 연결",
        errorMessage || "서버 데이터를 불러오는 중입니다.",
      );
      root.append(button("다시 연결", () => run(initialize), "primary"));
      return;
    }
    ({
      home,
      recommend: recommendation,
      recipe,
      cook,
      record,
      inventory,
      report,
      profile,
    })[current](root);
  }
  async function initialize() {
    await api.initialize();
    ready = true;
    await Promise.all([loadInventory(), api.refreshData()]);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-view-link]");
    if (b && !busy) {
      e.preventDefault();
      go(b.dataset.viewLink);
    }
  });
  window.addEventListener("hashchange", () => go(location.hash.slice(1)));
  $("#household-settings").onclick = () => go("profile");
  $("#notice-button").onclick = () => go("profile");
  $("#demo-tools").onclick = () =>
    run(async () => {
      if (!ready) return initialize();
      await Promise.all([loadInventory(), api.refreshData()]);
    });
  $("#dialog-close").onclick = () => $("#scenario-dialog").close();
  function setDevice(device) {
    $("#app-shell").dataset.device = device;
    document.documentElement.dataset.device = device;
    $("#device-label").textContent =
      device === "fridge" ? "냉장고 디스플레이" : "모바일 앱";
    document
      .querySelectorAll("[data-device-choice]")
      .forEach((b) =>
        b.setAttribute(
          "aria-pressed",
          String(b.dataset.deviceChoice === device),
        ),
      );
    $('meta[name="theme-color"]').content =
      device === "fridge" ? "#101719" : "#f7f7f8";
  }
  document.querySelectorAll("[data-device-choice]").forEach(
    (b) =>
      (b.onclick = () => {
        if (busy) return;
        setDevice(b.dataset.deviceChoice);
        render();
      }),
  );
  const explicit =
    location.pathname === "/fridge"
      ? "fridge"
      : location.pathname === "/mobile"
        ? "mobile"
        : null;
  setDevice(
    explicit ||
      (window.matchMedia("(max-width:660px)").matches ? "mobile" : "fridge"),
  );
  current = location.hash.slice(1) || "home";
  if (!views.some((v) => v.dataset.view === current)) current = "home";
  render();
  run(initialize);
})();
