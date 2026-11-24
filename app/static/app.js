const inventoryList = document.querySelector("#inventory-list");
const answerCard = document.querySelector("#answer-card");
const answerText = document.querySelector("#answer-text");
const answerMeta = document.querySelector("#answer-meta");
const chatState = document.querySelector("#chat-state");
const voiceState = document.querySelector("#voice-state");
const speakButton = document.querySelector("#speak-button");
const player = document.querySelector("#audio-player");
const download = document.querySelector("#audio-download");
const nutritionCard = document.querySelector("#nutrition-card");
const recipeState = document.querySelector("#recipe-state");
const recipeList = document.querySelector("#recipe-list");

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "요청을 처리하지 못했습니다.");
  return data;
}

function setState(element, message, error = false) {
  element.textContent = message;
  element.classList.toggle("error", error);
}

function renderInventory(inventory) {
  inventoryList.replaceChildren();
  if (!inventory.items?.length) {
    inventoryList.innerHTML = "<li><strong>등록된 재고가 없습니다.</strong><span>app/data/inventory.json을 수정해 주세요.</span></li>";
    return;
  }
  inventory.items.forEach((item) => {
    const li = document.createElement("li");
    const name = document.createElement("strong");
    const details = document.createElement("span");
    name.textContent = `${item.name} · ${item.quantity}${item.unit}`;
    details.textContent = `${item.location} · 소비기한 ${item.use_by || "미입력"}`;
    const nutritionButton = document.createElement("button");
    nutritionButton.type = "button";
    nutritionButton.textContent = "영양성분 보기";
    nutritionButton.addEventListener("click", () => loadNutrition(item.name));
    li.append(name, details, nutritionButton);
    inventoryList.append(li);
  });
}

async function loadNutrition(foodName) {
  nutritionCard.hidden = false;
  nutritionCard.innerHTML = `<p>${foodName}의 영양성분을 불러오는 중입니다.</p>`;
  try {
    const nutrition = await requestJson(`/api/nutrition?food_name=${encodeURIComponent(foodName)}`);
    nutritionCard.replaceChildren();
    const title = document.createElement("h3");
    const values = document.createElement("p");
    const note = document.createElement("p");
    title.textContent = `${nutrition.food_name} · ${nutrition.serving_size}`;
    values.textContent = `열량 ${nutrition.energy_kcal ?? "-"}kcal · 탄수화물 ${nutrition.carbohydrate_g ?? "-"}g · 단백질 ${nutrition.protein_g ?? "-"}g · 지방 ${nutrition.fat_g ?? "-"}g · 나트륨 ${nutrition.sodium_mg ?? "-"}mg`;
    note.textContent = nutrition.match_note;
    nutritionCard.append(title, values, note);
  } catch (error) {
    nutritionCard.innerHTML = `<p>${error.message}</p>`;
  }
}

function renderRecipes(recipes) {
  recipeList.replaceChildren();
  if (!recipes.length) {
    recipeList.textContent = "현재 재고와 겹치는 레시피를 찾지 못했습니다.";
    return;
  }
  recipes.forEach((recipe) => {
    const card = document.createElement("article");
    card.className = "recipe-card";
    const title = document.createElement("h3");
    const meta = document.createElement("p");
    const match = document.createElement("p");
    const nutrition = document.createElement("p");
    title.textContent = recipe.name;
    meta.className = "recipe-meta";
    meta.textContent = [recipe.category, recipe.method].filter(Boolean).join(" · ") || "조리법 정보";
    match.className = "recipe-match";
    const extra = recipe.additional_core_ingredients?.length ? ` · 추가 필요: ${recipe.additional_core_ingredients.join(", ")}` : " · 추가 핵심 재료 확인 없음";
    match.textContent = `내 재고와 겹침: ${recipe.matched_items.join(", ")}${extra}`;
    nutrition.className = "recipe-nutrition";
    nutrition.textContent = `1인분 기준 열량 ${recipe.nutrition.energy_kcal ?? "-"}kcal · 탄수화물 ${recipe.nutrition.carbohydrate_g ?? "-"}g · 단백질 ${recipe.nutrition.protein_g ?? "-"}g · 지방 ${recipe.nutrition.fat_g ?? "-"}g · 나트륨 ${recipe.nutrition.sodium_mg ?? "-"}mg`;
    card.append(title, meta, match, nutrition);
    recipeList.append(card);
  });
}

async function loadInventory() {
  try {
    renderInventory(await requestJson("/api/inventory"));
  } catch (error) {
    inventoryList.innerHTML = `<li><strong>재고를 불러오지 못했습니다.</strong><span>${error.message}</span></li>`;
  }
}

function showAnswer(data) {
  answerCard.classList.remove("empty");
  answerText.textContent = data.answer;
  const sourceLabels = { inventory: "로컬 재고 JSON", recipe_db: "식약처 레시피 DB", ollama: "Ollama" };
  const itemText = data.selected_items.length ? data.selected_items.map((item) => item.name).join(", ") : "없음";
  answerMeta.textContent = `근거: ${sourceLabels[data.answer_source] || "확인 중"} · 참조 재고: ${itemText}${data.model ? ` · ${data.model}` : ""}`;
  speakButton.disabled = false;
  player.hidden = true;
  player.removeAttribute("src");
  download.hidden = true;
}

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = document.querySelector("#question").value;
  setState(chatState, "재고를 확인하고 답변을 만드는 중입니다.");
  try {
    const data = await requestJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    showAnswer(data);
    setState(chatState, "");
  } catch (error) {
    setState(chatState, error.message, true);
  }
});

document.querySelector("#voice-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.querySelector("#audio-file");
  if (!fileInput.files[0]) return;
  setState(voiceState, "로컬 Whisper가 음성을 전사하는 중입니다.");
  try {
    const data = await requestJson("/api/voice", { method: "POST", body: new FormData(event.target) });
    document.querySelector("#question").value = data.transcript;
    showAnswer(data);
    setState(voiceState, `전사 완료: ${data.transcript}`);
  } catch (error) {
    setState(voiceState, error.message, true);
  }
});

speakButton.addEventListener("click", async () => {
  setState(chatState, "로컬 Piper가 답변을 음성으로 만드는 중입니다.");
  try {
    const data = await requestJson("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: answerText.textContent }),
    });
    player.src = data.audio_url;
    player.hidden = false;
    download.href = data.audio_url;
    download.download = "fridge-assistant-reply.wav";
    download.textContent = "WAV 다운로드";
    download.hidden = false;
    setState(chatState, "WAV 음성 파일을 만들었습니다.");
  } catch (error) {
    setState(chatState, error.message, true);
  }
});

document.querySelector("#refresh-inventory").addEventListener("click", loadInventory);
document.querySelector("#recipe-button").addEventListener("click", async () => {
  setState(recipeState, "식품안전나라 레시피를 찾는 중입니다.");
  try {
    const data = await requestJson("/api/recipes");
    renderRecipes(data.recipes);
    setState(recipeState, `${data.recipes.length}개 레시피를 찾았습니다.`);
  } catch (error) {
    setState(recipeState, error.message, true);
  }
});
loadInventory();
