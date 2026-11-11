"""Look memes up on the web outside Bilibili: what search engines and wikis say a phrase
means, and the Bilibili videos they find that explain it (explainers.py reads those).

Bilibili asks a visitor's search for a captcha more often than not, so the bot's own search
for 阿一古是什么梗 rarely works (it found no video in the first two real runs). Search
engines find the same videos, and the web pages that explain memes too:

    search(query, names)    a search engine's results: [{"title", "url", "snippet", "site",
                            "engine"}], from the first engine in ENGINES whose results name
                            the phrase (Bing sometimes answers a bot with pages about
                            something else entirely: then DuckDuckGo is asked)
    videos(results, names)  the Bilibili videos among them whose titles name a phrase
    pages(results, names)   the other results that name it, the likeliest explainers first
    page(url, phrase)       what a web page says around a phrase, for the model
    baike(phrase)           百度百科's entry for it, if it has one
    moegirl(phrase)         萌娘百科's, from its mobile site's search, which goes straight
                            to the entry of that name (its API wants a login since 2026-11)
    jikipedia(phrase)       小鸡词典's public search page

It only reads, like a visitor who isn't logged in, and it never solves a captcha: a site
that asks for one, or refuses the bot (HTTP 403, 429), is left alone for the rest of the
run (refused). It waits DELAY to DELAY + JITTER seconds between two requests to the same
site. 小鸡词典's own API wants an encrypted header that only its page makes (XID); the bot
doesn't make one, so it only reads what 小鸡词典 shows anyone.

check() tries every place for some phrases and says which ones answer from this computer
(py -m memelearner sources), keeping what each sent in a folder when asked to (raw).
"""

import base64
import gzip
import html
import http.client
import http.cookiejar
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import bilibili, store

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
DELAY = 4  # seconds between two requests to the same site, plus up to JITTER at random
JITTER = 2
TIMEOUT = 20
MAX_BYTES = 3_000_000  # most read of one answer
RESULTS = 10  # search results kept for a query
TEXT = 1500  # characters of a page's text kept around the phrase
SNIPPET = 200  # characters kept of a search result's snippet
FAILS = 2  # a site that can't be reached this many times is left alone for the rest of the run

# The search engines, tried in this order until one answers: Bing's Chinese site,
# DuckDuckGo's page that works without scripts, and Baidu (which mostly wants a captcha)
ENGINES = ("bing", "duckduckgo", "baidu")
SEARCH_URLS = {
    "bing": "https://cn.bing.com/search?q={q}&ensearch=0",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={q}&kl=cn-zh",
    "baidu": "https://www.baidu.com/s?wd={q}&ie=utf-8",
}
SITES = ("baike", "moegirl", "jikipedia")  # the wikis check() tries besides the engines

# Sites whose pages are videos, apps or log-in walls: their results are used for their
# titles and snippets, but the pages aren't read
NOT_READ = ("bilibili.com/video", "b23.tv", "douyin.com", "iesdouyin.com", "kuaishou.com",
            "ixigua.com", "youtube.com", "xiaohongshu.com", "weibo.com", "weibo.cn",
            "zhihu.com/video", "music.163.com", "y.qq.com", ".pdf")
# Sites that explain words and memes: their pages are read first
EXPLAINER_SITES = ("moegirl.org.cn", "jikipedia.com", "baike.baidu.com", "zhidao.baidu.com",
                   "baike.sogou.com", "baike.so.com", "bilibili.com/read", "zhihu.com",
                   "tieba.baidu.com", "nga.cn", "hupu.com")
MEME_TITLE = re.compile(r"梗|什么意思|啥意思|出处|由来|来源|是什么|含义|意思")

# What a page that wants a captcha, or that only scripts can get through, looks like
CHALLENGE = re.compile(r"captcha|challenge-platform|cf-chl|Just a moment\.\.\.|"
                       r"Attention Required|unusual traffic|百度安全验证|安全验证|请输入验证码|"
                       r"验证码|人机验证|anomaly-modal|bots use DuckDuckGo", re.I)
