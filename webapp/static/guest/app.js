const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";

const els = {
  spinBtn: document.getElementById("spinBtn"),
  prizeResult: document.getElementById("prizeResult"),
  vaultList: document.getElementById("vaultList"),
  vaultBadge: document.getElementById("vaultBadge"),
  userChip: document.getElementById("userChip"),
  tabs: document.querySelectorAll(".tab-btn"),
  views: document.querySelectorAll(".view"),
};

function api(path, body = {}) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData, ...body }),
  }).then((r) => r.json());
}

function switchView(id) {
  els.views.forEach((v) => v.classList.toggle("active", v.id === id));
  els.tabs.forEach((t) => t.classList.toggle("active", t.dataset.view === id));
  if (id === "view-vault") loadVault();
}

els.tabs.forEach((t) => t.addEventListener("click", () => switchView(t.dataset.view)));

async function init() {
  const res = await api("/api/verify");
  if (res.ok) {
    els.userChip.textContent = "@" + (res.user.username || res.user.first_name || "guest");
  } else {
    els.userChip.textContent = "нет доступа";
  }
  refreshVaultBadge();
}

async function refreshVaultBadge() {
  const res = await api("/api/vault");
  if (!res.ok) return;
  const lockedCount = res.items.filter((i) => i.display_status === "locked").length;
  if (lockedCount > 0) {
    els.vaultBadge.textContent = lockedCount;
    els.vaultBadge.classList.remove("hidden");
  } else {
    els.vaultBadge.classList.add("hidden");
  }
}

els.spinBtn.addEventListener("click", async () => {
  els.spinBtn.disabled = true;
  tg?.HapticFeedback?.impactOccurred?.("medium");
  const res = await api("/api/spin");
  els.spinBtn.disabled = false;

  if (!res.ok) {
    els.prizeResult.innerHTML = `<p>${res.error || "Не удалось получить приз"}</p>`;
    els.prizeResult.classList.remove("hidden");
    return;
  }
  tg?.HapticFeedback?.notificationOccurred?.("success");
  els.prizeResult.innerHTML = `
    <h3>🎉 ${res.prize.prize_name}</h3>
    <p>Приз добавлен в корзинку. Замок снимется через 24 часа.</p>
  `;
  els.prizeResult.classList.remove("hidden");
  refreshVaultBadge();
});

function fmtTime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}ч ${m}м до разблокировки`;
}

async function loadVault() {
  els.vaultList.innerHTML = `<p class="empty-state">Загрузка...</p>`;
  const res = await api("/api/vault");
  if (!res.ok || res.items.length === 0) {
    els.vaultList.innerHTML = `<p class="empty-state">Пока пусто. Забери первый приз на вкладке «Призы» 🎁</p>`;
    return;
  }
  els.vaultList.innerHTML = res.items
    .map((item) => {
      const status = item.display_status;
      const icon = status === "locked" ? "🔒" : status === "unlocked" ? "🔓" : "✅";
      const statusText =
        status === "locked"
          ? fmtTime(item.seconds_left)
          : status === "unlocked"
          ? "Готов к выдаче на ресепшене"
          : "Уже выдан";
      return `
        <div class="vault-card ${status}">
          <div class="vault-icon">${icon}</div>
          <div class="vault-info">
            <h4>${item.prize_name}</h4>
            <div class="status-line ${status}">${statusText}</div>
          </div>
        </div>`;
    })
    .join("");
}

init();
