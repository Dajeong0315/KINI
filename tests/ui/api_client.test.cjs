const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  vm = require("node:vm");
function client(handler) {
  const calls = [];
  const c = vm.createContext({
    AbortController,
    setTimeout,
    clearTimeout,
    crypto: require("node:crypto").webcrypto,
    fetch: async (path, options) => {
      calls.push({
        path,
        ...options,
        body: options.body ? JSON.parse(options.body) : undefined,
      });
      return handler(path, options);
    },
  });
  vm.runInContext(fs.readFileSync("app/static/api_client.js", "utf8"), c);
  return { api: c.KINIAPI.createClient(), calls };
}
const response = (body, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
});
const success = {
  request_id: 1,
  status: "recommendations_available",
  recommendations: [{ recipe_id: 2, name: "server recipe" }],
};
function seed(api) {
  api.state.household = { id: 1, members: [{ id: 3 }, { id: 4 }] };
  api.selectMembers([3, 4]);
}
test("login authenticates member lookup and clears a rejected session", async () => {
  for (const status of [200, 401, 403]) {
    const { api, calls } = client((path) =>
      path === "/api/auth/login"
        ? response({ access_token: "synthetic-token", household_id: 1,
            role: "owner", expires_at: Date.now() / 1000 + 3600 })
        : response({ members: [{ id: 3 }] }, status),
    );
    if (status === 200) {
      await api.login("owner", "synthetic-password", 1);
      assert.equal(api.state.household.members[0].id, 3);
    } else {
      await assert.rejects(() => api.login("owner", "synthetic-password", 1));
      assert.equal(api.state.session, null);
      await assert.rejects(() => api.inventory(), { status: 401 });
    }
    assert.equal(calls[1].headers.Authorization, "Bearer synthetic-token");
    assert.equal(calls[1].path, "/api/members?household_id=1");
  }
});
test("every terminal response removes candidates even if server includes stale items", async () => {
  for (const status of [
    "privacy_blocked",
    "api_error",
    "no_safe_recommendation",
    "stale_unvalidated",
  ]) {
    let fail = false;
    const { api } = client(() =>
      response(fail ? { ...success, status } : structuredClone(success)),
    );
    seed(api);
    await api.recommend({});
    api.state.picked = 2;
    fail = true;
    await api.recommend({});
    assert.equal(api.state.result.recommendations.length, 0);
    assert.equal(api.state.picked, null);
  }
});
test("network, malformed JSON, unknown status, and HTTP errors clear old selection", async () => {
  for (const failure of [
    () => {
      throw Error("offline");
    },
    () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw Error("bad JSON");
      },
    }),
    () => response({ status: "unknown", recommendations: [] }),
    () => response({ error: "bad request" }, 500),
  ]) {
    let fail = false;
    const { api } = client(() =>
      fail ? failure() : response(structuredClone(success)),
    );
    seed(api);
    await api.recommend({});
    api.state.picked = 2;
    fail = true;
    await assert.rejects(() => api.recommend({}));
    assert.equal(api.state.result.status, "api_error");
    assert.equal(api.state.result.recommendations.length, 0);
    assert.equal(api.state.picked, null);
  }
});
test("more requests carry exclusions and condition changes invalidate them", async () => {
  const { api, calls } = client(() => response(structuredClone(success)));
  seed(api);
  await api.recommend({});
  await api.recommend({}, true);
  assert.deepEqual(Array.from(calls[1].body.excluded_recipe_ids), [2]);
  api.invalidate();
  await api.recommend({});
  assert.deepEqual(Array.from(calls[2].body.excluded_recipe_ids), []);
});
test("stale revalidation prevents meal and cooking writes", async () => {
  const { api, calls } = client(() =>
    response({ ...success, status: "stale_unvalidated" }),
  );
  seed(api);
  api.state.result = structuredClone(success);
  await assert.rejects(() => api.startCooking(2, true, {}));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(api.state.result.recommendations.length, 0);
});
test("failed changes preserve verified draft; completion omits non-eaters and prevents duplicate writes", async () => {
  let fail = true;
  const { api, calls } = client((path) =>
    path === "/api/auth/login"
      ? response({
          access_token: "test-token",
          household_id: 1,
          role: "owner",
          expires_at: Date.now() / 1000 + 3600,
        })
      : path.startsWith("/api/members")
        ? response({ members: [{ id: 3 }, { id: 4 }] })
        : fail
          ? response({ error: "invalid change" }, 400)
          : response({
              record_id: 9,
              nutrition_per_serving: { protein_g: 10 },
            }),
  );
  await api.login("owner", "test-password", 1);
  seed(api);
  api.state.cooking = {
    id: 4,
    status: "active",
    ingredients: [{ ingredient_id: 1, quantity_g: 100 }],
  };
  api.state.result = structuredClone(success);
  api.state.picked = 2;
  await assert.rejects(() => api.changeIngredient("bad", 3));
  assert.equal(api.state.cooking.ingredients[0].quantity_g, 100);
  fail = false;
  await api.complete(
    [
      { member_id: 3, servings: 0.5 },
      { member_id: 4, servings: 0 },
    ],
    true,
    false,
    [10],
  );
  assert.deepEqual(calls[3].body.items, [{ member_id: 3, servings: 0.5 }]);
  assert.deepEqual(calls[3].body.inventory_event_ids, [10]);
  assert.equal(calls[3].headers.Authorization, "Bearer test-token");
  assert.equal(api.state.cooking.status, "completed");
  await assert.rejects(() =>
    api.complete(
      [
        { member_id: 3, servings: 1 },
        { member_id: 4, servings: 1 },
      ],
      true,
    ),
  );
  assert.equal(calls.length, 4);
});
test("partial data state errors do not invent published references", async () => {
  const { api } = client((path) =>
    path.endsWith("summary")
      ? response({ error: "pending" }, 404)
      : response({
          current: { nutrition_release_id: null },
          nutrition_releases: [],
        }),
  );
  await api.refreshData();
  assert.equal(api.state.data.quality, null);
  assert.equal(api.state.data.releases.current.nutrition_release_id, null);
  assert.equal(api.state.dataErrors.length, 1);
});

