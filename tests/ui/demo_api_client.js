/* Role C adapter. All six scenarios currently use a synthetic in-memory provider.
 * Replace the provider behind this boundary when A/B contracts are ready.
 * No HTTP, localStorage, analytics, or external LLM calls are made here. */
(() => {
  function createClient() {
    let provider = globalThis.KINIDemo.createModel();
    return {
      mode: "demo",
      read: () => provider.snapshot(),
      catalog: () => provider.catalog(),
      recipes: () => provider.recipes(),
      today: () => provider.today(),
      report: (options) => provider.report(options),
      async call(method, ...args) {
        if (method === "reset") { provider = globalThis.KINIDemo.createModel(); return provider.snapshot(); }
        if (typeof provider[method] !== "function") throw new Error("연결되지 않은 데모 기능입니다.");
        if (method === "recommend") await new Promise((resolve) => setTimeout(resolve, 180));
        return provider[method](...args);
      },
    };
  }
  globalThis.KINIAPI = { createClient };
})();
