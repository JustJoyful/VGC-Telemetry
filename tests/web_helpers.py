from html.parser import HTMLParser


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.links = []
        self.anchor = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append((tag, values))
        if tag == "a":
            self.anchor = [values, ""]

    def handle_data(self, data):
        if self.anchor:
            self.anchor[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            self.links.append(self.anchor)
            self.anchor = None

    def link(self, label):
        return next(attrs["href"] for attrs, text in self.links if label in text)
