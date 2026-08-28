"""Pure unit test of the markdown->PDF rendering logic. No Temporal server
needed -- calls the same markdown/xhtml2pdf code path the activity uses,
directly, so this runs fast in CI on every push.
"""

import io
import sys
from pathlib import Path

import markdown
from xhtml2pdf import pisa

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "poc"))

FIXTURE = REPO_ROOT / "poc" / "fixtures" / "sample.md"


def render(md_text: str) -> bytes:
    html = markdown.markdown(md_text)
    out = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=out)
    assert result.err == 0, f"xhtml2pdf reported {result.err} error(s)"
    return out.getvalue()


def test_renders_fixture_to_valid_pdf():
    md_text = FIXTURE.read_text()
    pdf_bytes = render(md_text)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 0


def test_renders_simple_markdown_features():
    pdf_bytes = render("# Title\n\n**bold** and a list:\n\n- one\n- two\n")
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500  # a real multi-element PDF, not an empty stub
