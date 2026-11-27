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
    URLSearchParams,
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

test("meal feedback uses authenticated PUT and invalidates old recommendation", async () => {
  const {api,calls}=client(path => identity(path) || response({record_id:7,member_id:3,scope:"meal",revision:1}));
  await api.login("synthetic","synthetic-password",1);
  api.state.result={status:"recommendations_available",recommendations:[]};
  await api.saveFeedback(7,3,{status:"submitted",expected_revision:0,ratings:{taste:"taste_satisfied"}});
  const saved=calls.at(-1);
  assert.equal(saved.method,"PUT");
  assert.equal(saved.headers.Authorization,"Bearer synthetic-token");
  assert.equal(saved.body.expected_revision,0);
  assert.equal(api.state.result,null);
});

function identity(path) {
  if (path === "/api/auth/login") return response({ access_token: "synthetic-token", household_id: 1,
    role: "owner", expires_at: Date.now()/1000 + 3600 });
  if (path === "/api/households/1") return response({id:1,members:[{id:3}]});
}

test('vision confirmation freezes a lost request, retries same payload, and clears on owner change', async () => {
  let lost = true;
  const {api,calls} = client(path => {
    const auth = identity(path);
    if (auth) return auth;
    if (path.endsWith('/confirm')) {
      if (lost) { lost = false; throw new Error('synthetic loss'); }
      return response({status:'applied',event_id:7});
    }
    return response({});
  });
  await api.login('owner','synthetic-password',1);
  const body = {ingredient_id:901,quantity_g:10,inventory_item_id:3};
  await assert.rejects(api.confirmVision(7,body));
  await assert.rejects(api.confirmVision(7,{...body,quantity_g:20}),/이전 확정/);
  await assert.rejects(api.rejectVision(7),/결과를 먼저/);
  assert.equal((await api.confirmVision(7,body)).status,'applied');
  const writes = calls.filter(x => x.path.endsWith('/confirm'));
  assert.equal(writes.length,2);
  assert.deepEqual(writes[0].body,writes[1].body);
  assert.equal(api.visionConfirmation(7),undefined);
  lost = true;
  await assert.rejects(api.confirmVision(8,body));
  assert.ok(api.visionConfirmation(8));
  await api.login('other-owner','synthetic-password',1);
  assert.equal(api.visionConfirmation(8),undefined);
});

test("inventory normalization and all private calls attach the household bearer", async () => {
  const {api,calls}=client(path => identity(path) || response(path.includes("normalize")
    ? {ingredient:{ingredient_id:1},conversion:{quantity_g:25}}
    : path.endsWith("/confirm") ? {status:"applied"} : {event_id:9}));
  await api.login("synthetic","synthetic-password",1);
  await api.addInventory("synthetic ingredient",25,null);
  for (const call of calls.slice(1)) assert.equal(call.headers.Authorization,"Bearer synthetic-token");
  assert.ok(calls.some(call=>call.path==="/api/inventory/normalize"));
});

test("stored reports distinguish empty from failed lookup and clear on expiration", async () => {
  let fail=false;
  const {api,calls}=client(path=>identity(path) || (fail ? response({error:"offline"},503)
    : response(path.startsWith("/api/meal-records?") ? {household_id:1,records:[],total:0}
      : {household_id:1,nutrition_totals:{energy_kcal:null}})));
  await api.login("synthetic","synthetic-password",1);
  await api.loadReports({period:"today",member_id:3});
  assert.equal(api.state.reportsStatus,"ready");
  assert.equal(api.state.history.total,0);
  assert.equal(api.state.nutritionReport.nutrition_totals.energy_kcal,null);
  assert.ok(calls.find(c=>c.path.includes("member_id=3")&&c.path.includes("period=today")));
  fail=true;
  await assert.rejects(()=>api.loadReports());
  assert.equal(api.state.reportsStatus,"error");
  assert.equal(api.state.history,null);
  api.state.session.expiresAt=0;
  await assert.rejects(()=>api.loadReports(),{status:401});
  assert.equal(api.state.session,null);
  assert.equal(api.state.nutritionReport,null);
});

test("a report response from a previous session never populates the next family", async () => {
  const pending=[];
  const {api}=client(path=>identity(path) || new Promise(resolve=>pending.push(resolve)));
  await api.login("synthetic","synthetic-password",1);
  const reports=api.loadReports();
  api.clearSession();
  api.state.household={id:2,members:[]};
  pending[0](response({household_id:1,records:[{record_id:90}]}));
  pending[1](response({household_id:1,nutrition_totals:{energy_kcal:500}}));
  await assert.rejects(()=>reports,{status:409});
  assert.equal(api.state.history,null);
  assert.equal(api.state.nutritionReport,null);
});

