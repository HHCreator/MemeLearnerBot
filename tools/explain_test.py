"""Try the explain step on memes the bot has already learned (Hao's ask, 2026-11-10): do the
videos and web pages that explain a meme make the model understand it?

    explain-test.bat runs it on Hao's PC:   py tools\\explain_test.py [PHRASE ...]

It never changes memes.json, the bot's own cache or its working copy: everything it writes
goes to the folder MEMELEARNER_HOME names (the .bat sets it to .cache\\explain-test). It runs
on the code of the "Run the meme bot for days" branch, whose lookup on the web (web.py) it
uses as it is.

    1. Find     Bilibili's search asks a visitor for a captcha, so explain found no video in
                the first real runs. This tries the ways a visitor has, a request every 5 to
                7 seconds, and writes down what each one answers:
                  links     the videos the lookup test found (lookup-test\\links.json beside
                            this test's folder), and the ones in links.txt (a phrase, a
                            space, a video link on each line)
                  related   the lists beside those explainer videos, beside a few game
                            videos the bot read, and beside the explainer videos found
                            there, and their collections (合集)
                  web       the Bilibili videos a search engine finds (the bot's lookup)
                  up-...    what the uploaders of explainer videos list: their collections
                            and series (up-lists), the videos in them (up-collection,
                            up-series) and their newest videos (up-videos)
                  api-type  the search the bot uses now
                  api-old   the same search at its old address, without the signature
                  api-all   the "all results" search
                  page      the search page itself (search.bilibili.com), as a browser opens it
                  api-page  the bot's search again, with the cookies and the Referer the
                            search page gives a browser
                The first PROBES phrases try the five searches; the others use the ones that
                worked. It never logs in and never solves a captcha: a way that is refused
                is dropped. The videos given as links are read first, so that a way that
                gets the bot refused later doesn't lose them.
    2. Read     Up to VIDEOS videos for each phrase, as explain does (explainers.read()),
                and heard on the processor when Whisper is installed (the .bat offers to
                put it in the test's own folder). The sound is fetched by hear_video(),
                which tries the ways a visitor has and writes down what each answered:
                on 2026-11-23 every video's sound was refused to the bot's own way, and a
                later run hears those videos again. The web pages about each phrase, as
                the bot reads them (explainers.read_pages()). What the lookup test and the
                bot's own runs read already isn't asked for again.
    3. Sum up   explainers.summarize() for a video: once from what its page shows (title,
                description, comments, danmu), and once more with what was heard.
                explainers.summarize_pages() for the web pages.
    4. Judge    judging.judge_one() up to four times for each phrase, with the same danmu
                lines: with nothing else, with what the web pages say, with what the videos
                say as read, and with what they say as heard. The learned memes are not
                shown to the model in any of them, so what it writes comes from the danmu
                and the summaries alone.

It writes result.json, result.md and log.txt, and keeps state.json so that a second run only
does what the first one didn't. Each phrase's meaning in memes.json (Hao's) is beside the
model's answers; nobody is marked here.
"""

import html
import http.client
import json
import os
import random
import re
import socket
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

from memelearner import bilibili, explainers, judging, store, surf, web

# Memes Hao corrected or added to: the model alone didn't get them right. The first PROBES
# are old, well-known ones, where a video is most likely to exist.
PHRASES = ["阿一古", "汽车人", "正义必胜", "别吃", "神像尽显", "干燥", "老霸道了", "肯恰那",
           "打钱了", "脸色对了", "老九他没毛病", "千言万语感谢现役", "我们是冠军",
           "目前不知道怎么赢", "超威同意了", "桌子翻了", "脖子捂汗", "中华有尺", "阿一占",
           "涅槃打野", "春风若有怜花意", "蝴蝶飞不过沧海", "皮肤局", "我的麦很炸吗"]
PROBES = 3  # phrases that try every way to search
VIDEOS = explainers.VIDEOS  # videos read for each phrase
GIVEN = 8  # explainer videos given as links whose related lists and collections are read
SEEDS = 4  # game videos the bot read whose related lists are read
HOPS = 14  # explainer videos found there whose related lists and collections are read
SLOW = (11, 8)  # seconds between requests to Bilibili and to a web site while the bot's own
#                 daily run is likely going on this computer
LOOKUP = "lookup-test"  # the lookup test's folder, beside this test's
DROP_AFTER = 3  # times in a row a way to search may fail before it is dropped
RAW = 400_000  # characters kept of an answer saved as it came
PAGE = "https://search.bilibili.com/video?keyword={}"
ROUTES = ("api-type", "api-old", "api-all", "page", "api-page")
UP_ROUTES = ("up-lists", "up-collection", "up-series", "up-videos")
UPLOADERS = 3  # uploaders of explainer videos whose lists are asked for
COLLECTIONS = 2  # collections read of each
SPACE = "https://space.bilibili.com/{}"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

HOME = os.environ.get("MEMELEARNER_HOME") or ""
STATE = os.path.join(HOME, "state.json")
_log = None
_requests = []  # every request made here: {"to", "status", "outcome"}
_hearing = {"tried": False, "ready": False}  # Whisper: loaded when the first video needs it


