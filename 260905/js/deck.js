import { getCards, getCardSets, addCard, updateCard, deleteCard, importSet } from "./store.js";
import { retentionAt } from "./srs.js";
import { formatDate, escapeHtml, inkForRetention } from "./utils.js";

let frontInput, backInput, editIdInput, submitBtn, cancelBtn, cardForm;

export function renderDeck() {
  const list = document.getElementById("card-list");
  const emptyMsg = document.getElementById("card-list-empty");
  const cards = getCards();
  list.innerHTML = "";

  if (cards.length === 0) {
    emptyMsg.hidden = false;
    return;
  }
  emptyMsg.hidden = true;

  const now = Date.now();
  cards.slice().sort((a, b) => a.dueAt - b.dueAt).forEach((card) => {
    const r = retentionAt(card, now);
    const li = document.createElement("li");
    li.className = "card-row";
    li.innerHTML = `
      <div class="card-row-text">
        <div class="card-row-front" style="color:${inkForRetention(r)}">${escapeHtml(card.front)}</div>
        <div class="card-row-meta">次回復習: ${formatDate(card.dueAt)}（保持率 約${Math.round(r * 100)}%）</div>
      </div>
      <div class="card-row-actions">
        <button type="button" data-action="edit" data-id="${card.id}">編集</button>
        <button type="button" data-action="delete" data-id="${card.id}">削除</button>
      </div>
    `;
    list.appendChild(li);
  });
}

function resetCardForm() {
  cardForm.reset();
  editIdInput.value = "";
  submitBtn.textContent = "カードを追加";
  cancelBtn.hidden = true;
}

function initCardForm() {
  cardForm = document.getElementById("card-form");
  frontInput = document.getElementById("card-front");
  backInput = document.getElementById("card-back");
  editIdInput = document.getElementById("card-edit-id");
  submitBtn = document.getElementById("card-form-submit");
  cancelBtn = document.getElementById("card-form-cancel");

  cardForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const front = frontInput.value.trim();
    const back = backInput.value.trim();
    if (!front || !back) return;
    if (editIdInput.value) {
      updateCard(editIdInput.value, front, back);
    } else {
      addCard(front, back);
    }
    resetCardForm();
    renderDeck();
  });

  cancelBtn.addEventListener("click", resetCardForm);

  document.getElementById("card-list").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === "delete") {
      if (confirm("このカードを削除しますか？")) {
        deleteCard(id);
        renderDeck();
      }
    } else if (btn.dataset.action === "edit") {
      const card = getCards().find((c) => c.id === id);
      frontInput.value = card.front;
      backInput.value = card.back;
      editIdInput.value = card.id;
      submitBtn.textContent = "カードを更新";
      cancelBtn.hidden = false;
      frontInput.focus();
    }
  });
}

function initSetImport() {
  const select = document.getElementById("set-select");
  const status = document.getElementById("set-import-status");
  const sets = getCardSets();

  if (sets.length === 0) {
    document.querySelector(".set-import").hidden = true;
    return;
  }

  select.innerHTML = sets.map((s) => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}（${s.cards.length}枚）</option>`).join("");

  document.getElementById("set-import-btn").addEventListener("click", () => {
    const { added, skipped } = importSet(select.value);
    status.textContent = skipped > 0
      ? `${added}枚を追加しました（${skipped}枚は既に登録済みのためスキップ）`
      : `${added}枚を追加しました`;
    renderDeck();
  });
}

export function initDeck() {
  initCardForm();
  initSetImport();
}
