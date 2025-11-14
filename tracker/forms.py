from __future__ import annotations

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format

from tracker import models
from tracker.services import account_seeding


class TransactionFilterForm(forms.Form):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        qs = models.Category.objects.filter(is_active=True)
        if user is not None and hasattr(models.Category, "user_id"):
            qs = qs.filter(user=user)
        self.fields["category"].queryset = qs.order_by("name")
        sub_qs = models.Subcategory.objects.select_related("category")
        if user is not None and hasattr(models.Subcategory, "user_id"):
            sub_qs = sub_qs.filter(user=user)
        self.fields["subcategory"].queryset = sub_qs.order_by("category__name", "name")

        card_choices = [("", "Todas las tarjetas")]
        card_qs = models.Card.objects.filter(is_active=True)
        if user is not None and hasattr(models.Card, "user_id"):
            card_qs = card_qs.filter(Q(user=user) | Q(user__isnull=True))
        last4_values = list(card_qs.values_list("last4", flat=True))
        if user is not None and hasattr(models.Transaction, "user_id"):
            tx_last4 = (
                models.Transaction.objects.filter(user=user)
                .exclude(card_last4="")
                .values_list("card_last4", flat=True)
                .distinct()
            )
            last4_values.extend(tx_last4)
        unique_last4 = sorted({val for val in last4_values if val})
        card_choices += [(val, f"**** {val}") for val in unique_last4]
        self.fields["card_last4"].choices = card_choices

        merchant_choices = [("", "Todos los comercios")]
        merchant_qs = models.Transaction.objects.exclude(merchant_name="")
        if user is not None and hasattr(models.Transaction, "user_id"):
            merchant_qs = merchant_qs.filter(user=user)
        merchant_names = (
            merchant_qs.order_by("merchant_name")
            .values_list("merchant_name", flat=True)
            .distinct()
        )
        merchant_choices += [(name, name) for name in merchant_names]
        self.fields["merchant"].choices = merchant_choices
    search = forms.CharField(
        required=False,
        label="Buscar",
        widget=forms.TextInput(attrs={"placeholder": "Comercio, descripción o referencia"}),
    )
    merchant = forms.ChoiceField(
        required=False,
        label="Comercio",
        choices=[],
    )
    category = forms.ModelChoiceField(
        queryset=models.Category.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Categoría",
        empty_label="Todas",
    )
    uncategorized = forms.BooleanField(
        required=False,
        label="Solo sin categoría",
    )
    subcategory = forms.ModelChoiceField(
        queryset=models.Subcategory.objects.none(),
        required=False,
        label="Subcategoría",
        empty_label="Todas",
    )
    card_last4 = forms.ChoiceField(
        required=False,
        label="Tarjeta (últimos 4)",
        choices=[],
    )
    date_from = forms.DateField(
        required=False,
        label="Desde",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label="Hasta",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    min_amount = forms.DecimalField(
        required=False,
        label="Monto mínimo",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    max_amount = forms.DecimalField(
        required=False,
        label="Monto máximo",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )

    def clean_card_last4(self):
        data = (self.cleaned_data.get("card_last4") or "").strip()
        if data and not data.isdigit():
            raise forms.ValidationError("Use solo dígitos para la tarjeta.")
        return data

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        subcategory = cleaned.get("subcategory")
        uncategorized = cleaned.get("uncategorized")
        if subcategory and category and subcategory.category != category:
            self.add_error("subcategory", "La subcategoría no pertenece a la categoría seleccionada.")
        if uncategorized and (category or subcategory):
            self.add_error(
                "uncategorized",
                "No combines el filtro de 'sin categoría' con una categoría o subcategoría específica.",
            )
        return cleaned


class TransactionUpdateForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        qs = models.Category.objects.filter(is_active=True)
        if user is not None and hasattr(models.Category, "user_id"):
            qs = qs.filter(user=user)
        self.fields["category"].queryset = qs.order_by("name")
        sub_qs = models.Subcategory.objects.select_related("category")
        if user is not None and hasattr(models.Subcategory, "user_id"):
            sub_qs = sub_qs.filter(user=user)
        self.fields["subcategory"].queryset = sub_qs.order_by("category__name", "name")

    class Meta:
        model = models.Transaction
        fields = [
            "merchant_name",
            "description",
            "amount",
            "currency_code",
            "transaction_date",
            "category",
            "subcategory",
        ]
        widgets = {
            "transaction_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        subcategory = cleaned.get("subcategory")
        if subcategory and subcategory.category != category:
            self.add_error("subcategory", "La subcategoría no pertenece a la categoría seleccionada.")
        return cleaned


class CardForm(forms.ModelForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    class Meta:
        model = models.Card
        fields = ["label", "last4", "bank_name", "expense_account", "is_active", "notes"]
        widgets = {
            "last4": forms.TextInput(attrs={"maxlength": 4}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class CardLabelForm(forms.Form):
    card_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    last4 = forms.CharField(
        max_length=4,
        widget=forms.HiddenInput(),
    )
    label = forms.CharField(
        max_length=128,
        label="Nombre",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Tarjeta viajes"}),
    )
    expense_account = forms.ChoiceField(
        required=False,
        label="Cuenta de gastos",
        choices=[],
        widget=forms.Select(
            attrs={"class": "expense-select", "data-expense-select": "1"}
        ),
    )
    new_expense_account = forms.CharField(
        max_length=128,
        required=False,
        label="Nueva cuenta",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Viajes"}),
    )

    def __init__(self, *args, user=None, expense_choices=None, **kwargs):
        self.user = user
        self.expense_choices = expense_choices or []
        super().__init__(*args, **kwargs)
        self._configure_expense_choices()

    def _configure_expense_choices(self):
        def normalize(values):
            seen = []
            for value in values:
                if value and value not in seen:
                    seen.append(value)
            return seen

        existing_values = normalize(self.expense_choices)
        initial_value = (
            (self.initial.get("expense_account") if hasattr(self, "initial") else None)
            or (self.data.get("expense_account") if hasattr(self, "data") else None)
        )
        if initial_value and initial_value not in existing_values and initial_value != "__new__":
            existing_values.insert(0, initial_value)
        choices = [("", "Selecciona una cuenta")]
        choices.extend((value, value) for value in existing_values)
        choices.append(("__new__", "Crear nueva cuenta…"))
        self.fields["expense_account"].choices = choices

    def clean_card_id(self):
        card_id = self.cleaned_data.get("card_id")
        if not card_id:
            return None
        if not self.user:
            raise forms.ValidationError("No se puede validar la tarjeta sin usuario.")
        if not models.Card.objects.filter(pk=card_id, user=self.user).exists():
            raise forms.ValidationError("Tarjeta no encontrada.")
        return card_id

    def clean_last4(self):
        last4 = (self.cleaned_data.get("last4") or "").strip()
        if not last4.isdigit() or len(last4) != 4:
            raise forms.ValidationError("Los últimos 4 dígitos no son válidos.")
        return last4

    def clean(self):
        cleaned = super().clean()
        card_id = cleaned.get("card_id")
        last4 = cleaned.get("last4")
        expense_choice = cleaned.get("expense_account") or ""
        new_expense = (cleaned.get("new_expense_account") or "").strip()
        if expense_choice == "__new__":
            if not new_expense:
                self.add_error("new_expense_account", "Ingresa el nombre de la nueva cuenta.")
            cleaned["resolved_expense_account"] = new_expense
        else:
            cleaned["resolved_expense_account"] = expense_choice
        if not card_id and last4 and models.Card.objects.filter(last4=last4).exists():
            raise forms.ValidationError("Esta tarjeta ya fue etiquetada.")
        return cleaned

    def save(self):
        if not self.user:
            raise ValueError("CardLabelForm.save() requiere un usuario.")
        card_id = self.cleaned_data.get("card_id")
        payload = {
            "label": self.cleaned_data["label"],
            "expense_account": self.cleaned_data.get("resolved_expense_account", ""),
        }
        if payload["expense_account"]:
            account_seeding.ensure_account(self.user, payload["expense_account"])
        if card_id:
            card = models.Card.objects.get(pk=card_id, user=self.user)
            for attr, value in payload.items():
                setattr(card, attr, value)
            card.save(update_fields=list(payload.keys()))
            return card
        return models.Card.objects.create(
            user=self.user,
            last4=self.cleaned_data["last4"],
            **payload,
        )


class CategoryRuleForm(forms.ModelForm):
    class Meta:
        model = models.CategoryRule
        fields = [
            "category",
            "subcategory",
            "match_field",
            "match_type",
            "match_value",
            "card_last4",
            "priority",
            "is_active",
            "notes",
        ]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        qs = models.Category.objects.all()
        if user is not None and hasattr(models.Category, "user_id"):
            qs = qs.filter(user=user)
        self.fields["category"].queryset = qs.order_by("name")
        sub_qs = models.Subcategory.objects.select_related("category")
        if user is not None and hasattr(models.Subcategory, "user_id"):
            sub_qs = sub_qs.filter(user=user)
        self.fields["subcategory"].queryset = sub_qs.order_by("category__name", "name")
        self.fields["subcategory"].empty_label = "Sin subcategoría"

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        subcategory = cleaned.get("subcategory")
        if subcategory and category and subcategory.category_id != category.id:
            self.add_error(
                "subcategory",
                "La subcategoría no pertenece a la categoría seleccionada.",
            )
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        if hasattr(obj, "user_id") and not obj.user_id:
            obj.user = self.user
        if commit:
            obj.save()
        return obj


class RuleSuggestionDecisionForm(forms.Form):
    suggestion_id = forms.IntegerField(widget=forms.HiddenInput())
    action = forms.ChoiceField(
        choices=(("accept", "Aceptar"), ("reject", "Rechazar")),
        widget=forms.HiddenInput(),
    )
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), max_length=255)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = models.Category
        fields = ["name", "code", "description", "budget_limit", "is_active"]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if hasattr(obj, "user") and not obj.user:
            obj.user = self.user
        if commit:
            obj.save()
        return obj


class SubcategoryForm(forms.ModelForm):
    class Meta:
        model = models.Subcategory
        fields = ["category", "name", "code", "budget_limit"]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        qs = models.Category.objects.filter(is_active=True)
        if user is not None and hasattr(models.Category, "user_id"):
            qs = qs.filter(Q(user=user))
        self.fields["category"].queryset = qs.order_by("name")

    def save(self, commit=True):
        obj = super().save(commit=False)
        if hasattr(obj, "user") and not obj.user:
            obj.user = self.user
        if commit:
            obj.save()
        return obj


class CategoryInlineForm(forms.ModelForm):
    class Meta:
        model = models.Category
        fields = ["name", "description", "budget_limit", "is_active"]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class SubcategoryInlineForm(forms.ModelForm):
    class Meta:
        model = models.Subcategory
        fields = ["category", "name", "budget_limit"]

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        qs = models.Category.objects.all()
        if user is not None and hasattr(models.Category, "user_id"):
            qs = qs.filter(Q(user=user))
        self.fields["category"].queryset = qs.order_by("name")


class ImportForm(forms.Form):
    RECENT_CHOICE = "recent"
    YEARS_CHOICES = (
        ("1", "Último año"),
        ("2", "Últimos 2 años"),
        ("3", "Últimos 3 años"),
    )
    years = forms.ChoiceField(choices=YEARS_CHOICES, label="Rango a importar", initial=RECENT_CHOICE)

    def __init__(self, *args, user=None, last_transaction_date=None, **kwargs):
        self.user = user
        self._provided_last_transaction = last_transaction_date
        super().__init__(*args, **kwargs)
        self._recent_start_date = self._resolve_recent_start_date()
        self._recent_label = self._build_recent_label()
        choices = list(self.YEARS_CHOICES)
        if self._recent_start_date:
            choices.insert(0, (self.RECENT_CHOICE, self._recent_label))
            self.fields["years"].initial = self.RECENT_CHOICE
        else:
            self.fields["years"].initial = "1"
        self.fields["years"].choices = choices

    @property
    def recent_start_date(self):
        return self._recent_start_date

    @property
    def recent_choice_label(self):
        return self._recent_label

    def _resolve_recent_start_date(self):
        if self._provided_last_transaction:
            return timezone.localdate(self._provided_last_transaction)
        if not self.user or not hasattr(models.Transaction, "user_id"):
            return None
        last_transaction = (
            models.Transaction.objects.filter(user=self.user)
            .order_by("-transaction_date", "-created_at")
            .values_list("transaction_date", flat=True)
            .first()
        )
        if last_transaction:
            return timezone.localdate(last_transaction)
        return None

    def _build_recent_label(self):
        if self._recent_start_date:
            formatted = date_format(self._recent_start_date, "DATE_FORMAT")
            return f"Última actualización detectada ({formatted} → hoy)"
        return "Desde hoy (sin histórico previo)"