class Tee:
    """What is printed, kept in log.txt too (the bot's own lines included)."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.stream.write(text)
        self.log.write(text)
        return len(text)

    def flush(self):
        self.stream.flush()
        self.log.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def say(text=""):
    print(text, flush=True)


def save_state(state):
    surf._save_json(STATE, state)


def beside(*parts):
    """A path in the bot's cache folder, which this test's folder is in."""
    return os.path.join(os.path.dirname(HOME.rstrip("\\/")), *parts)


def earlier():
    """What the bot's own runs and the lookup test found on this computer (their
    explainers.json): ({"game:phrase": entry}, {bvid: an explainer video they saw})."""
    phrases, seen = {}, {}
    for folder in ("surf", os.path.join(LOOKUP, "surf")):
        data = surf._load_json(beside(folder, "explainers.json"), {})
        for key, entry in (data.get("phrases") or {}).items():
            if isinstance(entry, dict) and (key not in phrases or entry.get("pages")
                                            or not phrases[key].get("pages")):
                phrases[key] = entry
        seen.update({bvid: video for bvid, video in (data.get("seen") or {}).items()
                     if isinstance(video, dict)})
    return phrases, seen


def daily_run_near():
    """Whether the bot's own daily run (py -m memelearner auto) is likely going now: from 5
    minutes before its time of day to 2 hours after, while its days last."""
    settings = surf._load_json(beside("surf", "auto", "settings.json"), {})
    try:
        hour, minute = (int(part) for part in str(settings["at"]).split(":"))
        until = time.mktime(time.strptime(settings["until"], "%Y-%m-%d %H:%M"))
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    now = time.localtime()
    since = (now.tm_hour * 60 + now.tm_min - hour * 60 - minute) % (24 * 60)
    return time.time() < until and (since < 120 or since >= 24 * 60 - 5)


# -- asking Bilibili ----------------------------------------------------------------------

def fetch(url, cookies=None, referer=None, origin=None, accept=None):
    """One GET, waiting its turn like every request of the bot: (HTTP status, headers,
    text); status None and the reason as text when there was no answer. origin=False sends
    no Origin, as a browser opening a page does."""
    bilibili._wait_turn()
    headers = dict(bilibili.HEADERS)
    if referer:
        headers["Referer"] = referer
    if origin is False:
        headers.pop("Origin", None)
    elif origin:
        headers["Origin"] = origin
    if accept:
        headers["Accept"] = accept
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                          timeout=25)
    except urllib.error.HTTPError as error:  # an answer too: 412 is Bilibili refusing a bot
        response = error
    except (urllib.error.URLError, OSError) as error:
        return None, {}, f"no answer: {error}"
    try:
        body = response.read()
    except Exception:  # an error with nothing to read
        body = b""
    status, got = response.getcode(), response.headers or {}
    try:
        response.close()
    except Exception:
        pass
    text = bilibili._decompress(body, got.get("Content-Encoding", "") or "")
    return status, got, text.decode("utf-8", "replace")


def gateway(headers):
    """The headers of an answer that Bilibili's own gateway adds (bili-status-code,
    x-bili-gaia-vvoucher ...), cut short: they say why a request is refused."""
    try:
        return {name: str(value)[:80] for name, value in headers.items()
                if "bili" in name.lower() or name.lower().startswith("x-")}
    except AttributeError:
        return {}


def set_cookies(headers):
    """{name: value} of the cookies an answer sets."""
    found = {}
    for line in (headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else None) or []:
        name, _, rest = line.partition("=")
        if name.strip() and rest:
            found[name.strip()] = rest.split(";")[0].strip()
    return found


def videos_in(node, found=None):
    """{bvid: video} for every video in a JSON answer, wherever it is."""
    found = {} if found is None else found
    if isinstance(node, dict):
        bvid, title = node.get("bvid"), node.get("title")
        if isinstance(bvid, str) and bilibili.BVID.fullmatch(bvid) and isinstance(title, str) \
                and title.strip() and bvid not in found:
            try:
                found[bvid] = bilibili._video(node)
            except (KeyError, TypeError, ValueError, AttributeError):
                found[bvid] = {"bvid": bvid, "title": title, "plays": 0, "pubdate": 0,
                               "author": ""}
        for value in node.values():
            videos_in(value, found)
    elif isinstance(node, list):
        for value in node:
            videos_in(value, found)
    return found


BVID_FIELD = re.compile(r'"?bvid"?\s*:\s*"(BV[0-9A-Za-z]{10})"')
TITLE_FIELD = re.compile(r'"?title"?\s*:\s*"((?:[^"\\]|\\.)*)"')
PLAY_FIELD = re.compile(r'"?play"?\s*:\s*(\d+)')
VIDEO_LINK = re.compile(r'/video/(BV[0-9A-Za-z]{10})')
CARD_TITLE = (re.compile(r'<h3[^>]*\btitle="([^"]+)"'), re.compile(r'\balt="([^"]+)"'),
              re.compile(r'\btitle="([^"]+)"'))


def _title(text):
    try:
        text = json.loads(f'"{text}"')
    except ValueError:
        pass
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def videos_in_page(text):
    """{bvid: video} for the videos a search page carries: in the data for its scripts
    (bvid:"BV...", title:"..."), or as the cards themselves (links, with a title)."""
    found = {}
    for match in BVID_FIELD.finditer(text):
        after = text[match.end():match.end() + 1500]
        after = after[:m.start()] if (m := BVID_FIELD.search(after)) else after
        title = TITLE_FIELD.search(after)
        if title and _title(title.group(1)) and match.group(1) not in found:
            plays = PLAY_FIELD.search(after)
            found[match.group(1)] = {"bvid": match.group(1), "title": _title(title.group(1)),
                                     "plays": int(plays.group(1)) if plays else 0,
                                     "pubdate": 0, "author": "", "from": "data"}
    for match in VIDEO_LINK.finditer(text):
        bvid = match.group(1)
        if bvid in found:
            continue
        after = text[match.end():match.end() + 3000]
        other = next((m for m in VIDEO_LINK.finditer(after) if m.group(1) != bvid), None)
        after = after[:other.start()] if other else after
        for pattern in CARD_TITLE:
            title = pattern.search(after)
            if title and _title(title.group(1)):
                found[bvid] = {"bvid": bvid, "title": _title(title.group(1)), "plays": 0,
                               "pubdate": 0, "author": "", "from": "card"}
                break
    return found


def keep_raw(name, text):
    folder = os.path.join(HOME, "raw")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(text[:RAW])


class Searcher:
    """The ways to search, and what each answered so far."""

    def __init__(self, state):
        self.state = state
        self.jar = {}  # the cookies the search page gave
        self.tally = state.setdefault("routes", {})  # {route: {"videos", "empty", ...}}
        self.failed = {}  # {route: times in a row}
        self.dropped = set(state.get("dropped") or [])
        self.count = 0

    def _api(self, route, path, params, signed, cookies, referer=None, origin=None):
        query = dict(params)
        if signed:
            query = bilibili.sign(query, bilibili._wbi_key())
        url = f"{bilibili.API}{path}?" + urllib.parse.urlencode(query,
                                                                quote_via=urllib.parse.quote)
        status, headers, text = fetch(url, cookies, referer, origin)
        out = {"route": route, "url": url, "status": status, "bytes": len(text),
               "headers": gateway(headers)}
        if status is None:
            return dict(out, outcome="error", note=text[:120]), {}, text
        if status in (412, 429):
            return dict(out, outcome="refused", note=f"HTTP {status}"), {}, text
        try:
            answer = json.loads(text)
        except ValueError:
            return dict(out, outcome="error", note="not JSON: " + text[:80]), {}, text
        code, data = answer.get("code"), answer.get("data")
        out.update(code=code, message=str(answer.get("message") or "")[:60])
        if isinstance(data, dict) and "v_voucher" in data and len(data) <= 2:
            return dict(out, outcome="captcha", note="it asks for a captcha"), {}, text
        if code in bilibili.BLOCKED_CODES:
            return dict(out, outcome="refused"), {}, text
        if code != 0:
            return dict(out, outcome="error"), {}, text
        videos = videos_in(data)
        if isinstance(data, dict):
            out["results"] = data.get("numResults")
        return dict(out, outcome="videos" if videos else "empty"), videos, text

    def _page(self, query):
        url = PAGE.format(urllib.parse.quote(query))
        status, headers, text = fetch(url, self.jar or None, "https://www.bilibili.com/",
                                      False, BROWSER_ACCEPT)
        out = {"route": "page", "url": url, "status": status, "bytes": len(text),
               "headers": gateway(headers)}
        if status is None:
            return dict(out, outcome="error", note=text[:120]), {}, text
        given = set_cookies(headers)
        self.jar.update(given)
        out["cookies"] = sorted(given)  # their names only
        if status in (412, 429):
            return dict(out, outcome="refused", note=f"HTTP {status}"), {}, text
        if status != 200:
            return dict(out, outcome="error", note=f"HTTP {status}"), {}, text
        videos = videos_in_page(text)
        out["from"] = sorted({video.pop("from") for video in videos.values()})
        if videos:
            return dict(out, outcome="videos"), videos, text
        asks = re.search(r"v_voucher|geetest|captcha|验证码|风控", text, re.I)
        return dict(out, outcome="captcha" if asks else "empty",
                    note=f"the page says {asks.group(0)}" if asks else
                    "no video in the page: its scripts fetch them"), {}, text

    def _browser(self):
        """The cookies a browser has on the search page: the ones the page set, and the two
        its scripts add (buvid4 for its visitor id, and the ticket). The bot's own visitor
        cookies when the page gave no visitor id."""
        visitor = bilibili._visitor_cookies()
        if "buvid3" not in self.jar:
            return dict(visitor)
        if "buvid4" not in self.jar:
            _, _, text = fetch(f"{bilibili.API}/x/frontend/finger/spi", self.jar)
            try:
                self.jar["buvid4"] = json.loads(text)["data"]["b_4"]
            except (ValueError, KeyError, TypeError):
                pass
        cookies = dict(self.jar)
        for name in ("bili_ticket", "bili_ticket_expires"):
            if name in visitor:
                cookies.setdefault(name, visitor[name])
        return cookies

    def ask(self, route, phrase):
        """One search for a phrase one way: (what it answered, {bvid: video})."""
        query = f"{phrase}是什么梗"
        kind = {"search_type": "video", "keyword": query, "order": "totalrank", "page": 1}
        try:
            if route == "api-type":
                out, videos, text = self._api(route, "/x/web-interface/wbi/search/type", kind,
                                              True, bilibili._visitor_cookies())
            elif route == "api-old":
                out, videos, text = self._api(route, "/x/web-interface/search/type", kind,
                                              False, bilibili._visitor_cookies())
            elif route == "api-all":
                out, videos, text = self._api(route, "/x/web-interface/wbi/search/all/v2",
                                              {"keyword": query, "page": 1}, True,
                                              bilibili._visitor_cookies())
            elif route == "page":
                out, videos, text = self._page(query)
            else:  # api-page: as the search page's own scripts ask
                if not self.jar:  # a later run: open the page first, as a browser does
                    self._page(query)
                out, videos, text = self._api(
                    route, "/x/web-interface/wbi/search/type", kind, True, self._browser(),
                    PAGE.format(urllib.parse.quote(query)), "https://search.bilibili.com")
                out["page_cookies"] = sorted(self.jar)
        except bilibili.Blocked:
            raise
        except Exception as error:  # a shape nobody expected: write it down, go on
            out, videos, text = {"route": route, "outcome": "error",
                                 "note": f"{error!r}"[:200]}, {}, traceback.format_exc()
        return self._record(route, phrase, out, videos, text)

    def probe(self, route, label, path, params, referer=None):
        """One of an uploader's lists, asked for as the search is (signed, with the bot's
        visitor cookies), kept and counted like a search: (what it answered, {bvid: video},
        the answer's data)."""
        try:
            out, videos, text = self._api(route, path, params, True,
                                          bilibili._visitor_cookies(), referer)
        except bilibili.Blocked:
            raise
        except Exception as error:  # a shape nobody expected: write it down, go on
            out, videos, text = {"route": route, "outcome": "error",
                                 "note": f"{error!r}"[:200]}, {}, traceback.format_exc()
        try:
            data = json.loads(text).get("data")
        except (ValueError, AttributeError):
            data = None
        out, videos = self._record(route, label, out, videos, text)
        return out, videos, data if isinstance(data, dict) else {}

    def _record(self, route, phrase, out, videos, text):
        """Count an answer, keep the first of each kind as it came, and say what it was."""
        self.count += 1
        out["phrase"] = phrase
        listed = self.state.setdefault("route_videos", {}).setdefault(route, {})
        for bvid, video in videos.items():
            listed.setdefault(bvid, video["title"])
        if videos:
            self.state.setdefault("examples", {}).setdefault(route, out.get("url", ""))
        tally = self.tally.setdefault(route, {})
        tally[out["outcome"]] = tally.get(out["outcome"], 0) + 1
        good = out["outcome"] in ("videos", "empty")
        self.failed[route] = 0 if good else self.failed.get(route, 0) + 1
        # The first answers of each kind are kept as they came, to read their shape
        if tally[out["outcome"]] <= 2:
            keep_raw(f"{route}-{out['outcome']}-{tally[out['outcome']]}.txt", text)
        out["titles"] = [video["title"] for video in list(videos.values())[:10]]
        _requests.append({"to": route, "status": out.get("status"), "outcome": out["outcome"]})
        say(f"      {route:13} {out['outcome']:8} "
            + (f"{len(videos)} videos" if videos else out.get("note") or out.get("message")
               or f"code {out.get('code')}"))
        if out.get("status") in (412, 429):
            # Refused outright: not that way again. And is it only this address, or
            # everything from this computer now?
            self.dropped.add(route)
            self.state["dropped"] = sorted(self.dropped)
            status, _, _ = fetch(f"{bilibili.API}/x/frontend/finger/spi")
            if status in (412, 429):
                self.state["refused_after"] = route
                raise bilibili.Blocked(f"HTTP {status} for everything, after trying {route}")
        return out, videos

    def working(self):
        """The ways that found videos, the best first."""
        good = [route for route in ROUTES if route not in self.dropped
                and self.tally.get(route, {}).get("videos")]
        return sorted(good, key=lambda route: -self.tally[route]["videos"])

    def drop_failing(self):
        for route, times in self.failed.items():
            if times >= DROP_AFTER and route not in self.dropped:
                self.dropped.add(route)
                say(f"      ({route} failed {times} times in a row: not tried again)")
        self.state["dropped"] = sorted(self.dropped)


def names_of(phrase, entry):
    return [phrase] + list(entry.get("variants") or [])


def matching(videos, names):
    """The videos whose titles name one of names, as explain picks them: the ones made to
    explain a phrase first, then the most played."""
    hits = []
    for video in videos:
        title = video.get("title") or ""
        named = [n for n, name in enumerate(names) if explainers.mentions(title, name)]
        if named:
            hits.append((not explainers.MADE_TO_EXPLAIN.search(title), named[0],
                         -(video.get("plays") or 0), video))
    return [video for *_, video in sorted(hits, key=lambda hit: hit[:3])]


# -- the related lists --------------------------------------------------------------------

def note_detail(state, bvid, data):
    """Keep what a video's page shows of other videos: the ones beside it, and the other
    episodes of its collection (合集)."""
    pool = state.setdefault("pool", {})
    view = data.get("View") if isinstance(data.get("View"), dict) else {}
    related = [bilibili._video(item) for item in data.get("Related") or []
               if isinstance(item, dict) and item.get("bvid")]
    season = view.get("ugc_season") if isinstance(view.get("ugc_season"), dict) else {}
    episodes = []
    for section in season.get("sections") or []:
        for episode in (section.get("episodes") or []) if isinstance(section, dict) else []:
            if isinstance(episode, dict) and episode.get("bvid"):
                arc = episode.get("arc") if isinstance(episode.get("arc"), dict) else {}
                episodes.append({"bvid": episode["bvid"],
                                 "title": episode.get("title") or arc.get("title") or "",
                                 "author": (view.get("owner") or {}).get("name", ""),
                                 "pubdate": arc.get("pubdate") or 0,
                                 "plays": (arc.get("stat") or {}).get("view") or 0})
    for how, videos in (("related", related), ("collection", episodes)):
        for video in videos:
            pool.setdefault(video["bvid"], {"title": video["title"],
                                            "plays": video.get("plays") or 0,
                                            "author": video.get("author") or "", "how": how,
                                            "beside": bvid})
    explainers.remember(episodes, "collection")
    looks = lambda videos: sum(1 for v in videos if explainers.looks_like_explainer(v["title"]))
    state.setdefault("pages", {})[bvid] = {
        "title": view.get("title", ""), "author": (view.get("owner") or {}).get("name", ""),
        "mid": (view.get("owner") or {}).get("mid"),
        "related": len(related), "explainers": looks(related),
        "collection": season.get("title", "") if season else "", "episodes": len(episodes),
        "collection_explainers": looks(episodes)}


def crawl(state, links, seen, memes):
    """The related lists and collections of the explainer videos given as links, of a few
    game videos the bot read, and then of the explainer videos seen there: how many
    explainer videos does that give, and how many of the learned memes do they name?"""
    done = state.setdefault("crawled", [])
    pool = state.setdefault("pool", {})
    for bvid, video in seen.items():  # what the bot's own runs saw beside the videos they read
        if bilibili.BVID.fullmatch(bvid) and video.get("title"):
            pool.setdefault(bvid, {"title": video["title"], "plays": video.get("plays") or 0,
                                   "author": video.get("author") or "", "how": "seen",
                                   "beside": ""})
    if state.get("crawl_done"):
        say("1a. The lists beside the videos were read in an earlier run.")
        return
    given = []
    for link in (link for found in links.values() for link in found):
        match = bilibili.BVID.search(link)
        if match and match.group() not in given:
            given.append(match.group())
    given = given[:GIVEN]
    seeds = given + [bvid for bvid in surf._load_json(surf.VIDEOS_FILE, {})
                     if bilibili.BVID.fullmatch(bvid)][:SEEDS]
    todo = [bvid for bvid in seeds if bvid not in done]
    say(f"1a. The lists beside {len(todo)} videos: the explainer videos given as links, and "
        "a few game videos the bot read ...")
    for bvid in todo:
        read_page(state, bvid, done)
    hopped = sum(1 for bvid in done if bvid not in seeds)
    say(f"1b. ... and beside up to {max(0, HOPS - hopped)} explainer videos seen there ...")
    while hopped < HOPS:
        bvid = next_hop(state, memes, given, done)
        if not bvid:
            break
        read_page(state, bvid, done)
        hopped += 1
    state["crawl_done"] = True
    found = sum(1 for video in pool.values() if explainers.looks_like_explainer(video["title"]))
    say(f"    {len(pool)} videos seen beside {len(done)}, {found} of them look like explainers")


def next_hop(state, memes, given, done):
    """The explainer video in the pool whose lists to read next: first the ones by people who
    explain this game's memes (who made a video given as a link, or one that names a learned
    meme), then the ones made to explain something (XX是什么梗), the most played first."""
    pool, pages = state.get("pool") or {}, state.get("pages") or {}
    named = {hit["bvid"] for hits in pool_matches(state, memes).values() for hit in hits}
    authors = {pool[bvid].get("author") for bvid in named} | \
        {(pages.get(bvid) or {}).get("author") for bvid in given}
    authors -= {"", None}
    looks = [bvid for bvid, video in pool.items() if bvid not in done
             and explainers.looks_like_explainer(video["title"])]
    looks.sort(key=lambda bvid: (pool[bvid].get("author") not in authors,
                                 not explainers.MADE_TO_EXPLAIN.search(pool[bvid]["title"]),
                                 -pool[bvid]["plays"]))
    return looks[0] if looks else None


def read_page(state, bvid, done):
    try:
        bilibili.video_detail(bvid)  # note_detail() keeps what it shows
        page = state["pages"].get(bvid) or {}
        say(f"    {bvid}  {page.get('related', 0)} beside ({page.get('explainers', 0)} explain "
            f"something), collection of {page.get('episodes', 0)}  {page.get('title', '')[:30]}")
    except bilibili.Captcha as error:
        say(f"    {bvid}  skipped: {error}")
    except bilibili.Error as error:
        say(f"    {bvid}  skipped: {error}")
    except (urllib.error.URLError, OSError) as error:
        say(f"    {bvid}  skipped (network trouble: {error})")
    done.append(bvid)
    save_state(state)


def pool_matches(state, memes, videos=None):
    """{phrase: [video]} for every learned meme a video in the pool (or in videos, {bvid:
    {"title"}}) names in its title."""
    pool = (state.get("pool") or {}) if videos is None else videos
    parts = {bvid: surf.units(surf.clean(video["title"])) for bvid, video in pool.items()}
    joined = {bvid: "".join(units) for bvid, units in parts.items()}
    found = {}
    for phrase, entry in memes.items():
        for name in names_of(phrase, entry):
            wanted = surf.units(surf.clean(name))
            wanted = surf.phrase_of(wanted)[1] or wanted
            text = "".join(wanted)
            if len(text) < 2:
                continue
            for bvid, units in parts.items():
                if text in joined[bvid] and surf.contains(units, wanted):
                    hits = found.setdefault(phrase, [])
                    if bvid not in [hit["bvid"] for hit in hits]:
                        hits.append(dict(pool[bvid], bvid=bvid, explains=explainers.
                                         looks_like_explainer(pool[bvid]["title"])))
    return found


def uploaders(state, searcher):
    """Other lists a visitor may get without searching: what the people who made explainer
    videos have uploaded. For up to UPLOADERS of them (whose pages were read here, the ones
    with the most explainer videos seen first): their collections and series (up-lists), the
    videos of up to COLLECTIONS collections (up-collection) and of one series (up-series),
    and their newest videos (up-videos: it wanted a captcha on 2026-10-31). What they list
    goes to the pool, with how "uploader". Returns how many videos that added."""
    pool, pages = state.setdefault("pool", {}), state.get("pages") or {}
    asked = state.setdefault("uploaders", {})
    seen, mids = {}, {}
    for video in pool.values():
        if explainers.looks_like_explainer(video["title"]) and video.get("author"):
            seen[video["author"]] = seen.get(video["author"], 0) + 1
    for page in pages.values():
        if page.get("mid") and page.get("author") and \
                explainers.looks_like_explainer(page.get("title", "")):
            mids.setdefault(page["author"], page["mid"])
    todo = [name for name in sorted(mids, key=lambda name: -seen.get(name, 0))[:UPLOADERS]
            if str(mids[name]) not in asked]
    if not todo:
        return 0
    say(f"1d. What {len(todo)} uploaders of explainer videos list (their collections, series "
        "and newest videos) ...")
    metas = lambda items: [item["meta"] for item in items or []
                           if isinstance(item, dict) and isinstance(item.get("meta"), dict)]
    best = lambda found: sorted((item for item in found if item[0]), key=lambda item: (
        not explainers.looks_like_explainer(item[1]), -item[2]))
    before = len(pool)
    sizes = [100, 30]  # videos asked for of a collection at once: 30 is what the page asks for
    for name in todo:
        mid, got = mids[name], {}
        entry = asked[str(mid)] = {"name": name, "lists": [], "videos": 0, "explainers": 0}
        referer = SPACE.format(mid)
        say(f"    {name} ({mid}): {seen.get(name, 0)} explainer videos seen so far")
        if "up-lists" not in searcher.dropped:
            _, videos, data = searcher.probe(
                "up-lists", name, "/x/polymer/web-space/home/seasons_series",
                {"mid": mid, "page_num": 1, "page_size": 20}, referer)
            got.update(videos)
            lists = data.get("items_lists") if isinstance(data.get("items_lists"), dict) else {}
            seasons = [(meta.get("season_id"), str(meta.get("name") or ""), meta.get("total") or 0)
                       for meta in metas(lists.get("seasons_list"))]
            series = [(meta.get("series_id"), str(meta.get("name") or ""), meta.get("total") or 0)
                      for meta in metas(lists.get("series_list"))]
            entry["lists"] = [{"kind": kind, "id": item[0], "name": item[1], "total": item[2]}
                              for kind, found in (("collection", seasons), ("series", series))
                              for item in found]
            for number, title, total in best(seasons)[:COLLECTIONS]:
                if "up-collection" in searcher.dropped:
                    break
                for size in list(sizes):
                    out, videos, _ = searcher.probe(
                        "up-collection", f"{name}: {title}",
                        "/x/polymer/web-space/seasons_archives_list",
                        {"mid": mid, "season_id": number, "sort_reverse": "false",
                         "page_num": 1, "page_size": size}, referer)
                    if out["outcome"] != "error" or "up-collection" in searcher.dropped:
                        break
                    sizes.remove(size)  # too many at once, perhaps: not asked for again
                got.update(videos)
            for number, title, total in best(series)[:1]:
                if "up-series" in searcher.dropped:
                    break
                _, videos, _ = searcher.probe(
                    "up-series", f"{name}: {title}", "/x/series/archives",
                    {"mid": mid, "series_id": number, "only_normal": "true", "sort": "desc",
                     "pn": 1, "ps": 30}, referer)
                got.update(videos)
        if "up-videos" not in searcher.dropped:
            _, videos, _ = searcher.probe(
                "up-videos", name, "/x/space/wbi/arc/search",
                {"mid": mid, "ps": 30, "pn": 1, "order": "pubdate"}, referer)
            got.update(videos)
        for bvid, video in got.items():
            pool.setdefault(bvid, {"title": video["title"], "plays": video.get("plays") or 0,
                                   "author": video.get("author") or name, "how": "uploader",
                                   "beside": ""})
        entry.update(videos=len(got), explainers=sum(
            1 for video in got.values() if explainers.looks_like_explainer(video["title"])))
        searcher.drop_failing()
        save_state(state)
    say(f"    {len(pool) - before} videos more in the pool")
    return len(pool) - before


# -- the phrases --------------------------------------------------------------------------

def read_links():
    """{phrase: [link]}: the videos the lookup test found (its links.json), and the ones in
    links.txt beside the results: a phrase, a space, a link on each line."""
    links = {}
    given = surf._load_json(beside(LOOKUP, "links.json"), {})
    for phrase, found in (given.items() if isinstance(given, dict) else ()):
        links.setdefault(store.normal(phrase), []).extend(
            link for link in (found if isinstance(found, list) else []) if isinstance(link, str))
    try:
        with open(os.path.join(HOME, "links.txt"), encoding="utf-8-sig") as f:
            for line in f:
                words = line.split()
                if len(words) >= 2 and not line.lstrip().startswith("#"):
                    links.setdefault(store.normal(words[0]), []).extend(
                        link for link in words[1:] if link not in links.get(
                            store.normal(words[0]), ()))
    except OSError:
        pass
    return links


def find_videos(state, wanted, memes, links, hearing, materials, known):
    """Steps 1 and 2 for the phrases, one after the other, so that what was read is kept
    when Bilibili refuses later: state["found"][phrase] = {"searches", "videos"},
    state["web"][phrase] = what the web gave (look_up()), and materials[phrase] = what
    explain reads of up to VIDEOS of the videos (read_videos())."""
    found = state.setdefault("found", {})
    searcher = Searcher(state)
    say(f"1c. Searching for \"X是什么梗\" on Bilibili (the first {PROBES} phrases every way, "
        f"the rest the ways that worked) and on the web, and reading up to {VIDEOS} videos "
        "and the web pages for each phrase"
        + ("" if hearing else "; not hearing the videos, Whisper isn't installed") + " ...")
    try:
        for phrase in wanted:  # the videos given as links: read before anything is searched
            given = given_videos(links.get(phrase, ()))
            if given:
                entry = found.setdefault(phrase, {"searches": [], "videos": []})
                entry["videos"] = given + [video for video in entry["videos"] if video["bvid"]
                                           not in [g["bvid"] for g in given]]
                say(f"    {phrase} (the videos given)")
                read_videos(state, phrase, entry, hearing, materials)
                save_state(state)
        _find_videos(state, wanted, memes, links, hearing, materials, found, searcher, known)
        if uploaders(state, searcher):
            say("1e. The phrases once more, with what the uploaders list ...")
            _find_videos(state, wanted, memes, links, hearing, materials, found, searcher,
                         known)
    finally:
        state["search_requests"] = state.get("search_requests", 0) + searcher.count
        save_state(state)


def _find_videos(state, wanted, memes, links, hearing, materials, found, searcher, known):
    for number, phrase in enumerate(wanted):
        entry = found.setdefault(phrase, {"searches": [], "videos": []})
        names = names_of(phrase, memes[phrase])
        say(f"    {phrase}")
        given = given_videos(links.get(phrase, ()))
        if entry.get("searched"):
            say("      searched in an earlier run")
        elif number < PROBES and not state.get("probed", {}).get(phrase):
            # Every way, the page first for every other phrase: a refusal may count against
            # the ones tried after it
            order = ("page", "api-page", "api-type", "api-old", "api-all") if number % 2 == 0 \
                else ROUTES
            for route in order:
                if route in searcher.dropped:  # refused outright before: not again
                    continue
                out, videos = searcher.ask(route, phrase)
                entry["searches"].append(out)
                add_matches(entry, videos, names, route)
            state.setdefault("probed", {})[phrase] = True
            entry["searched"] = True
        else:
            for route in searcher.working()[:2]:
                out, videos = searcher.ask(route, phrase)
                entry["searches"].append(out)
                add_matches(entry, videos, names, route)
                if out["outcome"] in ("videos", "empty"):
                    entry["searched"] = True
                    break
            else:
                if not searcher.working():
                    say("      no way to search works: not searched")
            searcher.drop_failing()
        game = (memes[phrase].get("games") or ["lol"])[0]
        look_up(state, phrase, names, known.get(f"{game}:{phrase}") or {}, entry)
        # What the related lists and collections gave, and the links, which are read first
        seen = [dict(video, bvid=bvid) for bvid, video in (state.get("pool") or {}).items()
                if explainers.looks_like_explainer(video["title"])]
        for video in matching(seen, names):
            if video["bvid"] not in [v["bvid"] for v in entry["videos"]]:
                entry["videos"].append({"bvid": video["bvid"], "title": video["title"],
                                        "how": video["how"], "plays": video["plays"]})
        entry["videos"] = given + [video for video in entry["videos"]
                                   if video["bvid"] not in [g["bvid"] for g in given]]
        if entry["videos"]:
            say(f"      -> {len(entry['videos'])} videos name it: "
                + "; ".join(f"{v['title'][:28] or v['bvid']} [{v['how']}]"
                            for v in entry["videos"][:3]))
        save_state(state)
        read_videos(state, phrase, entry, hearing, materials)
        save_state(state)


def given_videos(links):
    """[{"bvid", "title", "how": "link", "plays"}] for some video links."""
    given = []
    for link in links:
        match = bilibili.BVID.search(link)  # a short link (b23.tv) has to be opened
        try:
            bvid = match.group() if match else bilibili.bvid_of(link)
        except OSError as error:
            say(f"      couldn't open {link}: {error}")
            continue
        if bvid and bvid not in [video["bvid"] for video in given]:
            given.append({"bvid": bvid, "title": "", "how": "link", "plays": 0})
    return given


def add_matches(entry, videos, names, route):
    for video in matching(list(videos.values()), names):
        if video["bvid"] not in [v["bvid"] for v in entry["videos"]]:
            entry["videos"].append({"bvid": video["bvid"], "title": video["title"],
                                    "how": route, "plays": video.get("plays") or 0})


def look_up(state, phrase, names, known, entry):
    """The bot's own lookup on the web for a phrase, as it is (explainers._web(),
    read_pages()): the Bilibili videos a search engine finds go to entry["videos"], and
    state["web"][phrase] keeps {"web": the search results, "texts": the pages read, or
    "pages": what they say when an earlier run summed them up}. What an earlier run on this
    computer found (known: the phrase's entry in its explainers.json) isn't asked for again."""
    kept = state.setdefault("web", {}).setdefault(phrase, {})
    if "web" not in kept and (known.get("web") or {}).get("results"):
        kept.update(web=known["web"], searched_by="an earlier run")
    try:
        videos = explainers._web(phrase, names, kept, "web", False)
    except Exception as error:  # the lookup is the other thread's: write it down, go on
        say(f"      the web search failed: {error!r}")
        videos = []
    for video in videos:
        if video["bvid"] not in [v["bvid"] for v in entry["videos"]]:
            entry["videos"].append({"bvid": video["bvid"], "title": video["title"],
                                    "how": "web", "plays": 0})
    results = (kept.get("web") or {}).get("results") or []
    if "pages" not in kept and "texts" not in kept:
        told = [page for page in known.get("pages") or [] if isinstance(page, dict)]
        if told:
            kept.update(pages=told, pages_by="an earlier run")
        else:
            try:
                kept["texts"] = explainers.read_pages(phrase, names, results)
            except Exception as error:
                say(f"      reading the web pages failed: {error!r}")
    engines = sorted({str(result.get("engine") or "?") for result in results})
    say(f"      the web: {len(results)} results"
        + (f" ({', '.join(engines)})" if results else "")
        + f", {len(videos)} Bilibili videos that name it, "
        + (f"its pages summed up {'by an earlier run' if kept.get('pages_by') else 'before'}"
           if kept.get("pages") else f"{len(kept.get('texts') or [])} pages read"))


def whisper_ready(state):
    """Load Whisper on the processor, once (the first time it downloads the model). The bot
    itself tries the graphics card first; when only some of the card's files are installed
    that can end the whole program, so this test doesn't."""
    if not _hearing["tried"]:
        _hearing["tried"] = True
        started = time.time()
        try:
            explainers._whisper = (explainers._load_whisper("cpu"), "cpu")
        except Exception as error:  # no download, no room on the disk, a broken install
            state["whisper"] = {"problem": f"{error!r}"[:300]}
            say(f"      Whisper couldn't be loaded ({error!r}): not hearing the videos in "
                "this run")
        else:
            _hearing["ready"] = True
            state["whisper"] = {"model": explainers.WHISPER, "on": "processor",
                                "threads": os.environ.get("OMP_NUM_THREADS", ""),
                                "load_seconds": round(time.time() - started)}
    return _hearing["ready"]


def read_videos(state, phrase, entry, hearing, materials):
    """Step 2 for a phrase: read up to VIDEOS of its videos, as explain does, and hear the
    ones without subtitles when Whisper is there. materials[phrase] = [{"how", "read": the
    video as its page shows it, "heard": the same with what was heard, or None}]."""
    read = [material["read"]["bvid"] for material in materials.get(phrase, [])]
    for video in (entry.get("videos") or [])[:VIDEOS]:
        if video["bvid"] in read:  # in the pass before
            continue
        try:
            material = explainers.read(video["bvid"], phrase,
                                       hearing and whisper_ready(state))
        except bilibili.Captcha as error:
            say(f"      skipped {video['bvid']}: {error}")
            continue
        except bilibili.Error as error:
            say(f"      skipped {video['bvid']}: {error}")
            continue
        except (urllib.error.URLError, OSError) as error:
            say(f"      skipped {video['bvid']} (network trouble: {error})")
            continue
        except (KeyError, TypeError, ValueError) as error:
            say(f"      skipped {video['bvid']} (an unexpected answer: {error!r})")
            continue
        heard = dict(material) if material.get("heard") == "audio" else None
        plain = dict(material, said="", heard="") if heard else dict(material)
        materials.setdefault(phrase, []).append({"how": video["how"], "read": plain,
                                                 "heard": heard})
        say(f"      read {video['bvid']} {material.get('title', '')[:30]} | said "
            f"{len(material.get('said') or '')} characters"
            f"{' (' + material['heard'] + ')' if material.get('heard') else ''}, "
            f"{len(material.get('comments') or [])} comments, "
            f"{len(material.get('danmu') or [])} danmu"
            + (f", not heard: {material['unheard']}" if material.get("unheard") else ""))


# -- hearing: a way to fetch the sound ----------------------------------------------------
#
# On 2026-11-23 the sound of every video was refused (HTTP 403 or 567) when the bot asked
# for it as it does (bilibili.download(): the first address of the smallest stream, with the
# API's headers and no Range). This tries the other ways a visitor has, writes down what
# each one answered, and hears the video with the first way that works:
#   sources  page   the streams the video's page carries (window.__playinfo__), as the bot
#            api    the player's API (/x/player/wbi/playurl), the sound alone
#            html5  the same API as a phone's browser asks it: one small mp4, sound and
#                   pictures
#   servers  the addresses as given (the first, then the spare ones), then the first one
#            moved to the other UPOS servers (AUDIO_HOSTS), as some downloaders do
#   styles   player  what the player in a browser sends: the video's page as Referer,
#                    Origin, Range
#            plain   what most downloaders send: the site as Referer, Range
#            bare    no Referer, Range
#            bot     what the bot sends now
# Each try asks for the first PROBE_BYTES only. The first video tries every way, so the
# results show them all; later videos try the ways that worked first, and every way only
# when those fail. After GIVE_UP videos in a row that no way could fetch, the run stops
# trying. Nothing written down holds a whole address: they name this computer (oi) and its
# visitor id.

AUDIO_HOSTS = ("upos-sz-mirrorcos.bilivideo.com", "upos-sz-mirrorali.bilivideo.com",
               "upos-sz-mirrorhw.bilivideo.com", "upos-sz-mirror08c.bilivideo.com",
               "upos-sz-mirrorcosov.bilivideo.com", "upos-sz-mirroraliov.bilivideo.com")
SOURCES = ("page", "api", "html5")
STYLES = ("player", "plain", "bare", "bot")
MOVED_STYLES = ("player", "plain")  # the styles tried on a moved address
PROBE_BYTES = 1024
CDN_DELAY = 2  # seconds between two requests for a stream, plus up to 1 at random
GIVE_UP = 2  # videos in a row that no way could fetch before the run stops trying
MAX_VIDEO_FILE = 80_000_000  # bytes of an html5 mp4 saved at most (sound and pictures)
# Why an earlier run didn't hear a video, when a way tried here may fetch its sound
FETCH_FAILED = re.compile(r"the audio stream answered|/x/player/|no way fetched its sound|"
                          r"not trying")
IP_LIKE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}\b",
                     re.I)
