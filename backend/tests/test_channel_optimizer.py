"""Regression tests for the Channel Optimizer's apply() consent gate.

apply() is the only thing standing between the auto-drafted branding
(title/description/keywords) and a real, unauthorized write to the user's
YouTube channel via yt.update_branding. A field only lands in the write
patch when action == "auto" (the current value was empty, so filling it is
safe) or when action == "confirm" AND the user explicitly listed that key in
confirmed_fields. These tests pin that gate down.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from backend.agents import channel_optimizer as optimizer
from backend.models import PlatformAccount

_BRANDING = {
    "keywords": "",                 # empty -> action "auto"
    "description": "Old description",  # set & differs -> action "confirm"
    "country": "US",                # set & differs -> action "confirm"
    "defaultLanguage": "pt",        # set & matches proposed -> action "nochange"
}

_CLIENT_TARGETS = {
    "keywords": "clipe futebol melhores momentos",
    "description": "Nova descricao com proposta de valor.",
}


def _account() -> PlatformAccount:
    return PlatformAccount(
        platform="youtube", display_name="Canal X", channel_id="UCabc123",
        niche="futebol", target_audience="torcedores", content_tone="animado",
        content_language="pt-BR", channel_optimization={},
    )


def _run_apply(confirmed_fields):
    account = _account()
    creds = {"token": "fake"}
    with patch("backend.agents.channel_optimizer.yt.get_branding",
               return_value={"ok": True, "channel_id": "UCabc123", "title": "Canal X",
                             "branding": dict(_BRANDING)}), \
         patch("backend.agents.channel_optimizer.yt.update_branding",
               return_value={"ok": True, "applied": {}, "dropped": []}) as fake_update, \
         patch("backend.agents.channel_optimizer.yt.ensure_playlist", return_value=None), \
         patch("backend.agents.channel_optimizer.yt.ensure_section", return_value=False):
        result = asyncio.run(optimizer.apply(
            account, creds, confirmed_fields=confirmed_fields, client_targets=_CLIENT_TARGETS,
        ))
    return result, fake_update, account


class ApplyConsentGateTests(unittest.TestCase):
    def test_confirm_field_without_confirmation_is_not_applied(self) -> None:
        result, fake_update, _account = _run_apply(confirmed_fields=None)

        fake_update.assert_called_once()
        patch_sent = fake_update.call_args.args[1]
        self.assertIn("keywords", patch_sent)          # auto -> always applied
        self.assertNotIn("description", patch_sent)    # confirm, not confirmed -> withheld
        self.assertNotIn("country", patch_sent)         # confirm, not confirmed -> withheld
        self.assertEqual(set(result["applied"]), {"keywords"})

    def test_auto_field_is_always_applied_regardless_of_confirmed_fields(self) -> None:
        result, fake_update, _account = _run_apply(confirmed_fields=["something_unrelated"])

        patch_sent = fake_update.call_args.args[1]
        self.assertIn("keywords", patch_sent)
        self.assertNotIn("description", patch_sent)
        self.assertNotIn("country", patch_sent)
        self.assertEqual(set(result["applied"]), {"keywords"})

    def test_confirmed_field_is_applied_alongside_auto_fields(self) -> None:
        result, fake_update, _account = _run_apply(confirmed_fields=["description"])

        patch_sent = fake_update.call_args.args[1]
        self.assertIn("keywords", patch_sent)
        self.assertIn("description", patch_sent)
        self.assertNotIn("country", patch_sent)         # still not confirmed
        self.assertEqual(set(result["applied"]), {"keywords", "description"})

    def test_all_confirmed_fields_are_applied(self) -> None:
        result, fake_update, _account = _run_apply(confirmed_fields=["description", "country"])

        patch_sent = fake_update.call_args.args[1]
        self.assertEqual(set(patch_sent), {"keywords", "description", "country"})
        self.assertEqual(set(result["applied"]), {"keywords", "description", "country"})

    def test_nochange_field_is_never_applied_even_if_confirmed(self) -> None:
        """defaultLanguage already matches the proposed value (action "nochange") --
        confirming it must not force a spurious write."""
        result, fake_update, _account = _run_apply(
            confirmed_fields=["description", "country", "defaultLanguage"],
        )

        patch_sent = fake_update.call_args.args[1]
        self.assertNotIn("defaultLanguage", patch_sent)
        self.assertNotIn("defaultLanguage", result["applied"])

    def test_applied_state_is_persisted_on_the_account(self) -> None:
        _result, _fake_update, account = _run_apply(confirmed_fields=["description"])

        self.assertTrue(account.channel_optimization["activated"])
        self.assertEqual(set(account.channel_optimization["applied"]), {"keywords", "description"})


if __name__ == "__main__":
    unittest.main()
