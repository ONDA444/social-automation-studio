"""Minimal regression test for ComplianceAgent approve/block behavior."""
from backend.agents.compliance_agent import ComplianceAgent


def test_financial_promise_in_title_blocks_content():
    """A YouTube title promising easy money must block approval, not slip
    through to the human review queue.
    """
    seo = {
        "youtube": {"title": "GANHE DINHEIRO FÁCIL AGORA", "description": "", "tags": ["a"]},
        "tiktok": {"caption": "ok #fyp"},
        "instagram": {"caption": "hi", "hashtags": []},
    }

    agent = ComplianceAgent(job_id=1, context={}, emit=False)
    report = agent._check(seo, {}, [], ["youtube", "tiktok", "instagram"])

    assert report["status"] == "blocked"
    assert any("financeiros" in b for b in report["blocks"])


def test_clean_content_is_approved():
    """Content with no policy violations must be approved so it can reach
    the human approval queue.
    """
    seo = {
        "youtube": {"title": "Um vídeo normal sobre gatos", "description": "", "tags": ["gatos"]},
        "tiktok": {"caption": "confira #gatos #fyp"},
        "instagram": {"caption": "olha meu gato", "hashtags": ["#gatos"]},
    }

    agent = ComplianceAgent(job_id=2, context={}, emit=False)
    report = agent._check(seo, {}, [], ["youtube", "tiktok", "instagram"])

    assert report["status"] == "approved"
    assert report["blocks"] == []


def test_ai_narrated_video_requires_disclosure_flag():
    """AI-narrated video must carry the disclosure action (suggestion + machine
    flag) but still reach approvals — the API can't tick the Studio checkbox.
    """
    seo = {"youtube": {"title": "Como dobrar seu FPS", "description": "guia",
                       "tags": ["fps"]}}
    agent = ComplianceAgent(job_id=3, context={}, emit=False)
    report = agent._check(seo, {}, [], ["youtube"],
                          {"ai_narrated": True, "source": None})

    assert report["status"] == "approved"
    assert "ai_disclosure_required" in report["flags"]
    assert any("conteúdo alterado" in s for s in report["suggestions"])


def test_drive_pure_reupload_blocks():
    """Drive video without the curation layer is the YPP reused-content pattern
    and must block with a message that tells exactly how to fix.
    """
    seo = {"youtube": {"title": "Melhores momentos", "description": "x", "tags": ["t"]}}
    main = {"main_video_path": "/out/ready_video.mp4"}
    agent = ComplianceAgent(job_id=4, context={}, emit=False)
    report = agent._check(seo, main, [], ["youtube"],
                          {"ai_narrated": False, "source": "drive_ready_video"})

    assert report["status"] == "blocked"
    assert any("reutilizado" in b for b in report["blocks"])


def test_drive_curated_passes_with_check_note():
    """Curated Drive video (curated_ prefix) passes — with a reminder to verify
    the commentary is audible.
    """
    seo = {"youtube": {"title": "Melhores momentos", "description": "x", "tags": ["t"]}}
    main = {"main_video_path": "/out/curated_ready_video.mp4"}
    agent = ComplianceAgent(job_id=5, context={}, emit=False)
    report = agent._check(seo, main, [], ["youtube"],
                          {"ai_narrated": False, "source": "drive_ready_video"})

    assert report["status"] == "approved"
    assert any("audível" in s for s in report["suggestions"])


def test_affiliate_links_without_disclosure_suggest():
    """Links in the description without a disclosure sentence must suggest one
    (FTC + YouTube paid-promotion policy).
    """
    seo = {"youtube": {"title": "Ferramentas que uso", "description": "compre aqui https://loja.com/x",
                       "tags": ["t"]}}
    agent = ComplianceAgent(job_id=6, context={}, emit=False)
    report = agent._check(seo, {}, [], ["youtube"])

    assert report["status"] == "approved"
    assert any("afiliado" in s for s in report["suggestions"])


def test_affiliate_links_with_disclosure_pass_clean():
    """Same links WITH the disclosure sentence must not suggest anything."""
    seo = {"youtube": {"title": "Ferramentas que uso",
                       "description": "compre aqui https://loja.com/x (links afiliados)",
                       "tags": ["t"]}}
    agent = ComplianceAgent(job_id=7, context={}, emit=False)
    report = agent._check(seo, {}, [], ["youtube"])

    assert report["status"] == "approved"
    assert not any("afiliado" in s for s in report["suggestions"])


def test_spammy_title_punctuation_suggests():
    """Title stuffed with !?!? reads as spam and must suggest restraint."""
    seo = {"youtube": {"title": "INCRÍVEL!!! Você não vai acreditar?!?!", "description": "",
                       "tags": ["t"]}}
    agent = ComplianceAgent(job_id=8, context={}, emit=False)
    report = agent._check(seo, {}, [], ["youtube"])

    assert report["status"] == "approved"
    assert any("spam" in s for s in report["suggestions"])
