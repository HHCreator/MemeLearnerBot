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
                  api-type  the search the bot uses now
                  api-old   the same search at its old address, without the signature
                  api-all   the "all results" search
                  page      the search page itself (search.bilibili.com), as a browser opens it
                  api-page  the bot's search again, with the cookies and the Referer the
                            search page gives a browser
                The first PROBES phrases try the five searches; the others use the ones that
                worked. It never logs in and never solves a captcha: a way that is refused
                is dropped.
    2. Read     Up to VIDEOS videos for each phrase, as explain does (explainers.read()),
                and heard on the processor when Whisper is installed (the .bat offers to
                put it in the test's own folder). The web pages about each phrase, as the
                bot reads them (explainers.read_pages()). What the lookup test and the
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
import json
import os
import re
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
        out = {"route": route, "status": status, "bytes": len(text),
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
        out = {"route": "page", "status": status, "bytes": len(text),
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
        self.count += 1
        out["phrase"] = phrase
        tally = self.tally.setdefault(route, {})
        tally[out["outcome"]] = tally.get(out["outcome"], 0) + 1
        good = out["outcome"] in ("videos", "empty")
        self.failed[route] = 0 if good else self.failed.get(route, 0) + 1
        # The first answers of each kind are kept as they came, to read their shape
        if tally[out["outcome"]] <= 2:
            keep_raw(f"{route}-{out['outcome']}-{tally[out['outcome']]}.txt", text)
        out["titles"] = [video["title"] for video in list(videos.values())[:10]]
        _requests.append({"to": route, "status": out.get("status"), "outcome": out["outcome"]})
        say(f"      {route:9} {out['outcome']:8} "
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


def pool_matches(state, memes):
    """{phrase: [video]} for every learned meme a video in the pool names in its title."""
    pool = state.get("pool") or {}
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
        _find_videos(state, wanted, memes, links, hearing, materials, found, searcher, known)
    finally:
        state["search_requests"] = state.get("search_requests", 0) + searcher.count
        save_state(state)


def _find_videos(state, wanted, memes, links, hearing, materials, found, searcher, known):
    for number, phrase in enumerate(wanted):
        entry = found.setdefault(phrase, {"searches": [], "videos": []})
        names = names_of(phrase, memes[phrase])
        say(f"    {phrase}")
        given = []
        for link in links.get(phrase, ()):
            try:
                bvid = bilibili.bvid_of(link)
            except OSError as error:
                say(f"      couldn't open {link}: {error}")
                continue
            if bvid:
                given.append({"bvid": bvid, "title": "", "how": "link", "plays": 0})
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
    for video in (entry.get("videos") or [])[:VIDEOS]:
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
    for phrase in wanted:
        game, entry = game_of[phrase], memes[phrase]
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
        candidate = dict(known.get((game, phrase)) or {
            "phrase": phrase, "game": game,
            "variants": [{"phrase": v, "people": 0} for v in entry.get("variants") or []]})
        candidate.pop("judge", None)
        where = (game, phrase)
        used = lines.get(where) or [e if isinstance(e, str) else e.get("text", "")
                                    for e in (candidate.get("examples") or [])
                                    + list(entry.get("examples") or [])]
        shown = judging._titles(uses.get(where) or [], titles)
        old = judged.get(phrase) or {}
        if old.get("model") != model.name or old.get("lines") != len(used):
            old = {}
        result = {"model": model.name, "lines": len(used), "marks": {},
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


# -- what came out ------------------------------------------------------------------------

def _cell(text):
    return " ".join(str(text or "").split()).replace("|", "/")


QUOTE = {"title": "its title", "description": "its description", "said": "what is said in it",
         "comments": "a comment", "danmu": "a danmu"}


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
    result = {
        "made": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - started),
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
           "| Way | Found videos | Nothing found | Captcha | Refused | Error |", "|---|---|---|---|---|---|"]
    for route in ROUTES:
        tally = (state.get("routes") or {}).get(route) or {}
        out.append(f"| {route}{' (dropped)' if route in result['dropped'] else ''} | "
                   + " | ".join(str(tally.get(k, 0)) for k in
                                ("videos", "empty", "captcha", "refused", "error")) + " |")
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
            + ".", "", "## Phrases", ""]
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
