from __future__ import annotations

from django import forms

from tracker import models


class TransactionFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        label="Buscar",
        widget=forms.TextInput(attrs={"placeholder": "Comercio, descripción o referencia"}),
    )
    category = forms.ModelChoiceField(
        queryset=models.Category.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Categoría",
        empty_label="Todas",
    )
    card_last4 = forms.CharField(
        required=False,
        label="Tarjeta (últimos 4)",
        widget=forms.TextInput(attrs={"maxlength": 4}),
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

