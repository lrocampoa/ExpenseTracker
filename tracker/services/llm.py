"""LLM helpers for categorization fallback."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from openai import OpenAI

from tracker import models
from tracker.services.categorizer import CategorizationResult

logger = logging.getLogger(__name__)


def categorize_with_llm(trx: models.Transaction) -> Optional[CategorizationResult]:
    if not settings.LLM_CATEGORIZATION_ENABLED:
        return None
    if not settings.OPENAI_API_KEY or not settings.OPENAI_MODEL:
        logger.warning("LLM categorization enabled but OpenAI credentials missing.")
        return None

    cache_key = _cache_key(trx)
    cached = _load_cached_decision(cache_key, trx.user_id)
    if cached:
        category = cached["category"]
        metadata = cached["metadata"]
        return CategorizationResult(
            category=category,
            confidence=metadata.get("confidence", 0.6),
            source=f"llm-cache:{settings.OPENAI_MODEL}",
        )

    if _daily_limit_exceeded(trx.user_id):
        logger.info("Skipping LLM categorization: daily cap reached.")
        return None

    category = _call_openai_for_category(trx)
    if not category:
        return None

    metadata = category["metadata"]
    log = models.LLMDecisionLog.objects.create(
        email=trx.email,
        transaction=trx,
        decision_type=models.LLMDecisionLog.DecisionType.CATEGORIZATION,
        model_name=settings.OPENAI_MODEL,
        prompt=category["prompt"],
        response=category["raw_response"],
        tokens_prompt=category["usage"].get("input_tokens"),
        tokens_completion=category["usage"].get("output_tokens"),
        metadata=metadata,
        cache_key=cache_key,
        user=trx.user,
    )
    logger.debug("Stored LLM decision log %s", log.id)

    return CategorizationResult(
        category=category["category"],
        confidence=metadata.get("confidence", 0.7),
        source=f"llm:{settings.OPENAI_MODEL}",
    )


def _cache_key(trx: models.Transaction) -> str:
    base = "|".join(
        [
            (trx.merchant_name or "").strip().lower(),
            (trx.description or "").strip().lower(),
            str(trx.amount or ""),
            trx.currency_code or "",
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _load_cached_decision(cache_key: str, user_id: Optional[int]):
    qs = models.LLMDecisionLog.objects.select_related("transaction").filter(
        decision_type=models.LLMDecisionLog.DecisionType.CATEGORIZATION,
        cache_key=cache_key,
    )
    if user_id and hasattr(models.LLMDecisionLog, "user_id"):
        qs = qs.filter(user_id=user_id)
    log = qs.order_by("-created_at").first()
    if not log:
        return None
    metadata = log.metadata or {}
    category_id = metadata.get("category_id")
    if not category_id:
        return None
    try:
        category = models.Category.objects.get(id=category_id)
    except models.Category.DoesNotExist:
        return None
    return {
        "category": category,
        "metadata": metadata,
    }


def _daily_limit_exceeded(user_id: Optional[int]) -> bool:
    today = timezone.now().date()
    qs = models.LLMDecisionLog.objects.filter(
        decision_type=models.LLMDecisionLog.DecisionType.CATEGORIZATION,
        created_at__date=today,
    )
    if user_id and hasattr(models.LLMDecisionLog, "user_id"):
        qs = qs.filter(user_id=user_id)
    count = qs.count()
    return count >= settings.LLM_MAX_CALLS_PER_DAY


def _call_openai_for_category(trx: models.Transaction):
    categories_qs = models.Category.objects.filter(is_active=True)
    if trx.user_id and hasattr(models.Category, "user_id"):
        categories_qs = categories_qs.filter(Q(user=trx.user) | Q(user__isnull=True))
    categories = list(categories_qs)
    if not categories:
        logger.info("No categories available for LLM fallback.")
        return None

    category_lines = "\n".join(f"- {cat.name} (code: {cat.code})" for cat in categories)
    prompt = (
        "You classify personal finance transactions. "
        "Reply as JSON with keys category_code, category_name, confidence (0-1), and reasoning. "
        "Use one of the category codes provided. If nothing fits, pick the closest match.\n"
        f"Categories:\n{category_lines}\n\n"
        f"Transaction:\nMerchant: {trx.merchant_name or 'N/A'}\n"
        f"Description: {trx.description or 'N/A'}\n"
        f"Amount: {trx.amount} {trx.currency_code}\n"
    )

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": "You return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception:  # pragma: no cover - network failure
        logger.exception("OpenAI chat.completions call failed")
        return None

    message = response.choices[0].message.content
    data = _safe_json_loads(message)
    if not data:
        logger.warning("LLM returned non-JSON content: %s", message)
        return None

    code = (data.get("category_code") or "").lower()
    confidence = float(data.get("confidence", 0.5))
    category = _match_category_by_code_or_name(code, data.get("category_name"), categories)
    if not category:
        logger.warning("LLM returned unknown category %s", code)
        return None

    metadata = {
        "category_id": category.id,
        "category_code": category.code,
        "category_name": category.name,
        "confidence": confidence,
        "reasoning": data.get("reasoning"),
    }
    usage = {
        "input_tokens": getattr(response.usage, "prompt_tokens", None),
        "output_tokens": getattr(response.usage, "completion_tokens", None),
    }
    return {
        "category": category,
        "metadata": metadata,
        "usage": usage,
        "prompt": prompt,
        "raw_response": message,
    }


def _match_category_by_code_or_name(code: str, name: Optional[str], categories):
    if code:
        for cat in categories:
            if cat.code.lower() == code:
                return cat
    if name:
        name_l = name.lower()
        for cat in categories:
            if cat.name.lower() == name_l:
                return cat
    return None


def _safe_json_loads(content: str):
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None
