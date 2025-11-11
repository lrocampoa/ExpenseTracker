document.addEventListener("DOMContentLoaded", () => {
  const closeRows = (selector) => {
    document.querySelectorAll(selector).forEach((row) => {
      row.classList.remove("is-visible");
    });
  };

  const focusFirstInput = (row) => {
    const firstInput = row.querySelector("input, select, textarea");
    if (firstInput) {
      firstInput.focus();
    }
  };

  document.addEventListener("click", (event) => {
    const editBtn = event.target.closest("[data-edit-toggle]");
    if (editBtn) {
      event.preventDefault();
      const target = editBtn.getAttribute("data-edit-toggle");
      const row = document.querySelector(`[data-edit-row="${target}"]`);
      if (!row) {
        return;
      }
      const alreadyOpen = row.classList.contains("is-visible");
      closeRows("[data-edit-row].is-visible");
      closeRows("[data-create-row].is-visible");
      if (!alreadyOpen) {
        row.classList.add("is-visible");
        row.scrollIntoView({ behavior: "smooth", block: "center" });
        focusFirstInput(row);
      }
      return;
    }

    const editCancel = event.target.closest("[data-edit-cancel]");
    if (editCancel) {
      event.preventDefault();
      const target = editCancel.getAttribute("data-edit-cancel");
      const row = document.querySelector(`[data-edit-row="${target}"]`);
      if (row) {
        row.classList.remove("is-visible");
        row.querySelector("form")?.reset();
      }
      return;
    }

    const createBtn = event.target.closest("[data-create-toggle]");
    if (createBtn) {
      event.preventDefault();
      const target = createBtn.getAttribute("data-create-toggle");
      const row = document.querySelector(`[data-create-row="${target}"]`);
      if (!row) {
        return;
      }
      const alreadyOpen = row.classList.contains("is-visible");
      closeRows("[data-create-row].is-visible");
      closeRows("[data-edit-row].is-visible");
      if (!alreadyOpen) {
        row.classList.add("is-visible");
        row.scrollIntoView({ behavior: "smooth", block: "center" });
        focusFirstInput(row);
      }
      return;
    }

    const createCancel = event.target.closest("[data-create-cancel]");
    if (createCancel) {
      event.preventDefault();
      const target = createCancel.getAttribute("data-create-cancel");
      const row = document.querySelector(`[data-create-row="${target}"]`);
      if (row) {
        row.classList.remove("is-visible");
        row.querySelector("form")?.reset();
      }
    }
  });

  const replaceSection = (doc, selector) => {
    const fresh = doc.querySelector(selector);
    const current = document.querySelector(selector);
    if (fresh && current) {
      current.replaceWith(fresh);
    } else if (selector === ".flash-group") {
      if (fresh && !current) {
        const main = document.querySelector(".app-main");
        if (main) {
          main.insertAdjacentElement("afterbegin", fresh);
        }
      } else if (!fresh && current) {
        current.remove();
      }
    }
  };

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-inline-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    const confirmMsg = form.dataset.confirm;
    if (confirmMsg && !window.confirm(confirmMsg)) {
      return;
    }
    const submitButton =
      form.querySelector("button[type='submit'], input[type='submit']") || null;
    submitButton?.setAttribute("disabled", "disabled");
    const formData = new FormData(form);
    fetch(window.location.href, {
      method: form.getAttribute("method") || "POST",
      body: formData,
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
    })
      .then((response) => response.text())
      .then((html) => {
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");
        ["#categories-table", "#subcategories-table", "#rules-table", ".flash-group"].forEach(
          (selector) => replaceSection(doc, selector)
        );
      })
      .catch(() => {
        window.location.reload();
      })
      .finally(() => {
        submitButton?.removeAttribute("disabled");
      });
  });
});
