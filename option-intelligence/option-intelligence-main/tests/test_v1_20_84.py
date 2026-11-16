"""v1.20.84: the login page layout (footer beside the form, sign-up fields on the Log in tab)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_login_page_has_one_column():
    h = (ROOT / "static/login.html").read_text(encoding="utf-8")
    body = h[h.index("<body"):h.index("<script")]
    assert body.count("<main") == 1 and "<footer" not in body and body.strip().endswith("</main>")


def test_hidden_rows_stay_hidden():
    css = (ROOT / "static/style.css").read_text(encoding="utf-8")
    assert ".auth-card label[hidden]{display:none}" in css
    assert css.index(".auth-card label{display:flex") < css.index(".auth-card label[hidden]{display:none}")