_audio = {"state": None, "last": 0.0, "family": "", "opener": None}


def _host_kind(host):
    host = (host or "").lower()
    if host.endswith("akamaized.net"):
        return "akamai"
    if ".mcdn." in host or host.endswith(".szbdyd.com"):
        return "pcdn"  # servers in people's homes that Bilibili rents
    if host.startswith("upos-"):
        return "upos-ov" if host.split(".")[0].endswith("ov") else "upos"
    if host.startswith("cn-") and host.endswith(".bilivideo.com"):
        return "cn"
    return "other"


def _about(url):
    """What a stream address says of itself, without the parts that name this computer or
    someone's home: {"host", "kind", "os", "og", "platform", "oi": "IPv4", "IPv6" or ""
    (which kind of address the stream was given to)}."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    first = lambda name: (query.get(name) or [""])[0][:20]
    host = (parts.hostname or "").lower()
    if _host_kind(host) == "pcdn":
        host = "*." + host.split(".", 1)[-1]  # its first part is the home's address
    try:
        host += f":{parts.port}" if parts.port else ""
    except ValueError:
        pass
    oi = first("oi").lower()
    if oi.startswith("0x"):
        family = "IPv6" if len(oi) > 10 else "IPv4"
    elif oi.isdigit():
        family = "IPv4" if int(oi) < 2 ** 32 else "IPv6"
    else:
        family = ""
    return {"host": host, "kind": _host_kind(parts.hostname), "os": first("os"),
            "og": first("og"), "platform": first("platform"), "oi": family}


def _snippet(body):
    """The start of an answer's text, without tags or anything like an address."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    return IP_LIKE.sub("<address>", " ".join(re.sub(r"<[^>]+>", " ", text).split()))[:160]


