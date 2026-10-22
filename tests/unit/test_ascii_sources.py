"""Source and fixture files stay ASCII, so hidden or look-alike characters are always
written as escapes and visible in review."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
FILES = sorted(
    path
    for folder in ("src", "tests", "spikes")
    for path in (ROOT / folder).rglob("*")
    if path.suffix in (".py", ".json")
)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_file_is_ascii(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="surrogatepass")
    lines = [i for i, line in enumerate(text.splitlines(), 1) if not line.isascii()]
    assert not lines, f"non-ASCII characters on lines {lines[:10]}; write them as escapes"
