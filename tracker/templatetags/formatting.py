from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def format_number(value, decimals=2):
    """
    Format a numeric value with thousands separators and fixed decimals.

    Usage:
        {{ amount|format_number }}          -> 2 decimals by default
        {{ count|format_number:0 }}        -> no decimals
        {{ percent|format_number:1 }}      -> 1 decimal
    """
    if value in (None, ""):
        return ""
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        decimals = 2
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    fmt = f"{{:,.{decimals}f}}"
    return fmt.format(number)
