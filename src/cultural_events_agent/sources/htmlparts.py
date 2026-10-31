"""Leitura mínima de HTML (stdlib): links, scripts e o texto de uma seção por data-testid."""

from dataclasses import dataclass, field
from html.parser import HTMLParser

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}
_BLOCK = {
    "p", "div", "section", "article", "header", "footer", "li", "ul", "ol", "br", "tr", "table",
    "h1", "h2", "h3", "h4", "h5", "h6", "main", "nav", "aside", "form",
}  # fmt: skip
_SKIP_TEXT = {"script", "style", "noscript", "template"}


@dataclass
class PageParts:
    links: list[str] = field(default_factory=list)
    scripts: list[tuple[dict[str, str | None], str]] = field(default_factory=list)
    section_text: str = ""
    body_text: str = ""


class _Collector(HTMLParser):
    def __init__(self, section_testid: str) -> None:
        super().__init__(convert_charrefs=True)
        self._testid = section_testid
        self.parts = PageParts()
        self._script_attrs: dict[str, str | None] | None = None
        self._script_text: list[str] = []
        self._skip_depth = 0
        self._section_depth = 0
        self._section_chunks: list[str] = []
        self._body_chunks: list[str] = []
        self._section_done = False

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "a" and attr.get("href"):
            self.parts.links.append(attr["href"])
        if tag == "script":
            self._script_attrs = attr
            self._script_text = []
        if tag in _SKIP_TEXT:
            self._skip_depth += 1
        if tag in _BLOCK:
            self._chunk("\n")
        if self._section_depth:
            if tag not in _VOID:
                self._section_depth += 1
        elif attr.get("data-testid") == self._testid and not self._section_done:
            self._section_depth = 1

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self._chunk("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._script_attrs is not None:
            self.parts.scripts.append((self._script_attrs, "".join(self._script_text)))
            self._script_attrs = None
        if tag in _SKIP_TEXT and self._skip_depth:
            self._skip_depth -= 1
        if tag in _BLOCK:
            self._chunk("\n")
        if self._section_depth and tag not in _VOID:
            self._section_depth -= 1
            if self._section_depth == 0:
                self._section_done = True

    def handle_data(self, data):
        if self._script_attrs is not None:
            self._script_text.append(data)
        if not self._skip_depth:
            self._chunk(data)

    def _chunk(self, text: str) -> None:
        self._body_chunks.append(text)
        if self._section_depth:
            self._section_chunks.append(text)


def tidy_lines(text: str) -> str:
    """Uma linha por bloco: espaços (inclusive o não separável) colapsados, linhas vazias fora."""
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_page(html: str, section_testid: str = "") -> PageParts:
    collector = _Collector(section_testid)
    collector.feed(html)
    collector.close()
    collector.parts.section_text = tidy_lines("".join(collector._section_chunks))
    collector.parts.body_text = tidy_lines("".join(collector._body_chunks))
    return collector.parts
