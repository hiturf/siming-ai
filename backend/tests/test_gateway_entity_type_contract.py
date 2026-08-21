"""Keep Gateway capture producers aligned with the public sync protocol."""

from __future__ import annotations

from typing import get_args

from app.modules.gateway.application.contracts import (
    SYNC_ENTITY_TYPES,
    EntityType,
    SyncMutation,
)
from app.services.gateway_legacy_replication import RECORD_SPECS


def test_every_captured_entity_type_is_accepted_by_sync_mutations():
    captured_entity_types = {spec.entity_type for spec in RECORD_SPECS}

    assert set(get_args(EntityType)) == set(SYNC_ENTITY_TYPES)
    assert captured_entity_types <= set(SYNC_ENTITY_TYPES)

    for entity_type in captured_entity_types:
        mutation = SyncMutation(
            mutation_id=f"contract-{entity_type}",
            project_id="project-contract",
            entity_type=entity_type,
            entity_id="entity-contract",
            operation="upsert",
            base_revision=0,
            payload={"id": "entity-contract"},
        )
        assert mutation.entity_type == entity_type
