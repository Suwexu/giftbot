const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";

const els = {
  app: document.getElementById("app"),
  denied: document.getElementById("deniedScreen"),
  userChip: document.getElementById("userChip"),
  tabs: document.querySelectorAll(".tab-btn"),
  panels: document.querySelectorAll(".tab-panel"),
  statGrid: document.getElementById("statGrid"),
  prizesList: document.getElementById("prizesList"),
  vaultAdminList: document.getElementById("vaultAdminList"),
  addPrizeBtn: document.getElementById("addPrizeBtn"),
  modal: document.getElementById("prizeModal"),
  modalTitle: document.getElementById("modalTitle"),
  modalCancel: document.getElementById("modalCancel"),
  modalSave: document.getElementById("modalSave"),
  fName: document.getElementById("fName"),
  fDesc: document.getElementById("fDesc"),
  fImage: document.getElementById("fImage"),
  fWeight: document.getElementById("fWeight"),
  fStock: document.getElementById("fStock"),
  fActive: document.getElementById("fActive"),
  filterChips: document.querySelectorAll(".chip"),
};

let editingPrizeId = null;
let vaultFilter = "";

function api(path, body = {}) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData, ...body }),
  }).then((r) => r.json());
}

els.tabs.forEach((t) =>
  t.addEventListener("click", () => {
    els.tabs.forEach((x) => x.classList.toggle("active", x === t));
    els.panels.forEach((p) => p.classList.toggle("active", p.id === "tab-" + t.dataset.tab));
    if (t.dataset.tab === "dashboard") loadStats();
    if (t.dataset.tab === "prizes") loadPrizes();
    if (t.dataset.tab === "vault") loadVault();
  })
);

async function boot() {
  const res = await api("/api/verify");
  if (!res.ok || !res.user.is_admin) {
    els.denied.classList.remove("hidden");
    return;
  }
  els.userChip.textContent = "@" + (res.user.username || res.user.first_name || "admin");
  els.app.classList.remove("hidden");
  loadStats();
}

async function loadStats() {
  const res = await api("/api/admin/stats");
  if (!res.ok) return;
  const s = res.stats;
  const cards = [
    ["Гостей", s.users, "--cyan"],
    ["Активных призов", s.active_prizes, "--violet"],
    ["Под замком", s.locked_in_vault, "--amber"],
    ["Выдано призов", s.issued, "--green"],
  ];
  els.statGrid.innerHTML = cards
    .map(
      ([label, value, accent]) => `
    <div class="stat-card" style="--accent: var(${accent})">
      <div class="stat-value">${value}</div>
      <div class="stat-label">${label}</div>
    </div>`
    )
    .join("");
}

async function loadPrizes() {
  els.prizesList.innerHTML = `<p class="empty-state">Загрузка...</p>`;
  const res = await api("/api/admin/prizes");
  if (!res.ok) return;
  if (res.prizes.length === 0) {
    els.prizesList.innerHTML = `<p class="empty-state">Призов пока нет. Добавь первый →</p>`;
    return;
  }
  els.prizesList.innerHTML = res.prizes
    .map(
      (p) => `
    <div class="pcard ${p.active ? "" : "inactive"}">
      <div class="p-icon">🎁</div>
      <div class="pcard-info">
        <h4>${p.name}</h4>
        <div class="pcard-meta">вес ${p.weight} · остаток ${p.stock === -1 ? "∞" : p.stock} · ${p.active ? "активен" : "выключен"}</div>
      </div>
      <div class="pcard-actions">
        <button class="btn-small" data-edit="${p.id}">✏️</button>
        <button class="btn-danger" data-del="${p.id}">✕</button>
      </div>
    </div>`
    )
    .join("");

  res.prizes.forEach((p) => {
    els.prizesList.querySelector(`[data-edit="${p.id}"]`).addEventListener("click", () => openModal(p));
    els.prizesList.querySelector(`[data-del="${p.id}"]`).addEventListener("click", () => deletePrize(p.id));
  });
}

function openModal(prize = null) {
  editingPrizeId = prize?.id || null;
  els.modalTitle.textContent = prize ? "Редактировать приз" : "Новый приз";
  els.fName.value = prize?.name || "";
  els.fDesc.value = prize?.description || "";
  els.fImage.value = prize?.image_url || "";
  els.fWeight.value = prize?.weight ?? 1;
  els.fStock.value = prize?.stock ?? -1;
  els.fActive.checked = prize ? !!prize.active : true;
  els.modal.classList.remove("hidden");
}
function closeModal() {
  els.modal.classList.add("hidden");
}
els.addPrizeBtn.addEventListener("click", () => openModal());
els.modalCancel.addEventListener("click", closeModal);

els.modalSave.addEventListener("click", async () => {
  const payload = {
    name: els.fName.value.trim() || "Без названия",
    description: els.fDesc.value.trim(),
    image_url: els.fImage.value.trim(),
    weight: Number(els.fWeight.value) || 1,
    stock: Number(els.fStock.value),
  };
  if (editingPrizeId) {
    await api(`/api/admin/prizes/${editingPrizeId}/update`, { fields: { ...payload, active: els.fActive.checked ? 1 : 0 } });
  } else {
    await api("/api/admin/prizes/create", { prize: payload });
  }
  closeModal();
  loadPrizes();
});

async function deletePrize(id) {
  if (!confirm("Удалить приз?")) return;
  await api(`/api/admin/prizes/${id}/delete`);
  loadPrizes();
}

els.filterChips.forEach((c) =>
  c.addEventListener("click", () => {
    els.filterChips.forEach((x) => x.classList.toggle("active", x === c));
    vaultFilter = c.dataset.status;
    loadVault();
  })
);

function fmtTime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `осталось ${h}ч ${m}м`;
}

async function loadVault() {
  els.vaultAdminList.innerHTML = `<p class="empty-state">Загрузка...</p>`;
  const res = await api("/api/admin/vault", { status: vaultFilter || undefined });
  if (!res.ok) return;
  if (res.items.length === 0) {
    els.vaultAdminList.innerHTML = `<p class="empty-state">Пока пусто</p>`;
    return;
  }
  els.vaultAdminList.innerHTML = res.items
    .map((item) => {
      const who = item.username ? "@" + item.username : item.first_name || `id${item.user_id}`;
      const meta =
        item.display_status === "locked"
          ? fmtTime(item.seconds_left)
          : item.display_status === "unlocked"
          ? "можно выдавать"
          : "уже на руках у гостя";
      return `
      <div class="vcard">
        <div class="v-icon">${item.display_status === "locked" ? "🔒" : item.display_status === "unlocked" ? "🔓" : "✅"}</div>
        <div class="vcard-info">
          <h4>${item.prize_name}</h4>
          <div class="vcard-meta">${who} · ${meta}</div>
        </div>
        <div class="vcard-actions">
          <span class="status-pill ${item.display_status}">${item.display_status}</span>
          ${
            item.display_status === "unlocked"
              ? `<button class="btn-small" data-issue="${item.id}">Выдать</button>`
              : ""
          }
        </div>
      </div>`;
    })
    .join("");

  res.items
    .filter((i) => i.display_status === "unlocked")
    .forEach((i) => {
      const btn = els.vaultAdminList.querySelector(`[data-issue="${i.id}"]`);
      btn?.addEventListener("click", async () => {
        btn.disabled = true;
        const r = await api(`/api/admin/vault/${i.id}/issue`);
        if (r.ok) loadVault();
        else {
          alert(r.message || "Не удалось выдать");
          btn.disabled = false;
        }
      });
    });
}

boot();
