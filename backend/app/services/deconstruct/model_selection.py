"""Deconstruct model selection and configuration helpers."""
from typing import Optional

from sqlalchemy.orm import Session

from ...core.model_limits import ModelSafetyLimits, effective_model_limits
from ...database.models import APIConfig
from ...database.session import SessionLocal
from ...modules.model_runtime.application.execution import model_executor as LLMGateway
from .constants import DEFAULT_MAP_CONCURRENCY, MAP_MAX_TOKENS, MAX_MAP_CONCURRENCY


def module_options_from_payload(payload) -> dict:
    return {
        "golden_three": payload.include_golden_three,
        # Deconstruct is now a reading-analysis report. Project initialization
        # imports live in the cataloging workflow, so these legacy asset modules
        # stay disabled even if an older frontend sends them as true.
        "characters": False,
        "outline": False,
        "worldbuilding": False,
        "rhythm": payload.include_rhythm,
        "patterns": payload.include_patterns,
        "analysis_mode": analysis_mode_from_payload(payload),
    }


def map_concurrency_from_payload(payload) -> int:
    return max(1, min(payload.map_concurrency or DEFAULT_MAP_CONCURRENCY, MAX_MAP_CONCURRENCY))


def analysis_mode_from_payload(payload) -> str:
    return "fast"


def _deconstruct_default_model() -> Optional[str]:
    selection = LLMGateway.select_model_for_task(task_type="deconstruct")
    return selection.model


def provider_from_model(model: Optional[str], db: Session) -> Optional[str]:
    if model and ":" in model:
        return model.split(":", 1)[0]
    if model:
        cfg = db.query(APIConfig).filter(APIConfig.default_model == model).first()
        if cfg:
            return cfg.provider
    default_model = _deconstruct_default_model()
    if default_model and ":" in default_model:
        return default_model.split(":", 1)[0]
    return None


def models_from_payload(payload) -> tuple[Optional[str], Optional[str]]:
    base_model = payload.model or _deconstruct_default_model()
    map_model = payload.map_model or base_model
    reduce_model = payload.reduce_model or base_model or map_model
    return map_model, reduce_model


def model_limits_for(model: Optional[str]) -> ModelSafetyLimits:
    db = SessionLocal()
    try:
        try:
            provider = provider_from_model(model, db)
            model_name = model.split(":", 1)[1] if model and ":" in model else model
            config = db.query(APIConfig).filter(APIConfig.provider == provider).first() if provider else None
            if config:
                model_name = config.default_model if not model_name else model_name
                return effective_model_limits(
                    config.provider,
                    model_name,
                    max_output_tokens=config.max_output_tokens,
                    deconstruct_input_char_limit=config.deconstruct_input_char_limit,
                    deconstruct_item_char_limit=config.deconstruct_item_char_limit,
                )
            return effective_model_limits(provider, model_name)
        except Exception:
            provider = model.split(":", 1)[0] if model and ":" in model else None
            model_name = model.split(":", 1)[1] if model and ":" in model else model
            return effective_model_limits(provider, model_name)
    finally:
        db.close()


def model_output_limit_for(model: Optional[str], fallback: int = MAP_MAX_TOKENS) -> int:
    limits = model_limits_for(model)
    return limits.max_output_tokens or fallback


def map_output_limit_for(model: Optional[str]) -> int:
    configured = model_output_limit_for(model, MAP_MAX_TOKENS)
    return max(256, min(configured, MAP_MAX_TOKENS))


def limits_info_for(model: Optional[str]) -> dict:
    limits = model_limits_for(model)
    return {
        "max_output_tokens": limits.max_output_tokens,
        "deconstruct_input_char_limit": limits.deconstruct_input_char_limit,
        "deconstruct_item_char_limit": limits.deconstruct_item_char_limit,
    }