CHALLENGE_URL = re.compile(r"captcha|challenge|antispider|wappass\.baidu\.com|/verify", re.I)

_cookies = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookies))
_last = {}  # site: when it was last asked
_failed = {}  # site: how many times it couldn't be reached in this run
refused = {}  # site: why it is left alone for the rest of the run
raw = None  # a folder: when set, every answer is kept there as it came (check())
_saved = 0


class Refused(Exception):
    """A site wants a captcha solved, or refuses the bot: it is left alone for the rest of
    the run (refused). again is True when it was refused earlier in the run (old news)."""

    def __init__(self, message, again=False):
        super().__init__(message)
        self.again = again


class Error(Exception):
    """A site answered with an error (a page that isn't there, say)."""


def reset():
    """Forget which sites refused the bot and the cookies they gave: a new run."""
    global _cookies, _opener
    refused.clear()
    _last.clear()
    _failed.clear()
    _cookies = http.cookiejar.CookieJar()
    _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookies))


def site_of(url):
    """The site of an address: its host without www. (moegirl.org.cn for zh.moegirl.org.cn
    stays zh.moegirl.org.cn; it is only for showing and for waiting turns)."""
    host = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


# -- requests -----------------------------------------------------------------------------

def _fetch(url, data=None, headers=None):
    """Send one request; return (HTTP status, the address it ended at after redirects,
    the body as bytes, the Content-Type). Raises urllib.error.HTTPError for an error
    status. Tests replace this."""
    request = urllib.request.Request(url, data=data, headers=dict(HEADERS, **(headers or {})))
    with _opener.open(request, timeout=TIMEOUT) as response:
        body = _unzip(response.read(MAX_BYTES + 1)[:MAX_BYTES],
                      response.headers.get("Content-Encoding") or "")
        return (response.status, response.geturl(), body,
                response.headers.get("Content-Type") or "")


def _unzip(body, encoding=""):
    """A body as it was before the site compressed it (gzip or deflate)."""
    if "gzip" in encoding or body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    if "deflate" in encoding:
        try:
            return zlib.decompress(body, -zlib.MAX_WBITS)
        except zlib.error:
            return zlib.decompress(body)
    return body


def _wait(site):
    pause = _last.get(site, 0) + DELAY + random.uniform(0, JITTER) - time.time()
    if pause > 0:
        time.sleep(pause)
    _last[site] = time.time()


def _decode(body, kind=""):
    """The text of an answer, in the charset it says (or GB18030 for an old Chinese page
    that says none and isn't UTF-8)."""
    found = re.search(r"charset=[\"']?([\w-]+)", kind, re.I) or \
        re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4000], re.I)
    charset = found.group(1) if found else "utf-8"
    charset = charset.decode("ascii", "replace") if isinstance(charset, bytes) else charset
    if charset.lower().replace("-", "") in ("gb2312", "gbk"):
        charset = "gb18030"
    try:
        return body.decode(charset)
    except (LookupError, UnicodeDecodeError):
        text = body.decode("utf-8", "replace")
        if text.count("�") > 20:
            text = body.decode("gb18030", "replace")
        return text


def _keep(site, url, status, final, body):
    """Keep an answer as it came in the raw folder, when check() asks for that."""
    global _saved
    if not raw:
        return
    os.makedirs(raw, exist_ok=True)
    _saved += 1
    name = f"{_saved:03d}-{re.sub(r'[^0-9a-z.]+', '_', site)}.html"
    with open(os.path.join(raw, name), "wb") as f:
        f.write(body)
    with open(os.path.join(raw, "index.tsv"), "a", encoding="utf-8") as f:
        f.write(f"{name}\t{status}\t{url}\t{final}\t{len(body)}\n")