test("a lost confirmation response retries the same event and quantity", async () => {
  let confirmations = 0;
  const { api, calls } = client((path) => {
    if (path === "/api/auth/login")
      return response({
        access_token: "test-token",
        household_id: 1,
        role: "owner",
        expires_at: Date.now() / 1000 + 3600,
      });
    if (path.startsWith("/api/members"))
      return response({ members: [{ id: 3 }] });
    if (path === "/api/cooking-sessions/4")
      return response({
        id: 4,
        status: "active",
        ingredients: [{ ingredient_id: 1, quantity_g: 120 }],
      });
    if (path === "/api/data/inventory")
      return response({
        items: [
          {
            id: 11,
            ingredient_id: 1,
            quantity_g: 50,
            expiry_date: "2027-01-01",
            name: "쌀",
          },
          {
            id: 12,
            ingredient_id: 1,
            quantity_g: 90,
            expiry_date: "2027-01-02",
            name: "쌀",
          },
        ],
      });
    if (path === "/api/data/inventory/events")
      return response({ event_id: 20 + confirmations });
    if (++confirmations === 1) throw Error("response lost");
    return response({ status: "applied" });
  });
  await api.login("owner", "test-password", 1);
  api.state.cooking = { id: 4, status: "active" };
  await api.prepareOutflows();
  await assert.rejects(() => api.confirmOutflows());
  await api.confirmOutflows();
  const changes = calls.filter((call) => call.path.endsWith("/confirm"));
  assert.equal(changes[0].path, changes[1].path);
  assert.deepEqual(changes[0].body, changes[1].body);
  assert.equal(changes[0].body.quantity_g, 50);
  assert.equal(changes[2].body.quantity_g, 70);
  assert.equal(
    calls.filter((call) => call.path === "/api/data/inventory/events").length,
    2,
  );
});
