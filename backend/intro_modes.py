"""Canonical set of intro-mode values for a Channel.

Single source of truth consumed by:
  - backend.routers.channels            -> intro_mode request validation
  - backend.agents.ready_video_curation -> _resolve_intro_mode's actual behavior
  - backend.models.channel              -> Channel.intro_mode docstring

Both call sites used to carry their own copy of this list (same name,
`_INTRO_MODES`, defined twice); a mode added/removed in one and forgotten in
the other would make the router accept/reject something curation doesn't
recognise, silently. One list, imported everywhere, makes that impossible.
"""
from __future__ import annotations

# "tts" is the only mode that existed before "voice_bank"/"text_only"/"mixed"
# were added -- every unset/unrecognised/degraded mode falls back to it (see
# ready_video_curation.py's _resolve_intro_mode).
INTRO_MODES: tuple[str, ...] = ("tts", "voice_bank", "text_only", "mixed")
