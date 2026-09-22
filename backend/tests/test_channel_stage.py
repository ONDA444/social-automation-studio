"""channel_stage — estratégia de roteiro por maturidade do canal."""
from __future__ import annotations

import unittest

import pytest
from pydantic import ValidationError

from backend.agents.scriptwriter import _stage_prompt_block
from backend.routers.accounts import AccountUpdate


class StagePromptBlockTests(unittest.TestCase):
    def test_new_channel_gets_discovery_guidance(self) -> None:
        block = _stage_prompt_block("new")
        self.assertIn("NOVO", block)
        self.assertIn("DESCOBERTA", block)
        self.assertIn("inscrição", block.lower())

    def test_established_channel_gets_community_guidance(self) -> None:
        block = _stage_prompt_block("established")
        self.assertIn("ESTABELECIDO", block)
        self.assertIn("comunidade", block.lower())

    def test_growing_and_unknown_default(self) -> None:
        self.assertIn("CRESCIMENTO", _stage_prompt_block("growing"))
        # None/inválido caem no default "growing" sem quebrar o pipeline
        self.assertEqual(_stage_prompt_block(None), _stage_prompt_block("growing"))
        self.assertEqual(_stage_prompt_block("qualquer"), _stage_prompt_block("growing"))


class AccountUpdateStageValidationTests(unittest.TestCase):
    def test_accepts_known_stages(self) -> None:
        for stage in ("new", "growing", "established"):
            self.assertEqual(AccountUpdate(channel_stage=stage).channel_stage, stage)

    def test_rejects_unknown_stage(self) -> None:
        with pytest.raises(ValidationError):
            AccountUpdate(channel_stage="lendario")

    def test_omitted_stage_stays_none(self) -> None:
        self.assertIsNone(AccountUpdate().channel_stage)
