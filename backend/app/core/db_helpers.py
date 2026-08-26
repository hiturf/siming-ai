"""Shared database query helpers used by routers."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from sqlalchemy.orm import Session

from .exceptions import NotFoundError, ValidationError
from ..database.models import Character, OutlineNode, Project


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    return project


def existing_project_ids(db: Session, project_ids: Iterable[str]) -> set[str]:
    values = {str(value).strip() for value in project_ids if str(value).strip()}
    if not values:
        return set()
    return {
        str(row[0])
        for row in db.query(Project.id).filter(Project.id.in_(values)).all()
    }


def get_character_or_404(db: Session, project_id: str, character_id: str) -> Character:
    character = (
        db.query(Character)
        .filter(Character.id == character_id, Character.project_id == project_id)
        .first()
    )
    if not character:
        raise NotFoundError("角色不存在")
    return character


def get_outline_node_or_404(db: Session, project_id: str, outline_node_id: Optional[str]) -> Optional[OutlineNode]:
    if not outline_node_id:
        return None
    node = (
        db.query(OutlineNode)
        .filter(OutlineNode.id == outline_node_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not node:
        raise ValidationError("关联大纲节点必须属于当前作品")
    return node
