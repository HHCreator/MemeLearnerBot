"""Read public Bilibili data for the meme bot (surf.py): video search, danmu and comments.

Everything here only reads, anonymously: nothing is ever posted, liked or sent, and no account
is used. The bot sees what anyone sees on a video page without logging in.

    search_videos(keyword, since, until)   videos for a search word, most danmu first
    related_videos(bvid)                   the videos Bilibili shows beside a video
    bvid_of(link)                          the BV id of a video link, b23.tv short links too
    video_info(bvid)                       a video's title, ids, parts and counts
    danmu(cid)                             the danmu (弹幕) on one part of a video
    comments(aid)                          the hot comments under a video, with their replies

danmu() and comments() return messages as dicts: {"id", "kind", "who", "text", "sent"}, where
"who" is a short hash of the sender (enough to count different people; the real id is never
kept) and "sent" is the unix time the message was sent.

Bilibili's web API expects what a browser sends, so every request carries:

    - a browser User-Agent and a Referer from www.bilibili.com
    - a buvid3 cookie: the visitor id Bilibili gives every browser (/x/frontend/finger/spi)
    - for newer endpoints a "WBI" signature: w_rid = md5(sorted query + mixin key), where the
      mixin key is a fixed shuffle of two keys that Bilibili changes every day (sign())

When Bilibili thinks a visitor is a bot it answers with code -412 or -352 ("risk control").
Then this waits and tries again a couple of times, and raises Blocked if it still can't get
through, so a run never hammers the site. When it asks for a captcha (a "v_voucher"), this
gives up on that request at once and raises Captcha: the bot never solves captchas.
"""

import gzip
import hashlib
import hmac
import html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

API = "https://api.bilibili.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
# Seconds between requests, plus up to REQUEST_JITTER at random. From Hao's PC, Bilibili
# refused everything (HTTP 412, for about an hour) after 28 requests in under a minute.
REQUEST_DELAY = 5
REQUEST_JITTER = 2
RETRIES = 2  # extra tries after Bilibili's risk control refuses a request
RETRY_WAIT = 30  # seconds to wait before the first extra try; doubles each time
# 请求被拦截, 风控校验失败, 请求过于频繁 and friends: Bilibili thinks this is a bot
BLOCKED_CODES = {-412, -352, -351, -509, -799}

# The fixed shuffle Bilibili's own web page applies to its two daily WBI keys
MIXIN_KEY_ORDER = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
                   33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
                   61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
                   36, 20, 34, 44, 52]

_cookies = None  # {name: value} for the visitor cookies, filled by _visitor_cookies()
_wbi = None  # (mixin key, the day it was fetched); the keys change every day
_last_request = 0.0


class Blocked(Exception):
    """Bilibili's risk control keeps refusing this computer's requests for now."""


class Captcha(Blocked):
    """Bilibili wants a captcha solved before it answers. The bot never solves captchas and
    waiting doesn't help, so this gives up on the request at once; the caller skips it."""


class Error(Exception):
    """Bilibili answered with an error code (a deleted or private video, and so on)."""


# -- senders ------------------------------------------------------------------------------

def user_hash(user):
    """A short hash of a sender, enough to count different people; the real id is never kept."""
    return hashlib.sha1(f"bilibili:{user}".encode()).hexdigest()[:10]


def mid_hash(mid):
    """The sender hash danmu carry (crc32 of the user id in hex), so a commenter and a
    danmu sender who are the same person get the same user_hash()."""
    return format(zlib.crc32(str(mid).encode()), "x")


# -- signing ------------------------------------------------------------------------------

def mixin_key(img_key, sub_key):
    """Shuffle the two daily keys together the way Bilibili's page does; keep 32 characters."""
    keys = img_key + sub_key
    return "".join(keys[i] for i in MIXIN_KEY_ORDER)[:32]


