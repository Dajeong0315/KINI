// 핸드폰(camera.html)·PC(viewer.html) 가 함께 쓰는 그리기 도구

const KIND_COLOR = {
  item: "#22c55e",     // 목록 품목 (생김새로 인식)
  text: "#38bdf8",     // 포장 글씨로 이름을 읽은 것
  user: "#facc15",     // 사용자가 이름 붙인 품목
  unknown: "#ef4444",  // 판단할 수 없는 물체 → 사용자 입력
};

// 화면 요소(w×h) 안에 원본(fw×fh)을 contain/cover 로 넣었을 때의 위치
function fitRect(fw, fh, w, h, mode = "contain") {
  const s = mode === "cover" ? Math.max(w / fw, h / fh) : Math.min(w / fw, h / fh);
  const dw = fw * s, dh = fh * s;
  return { x: (w - dw) / 2, y: (h - dh) / 2, w: dw, h: dh, s };
}

// 인식 결과(res: {w,h,dets}) 를 ctx 의 rect 위치에 그림. mirror: 전면 카메라 거울 화면
function drawDetections(ctx, res, rect, { mirror = false, minConf = 0, hit = null, hint = true } = {}) {
  if (!res || !res.dets) return;
  const fontPx = Math.max(13, Math.round(rect.w / 45));
  ctx.lineWidth = Math.max(2, Math.round(rect.w / 320));
  ctx.font = `600 ${fontPx}px "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif`;
  ctx.textBaseline = "top";
  for (const d of res.dets) {
    if (d.conf < minConf) continue;
    let [x1, y1, x2, y2] = d.box.map((v, i) => v / (i % 2 === 0 ? res.w : res.h));
    if (mirror) [x1, x2] = [1 - x2, 1 - x1];
    const X = rect.x + x1 * rect.w, Y = rect.y + y1 * rect.h;
    const W = (x2 - x1) * rect.w, H = (y2 - y1) * rect.h;
    const color = KIND_COLOR[d.kind] || KIND_COLOR.item;
    ctx.strokeStyle = color;
    ctx.strokeRect(X, Y, W, H);
    // Unknown 은 짧게 (안내는 화면 위 배너): 핸드폰 '❓ 이름?' / PC 'Unknown'
    const label = d.kind === "unknown" ? (hint ? "❓ 이름?" : "Unknown") : `${d.name} ${(d.conf * 100).toFixed(0)}%`;
    const tw = ctx.measureText(label).width + fontPx * 0.6;
    const th = fontPx * 1.35;
    // 이름표가 보이는 화면 밖으로 나가지 않게 (cover 로 잘린 화면 포함)
    const viewW = ctx.canvas.clientWidth || rect.x + rect.w, top = Math.max(rect.y, 0);
    const lx = Math.max(0, Math.min(X, viewW - tw));
    const ly = Y - th >= top ? Y - th : Math.max(Y, top);   // 위쪽 공간이 없으면 상자 안쪽에
    ctx.fillStyle = color;
    ctx.fillRect(lx, ly, tw, th);
    ctx.fillStyle = d.kind === "user" ? "#1f2937" : "#ffffff";
    ctx.fillText(label, lx + fontPx * 0.3, ly + fontPx * 0.18);
    if (hit) hit.push({ d, X, Y, W, H });              // 터치 판정용
  }
}

// "오렌지 ×3, 버터 ×1" 같은 요약
function summarize(res, minConf = 0) {
  if (!res || !res.dets) return [];
  const m = new Map();
  for (const d of res.dets) {
    if (d.conf < minConf) continue;
    const k = d.name;
    m.set(k, { name: k, kind: d.kind, n: (m.get(k)?.n || 0) + 1 });
  }
  return [...m.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name, "ko"));
}

function renderList(el, res, minConf) {
  const rows = summarize(res, minConf);
  el.innerHTML = rows.length
    ? rows.map(r => `<li><span class="dot" style="background:${KIND_COLOR[r.kind]}"></span>${r.name}<b>×${r.n}</b></li>`).join("")
    : `<li class="muted">인식된 물체 없음</li>`;
}

function unknownCount(res, minConf = 0) {
  return res && res.dets ? res.dets.filter(d => d.kind === "unknown" && d.conf >= minConf).length : 0;
}

function wsUrl(path) {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}