def _cdn_headers(headers):
    """The headers of a stream server's answer that may say why it refused, cut short."""
    keep = ("server", "content-type", "content-length", "content-range", "via", "age")
    try:
        return {name.lower(): IP_LIKE.sub("<address>", str(value))[:80]
                for name, value in headers.items()
                if name.lower() in keep or "bili" in name.lower()
                or name.lower().startswith("x-")}
    except AttributeError:
        return {}


class _Watched(http.client.HTTPSConnection):
    """A connection that notes whether it went over IPv4 or IPv6 (to the proxy, when this
    computer has one)."""

    def connect(self):
        super().connect()
        _audio["family"] = "IPv6" if self.sock.family == socket.AF_INET6 else "IPv4"


class _WatchedHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Watched, req, context=getattr(self, "_context", None))


def _cdn_open(request, timeout=30):
    """Open a stream's address (the simulation replaces this)."""
    if _audio["opener"] is None:
        _audio["opener"] = urllib.request.build_opener(_WatchedHTTPS())
    return _audio["opener"].open(request, timeout=timeout)


def _style_headers(style, bvid):
    agent = bilibili.HEADERS["User-Agent"]
    if style == "bot":
        return dict(bilibili.HEADERS)
    if style == "player":
        return {"User-Agent": agent, "Accept": "*/*", "Accept-Encoding": "identity",
                "Accept-Language": bilibili.HEADERS["Accept-Language"],
                "Origin": "https://www.bilibili.com",
                "Referer": bilibili.PAGE.format(bvid=bvid), "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site"}
    if style == "plain":
        return {"User-Agent": agent, "Accept": "*/*", "Referer": "https://www.bilibili.com/"}
    return {"User-Agent": agent, "Accept": "*/*"}  # bare


