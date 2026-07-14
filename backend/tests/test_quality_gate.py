"""Minimal regression tests for the quality_gate.assess() approval heuristics.

Covers the critical decision: does a script pass straight through (auto-publish)
or get rejected (retry / manual review)? Regressions here could let generic or
placeholder-laden scripts ship automatically, or block valid ones.
"""
from backend.agents.quality_gate import assess, looks_generic


def _script(text: str, **overrides) -> dict:
    base = {
        "narration_text": text,
        "format": "long",
        "content_type": "sports_highlights",
    }
    base.update(overrides)
    return base


GOOD_NARRATION = (
    "Em 1998, Zinedine Zidane marcou dois gols na final da Copa do Mundo contra "
    "o Brasil, no Stade de France, diante de 75000 torcedores. A seleção francesa "
    "venceu por 3 a 0 e conquistou seu primeiro título mundial, um marco que mudou "
    "a história do futebol nacional para sempre e inspirou gerações de jogadores "
    "que vieram depois, criando um legado duradouro na cultura esportiva do país "
    "e no imaginário coletivo de milhões de fãs ao redor do mundo inteiro hoje."
)


def test_assess_passes_real_fact_grounded_script():
    ok, reason = assess(_script(GOOD_NARRATION))
    assert ok is True
    assert reason == "ok"
    assert looks_generic(_script(GOOD_NARRATION)) is False


def test_assess_rejects_unfilled_placeholder():
    text = GOOD_NARRATION + " O gol foi marcado por {nome do jogador} no minuto final."
    ok, reason = assess(_script(text))
    assert ok is False
    assert "placeholder" in reason


def test_assess_rejects_vague_filler_with_no_concrete_anchor():
    vague_text = (
        "Ele é simplesmente incrível, um talento sem igual, uma habilidade sem "
        "comparação. Dominou os campos com uma performance impressionante e "
        "inesquecível, um verdadeiro ícone extraordinário que todos admiram "
        "profundamente pela sua trajetória sensacional e espetacular no esporte "
        "que ama, deixando um legado memorável para as futuras gerações de fãs "
        "que sempre vão se lembrar desse momento surreal e absurdo de pura "
        "grandeza, um exemplo raro de dedicação e paixão que poucos conseguem "
        "igualar ao longo de toda uma carreira tão marcante e cheia de emoção."
    )
    ok, reason = assess(_script(vague_text))
    assert ok is False
    assert "âncora concreta" in reason
    assert looks_generic(_script(vague_text)) is True


def test_assess_rejects_stunted_short_script():
    ok, reason = assess(_script("Muito curto.", format="short"))
    assert ok is False
    assert "raso" in reason
