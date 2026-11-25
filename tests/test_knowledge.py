"""The knowledge pages say what the code does."""
import sys

from tests.conftest import ROOT


def test_pages_match_the_code():
    sys.path.insert(0, str(ROOT / "tools"))
    import knowledge
    for k, v in knowledge.pages().items():
        assert (ROOT / "docs/knowledge" / k).read_text(encoding="utf-8") == v, f"run tools/knowledge.py ({k})"