def _try_stream(url, style, bvid, path=None, limit=bilibili.MAX_DOWNLOAD):
    """One try at a stream: its first PROBE_BYTES (no path), or all of it into path.
    {"status" (None when it couldn't be reached), "bytes", "media" (not a web page),
    "headers", "note", "family", "ms"}."""
    headers = _style_headers(style, bvid)
    if style != "bot":
        headers["Range"] = "bytes=0-" if path else f"bytes=0-{PROBE_BYTES - 1}"
    wait = _audio["last"] + CDN_DELAY + random.random() - time.time()
    if wait > 0:
        time.sleep(wait)
    began, out = time.time(), {"status": None, "bytes": 0}
    _audio["family"] = ""
    try:
        with _cdn_open(urllib.request.Request(url, headers=headers)) as response:
            out.update(status=response.status, headers=_cdn_headers(response.headers))
            if getattr(response, "url", url) != url:  # sent on to another server
                out["went_to"] = _about(response.url)["host"]
            if path is None:
                head = response.read(PROBE_BYTES)
                out.update(bytes=len(head), media=not head.lstrip()[:1] in (b"<", b"{"))
            else:
                with open(path, "wb") as f:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        if not out["bytes"]:
                            out["media"] = not chunk.lstrip()[:1] in (b"<", b"{")
                        out["bytes"] += len(chunk)
                        if out["bytes"] > limit:
                            out["note"] = f"bigger than {limit // 1_000_000} MB"
                            break
                        f.write(chunk)
    except urllib.error.HTTPError as error:
        try:
            body = error.read(400)
        except Exception:
            body = b""
        out.update(status=error.code, headers=_cdn_headers(error.headers),
                   note=_snippet(body))
        if (getattr(error, "url", None) or url) != url:
            out["went_to"] = _about(error.url)["host"]
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        out["note"] = _snippet(f"{error!r}")
    _audio["last"] = time.time()
    out.update(family=_audio["family"], ms=round((time.time() - began) * 1000))
    return out


def _dash_urls(dash):
    """The addresses of the smallest sound stream in a player answer's "dash" part: the
    first one, then the spare ones."""
    streams = sorted((stream for stream in (dash or {}).get("audio") or []
                      if isinstance(stream, dict)),
                     key=lambda stream: stream.get("bandwidth") or 10 ** 9)
    for stream in streams:
        urls = [stream.get("baseUrl") or stream.get("base_url")] + list(
            stream.get("backupUrl") or stream.get("backup_url") or [])
        urls = [url for url in dict.fromkeys(urls) if url]
        if urls:
            return urls
    return []


def _stream_urls(source, bvid, cid):
    """The addresses of a video's smallest stream got one way (SOURCES), as given."""
    if source == "page":
        found = bilibili.PLAYINFO.search(bilibili._get(bilibili.PAGE.format(bvid=bvid),
                                                       cookies=bilibili._visitor_cookies()))
        if not found:
            raise bilibili.Error("its page carries no streams")
        try:
            data = json.loads(found.group(1)).get("data") or {}
        except ValueError:
            raise bilibili.Error("its page's streams aren't JSON") from None
        return _dash_urls(data.get("dash"))
    params = {"bvid": bvid, "cid": cid, "qn": 16, "fnver": 0, "fourk": 0}
    if source == "api":
        data = bilibili._request("/x/player/wbi/playurl", dict(params, fnval=16),
                                 signed=True) or {}
        return _dash_urls(data.get("dash"))
    data = bilibili._request("/x/player/wbi/playurl",
                             dict(params, fnval=1, platform="html5", high_quality=1),
                             signed=True) or {}
    urls = []
    for part in (data.get("durl") or [])[:1]:
        urls += [part.get("url")] + list(part.get("backup_url") or [])
    return [url for url in dict.fromkeys(urls) if url]


