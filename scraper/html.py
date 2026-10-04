"""Small structural HTML reader for captured public tables and team sheets."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def nodes(self, tag=None, class_name=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (
                    class_name is None or class_name in (child.attrs.get("class") or "").split()
                ):
                    yield child
                yield from child.nodes(tag, class_name)

    def text(self):
        return re.sub(
            r"\s+", " ", "".join(c.text() if isinstance(c, Node) else c for c in self.children)
        ).strip()


class Tree(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag == "tr" and any(n.tag == "tr" for n in self.stack):
            self.handle_endtag("tr")
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in (
            "img",
            "br",
            "hr",
            "input",
            "meta",
            "link",
            "source",
            "wbr",
            "area",
            "base",
            "embed",
            "param",
            "col",
        ):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def public_team_links(body, base_url, event_id):
    result = {}
    for node in Tree(body).root.nodes("a"):
        url = urljoin(base_url + "/", node.attrs.get("href") or "")
        parsed = urlsplit(url)
        match = re.fullmatch(r"/teamlist/public/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", parsed.path)
        if (
            parsed.scheme == "https"
            and parsed.netloc == urlsplit(base_url).netloc
            and not parsed.query
            and not parsed.fragment
            and match
            and match[1] == event_id
        ):
            result[match[2]] = url
    return result
