"""Constants for the project cataloging pipeline."""

JOB_RUNNING_STATUSES = {"queued", "running", "waiting_confirmation", "paused", "paused_on_failure"}

APPLY_ORDER = {
    "chapter_summary": 10,
    "outline_create": 20,
    "outline_update": 21,
    "character_create": 30,
    "character_update": 31,
    "character_state_update": 32,
    "character_timeline": 33,
    "character_relationship": 34,
    "character_merge_candidate": 35,
    "worldbuilding_create": 40,
    "worldbuilding_update": 41,
    "worldbuilding_timeline": 42,
    "chapter_link": 50,
}

from ..story_granularity import VALID_CANDIDATE_TYPES
from ..story_granularity import WORLD_DIMENSIONS  # noqa: F401 - compatibility export

VALID_ITEM_TYPES = set(VALID_CANDIDATE_TYPES)

CATALOGING_MAX_TOKENS = 20000
CATALOGING_TIMEOUT_SECONDS = 300
CATALOGING_STAGE_MAX_ATTEMPTS = 3
CATALOGING_FACTS_PROMPT_LIMIT = 12000
CATALOGING_CONTEXT_FACT_MATCH_LIMIT = 12000
CATALOGING_CHARACTER_INDEX_LIMIT = 180
CATALOGING_WORLDBUILDING_INDEX_LIMIT = 220
CATALOGING_RELEVANT_CHARACTER_LIMIT = 12
CATALOGING_RELEVANT_WORLDBUILDING_LIMIT = 12