def _candidates(urls):
    """[(address, server)] to try of one source: the addresses as given ("given:<kind>"),
    then the first one signed for the UPOS servers moved to each of AUDIO_HOSTS
    ("moved:<server>")."""
    out = [(url, "given:" + _host_kind(urllib.parse.urlsplit(url).hostname)) for url in urls]
    # one a UPOS server was given, when there is one: the others are signed for another kind
    signed = sorted((url for url in urls if "upsig=" in url and "/upgcxcode/" in url),
                    key=lambda url: not _host_kind(urllib.parse.urlsplit(url).hostname)
                    .startswith("upos"))
    signed = signed[0] if signed else None
    if signed:
        parts = urllib.parse.urlsplit(signed)
        have = {urllib.parse.urlsplit(url).hostname for url in urls}
        out += [(urllib.parse.urlunsplit(("https", host, parts.path, parts.query, "")),
                 "moved:" + host.split(".")[0].replace("upos-sz-", ""))
                for host in AUDIO_HOSTS if host not in have]
    return out


def _worked(out, whole=False):
    return out["status"] in (200, 206) and out["bytes"] > (1000 if whole else 0) \
        and out.get("media", True) and not str(out.get("note") or "").startswith("bigger")


def hear_video(video, phrase):
    """explainers._hear_video() with the ways above: the sound saved, heard and deleted;
    what is said. Raises bilibili.Error when no way fetched it (read() keeps that as the
    reason it wasn't heard)."""
    state = _audio["state"]
    audio = state.setdefault("audio", {})
    for key, empty in (("tries", []), ("works", {}), ("videos", {}), ("missed", 0),
                       ("swept", 0)):
        audio.setdefault(key, empty)
    audio.setdefault("proxy", bool(urllib.request.getproxies()))
    audio.setdefault("python", sys.version.split()[0])
    bvid = video["bvid"]
    if audio.get("stopped"):
        raise bilibili.Error("not trying: no way fetched its sound for the videos before")
    mine = audio["videos"][bvid] = {"sources": {}, "worked": None, "tries": 0}
    asked = {}  # source: the addresses it gave this time

    def urls(source):
        if source not in asked:
            try:
                asked[source] = _stream_urls(source, bvid, video["cid"])
                mine["sources"][source] = f"{len(asked[source])} addresses"
            except bilibili.Blocked:
                raise
            except (bilibili.Error, bilibili.Captcha, urllib.error.URLError, OSError,
                    KeyError, TypeError, ValueError) as error:
                asked[source] = []
                mine["sources"][source] = _snippet(str(error) or repr(error))
        return asked[source]

    def attempt(source, url, server, style, path=None):
        limit = MAX_VIDEO_FILE if source == "html5" else bilibili.MAX_DOWNLOAD
        out = _try_stream(url, style, bvid, path, limit)
        audio["tries"].append(dict(_about(url), bvid=bvid, source=source, server=server,
                                   style=style, whole=bool(path), **out))
        mine["tries"] += 1
        return _worked(out, bool(path))

    found, every = None, not audio["swept"]
    for way in ([] if every else sorted(audio["works"], key=lambda w: -audio["works"][w])):
        source, server, style = way.split("|")
        for url, label in _candidates(urls(source)):
            if label == server and attempt(source, url, label, style):
                found = (source, url, label, style)
                break
        if found:
            break
    if not found:
        audio["swept"] += 1
        for source in SOURCES:
            for url, label in _candidates(urls(source)):
                for style in STYLES if label.startswith("given") else MOVED_STYLES:
                    if attempt(source, url, label, style):
                        way = f"{source}|{label}|{style}"
                        audio["works"].setdefault(way, 0)
                        found = found or (source, url, label, style)
                        if not every:
                            break
                if found and not every:
                    break
            if found and not every:
                break
    save_state(state)
    if not found:
        audio["missed"] += 1
        if audio["missed"] >= GIVE_UP:
            audio["stopped"] = True
            say(f"      no way fetched the sound of {GIVE_UP} videos in a row: not trying "
                "for the others in this run")
        tally = {}
        for one in audio["tries"]:
            if one["bvid"] == bvid:
                key = f"{one['source']} {one['status'] or 'unreachable'}"
                tally[key] = tally.get(key, 0) + 1
        missing = [f"{source}: {note}" for source, note in mine["sources"].items()
                   if not note.endswith("addresses")]
        raise bilibili.Error("no way fetched its sound (" + ", ".join(
            f"{key} x{count}" for key, count in tally.items()) + "; ".join(
            [""] + missing) + ")")
    audio["missed"] = 0
    source, url, label, style = found
    way = f"{source}|{label}|{style}"
    audio["works"][way] = audio["works"].get(way, 0) + 1
    mine["worked"] = way
    folder = os.path.join(surf.SURF_DIR, "explainers", "audio")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{bvid}.{'mp4' if source == 'html5' else 'm4a'}")
    try:
        if not attempt(source, url, label, style, path):
            mine["worked"] = None
            raise bilibili.Error(f"its first bytes came {way}, but not the whole of it")
        say(f"      fetched the sound of {bvid}: {way}")
        return explainers.hear(path, phrase)
    finally:
        save_state(state)
        if os.path.exists(path):
            os.remove(path)


def hear_again():
    """Take off the reason an earlier run gave for not hearing a video when a way tried here
    may fetch its sound, so read() hears it this time. Returns how many."""
    folder = os.path.join(surf.SURF_DIR, "explainers")
    count = 0
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        path = os.path.join(folder, name)
        video = surf._load_json(path, None) if name.endswith(".json") else None
        if isinstance(video, dict) and FETCH_FAILED.search(str(video.get("unheard") or "")):
            video.pop("unheard")
            surf._save_json(path, video)
            count += 1
    return count


# -- the model ----------------------------------------------------------------------------

class Model:
    """The judge's model, and the one on this computer when it can't be reached."""

    def __init__(self):
        self.name = judging.MODEL
        try:
            judging.check_model(self.name)
        except judging._Unreachable as error:
            self.name = explainers._stand_in(error)

    def ask(self, call):
        """call(model), with the stand-in from then on when the online one stops answering."""
        try:
            return call(self.name)
        except judging._Unreachable as error:
            if self.name == judging.LOCAL_MODEL:
                raise
            self.name = explainers._stand_in(error, rest=True)
            return call(self.name)


def candidates_of_tests():
    """{(game, phrase): candidate} from the last two real runs' folders beside this one."""
    found = {}
    for test in ("report-test", "variants-test"):  # the newer one's wins
        path = beside(test, "surf", "candidates.json")
        for candidate in surf._load_json(path, {}).get("candidates") or []:
            if isinstance(candidate, dict) and candidate.get("phrase"):
                found[(candidate.get("game"), candidate["phrase"])] = candidate
    return found


def quote_from(quote, video):
    """Where in what a video gave the model's quote is: "title", "description", "said",
    "comments" or "danmu" (the first that has it), "" for none."""
    wanted = explainers._plain(quote or "")
    parts = (("title", video.get("title")), ("description", video.get("desc")),
             ("said", video.get("said")),
             ("comments", " ".join(comment["text"] for comment in video.get("comments") or [])),
             ("danmu", " ".join(video.get("danmu") or [])))
    return next((name for name, text in parts
                 if wanted and wanted in explainers._plain(text or "")), "")


def backs(summary):
    """Whether a summary (of a video, or of web pages) says what the phrase means."""
    return bool(summary and summary.get("about") and summary.get("meaning"))


ARMS = (("pages", "with the web pages"), ("read", "with the videos, as read"),
        ("heard", "with the videos, as heard"))


def judge_phrases(state, wanted, memes, materials, model):
    """Steps 3 and 4: sum up the web pages and the videos, and judge each phrase with the
    danmu alone, then with each kind of summary (ARMS)."""
    known = candidates_of_tests()
    summaries = state.setdefault("summaries", {})
    judged = state.setdefault("judged", {})
    looked = state.setdefault("web", {})
    game_of = {phrase: (memes[phrase].get("games") or ["lol"])[0] for phrase in wanted}
    say("3. Finding the danmu lines that use the phrases ...")
    asked = {(game_of[phrase], phrase) for phrase in wanted}
    try:
        uses = judging.uses(asked)
        lines = judging.usage_lines(asked, found=uses)
    except Exception as error:  # the copy of the danmu is missing or broken
        say(f"   couldn't read the danmu ({error!r}): using the memes' example lines")
        uses, lines = {}, {}
    titles = surf._load_json(surf.VIDEOS_FILE, {})
    say(f"4. Summing the pages and videos up and judging with {model.name} ...")
    kept_as_was = []
    for phrase in wanted:
        game, entry = game_of[phrase], memes[phrase]
        candidate = dict(known.get((game, phrase)) or {
            "phrase": phrase, "game": game,
            "variants": [{"phrase": v, "people": 0} for v in entry.get("variants") or []]})
        candidate.pop("judge", None)
        where = (game, phrase)
        used = lines.get(where) or [e if isinstance(e, str) else e.get("text", "")
                                    for e in (candidate.get("examples") or [])
                                    + list(entry.get("examples") or [])]
        old = judged.get(phrase) or {}
        if old.get("without") and (old.get("model") != model.name
                                   or old.get("lines") != len(used)
                                   or old.get("used", used) != used):
            # Judged in an earlier run with another model or other danmu lines: those
            # answers stay as they are (they are the ones being marked), and a new one
            # wouldn't compare with them
            kept_as_was.append(phrase)
            continue
        if old.get("model") != model.name or old.get("lines") != len(used):
            old = {}
        kept = looked.setdefault(phrase, {})
        if kept.get("texts") and "pages" not in kept:
            kept["pages"] = [model.ask(lambda name: explainers.summarize_pages(
                phrase, game, kept["texts"], name))]
            save_state(state)
        told = []
        for material in materials.get(phrase, []):
            row = {"how": material["how"]}
            for arm in ("read", "heard"):
                video = material[arm]
                if not video:
                    row[arm] = None
                    continue
                key = f"{phrase}|{video['bvid']}|{arm}"
                if key not in summaries or summaries[key].get("model") != model.name:
                    summaries[key] = model.ask(lambda name: explainers.summarize(
                        phrase, game, video, name))
                    save_state(state)
                row[arm] = dict(summaries[key], said=len(video.get("said") or ""),
                                comments=len(video.get("comments") or []),
                                danmu=len(video.get("danmu") or []),
                                seconds=video.get("duration") or 0,
                                quote_from=quote_from(summaries[key].get("quote"), video))
            told.append(row)
        says = {"pages": [page for page in kept.get("pages") or [] if backs(page)],
                "read": [row["read"] for row in told if backs(row["read"])],
                "heard": [row["heard"] or row["read"] for row in told
                          if backs(row["heard"] or row["read"])]
                if any(row["heard"] for row in told) else []}
        shown = judging._titles(uses.get(where) or [], titles)
        result = {"model": model.name, "lines": len(used), "used": used, "marks": {},
                  "without": old.get("without"), "told": told,
                  "web_says": kept.get("pages") or []}
        if not result["without"]:
            result["without"] = model.ask(lambda name: judging.judge_one(
                candidate, used, shown, name))
        for arm, _ in ARMS:
            marks = sorted(f"{s.get('bvid') or s.get('url')}:{s['meaning']}" for s in says[arm])
            result["marks"][arm] = marks
            if not marks:
                result[arm] = None
            elif old.get(arm) and (old.get("marks") or {}).get(arm) == marks:
                result[arm] = old[arm]
            else:
                result[arm] = model.ask(lambda name: judging.judge_one(
                    candidate, used, shown, name, says[arm]))
            judged[phrase] = result
            save_state(state)
        judged[phrase] = result
        save_state(state)
        short = lambda verdict: "-" if not verdict else \
            f"{verdict['kind']}: {verdict['meaning'] or '(no meaning)'}"
        say(f"   {phrase}")
        say(f"     danmu only: {short(result['without'])}")
        for page in result["web_says"]:
            say(f"     {page.get('site', 'the web')} says: "
                + (page["meaning"] if backs(page) else "(nothing about this phrase)"))
        for row in told:
            for arm in ("read", "heard"):
                if row[arm]:
                    say(f"     {row[arm]['bvid']} as {arm} says: " + (
                        row[arm]["meaning"] if backs(row[arm])
                        else "(it doesn't explain this phrase)"))
        for arm, label in ARMS:
            if result[arm]:
                say(f"     {label}: {short(result[arm])}")
    state["kept_as_was"] = kept_as_was
    if kept_as_was:
        say(f"   Not judged again, because the model or the danmu lines differ from the run "
            f"that judged them: {', '.join(kept_as_was)}")


