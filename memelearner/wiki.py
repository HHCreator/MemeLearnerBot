"""What the wikis say about a phrase, for the judge (judging.py).

    look_up("阿一古")    [{"site", "title", "url", "text"}, ...]: write-ups that name it

The judge never takes the local model's word for what a meme means, since memes change
faster than models are trained. So it reads what people wrote about the phrase lately and
shows the model those write-ups beside the lines that used it. Two wikis, both through their
MediaWiki API, which is made for programs:

    萌娘百科 (zh.moegirl.org.cn)    internet and ACG memes, often written up within days
    维基百科 (zh.wikipedia.org)      teams, players and tournaments: tells names from memes

Like bilibili.py it only reads, anonymously, one request at a time with a pause between
them, and its User-Agent names the bot, as Wikipedia asks of programs. A site that refuses
(an error, a web page or captcha instead of data, no answer) is left alone for the rest of
the run, in refused; the judge goes on with what it has. 小鸡词典 and 百度百科 have no API
for programs, so they aren't read.
"""

import html.parser
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import store

HEADERS = {"User-Agent": "MemeLearnerBot/0.1 (https://github.com/HHCreator/MemeLearnerBot)"}
SITES = {  # name: (API address, address of a page by its title)
    "萌娘百科": ("https://zh.moegirl.org.cn/api.php", "https://zh.moegirl.org.cn/"),
    "维基百科": ("https://zh.wikipedia.org/w/api.php", "https://zh.wikipedia.org/wiki/"),
}
REQUEST_DELAY = 2  # seconds between two requests to the same site
RESULTS = 3  # search results read on each site
PER_SITE = 2  # write-ups kept from each site
TEXT = 300  # characters kept of each write-up, around the first time it names the phrase

refused = {}  # site: why, for the sites that refused during this run (the judge clears it)
_last = {}  # site: when its last request was sent (time.monotonic())


class Refused(Exception):
    """A site won't answer the bot right now."""


def _fetch(url):
    """GET a URL and return its body as text. Tests replace this."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _api(site, params):
    """Call a site's MediaWiki API and return its JSON answer; raises Refused.

    maxlag=5 asks the site to refuse when its servers are busy, and variant=zh-cn asks for
    simplified characters, which is what the lines on Bilibili use.
    """
    wait = _last.get(site, -REQUEST_DELAY) + REQUEST_DELAY - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    params = {"format": "json", "formatversion": "2", "maxlag": "5", "variant": "zh-cn",
              **params}
    try:
        body = _fetch(f"{SITES[site][0]}?{urllib.parse.urlencode(params)}")
    except urllib.error.HTTPError as error:
        raise Refused(f"HTTP {error.code}")
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f"no answer: {getattr(error, 'reason', error)}")
    finally:
        _last[site] = time.monotonic()
    try:
        data = json.loads(body)
    except ValueError:
        raise Refused("it sent a web page instead of data, maybe a captcha")
    if not isinstance(data, dict):
        raise Refused("it answered in an unexpected way")
    if (data.get("error") or {}).get("code") == "maxlag":
        raise Refused("its servers are busy")
    return data


class _Paragraphs(html.parser.HTMLParser):
    """The text of a page's paragraphs (<p>), without tables (infoboxes, notices), footnote
    marks, styles and scripts."""

    SKIP = {"table", "sup", "style", "script"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.inside, self.current, self.paragraphs = 0, 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "p":
            self.inside += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "p" and self.inside:
            self.inside -= 1
            text = "".join(self.current).strip()
            if text:
                self.paragraphs.append(text)
            self.current = []

    def handle_data(self, data):
        if self.inside and not self.skip:
            self.current.append(data)


def paragraphs(page_html):
    """The paragraphs of a page's HTML, joined into one text."""
    parser = _Paragraphs()
    parser.feed(page_html or "")
    parser.close()
    return " ".join(parser.paragraphs)


def _intro(site, title):
    """The top of a page, for a site that can't send short extracts: its first section's
    paragraphs."""
    data = _api(site, {"action": "parse", "page": title, "prop": "text", "section": 0,
                       "redirects": 1, "disabletoc": 1})
    text = (data.get("parse") or {}).get("text") or ""
    return paragraphs(text if isinstance(text, str) else text.get("*", ""))


def _plain(snippet):
    """A search snippet's text, without the tags that mark the match and with entities
    undone (&quot; -> ")."""
    return html.unescape(re.sub(r"<[^>]+>", "", snippet or ""))


def _search(site, phrase):
    """The pages a site's search finds for a phrase, best first: [{"title", "url", "text"}],
    where text is the top of the page.

    One request asks for the results with the top of each page (extracts) and the bit of
    the page around the match (snippets). A site that can't make extracts gets one more
    request, for the top of a page named after the phrase; other pages keep their snippet.
    """
    data = _api(site, {
        "action": "query", "list": "search", "srsearch": phrase, "srlimit": RESULTS,
        "srprop": "snippet", "generator": "search", "gsrsearch": phrase, "gsrlimit": RESULTS,
        "prop": "extracts|info", "exintro": 1, "explaintext": 1, "exchars": 1200,
        "exlimit": RESULTS, "inprop": "url", "redirects": 1})
    query = data.get("query") or {}
    snippets = {hit.get("title"): _plain(hit.get("snippet")) for hit in query.get("search") or []}
    found = []
    for page in sorted(query.get("pages") or [], key=lambda page: page.get("index", 0)):
        if page.get("missing") or "title" not in page:
            continue
        text = page.get("extract")
        if text is None:  # the site makes no extracts
            text = _intro(site, page["title"]) if mentions(page["title"], phrase) else \
                snippets.get(page["title"], "")
        url = page.get("fullurl") or SITES[site][1] + urllib.parse.quote(
            page["title"].replace(" ", "_"))
        found.append({"title": page["title"], "url": url, "text": text})
    return found


def mentions(text, phrase):
    """Whether a text names a phrase (written the bot's way, store.normal()): anywhere for
    Chinese, as a whole word for letters and digits (aq is not in aqua)."""
    if re.fullmatch(r"[0-9a-z]+", phrase):
        text = unicodedata.normalize("NFKC", text or "").lower()
        return re.search(rf"(?<![0-9a-z]){re.escape(phrase)}(?![0-9a-z])", text) is not None
    return bool(phrase) and phrase in store.normal(text)


def excerpt(text, phrase):
    """Up to TEXT characters of a text, starting a little before it first names the phrase."""
    text = " ".join(unicodedata.normalize("NFKC", text or "").split())
    lower = text.lower()
    if len(lower) != len(text):  # a few letters grow when lowercased; keep the two in step
        text = lower
    at = lower.find(phrase)
    start = at - TEXT // 3 if at > TEXT // 3 else 0
    return text[start:start + TEXT]


def look_up(phrase, sites=None):
    """What the wikis write about a phrase: [{"site", "title", "url", "text"}], up to PER_SITE
    pages from each site whose title or top paragraphs name it, pages named after it first.

    Sites in refused are skipped; a site that refuses now is added to it with the reason.
    """
    found = []
    for site in sites or list(SITES):
        if site in refused:
            continue
        try:
            pages = _search(site, phrase)
        except Refused as why:
            refused[site] = str(why)
            continue
        named = [page for page in pages if mentions(page["title"], phrase)]
        named += [page for page in pages if page not in named and mentions(page["text"], phrase)]
        found += [{"site": site, "title": page["title"], "url": page["url"],
                   "text": excerpt(page["text"], phrase)} for page in named[:PER_SITE]]
    return found
