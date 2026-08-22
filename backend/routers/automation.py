"""Automation rules CRUD — a página Automações lê/edita daqui. Os gatilhos e
ações válidos vêm da engine (fonte única: backend/agents/automation_engine.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.automation_engine import ACTIONS, TRIGGERS
from backend.database import get_db
from backend.models import AutomationRule

router = APIRouter(prefix="/automation", tags=["automation"])


class RuleIn(BaseModel):
    name: str = Field(..., min_length=3, max_length=160)
    trigger: str
    action: str
    enabled: bool = True
    config: dict = Field(default_factory=dict)

    @field_validator("trigger")
    @classmethod
    def _valid_trigger(cls, v: str) -> str:
        if v not in TRIGGERS:
            raise HTTPException(400, f"gatilho inválido: {v}. Válidos: {', '.join(TRIGGERS)}")
        return v

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in ACTIONS:
            raise HTTPException(400, f"ação inválida: {v}. Válidas: {', '.join(ACTIONS)}")
        return v


class RulePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict | None = None


@router.get("/rules")
def list_rules(db: Session = Depends(get_db)):
    rules = db.execute(select(AutomationRule).order_by(AutomationRule.id)).scalars().all()
    return {
        "rules": [r.to_dict() for r in rules],
        "triggers": [{"id": k, "label": v} for k, v in TRIGGERS.items()],
        "actions": [{"id": k, "label": v} for k, v in ACTIONS.items()],
    }


@router.post("/rules")
def create_rule(payload: RuleIn, db: Session = Depends(get_db)):
    rule = AutomationRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule.to_dict()


@router.patch("/rules/{rule_id}")
def patch_rule(rule_id: int, payload: RulePatch, db: Session = Depends(get_db)):
    rule = db.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(404, "regra não encontrada")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return rule.to_dict()


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(404, "regra não encontrada")
    db.delete(rule)
    db.commit()
    return {"deleted": rule_id}
