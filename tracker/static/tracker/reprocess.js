document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".reprocess-form");
  if (!form) return;
  const progress = form.querySelector("[data-reprocess-progress]");
  const button = form.querySelector("[data-reprocess-button]");
  form.addEventListener("submit", () => {
    if (button) {
      button.disabled = true;
      button.textContent = "Reprocesando...";
    }
    if (progress) {
      progress.hidden = false;
    }
  });
});
