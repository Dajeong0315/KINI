/* Same-origin local API boundary. No demo fallback or external model calls. */
(() => {
  class APIError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.status = status;
    }
  }
  function createClient({
    fetchImpl = globalThis.fetch.bind(globalThis),
  } = {}) {
    const state = {
      household: null,
      selected: [],
      result: null,
      picked: null,
      cooking: null,
      meal: null,
      receipts: [],
      legacyInventory: null,
      data: null,
      dataErrors: [],
      ingredientNames: {},
    };
    let seen = [];
    async function request(path, body) {
      const c = new AbortController(),
        timer = setTimeout(() => c.abort(), 20000);
      try {
        const r = await fetchImpl(path, {
          method: body === undefined ? "GET" : "POST",
          headers: {
            Accept: "application/json",
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
          },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
          signal: c.signal,
          cache: "no-store",
        });
        let v;
        try {
          v = await r.json();
        } catch {
          throw new APIError("서버 응답을 읽을 수 없습니다.", r.status);
        }
        if (!r.ok)
          throw new APIError(v.error || "서버 요청에 실패했습니다.", r.status);
        return v;
      } catch (e) {
        if (e instanceof APIError) throw e;
        throw new APIError(
          e.name === "AbortError"
            ? "응답 시간이 초과되었습니다. 서버 상태를 확인해 주세요."
            : "로컬 서버에 연결할 수 없습니다. 다시 시도해 주세요.",
        );
      } finally {
        clearTimeout(timer);
      }
    }
    function unlocked() {
      if (state.cooking?.status === "active")
        throw new APIError("진행 중인 조리를 먼저 완료해 주세요.");
    }
    function invalidate() {
      state.result = null;
      state.picked = null;
      seen = [];
      state.meal = null;
    }
    function mealRequest(c) {
      if (!state.selected.length)
        throw new APIError("참여자를 한 명 이상 선택해 주세요.");
      return {
        household_id: state.household.id,
        participant_ids: [...state.selected],
        meal_type: c.meal_type || "dinner",
      };
    }
    async function initialize() {
      state.household = await request("/api/households/demo");
      const v = await request(
        `/api/members?household_id=${state.household.id}`,
      );
      state.household.members = v.members;
      state.selected = v.members.map((m) => m.id);
      return state;
    }
    async function refreshData() {
      const paths = ["/api/data-releases", "/api/data-quality/summary"],
        v = await Promise.allSettled(paths.map((p) => request(p)));
      state.dataErrors = v.flatMap((x, i) =>
        x.status === "rejected" ? [`${paths[i]}: ${x.reason.message}`] : [],
      );
      state.data = {
        releases: v[0].status === "fulfilled" ? v[0].value : null,
        quality: v[1].status === "fulfilled" ? v[1].value : null,
      };
      return state.data;
    }
    async function recommend(c, more = false) {
      unlocked();
      const prior =
        state.result?.recommendations?.map((r) => r.recipe_id) || [];
      state.result = null;
      state.picked = null;
      state.meal = null;
      seen = more ? [...new Set([...seen, ...prior])] : [];
      try {
        const v = await request("/api/recommendations", {
          ...mealRequest(c),
          cook_time_limit_minutes: c.cook_time_limit_minutes,
          purchase_allowed: !!c.purchase_allowed,
          excluded_ingredients: c.excluded_ingredients || [],
          excluded_recipe_ids: seen,
        });
        if (
          ![
            "recommendations_available",
            "no_safe_recommendation",
            "privacy_blocked",
            "api_error",
            "stale_unvalidated",
          ].includes(v.status) ||
          !Array.isArray(v.recommendations)
        )
          throw new APIError("추천 응답 계약을 확인해 주세요.");
        if (v.status !== "recommendations_available") v.recommendations = [];
        state.result = v;
        for (const i of v.inventory_snapshot || [])
          state.ingredientNames[i.ingredient_id] = i.name;
        return v;
      } catch (e) {
        state.result = {
          status: "api_error",
          message: e.message,
          recommendations: [],
          warnings: [],
          requires_confirmation: true,
        };
        throw e;
      }
    }
    async function startCooking(recipeId, confirmed, c) {
      unlocked();
      if (!confirmed) throw new APIError("재료와 데이터 상태를 확인해 주세요.");
      if (
        state.result?.status !== "recommendations_available" ||
        !state.result.recommendations.some((r) => r.recipe_id === recipeId)
      )
        throw new APIError("현재 추천 메뉴를 다시 선택해 주세요.");
      const current = await request(
        `/api/recommendations/${state.result.request_id}`,
      );
      if (current.status !== "recommendations_available") {
        state.result = { ...current, recommendations: [] };
        state.picked = null;
        throw new APIError("추천 정보가 변경되었습니다. 다시 추천해 주세요.");
      }
      if (!state.meal)
        state.meal = await request("/api/meal-sessions", mealRequest(c));
      state.cooking = await request("/api/cooking-sessions", {
        meal_session_id: state.meal.session_id,
        recommendation_request_id: state.result.request_id,
        recipe_id: recipeId,
      });
      state.picked = recipeId;
      return state.cooking;
    }
    async function changeIngredient(ingredient, quantity) {
      if (state.cooking?.status !== "active")
        throw new APIError("진행 중인 조리가 없습니다.");
      const n = Number(quantity);
      if (
        String(quantity).trim() === "" ||
        !Number.isFinite(n) ||
        n < 0 ||
        n > 10000
      )
        throw new APIError("0~10000g으로 입력해 주세요.");
      state.cooking = await request(
        `/api/cooking-sessions/${state.cooking.id}/ingredients`,
        { ingredient, quantity_g: n },
      );
      return state.cooking;
    }
    async function complete(items, confirmed) {
      if (!confirmed || state.cooking?.status !== "active")
        throw new APIError("조리 내용과 실제 섭취량을 확인해 주세요.");
      const ids = items.map((i) => i.member_id);
      if (
        new Set(ids).size !== ids.length ||
        ids.length !== state.selected.length ||
        items.some(
          (i) =>
            !state.selected.includes(i.member_id) ||
            !Number.isFinite(i.servings) ||
            i.servings < 0 ||
            i.servings > 10,
        )
      )
        throw new APIError("참여자별 0~10인분을 확인해 주세요.");
      const eaten = items.filter((i) => i.servings > 0);
      if (!eaten.length)
        throw new APIError("실제 식사한 참여자가 한 명 이상 필요합니다.");
      const v = await request(
        `/api/cooking-sessions/${state.cooking.id}/complete`,
        { confirmed: true, eaten_at: new Date().toISOString(), items: eaten },
      );
      state.cooking = {
        ...state.cooking,
        status: "completed",
        meal_record_id: v.record_id,
      };
      state.receipts.push({
        ...v,
        items: eaten,
        name: state.result.recommendations.find(
          (r) => r.recipe_id === state.picked,
        )?.name,
      });
      state.meal = null;
      return v;
    }
    return {
      state,
      request,
      initialize,
      refreshData,
      recommend,
      startCooking,
      changeIngredient,
      complete,
      selectMembers(ids) {
        unlocked();
        if (
          new Set(ids).size !== ids.length ||
          ids.some((id) => !state.household.members.some((m) => m.id === id))
        )
          throw new APIError("가족 구성원을 확인해 주세요.");
        state.selected = [...ids];
        invalidate();
      },
      invalidate() {
        unlocked();
        invalidate();
      },
      async inventory() {
        state.legacyInventory = await request("/api/inventory");
        return state.legacyInventory;
      },
      nutrition(name) {
        return request(`/api/nutrition?food_name=${encodeURIComponent(name)}`);
      },
      chat(question) {
        return request("/api/chat", { question });
      },
      question(question) {
        return request(`/api/cooking-sessions/${state.cooking.id}/questions`, {
          question,
        });
      },
      async addInventory(name, quantity, expiry_date) {
        unlocked();
        const n = await request("/api/inventory/normalize", {
          name,
          quantity,
          unit: "g",
        });
        if (!n.ingredient.ingredient_id || n.conversion.quantity_g == null)
          throw new APIError(
            "등록된 재료와 실제 중량을 확인해 주세요. 미매칭 재료 등록 API는 아직 없습니다.",
          );
        const v = await request("/api/inventory/items", {
          household_id: state.household.id,
          ingredient_id: n.ingredient.ingredient_id,
          name,
          quantity,
          unit: "g",
          expiry_date: expiry_date || null,
        });
        invalidate();
        return v;
      },
    };
  }
  globalThis.KINIAPI = { createClient, APIError };
})();