test("lost save response is resolved by GET without another completion write", async () => {
  let writes=0;
  const {api,calls}=client(path=>{
    if(identity(path)) return identity(path);
    if(path.endsWith("/complete")) {writes++;throw Error("lost response");}
    return response({id:4,status:"completed",meal_record_id:8,nutrition_per_serving:{energy_kcal:50}});
  });
  await api.login("synthetic","synthetic-password",1);
  api.selectMembers([3]);
  api.state.cooking={id:4,status:"active"};
  const items=[{member_id:3,servings:.5}];
  await assert.rejects(()=>api.complete(items,true,false,[9]));
  await assert.rejects(()=>api.complete([{member_id:3,servings:1}],true,false,[9]),{status:409});
  await api.complete(items,true,false,[9]);
  assert.equal(writes,1);
  assert.equal(api.state.cooking.meal_record_id,8);
  assert.equal(api.state.receipts[0].items[0].servings,.5);
  assert.equal(calls.filter(c=>c.path==="/api/cooking-sessions/4").length,1);
});
const success = {
  request_id: 1,
  status: "recommendations_available",
  recommendations: [{ recipe_id: 2, name: "server recipe" }],
};
async function signedInClient(handler) {
  const { api, calls } = client((path, options) => {
    if (path === "/api/auth/login")
      return response({
        access_token: "synthetic-token",
        household_id: 1,
        role: "owner",
        expires_at: Date.now() / 1000 + 3600,
      });
    if (path === "/api/households/1")
      return response({ id: 1, members: [{ id: 3 }, { id: 4 }] });
    if (options.headers.Authorization !== "Bearer synthetic-token")
      return response({ error: "Authentication required" }, 401);
    return handler(path, options);
  });
  await api.login("owner", "synthetic-password", 1);
  api.selectMembers([3, 4]);
  calls.length = 0;
  return { api, calls };
}
test("login authenticates member lookup and clears a rejected session", async () => {
  for (const status of [200, 401, 403]) {
    const { api, calls } = client((path) =>
      path === "/api/auth/login"
        ? response({ access_token: "synthetic-token", household_id: 1,
            role: "owner", expires_at: Date.now() / 1000 + 3600 })
        : response({ id: 1, name: "Test family", members: [{ id: 3 }] }, status),
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
    assert.equal(calls[1].path, "/api/households/1");
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
    const { api } = await signedInClient(() =>
      response(fail ? { ...success, status } : structuredClone(success)),
    );
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
    const { api } = await signedInClient(() =>
      fail ? failure() : response(structuredClone(success)),
    );
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
  const { api, calls } = await signedInClient(() => response(structuredClone(success)));
  await api.recommend({});
  await api.recommend({}, true);
  assert.deepEqual(Array.from(calls[1].body.excluded_recipe_ids), [2]);
  api.invalidate();
  await api.recommend({});
  assert.deepEqual(Array.from(calls[2].body.excluded_recipe_ids), []);
});
test("stale revalidation prevents meal and cooking writes", async () => {
  const { api, calls } = await signedInClient(() =>
    response({ ...success, status: "stale_unvalidated" }),
  );
  api.state.result = structuredClone(success);
  await assert.rejects(() => api.startCooking(2, true, {}));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(api.state.result.status, "stale_unvalidated");
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
      : path === "/api/households/1"
        ? response({ id: 1, name: "Test family", members: [{ id: 3 }, { id: 4 }] })
        : fail
          ? response({ error: "invalid change" }, 400)
          : response({
              record_id: 9,
              nutrition_per_serving: { protein_g: 10 },
            }),
  );
  await api.login("owner", "test-password", 1);
  api.selectMembers([3, 4]);
  api.state.cooking = {
    id: 4,
    status: "active",
    ingredients: [{ ingredient_id: 1, quantity_g: 100 }],
  };
  api.state.result = structuredClone(success);
  api.state.picked = 2;
  await assert.rejects(() => api.changeIngredient("bad", 3));
  assert.equal(calls[2].headers.Authorization, "Bearer test-token");
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
    if (path === "/api/households/1")
      return response({ id: 1, name: "Test family", members: [{ id: 3 }] });
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
  assert.equal(calls[2].headers.Authorization, "Bearer test-token");
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
