/* Six scenario screens backed by the local Flask API. */
(() => {
  const api = KINIAPI.createClient(),
    s = api.state,
    $ = (q, r = document) => r.querySelector(q);
  const views = [...document.querySelectorAll("[data-view]")];
  const intakeDraft = {};
  let busy = false,
    errorMessage = "",
    errorStatus = 0,
    current = "home",
    lastAnswer = "",
    inventoryMessage = "",
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
    errorStatus = 0;
    document.body.setAttribute("aria-busy", "true");
    const controls = [...document.querySelectorAll("button,input,select")];
    const disabled = controls.map((n) => n.disabled);
    controls.forEach((n) => (n.disabled = true));
    try {
      await action();
    } catch (e) {
      errorStatus = e.status || 0;
      errorMessage =
        e.status === 403
          ? "이 가구의 재고 출고 또는 조리 완료 권한을 확인해 주세요. 세션이 만료됐다면 다시 로그인해 주세요."
          : e.status === 409 && e.code === "inventory_confirmation_required"
            ? `출고 확인 필요 · ${e.message}`
            : e.message;
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
          await api.login(
            name.input.value,
            password.input.value,
            Number(household.input.value),
          );
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
  function home(root) {
    root.classList.add("fridge-home");
    const header = node("div", undefined, "meal-greeting");
    header.append(
      node("h1", "오늘 저녁, 함께 준비할까요?"),
      node("p", "냉장고 재료부터 활용해요", "greeting-subtitle"),
    );
    root.append(header);
    const hero = node("section", undefined, "meal-hero");
    const art = node("div", undefined, "meal-hero-art");
    art.setAttribute("role", "img");
    art.setAttribute("aria-label", "두부와 버섯을 곁들인 밥, 소개용 이미지");
    const intro = node("div", undefined, "meal-hero-copy");
    intro.append(
      node("p", "우리 가족의 한 끼", "eyebrow"),
      node("h2", "냉장고 재료로\n따뜻한 한 끼"),
      note("보유 재료로 만들 수 있는 메뉴를 찾아보세요"),
      button("오늘의 메뉴 추천", () => go("recommend"), "primary hero-cta"),
      node("small", "음식 사진은 소개용 이미지입니다."),
    );
    hero.append(art, intro);
    root.append(hero);
    const dashboard = node("div", undefined, "home-dashboard");
    root.append(dashboard);
    const family = panel(dashboard, "누구와 드시나요?");
    family.classList.add("family-widget");
    const members = node("div", undefined, "members");
    s.household.members.forEach((m, i) => {
      const chosen = s.selected.includes(m.id);
      const b = button(
        "",
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
      b.append(
        node("span", ["👨", "👩", "🧒", "👵"][i % 4], "member-portrait"),
        node("span", m.name),
        node("span", "✓", "member-check"),
      );
      b.classList.toggle("selected", chosen);
      b.setAttribute("aria-pressed", String(chosen));
      b.setAttribute("aria-label", m.name);
      b.disabled = busy || active();
      members.append(b);
    });
    family.append(members, note(`이번 식사 ${s.selected.length}명 선택`));
    if (active())
      family.append(button("진행 중인 조리 보기", () => go("cook"), "outline"));
    const stock = panel(dashboard, "냉장고 속 재료");
    stock.classList.add("stock-widget");
    stock.append(button("재고 보기", () => go("inventory"), "text-button"));
    const ingredients =
      s.inventory?.items.filter((item) => item.quantity_g > 0) || [];
    ingredients.slice(0, 3).forEach((i, index) => {
      const row = node("div", undefined, "stock-row");
      row.append(
        node("span", ["◒", "◌", "◐"][index], "ingredient-symbol"),
        node("b", i.name),
        node("span", `${i.quantity_g} g`),
      );
      stock.append(row);
    });
    if (!ingredients.length)
      stock.append(
        note(
          s.inventory
            ? "조회된 재고가 없습니다."
            : "재고 화면에서 최신 수량을 확인해 주세요.",
        ),
      );
    const records = node("section", undefined, "meal-record-widget card");
    records.append(node("h2", "오늘의 식사 기록"));
    const row = node("div", undefined, "meal-status-row");
    const today = new Date().toLocaleDateString("sv-SE", {
      timeZone: "Asia/Seoul",
    });
    for (const [key, label] of [
      ["breakfast", "아침"],
      ["lunch", "점심"],
      ["dinner", "저녁"],
    ]) {
      const done = s.receipts.some(
        (r) => r.meal_type === key && r.saved_date === today,
      );
      const chip = node("div", undefined, "meal-status");
      chip.classList.toggle("done", done);
      chip.append(
        node("b", label),
        node("span", done ? "저장 확인" : "기록 확인 전"),
      );
      row.append(chip);
    }
    row.append(
      button(
        active() ? "먹은 식사 기록" : "식사 기록 보기",
        () => go(active() ? "record" : "report"),
        "outline",
      ),
    );
    records.append(
      row,
      note(
        "이 연결에서 저장한 결과만 표시합니다. 이전 기록 조회는 준비 중입니다.",
      ),
    );
    root.append(records);
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
          existingOutflows = "";
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
            value: intakeDraft[`${s.cooking.id}:${m.id}`] ?? 1,
            min: 0,
            max: 10,
            step: 0.25,
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
          !!batch?.started,
          existingOutflows
            .split(",")
            .map((id) => id.trim())
            .filter(Boolean)
            .map((id) => (/^\d+$/.test(id) ? Number(id) : NaN)),
        );
        current = "report";
        await api.inventory();
      });
    };
    intake.append(form);
  }
  function inventory(root) {
    heading(root, "냉장고 재고", "로그인한 가구의 재고입니다.");
    const p = panel(root, "가구 재고 목록");
    p.append(button("재고 새로고침", () => run(() => api.inventory())));
    p.append(
      button("재고에 대해 질문", () => {
        const body = $("#dialog-body");
        body.replaceChildren();
        $("#dialog-title").textContent = "재고 질문";
        const q = field("질문"),
          form = node("form"),
          answer = node("p");
        const send = button("질문 보내기", () => {}, "primary");
        send.type = "submit";
        form.append(q.wrap, send);
        form.onsubmit = (e) => {
          e.preventDefault();
          run(async () => {
            answer.textContent = (await api.chat(q.input.value)).answer;
          });
        };
        body.append(
          note("재고 질문 도우미는 별도의 예시 재고를 사용합니다."),
          form,
          answer,
        );
        $("#scenario-dialog").showModal();
      }),
    );
    if (s.inventory)
      p.append(
        note(
          `재고 버전 ${s.inventory.inventory_version} · 확인 대기 ${s.inventory.pending_event_count}건`,
        ),
      );
    for (const i of s.inventory?.items || []) {
      const row = node("article", undefined, "inventory-item");
      row.append(
        node("h2", i.name),
        note(
          `${i.quantity_g == null ? "중량 미확인" : `${i.quantity_g}g`} · 소비기한 ${i.expiry_date || "미입력"} · ${i.data_status === "verified" ? "등록 확인" : "재료 정보 확인 필요"}`,
        ),
        button("영양 조회", () => showNutrition(i.name)),
      );
      p.append(row);
    }
    if (!s.inventory) p.append(note("재고를 새로고침해 주세요."));
    else if (!s.inventory.items.length)
      p.append(note("등록된 재고가 없습니다."));
    const sqlite = panel(root, "수동 입고");
    if (inventoryMessage) {
      const confirmation = note(inventoryMessage);
      confirmation.setAttribute("role", "status");
      sqlite.append(confirmation);
    }
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
      save = button("입고 확정", () => {}, "primary");
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
        inventoryMessage = `재고 항목 ${saved.inventory_item_id}을 등록했습니다.`;
        await api.inventory();
        current = "inventory";
      });
    };
    sqlite.append(form);
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
    p.append(
      note(
        `가구 권한 · ${{ owner: "소유자", editor: "편집", viewer: "조회" }[s.session.role] || s.session.role}`,
      ),
    );
    p.append(
      button("다시 로그인", () =>
        run(() => {
          api.clearSession();
          $("#dialog-body").replaceChildren();
          lastAnswer = inventoryMessage = "";
        }),
      ),
    );
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
    const now = new Date();
    $("#home-time").textContent = now.toLocaleTimeString("ko-KR", {
      timeZone: "Asia/Seoul",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    $("#home-date").textContent = now.toLocaleDateString("ko-KR", {
      timeZone: "Asia/Seoul",
      month: "long",
      day: "numeric",
      weekday: "long",
    });
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
      b.disabled = busy || !s.session;
      b.classList.toggle("active", b.dataset.viewLink === current);
    });
    $("#demo-tools").disabled = busy || !s.session;
    $("#household-settings").textContent = s.session
      ? s.household.name
      : "가족 로그인";
    const root = views.find((v) => v.dataset.view === current);
    if (errorMessage) {
      const e = node("p", errorMessage, "scenario-warning");
      e.setAttribute("role", "alert");
      e.dataset.httpStatus = String(errorStatus);
      root.append(e);
      if (errorStatus === 403)
        root.append(button("다시 로그인", () => run(() => api.clearSession())));
    }
    if (!s.session) {
      loginView(root);
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
      await Promise.all([api.inventory(), api.refreshData()]);
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
})();