def sign(params, key, now=None):
    """Return params with wts (the time) and w_rid (the WBI signature) added.

    The page sorts the parameters by name, removes the characters !'()* from the values,
    encodes them like JavaScript's encodeURIComponent and takes md5(query + mixin key).
    """
    params = dict(params, wts=int(time.time() if now is None else now))
    clean = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(params.items())}
    query = urllib.parse.urlencode(clean, quote_via=urllib.parse.quote)
    clean["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return clean


# -- requests -----------------------------------------------------------------------------

def _fetch(url, post=False, cookies=None):
    """Send one request; return (body bytes, Content-Encoding). Tests replace this."""
    headers = dict(HEADERS)
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    request = urllib.request.Request(url, data=b"" if post else None, headers=headers,
                                     method="POST" if post else "GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(), response.headers.get("Content-Encoding", "")


def _decompress(body, encoding=""):
    """Undo deflate or gzip. The danmu list always comes deflated, header or not."""
    if "gzip" in encoding or body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    if "deflate" in encoding or (body and body.lstrip()[:1] not in (b"{", b"[", b"<")):
        for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS):  # raw deflate, then zlib-wrapped
            try:
                return zlib.decompress(body, wbits)
            except zlib.error:
                pass
    return body


def _wait_turn():
    """Keep REQUEST_DELAY seconds (and a little random) between requests."""
    global _last_request
    pause = _last_request + REQUEST_DELAY + random.uniform(0, REQUEST_JITTER) - time.time()
    if pause > 0:
        time.sleep(pause)
    _last_request = time.time()


_VISITOR = object()  # "send the usual visitor cookies" (the default for _request)


def _request(path, params=None, signed=False, raw=False, ok_codes=(0,), post=False,
             cookies=_VISITOR, retries=RETRIES):
    """Call an API path; return the answer's "data" (or the raw body with raw=True).

    Retries when Bilibili's risk control refuses the request, then raises Blocked; raises
    Error for any other error code, or an answer that isn't JSON.
    """
    if cookies is _VISITOR:
        cookies = _visitor_cookies()
    wait = RETRY_WAIT
    for attempt in range(retries + 1):
        query = dict(params or {})
        if signed:
            query = sign(query, _wbi_key())
        url = f"{API}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        _wait_turn()
        try:
            body, encoding = _fetch(url, post=post, cookies=cookies)
        except urllib.error.HTTPError as error:
            # 412 is Bilibili's gateway refusing a bot; 429 is "too many requests"
            if error.code not in (412, 429):
                raise
            code, message = -error.code, f"HTTP {error.code}"
        else:
            body = _decompress(body, encoding)
            if raw:
                return body
            try:
                answer = json.loads(body.decode("utf-8"))
            except ValueError:
                raise Error(f"{path}: Bilibili's answer isn't JSON: {body[:80]!r}") from None
            code, message = answer.get("code", 0), answer.get("message", "")
            data = answer.get("data")
            if isinstance(data, dict) and list(data) == ["v_voucher"]:
                # Risk control can also answer "fine" with only a voucher for a captcha
                raise Captcha(f"Bilibili wants a captcha solved before it answers {path}")
            if code in ok_codes:
                return data
            if code not in BLOCKED_CODES:
                raise Error(f"{path}: Bilibili says {message or 'error'} (code {code})")
        if attempt < retries:
            print(f"  Bilibili refused a request ({message}, code {code}); "
                  f"waiting {wait} seconds before trying again...")
            time.sleep(wait)
            wait *= 2
    raise Blocked(f"Bilibili keeps refusing requests from this computer ({message}, code "
                  f"{code}). Wait an hour or so and try again, or read fewer videos.")


def _visitor_cookies():
    """The cookies a fresh browser gets: buvid3/buvid4 (visitor id) and, if it works,
    bili_ticket, a short-lived token the page fetches that makes risk control friendlier."""
    global _cookies
    if _cookies is None:
        data = _request("/x/frontend/finger/spi", cookies=None)
        cookies = {"buvid3": data["b_3"], "buvid4": data["b_4"], "b_nut": str(int(time.time()))}
        try:
            now = int(time.time())
            hexsign = hmac.new(b"XgwSnGZ1p", f"ts{now}".encode(), hashlib.sha256).hexdigest()
            ticket = _request("/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
                              {"key_id": "ec02", "hexsign": hexsign, "context[ts]": now,
                               "csrf": ""}, post=True, cookies=cookies, retries=0)
            cookies["bili_ticket"] = ticket["ticket"]
            cookies["bili_ticket_expires"] = str(ticket["created_at"] + ticket["ttl"])
        except (Error, Blocked, KeyError, TypeError, ValueError, OSError):
            pass  # optional: everything works without it, risk control is just stricter
        _cookies = cookies
    return _cookies


def _wbi_key():
    """Today's mixin key, from the two key image names the nav endpoint lists."""
    global _wbi
    today = time.strftime("%Y-%m-%d")
    if _wbi is None or _wbi[1] != today:
        # A visitor gets code -101 ("not logged in"), but the keys are there anyway
        data = _request("/x/web-interface/nav", ok_codes=(0, -101))
        name = lambda url: url.rsplit("/", 1)[-1].split(".")[0]
        _wbi = (mixin_key(name(data["wbi_img"]["img_url"]), name(data["wbi_img"]["sub_url"])),
                today)
    return _wbi[0]


# -- videos -------------------------------------------------------------------------------

def _number(value):
    """Search results give counts as numbers or as text like "1.2万"; return an int."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    scale = 10000 if text.endswith("万") else 100000000 if text.endswith("亿") else 1
    try:
        return int(float(text.rstrip("万亿")) * scale)
    except ValueError:
        return 0


BVID = re.compile(r"BV[0-9A-Za-z]{10}")
SHORT_LINK = re.compile(r"(?:https?://)?b23\.tv/[0-9A-Za-z]+")


def _video(item):
    """The bot's video dict for an entry in any of Bilibili's video lists: {"bvid", "aid",
    "title", "author", "pubdate", "plays", "danmu", "comments", "url"}."""
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    pubdate = item.get("pubdate") or item.get("created") or 0
    return {
        "bvid": item["bvid"],
        "aid": item.get("aid"),
        # Search results come with the search word highlighted: <em class="keyword">CS2</em>
        "title": html.unescape(re.sub(r"<[^>]+>", "", item.get("title") or "")),
        "author": owner.get("name") or item.get("author") or "",
        "pubdate": pubdate if isinstance(pubdate, int) else 0,
        "plays": _number(stat.get("view", item.get("play"))),
        "danmu": _number(stat.get("danmaku", item.get("video_review"))),
        "comments": _number(stat.get("reply", item.get("review"))),
        "url": f"https://www.bilibili.com/video/{item['bvid']}",
    }


def search_videos(keyword, since, until, order="dm", page=1):
    """Videos found by a search word, posted between the unix times since and until.

    order: dm (most danmu first), click (most played), pubdate (newest), scores (most
    comments). Bilibili often answers a visitor's search with a captcha (Captcha).
    """
    data = _request("/x/web-interface/wbi/search/type",
                    {"search_type": "video", "keyword": keyword, "order": order, "page": page,
                     "pubtime_begin_s": int(since), "pubtime_end_s": int(until)}, signed=True)
    videos = []
    for item in (data or {}).get("result") or []:
        if item.get("type") != "video" or not item.get("bvid"):
            continue
        video = _video(item)
        if since <= video["pubdate"] <= until:  # in case the search ignores the time range
            videos.append(video)
    return videos


def related_videos(bvid):
    """The videos Bilibili shows beside a video (相关推荐, about 40), like search_videos()
    returns them. Unlike the search, a visitor gets this list without a captcha."""
    data = _request("/x/web-interface/archive/related", {"bvid": bvid})
    return [_video(item) for item in data or [] if isinstance(item, dict) and item.get("bvid")]


def bvid_of(link):
    """The BV id in a video link (or a BV id on its own), or None. A b23.tv short link, the
    kind the app shares, is followed to the video it points to."""
    found = BVID.search(link)
    short = None if found else SHORT_LINK.search(link)
    if short:
        url = short.group()
        _wait_turn()
        request = urllib.request.Request(url if url.startswith("http") else f"https://{url}",
                                         headers=HEADERS)
        with urllib.request.urlopen(request, timeout=20) as response:
            found = BVID.search(response.geturl())
    return found.group() if found else None


def video_info(bvid):
    """A video's ids, title and counts: {"bvid", "aid", "title", "author", "pubdate",
    "danmu", "comments", "parts": [cid, ...]}. Each part (分P) has its own danmu."""
    data = _request("/x/web-interface/view", {"bvid": bvid}) or {}
    stat = data.get("stat") or {}
    parts = [page["cid"] for page in data.get("pages") or [] if page.get("cid")]
    return {
        "bvid": bvid,
        "aid": data["aid"],
        "title": data.get("title", ""),
        "author": (data.get("owner") or {}).get("name", ""),
        "pubdate": data.get("pubdate", 0),
        "danmu": stat.get("danmaku", 0),
        "comments": stat.get("reply", 0),
        "parts": parts or [data["cid"]],
    }


# -- danmu and comments -------------------------------------------------------------------

DANMU = re.compile(r'<d p="([^"]*)">(.*?)</d>', re.S)


def parse_danmu(xml):
    """Read the danmu list's XML into messages.

    Each danmu is <d p="seconds into the video,mode,size,color,unix time sent,pool,sender
    hash,id,...">text</d>. A regular expression reads it, which also copes with the odd
    control character that would stop an XML parser.
    """
    messages = []
    for fields, text in DANMU.findall(xml):
        p = fields.split(",")
        if len(p) < 8 or not text.strip():
            continue
        try:
            sent = int(p[4])
        except ValueError:
            continue
        messages.append({"id": f"d{p[7]}", "kind": "danmu", "who": user_hash(p[6]),
                         "text": html.unescape(text), "sent": sent})
    return messages


def danmu(cid):
    """The danmu on one part of a video.

    This is Bilibili's long-standing XML list, which needs no signature. It holds the newest
    danmu up to the video's limit (1,000 to 8,000, more for longer videos), deflated.
    """
    body = _request("/x/v1/dm/list.so", {"oid": cid}, raw=True)
    return parse_danmu(body.decode("utf-8", "replace"))


def _comment_messages(replies):
    """Messages for a page of comments and the replies Bilibili shows under each one."""
    messages = []
    for reply in replies or []:
        for item in [reply] + list(reply.get("replies") or []):
            text = (item.get("content") or {}).get("message", "")
            if text.strip():
                messages.append({"id": f"c{item.get('rpid')}", "kind": "comment",
                                 "who": user_hash(mid_hash(item.get("mid"))), "text": text,
                                 "sent": item.get("ctime", 0), "likes": item.get("like", 0)})
    return messages


def comments(aid, pages=3):
    """The hot comments under a video (most liked first), with the replies shown under them.

    Visitors who aren't logged in only get the first few pages, which hold the most liked
    comments anyway. Uses the signed endpoint the page uses now, and the older one if that
    fails.
    """
    messages, offset = [], ""
    try:
        for _ in range(pages):
            data = _request("/x/v2/reply/wbi/main",
                            {"oid": aid, "type": 1, "mode": 3, "plat": 1,
                             "pagination_str": json.dumps({"offset": offset},
                                                          separators=(",", ":")),
                             "web_location": 1315875}, signed=True) or {}
            messages += _comment_messages(data.get("replies"))
            cursor = data.get("cursor") or {}
            offset = (cursor.get("pagination_reply") or {}).get("next_offset") or ""
            if cursor.get("is_end") or not offset:
                break
        return messages
    except (Error, Captcha):
        if messages:
            return messages
    for page in range(1, pages + 1):  # sort=1: most liked first
        data = _request("/x/v2/reply", {"oid": aid, "type": 1, "sort": 1, "pn": page,
                                        "ps": 20}) or {}
        found = _comment_messages(data.get("replies"))
        if not found:
            break
        messages += found
    return messages