def get(url, site=None, data=None, headers=None, challenge=CHALLENGE):
    """GET (or POST data to) an address, waiting its site's turn; return (the text, the
    address it ended at). Raises Refused when the site wants a captcha or refuses the bot
    (and leaves it alone for the rest of the run), Error for another error status, and
    urllib.error.URLError or OSError when it can't be reached (FAILS times, and it is left
    alone too: a site this computer can't reach, like a blocked one, costs a timeout once)."""
    site = site or site_of(url)
    if site in refused:
        raise Refused(f"{site} refused the bot earlier in this run ({refused[site]})", again=True)
    _wait(site)
    try:
        status, final, body, kind = _fetch(url, data, headers)
    except urllib.error.HTTPError as error:
        body = error.read()[:MAX_BYTES] if error.fp else b""
        try:  # the error page, as the site sent it (Baike's 403 comes gzipped)
            body = _unzip(body, (error.headers or {}).get("Content-Encoding") or "")
        except (OSError, EOFError, zlib.error):
            pass
        _keep(site, url, error.code, url, body)
        if error.code in (403, 429, 503):
            refused[site] = f"HTTP {error.code}"
            raise Refused(f"{site} refused the bot (HTTP {error.code})") from None
        raise Error(f"{site}: HTTP {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        _failed[site] = _failed.get(site, 0) + 1
        if _failed[site] >= FAILS:
            refused[site] = f"couldn't reach it: {getattr(error, 'reason', error)}"
        raise
    except (http.client.HTTPException, zlib.error, EOFError) as error:  # a broken answer
        raise Error(f"{site}: {error!r}") from None
    _keep(site, url, status, final, body)
    text = _decode(body, kind)
    if CHALLENGE_URL.search(urllib.parse.urlsplit(final).path + "?" +
                            urllib.parse.urlsplit(final).netloc) or \
            (challenge and challenge.search(text[:20000]) and _looks_empty(text)):
        refused[site] = "a captcha"
        raise Refused(f"{site} wants a captcha solved")
    return text, final


def _looks_empty(text):
    """Whether a page has little text besides its scripts: a captcha or a page that needs
    scripts, not a real answer that happens to say 验证码 somewhere."""
    return len(plain(text)) < 3000


# -- text out of HTML ---------------------------------------------------------------------

DROP = re.compile(r"<(script|style|noscript|template|svg|head)\b.*?</\1\s*>|<!--.*?-->",
                  re.S | re.I)
BREAK = re.compile(r"<(br|/p|/div|/li|/h\d|/tr|/dd|/dt|/section|/article|/blockquote|p|div|li|"
                   r"h\d|tr)\b[^>]*>", re.I)
TAG = re.compile(r"<[^>]+>")


def plain(markup):
    """The text of some HTML, one block a line, without scripts, styles or tags."""
    text = BREAK.sub("\n", DROP.sub(" ", markup))
    text = html.unescape(TAG.sub("", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _inline(markup):
    """Some HTML as one line of text."""
    return " ".join(html.unescape(TAG.sub("", DROP.sub(" ", markup or ""))).split())


def _attr(tag, name):
    found = re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag) or \
        re.search(rf"\b{name}\s*=\s*'([^']*)'", tag)
    return html.unescape(found.group(1)) if found else ""


def unwrap(url):
    """The address a search engine's result link leads to: Bing's /ck/a?...&u=a1BASE64
    and DuckDuckGo's /l/?uddg=... are their redirects; others are as they are."""
    url = html.unescape(url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    if parts.netloc.endswith("bing.com") and parts.path.startswith("/ck/"):
        wrapped = (query.get("u") or [""])[0]
        if wrapped.startswith("a1"):
            wrapped = wrapped[2:] + "=" * (-len(wrapped[2:]) % 4)
            try:
                return base64.urlsafe_b64decode(wrapped).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return url
    if parts.netloc.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        return (query.get("uddg") or [url])[0]
    return url


# -- search engines -----------------------------------------------------------------------

def _result(title, url, snippet, engine):
    url = unwrap(url)
    return {"title": _inline(title)[:120], "url": url, "snippet": _inline(snippet)[:SNIPPET],
            "site": site_of(url), "engine": engine}


def parse_bing(markup):
    """Bing's results page: one <li class="b_algo"> a result, its link in <h2>, its snippet
    in the first <p> (or the caption) under it."""
    found = []
    starts = [m.start() for m in re.finditer(r'<li\b[^>]*class="[^"]*\bb_algo\b', markup)]
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(markup)
        block = markup[start:end]
        heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", block, re.S)
        link = re.search(r"<a\b([^>]*)>(.*?)</a>", heading.group(1) if heading else block, re.S)
        if not link or not _attr(link.group(1), "href"):
            continue
        rest = block[heading.end():] if heading else block
        snippet = re.search(r"<p\b[^>]*>(.*?)</p>", rest, re.S) or \
            re.search(r'<div\b[^>]*class="[^"]*\bb_caption\b[^"]*"[^>]*>(.*?)</div>', rest, re.S)
        found.append(_result(link.group(2), _attr(link.group(1), "href"),
                             snippet.group(1) if snippet else rest[:2000], "bing"))
    return found


def parse_duckduckgo(markup):
    """DuckDuckGo's page without scripts: a result is a <div> of class "result", its link
    <a class="result__a">, its snippet the next class="result__snippet"; ads (class
    "result--ad") are left out."""
    starts = []
    for match in re.finditer(r'<div\b[^>]*\bclass="([^"]*)"', markup):
        classes = match.group(1).split()
        if "result" in classes:
            starts.append((match.start(), "result--ad" in classes))
    found = []
    for n, (start, ad) in enumerate(starts):
        block = markup[start:starts[n + 1][0] if n + 1 < len(starts) else len(markup)]
        link = re.search(r'<a\b([^>]*class="[^"]*\bresult__a\b[^"]*"[^>]*)>(.*?)</a>', block,
                         re.S)
        if ad or not link:
            continue
        snippet = re.search(r'class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</(a|div|td)>',
                            block, re.S)
        found.append(_result(link.group(2), _attr(link.group(1), "href"),
                             snippet.group(1) if snippet else "", "duckduckgo"))
    return found


def parse_baidu(markup):
    """Baidu's results page: a result is a <div class="result c-container" mu="address">
    (its title links to a Baidu redirect, so the address comes from mu), title in <h3>."""
    found = []
    for match in re.finditer(r'<div\b([^>]*class="[^"]*\bc-container\b[^"]*"[^>]*)>', markup):
        block = markup[match.end():match.end() + 6000]
        title = re.search(r"<h3\b[^>]*>(.*?)</h3>", block, re.S)
        if not title:
            continue
        link = re.search(r"<a\b([^>]*)>", title.group(1))
        url = _attr(match.group(1), "mu") or (_attr(link.group(1), "href") if link else "")
        if not url:
            continue
        rest = block[title.end():]
        cut = re.search(r'<div\b[^>]*class="[^"]*\bc-container\b', rest)
        snippet = _inline(rest[:cut.start()] if cut else rest)
        found.append(_result(title.group(1), url, snippet, "baidu"))
    return found


def parse_any(markup, engine, names):
    """Any results page whose layout the parsers above don't know (any more): the links
    whose text names one of names, with the text that follows each as its snippet."""
    found = []
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", markup, re.S):
        title, url = _inline(match.group(2)), _attr(match.group(1), "href")
        if url.startswith(("http", "//")) and any(_names(title, name) for name in names):
            found.append(_result(title, url, markup[match.end():match.end() + 1500], engine))
    return found


PARSERS = {"bing": parse_bing, "duckduckgo": parse_duckduckgo, "baidu": parse_baidu}


def search_with(engine, query, names=()):
    """One search engine's results for a query, at most RESULTS (see search()). Raises
    Refused when it wants a captcha, Error when it answers with an error."""
    url = SEARCH_URLS[engine].format(q=urllib.parse.quote(query))
    markup, _ = get(url, site=engine)
    found = PARSERS[engine](markup) or parse_any(markup, engine, names or [query])
    seen, results = set(), []
    for result in found:
        if result["url"].startswith("http") and result["url"] not in seen and \
                not _internal(result["url"]):
            seen.add(result["url"])
            results.append(result)
    return results[:RESULTS]


def _internal(url):
    """Whether a link is a search engine's own page (its videos, images, other searches),
    not a result. Baidu's /link?url=... is a result: Baidu sends its visitor on."""
    parts = urllib.parse.urlsplit(url)
    site = site_of(url)
    return site.endswith(("bing.com", "duckduckgo.com")) or \
        site in ("baidu.com", "m.baidu.com") and not parts.path.startswith("/link")


def search(query, names=(), engines=ENGINES):
    """The results of the first search engine (in engines) whose results name one of names
    (the query, when none are given): [{"title", "url", "snippet", "site", "engine"}]; []
    when the ones that answered found nothing that names it, None when none answered.
    Results that name none of them are passed over, and the next engine is asked: Bing
    sometimes answers a bot with pages about something else entirely (the ARP protocol for
    龙颜大悦, Spotify for 滔一下, on 2026-11-11). An engine that wants a captcha, or that this
    computer can't reach, is left alone for the rest of the run."""
    answered, wanted = False, list(names) or [query]
    for engine in engines:
        if engine in refused:
            continue
        try:
            results = search_with(engine, query, names)
        except Refused as error:
            print(f"    {error}: not searching with it again in this run")
            continue
        except (Error, urllib.error.URLError, OSError, ValueError) as error:
            print(f"    {engine} couldn't search: {getattr(error, 'reason', error)}")
            continue
        answered = True
        if named(results, wanted):
            return results
    return [] if answered else None


def named(results, names):
    """Whether any of some search results names one of names (in its title or snippet)."""
    return any(_names(f"{result.get('title', '')} {result.get('snippet', '')}", name)
               for result in results or [] for name in names)


# -- what the results say -----------------------------------------------------------------

def _names(text, phrase):
    """Whether a text names a phrase, case, width and spaces aside."""
    wanted = store.normal(phrase)
    return bool(wanted) and wanted in store.normal(text or "")


def videos(results, names):
    """The Bilibili videos among some search results whose titles name one of names:
    [{"bvid", "title", "how": "web"}], in the engine's order."""
    found = {}
    for result in results:
        if "bilibili.com" not in result["site"] and "b23.tv" not in result["site"]:
            continue
        bvid = bilibili.BVID.search(result["url"])
        title = re.sub(r"_哔哩哔哩_bilibili$|_哔哩哔哩_?bilibili|-哔哩哔哩$", "", result["title"])
        if bvid and any(_names(title, name) for name in names):
            found.setdefault(bvid.group(), {"bvid": bvid.group(), "title": title.strip(),
                                            "how": "web"})
    return list(found.values())


def pages(results, names):
    """The search results besides videos that name one of names, in the title or the
    snippet, the likeliest explainers first: an explainer site (萌娘百科, 百度百科, 小鸡词典
    ...), then a title that names it and asks what it means (阿一古是什么梗), then the rest.
    Each is marked "read": False for a page that is only used for its snippet (a video site,
    or one that needs logging in)."""
    picked = []
    for n, result in enumerate(results):
        where = result["url"].lower()
        if "bilibili.com/video" in where or _internal(result["url"]) or not any(
                _names(result["title"] + " " + result["snippet"], name) for name in names):
            continue
        titled = any(_names(result["title"], name) for name in names)
        rank = (not any(site in where for site in EXPLAINER_SITES),
                not (titled and MEME_TITLE.search(result["title"])), not titled, n)
        picked.append((rank, dict(result, read=not any(site in where for site in NOT_READ))))
    return [result for _, result in sorted(picked, key=lambda item: item[0])]


def around(text, phrase, size=TEXT):
    """The lines of a text that name a phrase and the lines next to them, at most size
    characters, the first ones first: what a page says about it."""
    lines = text.splitlines()
    near = sorted({j for i, line in enumerate(lines) if _names(line, phrase)
                   for j in (i - 1, i, i + 1, i + 2) if 0 <= j < len(lines)})
    kept, used = [], 0
    for j in near:
        line = lines[j][:400]
        if used + len(line) > size:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def page(url, phrase):
    """What a web page says about a phrase: {"url", "site", "title", "text"}, the text
    being the lines around the phrase (around()); "" when the page doesn't name it. Raises
    Refused, Error, or urllib.error.URLError / OSError when it can't be read."""
    markup, final = get(url)
    title = re.search(r"<title\b[^>]*>(.*?)</title>", markup, re.S | re.I)
    return {"url": final, "site": site_of(final), "title": _inline(title.group(1))[:120]
            if title else "", "text": around(plain(markup), phrase)}


# -- wikis --------------------------------------------------------------------------------

def baike(phrase):
    """百度百科's entry for a phrase: {"url", "site", "title", "text"} (its summary), or
    None when it has none. Reads the entry's page, whose description holds the summary."""
    url = f"https://baike.baidu.com/item/{urllib.parse.quote(phrase)}"
    markup, final = get(url, site="baike.baidu.com")
    if "百度百科-验证" in markup[:5000]:
        refused["baike.baidu.com"] = "a captcha"
        raise Refused("baike.baidu.com wants a captcha solved")
    if "/item/" not in final:
        return None  # no such entry: Baike sends a search or an error page instead
    summary = re.search(r'<meta\b[^>]*name="description"[^>]*content="([^"]*)"', markup) or \
        re.search(r'<meta\b[^>]*content="([^"]*)"[^>]*name="description"', markup)
    title = re.search(r"<title\b[^>]*>(.*?)</title>", markup, re.S | re.I)
    title = _inline(title.group(1)) if title else ""
    text = html.unescape(summary.group(1)).strip() if summary else ""
    if not text or not _names(title + text, phrase):
        return None
    return {"url": final, "site": "baike.baidu.com", "title": title[:120], "text": text[:TEXT]}


MOEGIRL_SEARCH = "https://mzh.moegirl.org.cn/index.php?search={q}&title=Special:Search&go=Go"


def moegirl(phrase):
    """萌娘百科's entry for a phrase, from its mobile site's search, which goes straight to the
    entry of that name, as MediaWiki's search does ("go"): the entry's lines around the
    phrase, {"url", "site", "title", "text"}, or None when it has no entry of that name (the
    search stays on its results page). Its API answers a visitor who isn't logged in with
    "Unauthorized API call" since 2026-11, so the bot reads the page anyone sees. Raises
    Refused when what comes back isn't a wiki page (a check for bots, say)."""
    markup, final = get(MOEGIRL_SEARCH.format(q=urllib.parse.quote(phrase)), site="moegirl.org.cn")
    parts = urllib.parse.urlsplit(final)
    where = urllib.parse.unquote(f"{parts.path}?{parts.query}")
    if "search=" in where or "Special:" in where or "特殊:" in where:
        return None  # no entry of that name: the search's own results
    content = markup.rfind("<", 0, markup.find('id="mw-content-text"'))
    if 'id="mw-content-text"' not in markup or content < 0:
        refused["moegirl.org.cn"] = "an answer that isn't a wiki page"
        raise Refused("moegirl.org.cn answered with something that isn't a wiki page "
                      "(a check for bots?)")
    title = re.search(r'<h1\b[^>]*\bid="firstHeading"[^>]*>(.*?)</h1>', markup, re.S) or \
        re.search(r"<title\b[^>]*>(.*?)</title>", markup, re.S | re.I)
    title = re.sub(r"\s+-\s+萌娘百科.*$", "", _inline(title.group(1))) if title else ""
    text = around(plain(markup[content:]), phrase)
    if not text:
        return None
    return {"url": final.replace("://mzh.", "://zh.", 1),  # the address people open
            "site": "moegirl.org.cn", "title": title[:120] or phrase, "text": text}


def jikipedia(phrase):
    """小鸡词典's public search page for a phrase: {"url", "site", "title", "text"}, the
    text around the phrase, or None when it shows nothing for it (its definitions may only
    come through its API, which the bot doesn't use: see above)."""
    url = f"https://jikipedia.com/search?phrase={urllib.parse.quote(phrase)}"
    markup, final = get(url, site="jikipedia.com")
    text = around(plain(markup), phrase)
    if not text or len(store.normal(text)) <= len(store.normal(phrase)) + 4:
        return None  # only the phrase itself, as typed in the search box
    return {"url": final, "site": "jikipedia.com", "title": f"{phrase} - 小鸡词典",
            "text": text}


WIKIS = {"baike": baike, "moegirl": moegirl, "jikipedia": jikipedia}


# -- which places answer ------------------------------------------------------------------

def check(phrases, engines=ENGINES, sites=SITES, folder=None, queries=("{}是什么梗",)):
    """Try every search engine and wiki for some phrases, and say what each found:
    {"phrases": {phrase: {place: {"ok", "error", "results", "videos", "pages", "found"}}},
    "places": {place: {"answered", "found", "refused"}}}. With folder, every answer is kept
    there as it came (raw/), for fixing a parser that misreads a page. Each place is asked
    with the same waits as in a run; one that wants a captcha isn't asked again."""
    global raw
    raw = os.path.join(folder, "raw") if folder else None
    reset()
    report = {"phrases": {}, "places": {}}
    places = report["places"]
    for phrase in phrases:
        print(f"  {phrase}:")
        row = report["phrases"].setdefault(phrase, {})
        for engine in engines:
            for query in queries:
                place = engine if query == queries[0] else f"{engine} ({query.format('')})"
                row[place] = _try(lambda: search_with(engine, query.format(phrase), [phrase]),
                                  phrase, place, places)
        for site in sites:
            row[site] = _try(lambda: WIKIS[site](phrase), phrase, site, places)
    for place, counted in places.items():
        print(f"  {place}: answered {counted['answered']} of {counted['asked']}, found something "
              f"for {counted['found']}" + (f"; refused: {counted['refused']}"
                                           if counted["refused"] else ""))
    raw = None
    return report


def _try(ask, phrase, place, places):
    """Ask one place for one phrase, for check(): what it found, or why it couldn't."""
    counted = places.setdefault(place, {"asked": 0, "answered": 0, "found": 0, "refused": ""})
    counted["asked"] += 1
    started = time.time()
    try:
        answer = ask()
    except Refused as error:
        counted["refused"] = counted["refused"] or str(error)
        print(f"    {place}: {error}")
        return {"ok": False, "error": str(error)}
    except (Error, urllib.error.URLError, OSError, ValueError) as error:
        print(f"    {place}: couldn't ask it ({error})")
        return {"ok": False, "error": str(error)}
    counted["answered"] += 1
    seconds = round(time.time() - started, 1)
    if isinstance(answer, list):  # a search engine's results
        found = {"ok": True, "seconds": seconds, "results": answer,
                 "videos": videos(answer, [phrase]), "pages": pages(answer, [phrase])}
        found["found"] = bool(found["videos"] or found["pages"])
        print(f"    {place}: {len(answer)} results, {len(found['videos'])} Bilibili videos and "
              f"{len(found['pages'])} other results that name it")
    else:  # a wiki's entry, or None
        found = {"ok": True, "seconds": seconds, "entry": answer, "found": bool(answer)}
        print(f"    {place}: " + (f"{answer['title']}: {answer['text'][:60]}" if answer
                                  else "no entry"))
    counted["found"] += found["found"]
    return found
