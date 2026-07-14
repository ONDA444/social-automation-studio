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