# -- what came out ------------------------------------------------------------------------

def _cell(text):
    return " ".join(str(text or "").split()).replace("|", "/")


QUOTE = {"title": "its title", "description": "its description", "said": "what is said in it",
         "comments": "a comment", "danmu": "a danmu"}


def sound_section(audio):
    """result.md's lines on the ways hear_video() tried to fetch the sound."""
    tries = audio.get("tries") or []
    if not tries and not audio.get("videos"):
        return []
    videos = audio.get("videos") or {}
    out = ["## Ways to fetch the sound", "",
           f"{len(tries)} tries for {len(videos)} videos "
           f"({sum(1 for v in videos.values() if v.get('worked'))} fetched); Python "
           f"{audio.get('python', '?')}, a proxy on this computer: "
           f"{'yes' if audio.get('proxy') else 'no'}. Each try asked for the first "
           f"{PROBE_BYTES} bytes, except the ones marked whole.", "",
           "| Source | Server | Style | Tries | Worked | Answers |",
           "|---|---|---|---|---|---|"]
    ways = {}
    for one in tries:
        way = ways.setdefault((one["source"], one["server"], one["style"]),
                              {"tries": 0, "worked": 0, "answers": {}})
        way["tries"] += 1
        way["worked"] += _worked(one, one.get("whole"))
        answer = str(one["status"] or "unreachable") + (" whole" if one.get("whole") else "")
        way["answers"][answer] = way["answers"].get(answer, 0) + 1
    for (source, server, style), way in ways.items():
        out.append(f"| {source} | {server} | {style} | {way['tries']} | {way['worked']} | "
                   + ", ".join(f"{a} x{n}" for a, n in sorted(way["answers"].items())) + " |")
    seen, said = set(), []
    for one in tries:  # what the refusals said, once each
        if _worked(one, one.get("whole")):
            continue
        what = (f"{one['source']} {one['kind']} {one['host']} {one['style']} -> "
                f"{one['status'] or 'unreachable'}, server "
                f"{(one.get('headers') or {}).get('server', '?')}, os={one.get('os')} "
                f"og={one.get('og')} given to an {one.get('oi') or '?'} address, went over "
                f"{one.get('family') or '?'}"
                + (f", sent on to {one['went_to']}" if one.get("went_to") else "")
                + f": {_cell(one.get('note')) or '(nothing said)'}")
        key = (one["kind"], one["status"], one.get("note"))
        if key not in seen:
            seen.add(key)
            said.append(f"- {what}")
    if said:
        out += ["", "What the refusals said (once for each kind of server and answer):", ""]
        out += said[:20]
    out += ["", "Videos:", ""]
    for bvid, video in videos.items():
        out.append(f"- {bvid}: " + (f"fetched {video['worked']}" if video.get("worked")
                                     else "not fetched")
                   + f" after {video.get('tries', 0)} tries; "
                   + ", ".join(f"{source}: {note}"
                               for source, note in (video.get("sources") or {}).items()))
    if audio.get("stopped"):
        out.append(f"- Stopped trying after {GIVE_UP} videos in a row that no way fetched.")
    return out + [""]


