/* Same-origin local API boundary. No demo fallback or external model calls. */
(() => {
  class APIError extends Error {
    constructor(message, status = 0, details = {}) {
      super(message);
      this.status = status;
      this.code = details.code;
      this.ingredients = details.ingredients;
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
      inventory: null,
      session: null,
      outflows: null,
      data: null,
      dataErrors: [],
      ingredientNames: {},
    };
    let seen = [];
    let token = null;
    let submittedMeal = null;
    let incoming = null;
    function eventKey() {
      const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    async function request(path, body, authenticated = false) {
      if (authenticated && (!token || Date.now() >= state.session.expiresAt)) {
        clearSession();
        throw new APIError("다시 로그인해 주세요.", 401);
      }
      const c = new AbortController(),
        timer = setTimeout(() => c.abort(), 20000);
      try {
        const r = await fetchImpl(path, {
          method: body === undefined ? "GET" : "POST",
          headers: {
            Accept: "application/json",
            ...(authenticated ? { Authorization: `Bearer ${token}` } : {}),
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
          },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
          signal: c.signal,
          cache: "no-store",
        });
        if (authenticated && r.status === 401) clearSession();
        let v;
        try {
          v = await r.json();
        } catch {
          throw new APIError("서버 응답을 읽을 수 없습니다.", r.status);
        }
        if (!r.ok)
          throw new APIError(
            v.error || "서버 요청에 실패했습니다.",
            r.status,
            v,
          );
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
    function clearSession() {
      token = null;
      state.session = null;
      state.inventory = null;
    }
    async function login(login_name, password, household_id) {
      if (
        state.cooking?.status === "active" &&
        state.household.id !== household_id
      )
        throw new APIError(
          "진행 중인 조리를 이어가려면 같은 가구로 로그인해 주세요.",
        );
      const v = await request("/api/auth/login", {
        login_name,
        password,
        household_id,
      });
      state.session = { role: v.role, expiresAt: v.expires_at * 1000 };
      token = v.access_token;
      let members;
      try {
        members = (
          await request(`/api/members?household_id=${v.household_id}`, undefined, true)
        ).members;
      } catch (error) {
        clearSession();
        throw error;
      }
      if (state.household?.id !== v.household_id) {
        invalidate();
        state.selected = members.map((m) => m.id);
        state.receipts = [];
        state.ingredientNames = {};
        state.cooking = null;
        state.outflows = null;
        incoming = null;
      }
      state.household = {
        id: v.household_id,
        name: `가구 ${v.household_id}`,
        members,
      };
    }
    async function inventory() {
      state.inventory = await request("/api/data/inventory", undefined, true);
      for (const item of state.inventory.items)
        state.ingredientNames[item.ingredient_id] = item.name;
      return state.inventory;
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
      state.outflows = null;
      submittedMeal = null;
      return state.cooking;
    }
    async function changeIngredient(ingredient, quantity) {
      if (state.cooking?.status !== "active")
        throw new APIError("진행 중인 조리가 없습니다.");
      if (state.outflows?.started)
        throw new APIError(
          "출고를 시작한 뒤에는 조리 재료를 변경할 수 없습니다.",
        );
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
      state.outflows = null;
      return state.cooking;
    }
    async function prepareOutflows() {
      if (state.outflows?.started) return;
      state.outflows = null;
      const draft = await request(`/api/cooking-sessions/${state.cooking.id}`);
      if (draft.status !== "active")
        throw new APIError("진행 중인 조리가 없습니다.");
      state.cooking = draft;
      await inventory();
      const entries = [];
      for (const ingredient of draft.ingredients) {
        const name =
          state.ingredientNames[ingredient.ingredient_id] ||
          `재료 ${ingredient.ingredient_id}`;
        const lots = state.inventory.items.filter(
          (item) =>
            item.ingredient_id === ingredient.ingredient_id &&
            item.quantity_g > 0,
        );
        lots.sort(
          (a, b) =>
            (a.expiry_date || "9999").localeCompare(b.expiry_date || "9999") ||
            a.id - b.id,
        );
        let remaining = ingredient.quantity_g;
        for (const lot of lots) {
          const quantity = Math.min(remaining, lot.quantity_g);
          entries.push({
            clientId: eventKey(),
            name,
            expiry: lot.expiry_date,
            eventId: null,
            applied: false,
            body: {
              ingredient_id: ingredient.ingredient_id,
              inventory_item_id: lot.id,
              quantity_g: quantity,
            },
          });
          remaining -= quantity;
          if (remaining <= 1e-9) break;
        }
        if (remaining > 1e-9)
          throw new APIError(
            `${name} 재고가 ${Number(remaining.toFixed(6))}g 부족합니다.`,
            409,
          );
      }
      if (entries.length > 100)
        throw new APIError("한 번에 연결할 수 있는 출고는 최대 100건입니다.");
      state.outflows = { entries, started: false };
    }
    async function confirmOutflows() {
      const batch = state.outflows;
      if (!batch?.entries.length)
        throw new APIError("출고할 재고를 먼저 확인해 주세요.");
      batch.started = true;
      try {
        for (const entry of batch.entries) {
          if (entry.applied) continue;
          if (!entry.eventId) {
            const v = await request(
              "/api/data/inventory/events",
              {
                source: "manual",
                action: "stock_out",
                client_event_id: entry.clientId,
                detected_label: entry.name,
              },
              true,
            );
            entry.eventId = v.event_id;
          }
          const v = await request(
            `/api/data/inventory/events/${entry.eventId}/confirm`,
            entry.body,
            true,
          );
          if (v.status !== "applied")
            throw new APIError("재고 출고 확정을 확인해 주세요.", 409);
          entry.applied = true;
        }
      } finally {
        state.inventory = null;
      }
    }
    async function complete(items, confirmed, manual, existingIds = []) {
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
      const entries = state.outflows?.entries || [];
      if (
        manual &&
        (!entries.length || entries.some((entry) => !entry.applied))
      )
        throw new APIError("이번 조리의 수동 출고를 먼저 확정해 주세요.", 409, {
          code: "inventory_confirmation_required",
        });
      if (!manual && state.outflows?.started)
        throw new APIError(
          "이 조리에 이미 시작한 수동 출고를 이어서 확정해 주세요.",
        );
      const eventIds = manual
        ? entries.map((entry) => entry.eventId)
        : existingIds;
      if (
        !eventIds.length ||
        eventIds.length > 100 ||
        new Set(eventIds).size !== eventIds.length ||
        eventIds.some((id) => !Number.isSafeInteger(id) || id < 1)
      )
        throw new APIError("확정 출고 ID를 확인해 주세요.", 409, {
          code: "inventory_confirmation_required",
        });
      const path = `/api/cooking-sessions/${state.cooking.id}`;
      let v;
      if (submittedMeal) {
        const draft = await request(path);
        if (draft.status === "completed") {
          v = {
            record_id: draft.meal_record_id,
            nutrition_per_serving: draft.nutrition_per_serving,
            ingredients: draft.ingredients,
          };
        }
      }
      if (!v) {
        submittedMeal = eaten;
        v = await request(
          `${path}/complete`,
          {
            confirmed: true,
            eaten_at: new Date().toISOString(),
            items: eaten,
            inventory_event_ids: eventIds,
          },
          true,
        );
      }
      state.cooking = {
        ...state.cooking,
        status: "completed",
        meal_record_id: v.record_id,
      };
      state.receipts.push({
        ...v,
        meal_type: state.meal?.meal_type,
        saved_date: new Date().toLocaleDateString("sv-SE", {
          timeZone: "Asia/Seoul",
        }),
        items: submittedMeal,
        name: state.result.recommendations.find(
          (r) => r.recipe_id === state.picked,
        )?.name,
      });
      state.meal = null;
      submittedMeal = null;
      state.inventory = null;
      // A new recommendation must supply a fresh inventory snapshot after completion.
      if (state.result) state.result.inventory_snapshot = [];
      return v;
    }
    return {
      state,
      request,
      login,
      clearSession,
      refreshData,
      recommend,
      startCooking,
      changeIngredient,
      complete,
      prepareOutflows,
      confirmOutflows,
      inventory,
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
        const body = {
          ingredient_id: n.ingredient.ingredient_id,
          quantity_g: n.conversion.quantity_g,
          expiry_date: expiry_date || null,
        };
        const key = JSON.stringify([name, body]);
        if (incoming && incoming.key !== key)
          throw new APIError(
            "이전에 요청한 입고의 처리 여부를 먼저 확인해 주세요.",
          );
        incoming ||= { key, clientId: eventKey() };
        const event = await request(
          "/api/data/inventory/events",
          {
            source: "manual",
            action: "stock_in",
            client_event_id: incoming.clientId,
            detected_label: name,
          },
          true,
        );
        const v = await request(
          `/api/data/inventory/events/${event.event_id}/confirm`,
          body,
          true,
        );
        incoming = null;
        invalidate();
        state.inventory = null;
        return v;
      },
    };
  }
  globalThis.KINIAPI = { createClient, APIError };
})();
