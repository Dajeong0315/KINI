const shell = document.querySelector("#app-shell");
const views = [...document.querySelectorAll(".view")];
const navigation = [...document.querySelectorAll("[data-view-link]")];
const toast = document.querySelector("#toast");
const inventoryList = document.querySelector("#inventory-list");
const nutritionCard = document.querySelector("#nutrition-card");

if (window.matchMedia("(max-width: 660px)").matches) {
  shell.dataset.device = "mobile";
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "요청을 처리하지 못했습니다.");
  return data;
}

function notify(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove("visible"), 2400);
}

function openView(name) {
  if (!document.querySelector(`[data-view="${name}"]`)) return;
  views.forEach((view) => view.classList.toggle("active", view.dataset.view === name));
  navigation.forEach((item) => item.classList.toggle("active", item.dataset.viewLink === name));
  document.querySelector(".content").scrollTo({ top: 0, behavior: "smooth" });
  history.replaceState(null, "", `#${name}`);
}

navigation.forEach((item) => item.addEventListener("click", () => openView(item.dataset.viewLink)));

document.querySelector("#device-toggle").addEventListener("click", () => {
  const mobile = shell.dataset.device !== "mobile";
  shell.dataset.device = mobile ? "mobile" : "fridge";
  notify(mobile ? "ThinQ 앱 화면으로 전환했어요." : "냉장고 화면으로 전환했어요.");
});

document.querySelectorAll(".member").forEach((button) => {
  button.addEventListener("click", () => {
    button.classList.toggle("selected");
    const names = [...document.querySelectorAll(".member.selected")].map((item) => item.dataset.member);
    notify(`${names.join(", ")}의 식사 기준을 반영해요.`);
  });
});

document.querySelectorAll(".filters button").forEach((button) => button.addEventListener("click", () => button.classList.toggle("active")));

document.querySelectorAll(".recipe-option").forEach((option) => {
  option.addEventListener("click", () => {
    document.querySelectorAll(".recipe-option").forEach((item) => item.classList.toggle("selected", item === option));
    document.querySelector("#selected-recipe-title").textContent = option.dataset.recipe;
  });
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    openView("cook");
    const input = document.querySelector("#cook-question");
    input.value = button.dataset.question;
    input.focus();
  });
});

function getUrgency(item) {
  if (item.name === "떡") return ["냉동 보관", "calm"];
  if (item.name === "버섯") return ["2일 안에 활용", "soon"];
  if (item.name === "달걀") return ["여유 있음", "calm"];
  return [item.use_by || "상태 확인", "neutral"];
}

function renderInventory(inventory) {
  inventoryList.replaceChildren();
  const items = [{ name: "시금치", quantity: 200, unit: "g", location: "냉장실", priority: true }, ...(inventory.items || [])];
  items.forEach((item) => {
    const [label, state] = item.priority ? ["오늘 활용", "urgent"] : getUrgency(item);
    const card = document.createElement("article");
    card.className = "inventory-item";
    card.innerHTML = `<div class="inventory-icon ${item.priority ? "leaf" : ""}">${item.priority ? "✽" : "◌"}</div><div><h2>${item.name}</h2><p>${item.quantity}${item.unit} · ${item.location}</p></div><span class="chip ${state}">${label}</span><button class="inventory-detail" type="button" aria-label="${item.name} 영양성분 보기">›</button>`;
    card.querySelector(".inventory-detail").addEventListener("click", () => loadNutrition(item.name));
    inventoryList.append(card);
  });
}

async function loadInventory() {
  try {
    renderInventory(await requestJson("/api/inventory"));
  } catch (error) {
    inventoryList.innerHTML = `<p class="error-message">재고를 불러오지 못했습니다. ${error.message}</p>`;
  }
}

async function loadNutrition(foodName) {
  nutritionCard.hidden = false;
  nutritionCard.innerHTML = `<p>${foodName}의 영양성분을 불러오는 중입니다.</p>`;
  try {
    const nutrition = await requestJson(`/api/nutrition?food_name=${encodeURIComponent(foodName)}`);
    nutritionCard.innerHTML = `<button class="drawer-close" type="button" aria-label="닫기">×</button><p class="eyebrow">영양성분</p><h2>${nutrition.food_name} <small>${nutrition.serving_size}</small></h2><p class="nutrition-values">열량 ${nutrition.energy_kcal ?? "-"}kcal · 탄수화물 ${nutrition.carbohydrate_g ?? "-"}g · 단백질 ${nutrition.protein_g ?? "-"}g · 지방 ${nutrition.fat_g ?? "-"}g · 나트륨 ${nutrition.sodium_mg ?? "-"}mg</p><p class="data-note">${nutrition.match_note || "공용 영양 DB 기준"}</p>`;
  } catch (error) {
    nutritionCard.innerHTML = `<button class="drawer-close" type="button" aria-label="닫기">×</button><p>${foodName}의 영양성분을 아직 확인하지 못했습니다.</p><p class="data-note">${error.message}</p>`;
  }
  nutritionCard.querySelector(".drawer-close").addEventListener("click", () => { nutritionCard.hidden = true; });
}

document.querySelector("#refresh-inventory").addEventListener("click", () => { loadInventory(); notify("재고를 새로 확인했어요."); });
document.querySelector("#add-inventory").addEventListener("click", () => notify("카메라 인식 또는 음성 등록 화면을 연결할 수 있어요."));
document.querySelector("#refresh-recipes").addEventListener("click", () => notify("현재 재료와 기준으로 메뉴를 다시 비교했어요."));
document.querySelector("#quick-voice").addEventListener("click", () => notify("음성으로 오늘 저녁을 물어볼 수 있어요."));
document.querySelector("#next-step").addEventListener("click", () => notify("다음 조리 단계로 이동합니다."));

document.querySelector("#cook-voice").addEventListener("click", () => {
  const state = document.querySelector("#listening-state");
  state.textContent = state.textContent === "듣는 중" ? "대기 중" : "듣는 중";
});

document.querySelector("#cook-chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#cook-question");
  const question = input.value.trim();
  if (!question) return;
  const answer = document.querySelector("#cook-answer");
  answer.innerHTML = "<p>재료와 조리 기준을 확인하는 중이에요.</p>";
  try {
    const data = await requestJson("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    answer.innerHTML = `<p>${data.answer}</p><small>검증된 재고 정보 기준</small>`;
    if (/반 모|적게|줄일게|빼/.test(question)) {
      document.querySelector("#changed-items").innerHTML = "<b>변경된 재료</b><p><strong>두부</strong> · 원본 200g → 확인이 필요한 변경 초안</p>";
    }
  } catch (error) {
    answer.innerHTML = `<p>답변을 만들지 못했습니다.</p><small>${error.message}</small>`;
  }
  input.value = "";
});

const initial = location.hash.slice(1);
if (initial) openView(initial);
loadInventory();
