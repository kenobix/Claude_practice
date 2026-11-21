"use strict";

const button = document.querySelector("button[data-put]");
const status = document.querySelector("#action-status");

if (button && status) {
    button.addEventListener("click", async () => {
        const label = button.textContent.trim();
        if (!window.confirm(label + "？")) return;

        button.disabled = true;
        status.textContent = "処理中…";
        try {
            const response = await fetch(button.dataset.put, {
                method: "PUT",
                mode: "same-origin",
                redirect: "error",
                cache: "no-store",
            });
            if (response.ok) {
                window.location.reload();
                return;
            }
            status.textContent = response.status === 409
                ? "実行の状態が変わりました。画面を更新して確認してください。"
                : "操作を確認できませんでした。画面を更新して現在の状態を確認してください。";
        } catch {
            status.textContent = "通信が中断されました。画面を更新して現在の状態を確認してください。";
        }
        button.disabled = false;
    });
}
