(function () {
  function anyModalOpen() {
    return document.querySelector(".expense-modal.is-open");
  }

  function initExpensePickers() {
    const forms = document.querySelectorAll(".card-inline-form");
    forms.forEach((form) => {
      const select =
        form.querySelector("[data-expense-select]") ||
        form.querySelector("select[name$='expense_account']");
      const modal = form.querySelector("[data-expense-modal]");
      const trigger = form.querySelector("[data-expense-trigger]");
      const cancelButtons = form.querySelectorAll("[data-expense-cancel]");
      const confirm = form.querySelector("[data-expense-confirm]");
      const newInput = form.querySelector("input[name$='new_expense_account']");
      if (!select || !modal || !newInput) {
        return;
      }

      select.dataset.previousValue = select.value || "";
      const newOption = select.querySelector("option[value='__new__']");
      if (newOption && !select.dataset.newOptionLabel) {
        select.dataset.newOptionLabel = newOption.textContent || "";
      }

      const updateNewOptionLabel = (label) => {
        if (!newOption) {
          return;
        }
        if (label) {
          newOption.textContent = label;
        } else {
          newOption.textContent = select.dataset.newOptionLabel || "Crear nueva cuenta…";
        }
      };

      const openPopout = () => {
        modal.classList.add("is-open");
        document.body.classList.add("expense-modal-open");
        newInput.focus();
      };

      const closePopout = (reset = false) => {
        modal.classList.remove("is-open");
        if (reset) {
          select.value = select.dataset.previousValue || "";
          updateNewOptionLabel("");
        }
        if (select.value !== "__new__") {
          newInput.value = "";
          updateNewOptionLabel("");
        }
        if (!anyModalOpen()) {
          document.body.classList.remove("expense-modal-open");
        }
      };

      if (modal.dataset.initialOpen === "1") {
        openPopout();
      }

      select.setAttribute("data-expense-select", "1");

      select.addEventListener("focus", () => {
        select.dataset.previousValue = select.value || "";
      });

      select.addEventListener("change", () => {
        if (select.value === "__new__") {
          openPopout();
        } else {
          closePopout();
        }
      });

      trigger?.addEventListener("click", () => {
        select.dataset.previousValue = select.value || "";
        select.value = "__new__";
        openPopout();
      });

      cancelButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
          closePopout(true);
        });
      });

      confirm?.addEventListener("click", () => {
        const trimmed = newInput.value.trim();
        if (!trimmed) {
          newInput.focus();
          return;
        }
        select.value = "__new__";
        updateNewOptionLabel(`Nueva: ${trimmed}`);
        closePopout(false);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initExpensePickers);
  } else {
    initExpensePickers();
  }
})();