def write_results(state, wanted, memes, started, model, hearing, problem):
    pool = state.get("pool") or {}
    matches = pool_matches(state, memes)
    found, judged = state.get("found") or {}, state.get("judged") or {}
    looked, heard = state.get("web") or {}, state.get("heard") or []
    whisper = state.get("whisper") or {}
    rows = []
    for phrase in wanted:
        entry, verdicts, kept = memes[phrase], judged.get(phrase) or {}, looked.get(phrase) or {}
        rows.append({"phrase": phrase, "games": entry.get("games") or [],
                     "meaning": entry.get("meaning", ""), "notes": entry.get("notes") or [],
                     "found": (found.get(phrase) or {}).get("videos") or [],
                     "searches": (found.get(phrase) or {}).get("searches") or [],
                     "web": {"results": (kept.get("web") or {}).get("results") or [],
                             "searched_by": kept.get("searched_by") or "this test",
                             "summed_up_by": kept.get("pages_by") or "this test",
                             "read": [{"site": page.get("site"), "url": page.get("url"),
                                       "title": page.get("title"),
                                       "characters": len(page.get("text") or ""),
                                       "snippet": bool(page.get("snippet"))}
                                      for page in kept.get("texts") or []]},
                     "web_says": kept.get("pages") or [],
                     "videos": verdicts.get("told") or [], "lines": verdicts.get("lines"),
                     "without": verdicts.get("without"), "with_pages": verdicts.get("pages"),
                     "with_read": verdicts.get("read"), "with_heard": verdicts.get("heard")})
    listed = {}  # what each way listed: the searches and the uploaders' lists, then the pool
    groups = {route: {bvid: {"title": title, "plays": 0, "how": route}
                      for bvid, title in videos.items()}
              for route, videos in (state.get("route_videos") or {}).items()}
    for how in ("related", "collection", "seen", "uploader"):
        groups[how] = {bvid: video for bvid, video in pool.items() if video["how"] == how}
    for name, videos in groups.items():
        if videos:
            listed[name] = {
                "videos": len(videos),
                "explainers": sum(1 for video in videos.values()
                                  if explainers.looks_like_explainer(video["title"])),
                "memes": len(pool_matches(state, memes, videos))}
    examples = dict(state.get("examples") or {})
    if state.get("pages"):
        examples["related, collection"] = (f"{bilibili.API}/x/web-interface/view/detail?bvid="
                                           + next(iter(state["pages"])))
    result = {
        "made": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - started),
        "listed": listed, "examples": examples, "uploaders": state.get("uploaders") or {},
        "code": os.path.dirname(os.path.abspath(explainers.__file__)),
        "model": model, "hearing": hearing, "whisper": whisper, "heard": heard,
        "problem": problem, "slow": bool(state.get("slow")),
        "routes": state.get("routes") or {}, "dropped": state.get("dropped") or [],
        "requests": len(_requests), "search_requests": state.get("search_requests", 0),
        "web_refused": dict(web.refused),
        "pages": state.get("pages") or {},
        "pool": {"videos": len(pool),
                 "explainers": sum(1 for video in pool.values()
                                   if explainers.looks_like_explainer(video["title"])),
                 "collection": sum(1 for video in pool.values() if video["how"] == "collection"),
                 "titles": [f"{video['title']} | {video.get('author') or ''}"
                            for video in pool.values()
                            if explainers.looks_like_explainer(video["title"])][:400]},
        "pool_matches": matches,
        "usage": {name: dict(counts) for name, counts in judging.usage.items()},
        "audio": state.get("audio") or {}, "kept_as_was": state.get("kept_as_was") or [],
        "phrases": rows}
    surf._save_json(os.path.join(HOME, "result.json"), result)

    if whisper.get("model"):
        hears = (f"hearing the videos with Whisper {whisper['model']} on the processor "
                 f"({len(heard)} heard in {sum(h['seconds'] for h in heard):.0f} s)")
    elif whisper.get("problem"):
        hears = f"not hearing the videos (Whisper couldn't be loaded: {whisper['problem']})"
    else:
        hears = "not hearing the videos" + ("" if hearing else ": Whisper isn't installed")
    out = ["# Do explainer videos and web pages make the model understand our memes?", "",
           f"Made {result['made']} in {result['seconds']} s with {model or 'no model'}; {hears}."
           + (f" Stopped early: {problem}" if problem else ""), "",
           "## Ways to find the videos", "",
           "Each row counts requests by what they answered, then the different videos the "
           "way listed in all, how many of those look like explainers, and how many of the "
           f"{len(memes)} learned memes their titles name.", "",
           "| Way | Found videos | Nothing found | Captcha | Refused | Error | Videos listed "
           "| Explainers | Learned memes named |", "|---|---|---|---|---|---|---|---|---|"]
    for route in ROUTES + UP_ROUTES:
        tally = (state.get("routes") or {}).get(route) or {}
        out.append(f"| {route}{' (dropped)' if route in result['dropped'] else ''} | "
                   + " | ".join(str(tally.get(k, 0)) for k in
                                ("videos", "empty", "captcha", "refused", "error"))
                   + " | " + " | ".join(str(result["listed"].get(route, {}).get(k, 0)) for k in
                                        ("videos", "explainers", "memes")) + " |")
    for how in ("related", "collection", "seen", "uploader"):
        listed = result["listed"].get(how)
        if listed:
            out.append(f"| {how} (the lists beside videos) | | | | | | " + " | ".join(
                str(listed[k]) for k in ("videos", "explainers", "memes")) + " |")
    if result["examples"]:
        out += ["", "A request of each way that listed videos:", ""] + [
            f"- {route}: `{url}`" for route, url in result["examples"].items()]
    for mid, uploader in (state.get("uploaders") or {}).items():
        out.append(f"- Uploader {uploader['name']} ({mid}): {len(uploader['lists'])} "
                   f"collections and series, {uploader['videos']} videos listed, "
                   f"{uploader['explainers']} look like explainers")
    hows = {}
    for row in rows:
        for how in {video["how"] for video in row["found"]}:
            hows[how] = hows.get(how, 0) + 1
    out += ["", "Phrases with a video that names them, by how it was found: "
            + (", ".join(f"{how} {count}" for how, count in sorted(hows.items())) or "none")
            + f" (of {len(rows)} phrases).", "",
            f"Related lists and collections: {result['pool']['videos']} videos seen beside "
            f"{len(result['pages'])} pages, {result['pool']['explainers']} look like explainers; "
            f"their titles name {len(matches)} of the {len(memes)} learned memes"
            + (": " + ", ".join(list(matches)[:30]) if matches else "") + ".", "",
            f"The web (the bot's lookup): results for "
            f"{sum(1 for row in rows if row['web']['results'])} of {len(rows)} phrases, pages "
            f"that explain {sum(1 for row in rows if any(backs(p) for p in row['web_says']))}"
            + (f"; left alone in this run: {', '.join(web.refused)}" if web.refused else "")
            + ".", ""]
    out += sound_section(result["audio"])
    if result["kept_as_was"]:
        out += [f"Not judged again (another model or other danmu lines than the run that "
                f"judged them): {', '.join(result['kept_as_was'])}.", ""]
    out += ["## Phrases", ""]
    short = lambda v: "-" if not v else _cell(
        f"**{v['kind']}** ({v['verdict']}{', guess' if v.get('guess') else ''}): "
        f"{v['meaning'] or '(no meaning)'}")
    says = lambda summary: "doesn't explain it" if not backs(summary) else _cell(
        summary["meaning"] + (f" (出自 {summary['origin']})" if summary.get("origin") else ""))
    quoted = lambda summary: f" [its quote is from {QUOTE[summary['quote_from']]}]" \
        if summary.get("quote_from") in QUOTE else ""
    for row in rows:
        out += [f"### {row['phrase']} ({', '.join(row['games'])})", "",
                f"- Meaning we have: {_cell(row['meaning'])}"]
        out += [f"- Note: {_cell(note if isinstance(note, str) else json.dumps(note, ensure_ascii=False))}"
                for note in row["notes"]]
        out.append(f"- Danmu only ({row['lines'] if row['lines'] is not None else '?'} lines): "
                   f"{short(row['without'])}")
        for page in row["web_says"]:
            out.append(f"- Web page [{_cell(page.get('site'))}: {_cell(page.get('title'))[:40]}]"
                       f"({page.get('url', '')}), of {page.get('pages', '?')} shown"
                       + (", only its snippet in the search results" if page.get("snippet") else "")
                       + f": {says(page)}")
        if not row["web_says"]:
            out.append("- Web pages: " + (f"{len(row['web']['read'])} read, not summed up"
                                          if row["web"]["read"] else "none found"))
        if row["with_pages"]:
            out.append(f"- With the web pages: {short(row['with_pages'])}")
        if not row["videos"]:
            out.append("- Videos: " + (f"{len(row['found'])} found, not read" if row["found"]
                                       else "none found"))
        for video in row["videos"]:
            read, heard_ = video["read"], video["heard"]
            out.append(f"- Video [{read['bvid']}]({read.get('url', '')}) "
                       f"{_cell(read.get('title'))[:40]} [{video['how']}], as read "
                       f"({read['comments']} comments, {read['danmu']} danmu"
                       f"{', subtitles' if read.get('heard') == 'subtitles' else ''}): "
                       f"{says(read)}{quoted(read)}")
            if heard_:
                out.append(f"  - as heard ({heard_['said']} characters of {heard_['seconds']} s): "
                           f"{says(heard_)}{quoted(heard_)}")
        if row["with_read"]:
            out.append(f"- With the videos, as read: {short(row['with_read'])}")
        if row["with_heard"]:
            out.append(f"- With the videos, as heard: {short(row['with_heard'])}")
        out.append("")
    with open(os.path.join(HOME, "result.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main(argv=None):
    global _log
    argv = sys.argv[1:] if argv is None else argv
    if not HOME:
        sys.exit("Run it with explain-test.bat: it sets MEMELEARNER_HOME, the folder this "
                 "writes to, so the bot's own cache stays as it is.")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(HOME, exist_ok=True)
    # Whisper on the processor uses 4 threads unless it is told otherwise
    os.environ.setdefault("OMP_NUM_THREADS", str(min(os.cpu_count() or 4, 8)))
    _log = open(os.path.join(HOME, "log.txt"), "a", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, _log)
    started = time.time()
    say(f"\n=== explain test, {time.strftime('%Y-%m-%d %H:%M')} ===")
    say(f"Code: {os.path.dirname(os.path.abspath(explainers.__file__))}")
    say(f"Results: {HOME}")
    memes = store.load()["memes"]
    wanted = [store.normal(phrase) for phrase in (argv or PHRASES)]
    missing = [phrase for phrase in wanted if phrase not in memes]
    if missing:
        say(f"Not learned memes, left out: {', '.join(missing)}")
    links = read_links()
    wanted += [phrase for phrase in links if phrase in memes]  # the ones you gave links for
    wanted = [phrase for phrase in dict.fromkeys(wanted) if phrase in memes]
    state = surf._load_json(STATE, {})
    if state.get("version") != 2:
        state = {"version": 2}
    known, seen = earlier()
    hearing = explainers.can_hear()
    web.reset()
    say(f"{len(wanted)} learned memes, a request to Bilibili every 5 to 7 seconds: about half "
        "an hour, and up to an hour when it hears the videos. Closing the window stops it; "
        "a second run carries on.")
    if daily_run_near():
        bilibili.REQUEST_DELAY, web.DELAY = SLOW
        state["slow"] = True
        say("The bot's own daily run is likely going now, so this asks more slowly (a request "
            f"to Bilibili every {SLOW[0]} to {SLOW[0] + bilibili.REQUEST_JITTER} seconds).")

    # How long hearing takes, video by video
    hear = explainers.hear

    def timed(path, phrase=""):
        size, began = os.path.getsize(path), time.time()
        said = hear(path, phrase)
        state.setdefault("heard", []).append({
            "file": os.path.basename(path), "bytes": size, "characters": len(said),
            "seconds": round(time.time() - began, 1)})
        return said

    explainers.hear = timed
    # Fetch the sound the ways hear_video() tries, and try again the videos an earlier run
    # couldn't fetch
    _audio["state"] = state
    explainers._hear_video = hear_video
    if state.get("audio"):  # a new run: its first video tries every way again
        state["audio"].update(stopped=False, missed=0, swept=0)
    if hearing:
        again = hear_again()
        if again:
            say(f"{again} videos whose sound couldn't be fetched before are heard again, "
                "trying other ways to fetch it.")

    # Keep what every video page shows of other videos, whoever asks for it
    ask_bilibili = bilibili._request

    def recording(path, params=None, *args, **kwargs):
        try:
            data = ask_bilibili(path, params, *args, **kwargs)
        except Exception as error:
            _requests.append({"to": path, "outcome": type(error).__name__})
            raise
        _requests.append({"to": path, "outcome": "ok"})
        if path == "/x/web-interface/view/detail" and isinstance(data, dict):
            try:
                note_detail(state, (params or {}).get("bvid"), data)
            except Exception as error:  # a shape nobody expected
                say(f"    (couldn't keep the lists of {(params or {}).get('bvid')}: {error!r})")
        return data

    bilibili._request = recording
    problem, materials, model = "", {}, None
    try:
        model = Model()
    except judging.ModelUnavailable as error:
        problem = f"No model could be asked: {error}"
        say(f"\n{problem}\nSo this run only finds and reads the videos. Start Ollama (or set "
            "the DeepSeek key) and run it again for the model's part.\n")
    try:
        with judging.awake():
            try:
                crawl(state, links, seen, memes)
                find_videos(state, wanted, memes, links, hearing, materials, known)
            except bilibili.Blocked as error:
                problem = f"Bilibili refuses this computer for now ({error})"
                say(f"\n{problem}\nStopped reading Bilibili; going on with what was read.")
            save_state(state)
            try:
                if model:
                    judge_phrases(state, wanted, memes, materials, model)
            except judging.ModelUnavailable as error:
                problem = (problem + " " if problem else "") + f"The model stopped: {error}"
                say(f"\nThe model stopped answering: {error}")
    except KeyboardInterrupt:
        problem = (problem + " " if problem else "") + "Stopped by hand."
        say("\nStopped.")
    finally:
        save_state(state)
        write_results(state, wanted, memes, started, model.name if model else "", hearing,
                      problem)
    for name, counts in judging.usage.items():
        if judging.online(name) and counts.get("calls"):
            say(judging._spent(name, counts))
    say(f"\n{len(_requests)} requests to Bilibili, {round(time.time() - started)} s. Written to "
        f"{os.path.join(HOME, 'result.md')} (and result.json, log.txt, raw\\).")


if __name__ == "__main__":
    main()
