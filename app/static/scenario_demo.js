/* Six scenario fixtures and transitions. Synthetic, in-memory, never sent off-device.
 * This is a UI mock provider, not the production nutrition or safety engine. */
(() => {
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const nutrients = ["energy_kcal", "protein_g", "carbohydrate_g", "fat_g", "sodium_mg", "vegetable_g"];
  const round = (n) => Math.round(n * 100) / 100;
  const dateKey = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const catalog = [
    { id: "spinach", name: "시금치", energy_kcal: 23, protein_g: 2.9, carbohydrate_g: 3.6, fat_g: .4, sodium_mg: 79, vegetable_g: 100 },
    { id: "tofu", name: "두부", energy_kcal: 76, protein_g: 8, carbohydrate_g: 1.9, fat_g: 4.8, sodium_mg: 7, vegetable_g: 0 },
    { id: "egg", name: "달걀", aliases: ["계란"], energy_kcal: 143, protein_g: 12.6, carbohydrate_g: .7, fat_g: 9.5, sodium_mg: 142, vegetable_g: 0 },
    { id: "chicken", name: "닭가슴살", energy_kcal: 165, protein_g: 31, carbohydrate_g: 0, fat_g: 3.6, sodium_mg: 74, vegetable_g: 0 },
    { id: "rice", name: "밥", energy_kcal: 130, protein_g: 2.7, carbohydrate_g: 28, fat_g: .3, sodium_mg: 1, vegetable_g: 0 },
    { id: "mushroom", name: "버섯", energy_kcal: 22, protein_g: 3.1, carbohydrate_g: 3.3, fat_g: .3, sodium_mg: 5, vegetable_g: 100 },
    { id: "soy", name: "간장", energy_kcal: 53, protein_g: 8, carbohydrate_g: 5, fat_g: 0, sodium_mg: 5500, vegetable_g: 0 },
    { id: "carrot", name: "당근", energy_kcal: 41, protein_g: .9, carbohydrate_g: 10, fat_g: .2, sodium_mg: 69, vegetable_g: 100 },
  ];
  const recipes = [
    { id: 1, name: "시금치 두부 달걀국", minutes: 20, visual: "soup", taste: "담백", ingredients: [["spinach",150],["tofu",200],["egg",100],["soy",8]], steps: ["시금치를 씻고 두부를 먹기 좋은 크기로 썹니다.", "물을 끓인 뒤 두부와 간장을 넣습니다.", "시금치와 푼 달걀을 넣고 충분히 익힙니다.", "실제 사용량과 간을 확인하고 조리를 마칩니다."] },
    { id: 2, name: "닭가슴살 시금치 무침", minutes: 15, visual: "salad", taste: "담백", ingredients: [["chicken",200],["spinach",150],["soy",8]], steps: ["시금치를 씻고 데칩니다.", "닭가슴살을 속까지 충분히 익힙니다.", "먹기 좋게 썰어 간장과 함께 버무립니다."] },
    { id: 3, name: "시금치 달걀 볶음밥", minutes: 15, visual: "rice", taste: "고소", ingredients: [["rice",300],["spinach",100],["egg",100],["soy",8]], steps: ["시금치를 씻고 잘게 썹니다.", "달걀과 시금치를 충분히 익힙니다.", "밥과 간장을 넣고 함께 볶습니다."] },
    { id: 4, name: "두부 버섯 덮밥", minutes: 20, visual: "rice", taste: "담백", ingredients: [["rice",300],["tofu",200],["mushroom",150],["soy",8]], steps: ["두부와 버섯을 손질합니다.", "두부와 버섯을 간장과 함께 익힙니다.", "따뜻한 밥 위에 올립니다."] },
    { id: 5, name: "당근 달걀밥", minutes: 15, visual: "rice", taste: "고소", ingredients: [["rice",300],["carrot",120],["egg",100],["soy",8]], steps: ["당근을 잘게 썹니다.", "달걀과 당근을 충분히 익힙니다.", "밥과 간장을 넣어 볶습니다."] },
    { id: 6, name: "닭가슴살 버섯볶음", minutes: 25, visual: "salad", taste: "담백", ingredients: [["chicken",200],["mushroom",150],["carrot",100],["soy",8]], steps: ["닭가슴살과 채소를 손질합니다.", "닭가슴살을 충분히 익힙니다.", "채소와 간장을 넣고 함께 볶습니다."] },
  ].map((r) => ({ ...r, servings: 2, ingredients: r.ingredients.map(([id, grams]) => ({ id, grams })) }));
  function createModel({ now = () => new Date() } = {}) {
    const today = () => dateKey(now());
    const daysFromNow = (days) => { const d = new Date(now()); d.setDate(d.getDate() + days); return dateKey(d); };
    const seedHousehold = { name: "김하늘 가족", configured: false, principle: "재고 우선", members: [
      { id: 1, name: "하늘", goal: "단백질 관리", excluded: [], usual: 1, diabetesStatus: "unspecified", mealPrecautions: "" },
      { id: 2, name: "도윤", goal: "나트륨 관리", excluded: [], usual: 1, diabetesStatus: "unspecified", mealPrecautions: "" },
      { id: 3, name: "지아", goal: "영양 균형", excluded: [], usual: .5, diabetesStatus: "unspecified", mealPrecautions: "" },
    ] };
    const state = {
      household: clone(seedHousehold), selected: [1, 2],
      inventory: catalog.map((item, i) => ({ uid: i + 1, ingredient: item.id, name: item.name, grams: item.id === "soy" ? 100 : item.id === "spinach" ? 200 : 800, location: "냉장실", useBy: daysFromNow(item.id === "spinach" ? 1 : 5), confirmed: true })),
      inventoryUpdated: now().toISOString(), conditions: { method: "balance", time: 30, taste: "모두", purchase: false, meal: "dinner" },
      result: null, picked: null, cooking: null, records: [], supplements: [], nextGoal: "영양 균형", closedDays: [],
    };
    let revision = 0;
    let serial = 100;
    const fail = (message, code = "validation_error") => { const e = new Error(message); e.code = code; throw e; };
    const finite = (value, min, max) => { const n = Number(value); if (value === "" || value === null || !Number.isFinite(n) || n < min || n > max) fail(`수량은 ${min}~${max} 범위의 숫자로 입력해 주세요.`); return n; };
    const ingredient = (id) => catalog.find((item) => item.id === id);
    const members = () => state.household.members.filter((m) => state.selected.includes(m.id));
    const invalidate = () => { revision++; state.result = null; state.picked = null; };
    const usable = (row) => row.confirmed && row.grams > 0 && (!row.useBy || row.useBy >= today());
    const availability = (id) => state.inventory.filter((row) => row.ingredient === id && usable(row)).reduce((sum, row) => sum + row.grams, 0);
    const nutrition = (items, servings) => Object.fromEntries(nutrients.map((n) => [n, round(items.reduce((sum, item) => {
      const data = ingredient(item.id); if (!data) fail("영양정보가 없는 재료는 계산할 수 없습니다."); return sum + data[n] * item.grams / 100;
    }, 0) / servings)]));
    const requireFree = () => { if (state.cooking && state.cooking.status !== "completed") fail("진행 중인 조리를 먼저 마치거나 취소해 주세요."); };
    function validateItems(items, people, allowMissing = false) {
      if (!items.length) fail("최소 한 가지 재료가 필요합니다.");
      for (const item of items) {
        finite(item.grams, .01, 10000);
        const data = ingredient(item.id); if (!data) fail("재료·영양정보 확인이 필요합니다.");
        if (people.some((m) => m.excluded.includes(data.name) || (data.aliases || []).some((alias) => m.excluded.includes(alias)))) fail(`${data.name}: 설정된 제외 재료입니다.`);
        if (!allowMissing && availability(item.id) + .001 < item.grams) fail(`${data.name}: 확인된 재고가 부족합니다.`);
      }
    }
    function getRecipe(id) { const recipe = recipes.find((r) => r.id === Number(id)); if (!recipe) fail("메뉴를 찾지 못했습니다."); return recipe; }
    function getCooking() { if (!state.cooking || state.cooking.status !== "active") fail("진행 중인 조리가 없습니다."); return state.cooking; }
    const api = {
      snapshot: () => clone(state), catalog: () => clone(catalog), recipes: () => clone(recipes), today,
      saveHousehold(profile) {
        requireFree();
        const name = String(profile.name || "").trim();
        if (!name || name.length > 40 || !Array.isArray(profile.members) || !profile.members.length || profile.members.length > 8) fail("가족 이름과 1~8명의 구성원을 입력해 주세요.");
        const updated = profile.members.map((m, i) => {
          const label = String(m.name || "").trim(); if (!label || label.length > 20) fail("구성원 이름은 1~20자로 입력해 주세요.");
          const diabetesStatus = m.diabetesStatus ?? "unspecified";
          const mealPrecautions = String(m.mealPrecautions ?? "").trim();
          if (!["unspecified", "no", "yes"].includes(diabetesStatus) || mealPrecautions.length > 200) fail("건강정보 선택값과 식사 주의사항 길이를 확인해 주세요.");
          return { id: i + 1, name: label, goal: String(m.goal || "영양 균형"), excluded: (m.excluded || []).map((v) => String(v).trim()).filter(Boolean), usual: finite(m.usual ?? 1, .25, 3), diabetesStatus, mealPrecautions };
        });
        if (new Set(updated.map((m) => m.name)).size !== updated.length) fail("구성원 이름은 중복되지 않게 입력해 주세요.");
        if (state.records.length) fail("식사 기록이 있는 동안에는 구성원을 변경할 수 없습니다. 데모 초기화 후 다시 설정해 주세요.");
        state.household = { name, members: updated, principle: profile.principle || "재고 우선", configured: true };
        state.selected = updated.map((m) => m.id); invalidate(); return api.snapshot();
      },
      loadExampleHousehold() { requireFree(); if (state.records.length) fail("기록을 유지하려면 현재 가족을 사용하세요. 처음부터 보려면 초기화해 주세요."); state.household = { ...clone(seedHousehold), configured: true }; state.selected = [1,2]; invalidate(); return api.snapshot(); },
      selectMembers(ids) {
        requireFree(); const unique = [...new Set(ids)];
        if (unique.some((id) => !state.household.members.some((m) => m.id === id))) fail("가족 구성원을 확인해 주세요.");
        state.selected = unique; invalidate(); return api.snapshot();
      },
      setConditions(conditions) { requireFree(); state.conditions = { ...state.conditions, ...conditions, time: finite(conditions.time ?? state.conditions.time, 5, 120) }; invalidate(); return api.snapshot(); },
      recommend({ more = false, scenario = "success" } = {}) {
        requireFree(); if (!members().length) fail("이번 식사 참여자를 한 명 이상 선택해 주세요.");
        const previous = more && state.result?.seen ? state.result.seen : [];
        state.result = null; state.picked = null;
        const errors = { api_error: "AI 연결 오류로 추천을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.", privacy_blocked: "개인정보가 포함된 요청은 전송하지 않았습니다. 입력 내용을 확인해 주세요.", stale_unvalidated: "오래된 결과입니다. 현재 조건으로 다시 추천해 주세요." };
        if (errors[scenario]) { state.result = { status: scenario, message: errors[scenario], items: [], revision }; return api.snapshot(); }
        const excluded = [];
        let candidates = recipes.filter((r) => {
          if (previous.includes(r.id)) return false;
          try {
            if (r.minutes > state.conditions.time) throw new Error("조리 시간 조건");
            if (state.conditions.method === "concept" && state.conditions.taste !== "모두" && r.taste !== state.conditions.taste) throw new Error("선택한 맛 조건");
            validateItems(r.ingredients.map((i) => ({ ...i, grams: i.grams * members().length / r.servings })), members(), state.conditions.purchase);
            return true;
          } catch (e) { excluded.push({ name: r.name, reason: e.message }); return false; }
        });
        if (scenario === "no_safe_recommendation") candidates = [];
        const healthWarnings = members().some((m) => m.diabetesStatus === "yes" || m.mealPrecautions)
          ? ["건강상 주의사항이 등록된 참여자가 있습니다. 이 데모는 해당 건강 조건에 맞춘 식단을 검증하지 않습니다."] : [];
        const page = candidates.slice(0,3).map((r) => ({ ...clone(r), nutrition: nutrition(r.ingredients, r.servings), reasons: ["예시 재고·참여자 제외 조건을 적용한 화면 시연 후보입니다.", `${r.minutes}분 · ${r.taste}한 메뉴 예시`, `다음 관리 목표: ${state.nextGoal}`], data_quality: "demo_data", requires_confirmation: true, warnings: ["합성 데이터입니다. 실제 영양·알레르기 안전을 검증하지 않았습니다.", ...healthWarnings] }));
        state.result = { status: page.length ? "recommendations_available" : "no_safe_recommendation", message: page.length ? "예시 후보를 확인해 주세요." : "조건에 맞는 새 후보가 없습니다. 조건을 바꿔 다시 찾아보세요.", items: page, excluded, revision, seen: [...previous, ...page.map((r) => r.id)] };
        return api.snapshot();
      },
      pickRecipe(id) { if (!state.result || state.result.revision !== revision || state.result.status !== "recommendations_available" || !state.result.items.some((r) => r.id === Number(id))) fail("현재 조건으로 메뉴를 다시 추천해 주세요."); state.picked = Number(id); return api.snapshot(); },
      startCooking(confirmed) {
        requireFree(); if (confirmed !== true) fail("데모 데이터와 확인 필요 항목을 확인해 주세요.");
        if (!state.picked || state.result?.revision !== revision) fail("현재 추천에서 메뉴를 선택해 주세요.");
        const recipe = getRecipe(state.picked); const people = clone(members());
        const items = recipe.ingredients.map((item) => ({ ...item, grams: round(item.grams * people.length / recipe.servings) }));
        validateItems(items, people);
        state.cooking = { id: ++serial, recipe: clone(recipe), people, ingredients: items, servings: people.length, step: 0, changes: [], status: "active", nutrition: nutrition(items, people.length), reviewed: false };
        return api.snapshot();
      },
      changeIngredient(id, grams) {
        const c = getCooking(); const quantity = finite(grams, 0, 10000);
        if (!ingredient(id)) fail("등록된 기준 재료를 선택해 주세요.");
        const before = c.ingredients.find((i) => i.id === id)?.grams || 0;
        const proposed = c.ingredients.filter((i) => i.id !== id); if (quantity) proposed.push({ id, grams: quantity });
        validateItems(proposed, c.people);
        c.ingredients = proposed; c.nutrition = nutrition(proposed, c.servings); c.reviewed = false;
        c.changes.push({ name: ingredient(id).name, before, after: quantity }); return api.snapshot();
      },
      step(delta) { const c = getCooking(); c.step = Math.max(0, Math.min(c.recipe.steps.length - 1, c.step + delta)); return api.snapshot(); },
      cancelCooking() { getCooking(); state.cooking = null; return api.snapshot(); },
      reviewCooking() { const c = getCooking(); validateItems(c.ingredients, c.people); c.reviewed = true; return api.snapshot(); },
      ask(text) {
        const c = getCooking(); const question = String(text).trim(); if (!question || question.length > 500) fail("질문은 500자 이내로 입력해 주세요.");
        if (/끝|완료/.test(question)) return { intent: "complete", answer: "조리 중 변경된 재료와 분량을 한 번 확인한 뒤 섭취 기록으로 이동해 주세요." };
        const named = catalog.find((i) => question.includes(i.name) || (i.aliases || []).some((a) => question.includes(a)));
        const amount = question.match(/(\d+(?:\.\d+)?)\s*g/i);
        if (named && (/빼|제외|넣|추가|줄|변경/.test(question))) {
          const grams = /빼|제외/.test(question) ? 0 : amount ? Number(amount[1]) : null;
          return { intent: "change", ingredient: named.id, grams, answer: grams === null ? "단위가 불확실합니다. 실제 중량을 g으로 입력한 뒤 변경안을 확인해 주세요." : `${named.name} ${grams}g 변경안입니다. 아래 변경 폼에서 확인해야 반영됩니다.` };
        }
        return { intent: "question", answer: /짜|간/.test(question) ? "데모 안내: 간장 등 조미료를 조절하려면 실제 중량을 확인해 변경 폼에 입력해 주세요." : /씻|세척/.test(question) ? "데모 안내: 제품 표시의 세척·조리 방법을 확인해 주세요. 현재 레시피와 사용 재료는 왼쪽에서 확인할 수 있습니다." : `데모 안내: ${c.recipe.name}의 조리법을 참고해 주세요. 재료 변경은 확인 후에만 적용됩니다.` };
      },
      saveRecord({ portions, confirmed, directRecipeId = null, method = "manual", eatenAt = null }) {
        if (confirmed !== true) fail("섭취량과 예시 영양정보를 확인해 주세요.");
        let c;
        if (directRecipeId !== null) {
          requireFree(); const r = getRecipe(directRecipeId); const people = clone(members()); if (!people.length) fail("식사 참여자를 선택해 주세요.");
          c = { recipe: clone(r), people, ingredients: clone(r.ingredients), servings: people.length, nutrition: nutrition(r.ingredients,r.servings), direct: true };
        } else { c = getCooking(); if (!c.reviewed) fail("조리 변경사항을 먼저 확인해 주세요."); validateItems(c.ingredients,c.people); }
        const ids = c.people.map((p) => p.id);
        if (!Array.isArray(portions) || portions.length !== ids.length || new Set(portions.map((p) => p.memberId)).size !== ids.length || portions.some((p) => !ids.includes(p.memberId))) fail("모든 참여자의 섭취량을 중복 없이 확인해 주세요.");
        const amounts = portions.map((p) => ({ memberId: p.memberId, servings: finite(p.servings,0,3) }));
        const total = amounts.reduce((sum,p) => sum+p.servings,0);
        if (!total) fail("적어도 한 명의 실제 섭취량을 입력해 주세요.");
        if (!c.direct && total > c.servings + .001) fail(`만든 양은 ${c.servings}인분입니다. 총 섭취량을 확인해 주세요.`);
        const eatenDate = eatenAt ? new Date(eatenAt) : now(); if (!Number.isFinite(eatenDate.getTime()) || eatenDate > now()) fail("식사 시각을 확인해 주세요.");
        const record = { id: ++serial, name: c.recipe.name, recipeId: c.recipe.id, date: dateKey(eatenDate), eatenAt: eatenDate.toISOString(), meal: state.conditions.meal, method,
          people: clone(c.people), ingredients: clone(c.ingredients), nutritionPerServing: clone(c.nutrition), portions: amounts.map((p) => ({ ...p, nutrition: Object.fromEntries(nutrients.map((n) => [n,round(c.nutrition[n]*p.servings)])) })), direct: !!c.direct, data_quality: "demo_data" };
        if (!c.direct) {
          for (const item of c.ingredients) {
            let remaining = item.grams;
            for (const row of state.inventory.filter((r) => r.ingredient === item.id && usable(r)).sort((a,b) => (a.useBy || "9999").localeCompare(b.useBy || "9999"))) {
              const used = Math.min(row.grams, remaining); row.grams = round(row.grams-used); remaining -= used;
            }
          }
          c.status = "completed"; c.recordId = record.id;
        }
        state.records.push(record); state.closedDays = state.closedDays.filter((day) => day !== record.date); state.inventoryUpdated = now().toISOString(); invalidate(); return api.snapshot();
      },
      saveInventory({ uid = null, name, grams, location, useBy = "", confirmed = true }) {
        const label = String(name || "").trim(); if (!label || label.length > 60) fail("재료명은 1~60자로 입력해 주세요.");
        if (useBy && (!/^\d{4}-\d{2}-\d{2}$/.test(useBy) || !Number.isFinite(Date.parse(useBy)) || new Date(useBy).toISOString().slice(0,10) !== useBy)) fail("소비기한 날짜를 확인해 주세요.");
        const data = catalog.find((i) => i.name === label || (i.aliases || []).includes(label));
        const existing = uid === null ? null : state.inventory.find((r) => r.uid === uid); if (uid !== null && !existing) fail("재고 항목을 찾지 못했습니다.");
        const row = { uid: existing?.uid || ++serial, ingredient: data?.id || null, name: data?.name || label, grams: finite(grams,0,100000), location: location || "냉장실", useBy, confirmed: !!data && confirmed === true };
        if (existing) Object.assign(existing,row); else state.inventory.push(row);
        state.inventoryUpdated = now().toISOString(); invalidate(); return api.snapshot();
      },
      recognition(kind) {
        const examples = { matched: { name: "당근", grams: 200, confidence: "높음", matched: true }, uncertain: { name: "버섯", grams: null, confidence: "확인 필요", matched: true }, unknown: { name: "냉장만두 한 봉지", grams: null, confidence: "기준 재료 미매칭", matched: false } };
        if (!examples[kind]) fail("인식 예시를 선택해 주세요."); return clone(examples[kind]);
      },
      report({ memberId = null, days = 7 } = {}) {
        const start = new Date(now()); start.setDate(start.getDate() - days + 1); const startKey = dateKey(start);
        const relevant = state.records.filter((r) => r.date >= startKey && r.date <= today());
        const totals = Object.fromEntries(nutrients.map((n) => [n,0]));
        const daily = Array.from({length:days},(_,i) => { const d=new Date(start); d.setDate(d.getDate()+i); return {date:dateKey(d),protein_g:0}; });
        let count=0;
        for (const r of relevant) for (const p of r.portions.filter((p) => memberId === null || p.memberId === memberId)) {
          if (p.servings > 0) count++;
          for(const n of nutrients) totals[n] = round(totals[n]+p.nutrition[n]);
          daily.find((d)=>d.date===r.date).protein_g += p.nutrition.protein_g;
        }
        return clone({ totals, daily, count, records: relevant.filter((r)=>memberId===null||r.portions.some((p)=>p.memberId===memberId && p.servings>0)), nextGoal:state.nextGoal });
      },
      addSupplement({ name, time }) { const label=String(name||"").trim(); if(!label || label.length>40 || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) fail("일정 이름과 시각을 확인해 주세요."); state.supplements.push({id:++serial,name:label,time,takenDates:[]}); return api.snapshot(); },
      toggleSupplement(id) { const item=state.supplements.find((s)=>s.id===id); if(!item) fail("일정을 찾지 못했습니다."); item.takenDates=item.takenDates.includes(today())?item.takenDates.filter((d)=>d!==today()):[...item.takenDates,today()]; return api.snapshot(); },
      setNextGoal(goal) { if(!["영양 균형","나트륨 관리","단백질 관리","채소 섭취"].includes(goal)) fail("목표를 선택해 주세요."); state.nextGoal=goal; invalidate(); return api.snapshot(); },
      closeDay() { if(!state.closedDays.includes(today())) state.closedDays.push(today()); return api.snapshot(); },
      dateKey, nutrition,
    };
    return api;
  }
  globalThis.KINIDemo = { createModel };
})();
