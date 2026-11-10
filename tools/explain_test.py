"""Try the explain step on memes the bot has already learned (Hao's ask, 2026-11-10): do the
videos that explain a meme make the model understand it?

    explain-test.bat runs it on Hao's PC:   py tools\\explain_test.py [PHRASE ...]

It never changes memes.json, the bot's own cache or its working copy: everything it writes
goes to the folder MEMELEARNER_HOME names (the .bat sets it to .cache\\explain-test).

    1. Find     Bilibili's search asks a visitor for a captcha, so explain found no video in
                the two real runs. This tries the ways a visitor has, a request every 5 to 7
                seconds, and writes down what each one answers:
                  related   the lists beside the game videos the bot read, then beside the
                            explainer videos found there, and their collections (合集)
                  api-type  the search the bot uses now
                  api-old   the same search at its old address, without the signature
                  api-all   the "all results" search
                  page      the search page itself (search.bilibili.com), as a browser opens it
                  api-page  the bot's search again, with the cookies and the Referer the
                            search page gives a browser
                The first PROBES phrases try them all; the others use the ones that worked.
                It never logs in and never solves a captcha: a way that is refused is dropped.
    2. Read     Up to VIDEOS videos for each phrase, as explain does (explainers.read()),
                hearing them when Whisper is installed. Links in links.txt (a phrase, a
                space, a video link on each line) are read first.
    3. Sum up   explainers.summarize(), with the judge's model.
    4. Judge    judging.judge_one() twice for each phrase, with the same danmu lines: once
                with no videos and once with what the videos say. The learned memes are not
                shown to the model in either, so what it writes comes from the danmu and
                the videos alone.

It writes result.json, result.md and log.txt, and keeps state.json so that a second run only
does what the first one didn't. Each phrase's meaning in memes.json (Hao's) is beside the
model's two answers; nobody is marked here.
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

from memelearner import bilibili, explainers, judging, store, surf

# Memes Hao corrected or added to: the model alone didn't get them right. The first PROBES
# are old, well-known ones, where a video is most likely to exist.
PHRASES = ["阿一古", "汽车人", "正义必胜", "别吃", "神像尽显", "干燥", "老霸道了", "肯恰那",
           "打钱了", "脸色对了", "老九他没毛病", "千言万语感谢现役", "我们是冠军",
           "目前不知道怎么赢", "超威同意了", "桌子翻了", "脖子捂汗", "中华有尺", "阿一占",
           "涅槃打野", "春风若有怜花意", "蝴蝶飞不过沧海", "皮肤局", "我的麦很炸吗"]
PROBES = 3  # phrases that try every way to search
VIDEOS = explainers.VIDEOS  # videos read for each phrase
SEEDS = 10  # game videos whose related lists are read
HOPS = 12  # explainer videos found there whose related lists and collections are read
DROP_AFTER = 3  # times in a row a way to search may fail before it is dropped
RAW = 400_000  # characters kept of an answer saved as it came
PAGE = "https://search.bilibili.com/video?keyword={}"
ROUTES = ("api-type", "api-old", "api-all", "page", "api-page")
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

HOME = os.environ.get("MEMELEARNER_HOME") or ""
STATE = os.path.join(HOME, "state.json")
_log = None
_requests = []  # every request made here: {"to", "status", "outcome"}


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
                                            "plays": video.get("plays") or 0, "how": how,
                                            "beside": bvid})
    explainers.remember(episodes, "collection")
    looks = lambda videos: sum(1 for v in videos if explainers.looks_like_explainer(v["title"]))
    state.setdefault("pages", {})[bvid] = {
        "title": view.get("title", ""), "related": len(related), "explainers": looks(related),
        "collection": season.get("title", "") if season else "", "episodes": len(episodes),
        "collection_explainers": looks(episodes)}


def crawl(state):
    """The related lists beside the game videos the bot read, then beside the explainer
    videos found there: how many explainer videos does that give?"""
    done = state.setdefault("crawled", [])
    pool = state.setdefault("pool", {})
    if state.get("crawl_done"):
        say("1a. The lists beside the game videos were read in an earlier run.")
        return
    seeds = [bvid for bvid in surf._load_json(surf.VIDEOS_FILE, {})
             if bilibili.BVID.fullmatch(bvid)][:SEEDS]
    todo = [bvid for bvid in seeds if bvid not in done]
    say(f"1a. The lists beside {len(todo)} game videos the bot read ...")
    for bvid in todo:
        read_page(state, bvid, done)
    looks = [bvid for bvid, video in pool.items() if bvid not in done
             and explainers.looks_like_explainer(video["title"])]
    looks.sort(key=lambda bvid: (not explainers.MADE_TO_EXPLAIN.search(pool[bvid]["title"]),
                                 -pool[bvid]["plays"]))
    hops = looks[:max(0, HOPS - sum(1 for bvid in done if bvid not in seeds))]
    say(f"1b. ... and beside {len(hops)} explainer videos found there ...")
    for bvid in hops:
        read_page(state, bvid, done)
    state["crawl_done"] = True
    found = sum(1 for video in pool.values() if explainers.looks_like_explainer(video["title"]))
    say(f"    {len(pool)} videos seen beside {len(done)}, {found} of them look like explainers")


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
    """{phrase: [link]} from links.txt beside the results: a phrase, a space, a link."""
    links = {}
    try:
        with open(os.path.join(HOME, "links.txt"), encoding="utf-8-sig") as f:
            for line in f:
                words = line.split()
                if len(words) >= 2 and not line.lstrip().startswith("#"):
                    links.setdefault(store.normal(words[0]), []).extend(words[1:])
    except OSError:
        pass
    return links


def find_videos(state, wanted, memes, links, hearing, materials):
    """Steps 1 and 2 for the phrases, one after the other, so that what was read is kept
    when Bilibili refuses later: state["found"][phrase] = {"searches", "videos"}, and
    materials[phrase] = what explain reads of up to VIDEOS of them."""
    found = state.setdefault("found", {})
    searcher = Searcher(state)
    say(f"1c. Searching for \"X是什么梗\" (the first {PROBES} phrases every way, the rest "
        f"the ways that worked), and reading up to {VIDEOS} videos for each phrase"
        + ("" if hearing else "; not hearing them, Whisper isn't installed") + " ...")
    try:
        _find_videos(state, wanted, memes, links, hearing, materials, found, searcher)
    finally:
        state["search_requests"] = state.get("search_requests", 0) + searcher.count
        save_state(state)


def _find_videos(state, wanted, memes, links, hearing, materials, found, searcher):
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
        read_videos(phrase, entry, hearing, materials)
        save_state(state)


def add_matches(entry, videos, names, route):
    for video in matching(list(videos.values()), names):
        if video["bvid"] not in [v["bvid"] for v in entry["videos"]]:
            entry["videos"].append({"bvid": video["bvid"], "title": video["title"],
                                    "how": route, "plays": video.get("plays") or 0})


def read_videos(phrase, entry, hearing, materials):
    """Step 2 for a phrase: read up to VIDEOS of its videos, as explain does."""
    for video in (entry.get("videos") or [])[:VIDEOS]:
        try:
            material = explainers.read(video["bvid"], phrase, hearing)
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
        materials.setdefault(phrase, []).append(dict(material, how=video["how"]))
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
        path = os.path.join(os.path.dirname(HOME.rstrip("\\/")), test, "surf", "candidates.json")
        for candidate in surf._load_json(path, {}).get("candidates") or []:
            if isinstance(candidate, dict) and candidate.get("phrase"):
                found[(candidate.get("game"), candidate["phrase"])] = candidate
    return found


def judge_phrases(state, wanted, memes, materials, model):
    """Steps 3 and 4: sum the videos up, and judge each phrase without and with them."""
    known = candidates_of_tests()
    summaries = state.setdefault("summaries", {})
    judged = state.setdefault("judged", {})
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
    say(f"4. Summing the videos up and judging with {model.name} ...")
    for phrase in wanted:
        game, entry = game_of[phrase], memes[phrase]
        told = []
        for material in materials.get(phrase, []):
            key = f"{phrase}|{material['bvid']}|{material.get('heard') or ''}"
            if key not in summaries or summaries[key].get("model") != model.name:
                summaries[key] = model.ask(lambda name: explainers.summarize(
                    phrase, game, material, name))
                save_state(state)
            told.append(dict(summaries[key], how=material["how"],
                             said=len(material.get("said") or ""),
                             comments=len(material.get("comments") or []),
                             danmu=len(material.get("danmu") or [])))
        candidate = dict(known.get((game, phrase)) or {
            "phrase": phrase, "game": game,
            "variants": [{"phrase": v, "people": 0} for v in entry.get("variants") or []]})
        candidate.pop("judge", None)
        where = (game, phrase)
        used = lines.get(where) or [e if isinstance(e, str) else e.get("text", "")
                                    for e in (candidate.get("examples") or [])
                                    + list(entry.get("examples") or [])]
        shown = judging._titles(uses.get(where) or [], titles)
        backing = [summary for summary in told if summary.get("about") and summary.get("meaning")]
        marks = sorted(f"{s['bvid']}:{s['meaning']}" for s in backing)
        old = judged.get(phrase) or {}
        if old.get("model") != model.name or old.get("lines") != len(used):
            old = {}
        result = {"model": model.name, "lines": len(used), "videos": marks,
                  "without": old.get("without"), "with": old.get("with")}
        if not result["without"]:
            result["without"] = model.ask(lambda name: judging.judge_one(
                candidate, used, shown, name))
        if backing and (not result["with"] or old.get("videos") != marks):
            result["with"] = model.ask(lambda name: judging.judge_one(
                candidate, used, shown, name, backing))
        elif not backing:
            result["with"] = None
        result["told"] = told
        judged[phrase] = result
        save_state(state)
        short = lambda verdict: "-" if not verdict else \
            f"{verdict['kind']}: {verdict['meaning'] or '(no meaning)'}"
        say(f"   {phrase}")
        say(f"     without videos   {short(result['without'])}")
        for summary in told:
            say(f"     {summary['bvid']} says   " + (summary["meaning"] if summary["about"]
                                                    else "(it doesn't explain this phrase)"))
        if result["with"]:
            say(f"     with videos      {short(result['with'])}")


# -- what came out ------------------------------------------------------------------------

def _cell(text):
    return " ".join(str(text or "").split()).replace("|", "/")


def write_results(state, wanted, memes, started, model, hearing, problem):
    pool = state.get("pool") or {}
    matches = pool_matches(state, memes)
    found, judged = state.get("found") or {}, state.get("judged") or {}
    rows = []
    for phrase in wanted:
        entry, verdicts = memes[phrase], judged.get(phrase) or {}
        rows.append({"phrase": phrase, "games": entry.get("games") or [],
                     "meaning": entry.get("meaning", ""), "notes": entry.get("notes") or [],
                     "found": (found.get(phrase) or {}).get("videos") or [],
                     "searches": (found.get(phrase) or {}).get("searches") or [],
                     "videos": verdicts.get("told") or [], "lines": verdicts.get("lines"),
                     "without": verdicts.get("without"), "with": verdicts.get("with")})
    result = {
        "made": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - started),
        "code": os.path.dirname(os.path.abspath(explainers.__file__)),
        "model": model, "hearing": hearing, "problem": problem,
        "routes": state.get("routes") or {}, "dropped": state.get("dropped") or [],
        "requests": len(_requests), "search_requests": state.get("search_requests", 0),
        "pages": state.get("pages") or {},
        "pool": {"videos": len(pool),
                 "explainers": sum(1 for video in pool.values()
                                   if explainers.looks_like_explainer(video["title"])),
                 "collection": sum(1 for video in pool.values() if video["how"] == "collection"),
                 "titles": [video["title"] for video in pool.values()
                            if explainers.looks_like_explainer(video["title"])][:300]},
        "pool_matches": matches,
        "usage": {name: dict(counts) for name, counts in judging.usage.items()},
        "phrases": rows}
    surf._save_json(os.path.join(HOME, "result.json"), result)

    out = ["# Explainer videos on the memes we have: first run", "",
           f"Made {result['made']} in {result['seconds']} s with {model or 'no model'}; "
           f"{'hearing the videos (Whisper)' if hearing else 'not hearing the videos'}."
           + (f" Stopped early: {problem}" if problem else ""), "",
           "## Ways to find the videos", "",
           "| Way | Found videos | Nothing found | Captcha | Refused | Error |", "|---|---|---|---|---|---|"]
    for route in ROUTES:
        tally = (state.get("routes") or {}).get(route) or {}
        out.append(f"| {route}{' (dropped)' if route in result['dropped'] else ''} | "
                   + " | ".join(str(tally.get(k, 0)) for k in
                                ("videos", "empty", "captcha", "refused", "error")) + " |")
    out += ["", f"Related lists and collections: {result['pool']['videos']} videos seen beside "
            f"{len(result['pages'])} pages, {result['pool']['explainers']} look like explainers; "
            f"their titles name {len(matches)} of the {len(memes)} learned memes"
            + (": " + ", ".join(list(matches)[:30]) if matches else "") + ".", "",
            "## Phrases", "",
            "| Phrase | Meaning we have | Model, danmu only | What the videos say | Model, with the videos |",
            "|---|---|---|---|---|"]
    short = lambda v: "-" if not v else _cell(
        f"**{v['kind']}** ({v['verdict']}{', guess' if v.get('guess') else ''}): "
        f"{v['meaning'] or '(no meaning)'}")
    for row in rows:
        says = "<br>".join(_cell(
            f"[{video['bvid']}]({video.get('url', '')}) {video.get('title', '')[:30]} "
            f"[{video['how']}; {video.get('heard') or 'page only'}]: "
            + ((video["meaning"] + (f" (出自 {video['origin']})" if video.get("origin") else ""))
               if video["about"] else "doesn't explain it"))
            for video in row["videos"]) or ("no video found" if not row["found"] else "not read")
        out.append(f"| {row['phrase']} | {_cell(row['meaning'])} | {short(row['without'])} | "
                   f"{says} | {short(row['with'])} |")
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
    if state.get("version") != 1:
        state = {"version": 1}
    hearing = explainers.can_hear()
    say(f"{len(wanted)} learned memes, a request to Bilibili every 5 to 7 seconds: "
        "about 15 to 30 minutes. Closing the window stops it; a second run carries on.")

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
                crawl(state)
                find_videos(state, wanted, memes, links, hearing, materials)
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
