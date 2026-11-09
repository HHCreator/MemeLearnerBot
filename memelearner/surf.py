"""The meme bot: read Bilibili for the newest memes of each game, for you to look through.

    py surf.py                        # read Bilibili for every game, then list the phrases
    py surf.py --games cs2 dota2      # only some games (cs2, lol, valorant, dota2)
    py surf.py --videos 3             # fewer videos per game: a quick test
    py surf.py --days 3               # "new" means the last 3 days instead of 7
    py surf.py --no-fetch             # don't visit Bilibili; list again from what was read
    py surf.py --known G:\\SBtranslator\\sbtranslator\\data   # skip what another app knows

(py surf.py is short for py -m memelearner learn; cli.py has the other commands.)

It only reads, like a visitor who isn't logged in: it never posts, likes or replies, and it
waits 5 to 7 seconds between requests (bilibili.py), since Bilibili stops answering a visitor
who asks faster. A run reads about 40 videos in about 20 minutes; the first run reads about
twice as many (see step 2).

How it works:

    1. Find videos     Bilibili asks a visitor's search for a captcha more often than not, and
                       the bot never solves captchas. So it finds videos the way a viewer
                       does: in the related videos beside a video. For each game it starts
                       from the seeds in data/surf_sources.json and the newest videos it read
                       before, keeps following the newest videos it finds (EXPAND lists),
                       and reads the ones posted in the last DAYS days with the most danmu.
                       Only videos whose titles name the game count. The search is tried
                       too, once, and used while Bilibili answers it.
    2. Read            Read each video's danmu and hot comments into the cache folder (surf/),
                       with every sender replaced by a short hash. The first time, it also
                       reads some older videos (posted 30 to 90 days ago), so it knows what
                       people were saying before and can tell what's new.
    3. Find phrases    Memes are sent on their own: people send 阿一古 or 正义必胜 as the
                       whole danmu, while names and game words (奥拉夫, 号种子) sit inside
                       longer lines. So a phrase is a whole short danmu that at least
                       MIN_PEOPLE different people send, in at least MIN_VIDEOS videos, and
                       that at least MIN_ALONE of the people who use it send on its own. A
                       danmu that repeats itself counts once (正义必胜正义必胜 is 正义必胜), and
                       mood words at its end don't count (怎么翻的 is 怎么翻): phrase_of().
                       Variants of one meme (超威同意了, chovy同意了, 三叔不同意: see
                       variants.py) are listed together, under the one most people send
                       (_group()); twists that mean something else (千言万语赶紧退役 next to
                       千言万语感谢现役) are listed on their own. Known phrases
                       are left out (known_phrases()): the memes already learned or turned
                       down, everyday ones in data/surf_common.txt, and another app's words
                       if you say where; so are players' nicknames on their own (chooovy),
                       from data/nicknames.txt (nicknames.py).
    4. How new         Each phrase's share of the people writing in the last DAYS days is
                       compared with its share before that: nobody sent it before = new, at
                       least RISE times as common = rising, RISE times rarer = older, else
                       steady. Only a change that luck would explain less than once in
                       10^SURE counts, and steady needs enough lines on both sides to show a
                       change (MIN_EXPECTED): with too few, a phrase is a first look, or
                       older when it was more common before.
    5. Save            The phrases, with example lines and the videos they came from, go to
                       surf/candidates.json in the cache folder. Nothing is learned until you
                       accept a phrase (store.py), which adds it to data/memes.json.
"""

import collections
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error

from . import bilibili, nicknames, store

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "data", "surf_sources.json")
COMMON_FILE = os.path.join(HERE, "data", "surf_common.txt")
WORDS_FILE = os.path.join(HERE, "data", "words", "common_english.txt")


def _cache_dir():
    """Where the bot keeps what it read (never committed): MEMELEARNER_HOME if it is set,
    else .cache/ beside the code, or %LOCALAPPDATA%\\MemeLearnerBot when run as an exe."""
    if os.environ.get("MEMELEARNER_HOME"):
        return os.environ["MEMELEARNER_HOME"]
    if getattr(sys, "frozen", False):
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                            "MemeLearnerBot")
    return os.path.join(HERE, ".cache")


SURF_DIR = os.path.join(_cache_dir(), "surf")
VIDEOS_FILE = os.path.join(SURF_DIR, "videos.json")
CANDIDATES_FILE = os.path.join(SURF_DIR, "candidates.json")
# Other files whose phrases count as known, like another app's word tables: paths separated
# by ; on Windows (: elsewhere), the same as --known
KNOWN_ENV = "MEMELEARNER_KNOWN"
PARTICLES = set("了呢吧啊吗的哦呀嘛啦")  # mood words at the end of a phrase that don't change it

DAYS = 7  # "new" means used in the last DAYS days; videos posted in that time are read
VIDEOS_PER_GAME = 10  # the videos with the most danmu are read for each game
EXPAND = 8  # related-video lists read for each game to find its videos (a request each)
FILL_DAYS = 30  # too few videos from the last DAYS days: add ones from the last FILL_DAYS
BASELINE_DAYS = (90, 30)  # older videos (posted 90 to 30 days ago) show what's not new
BASELINE_SHARE = 1  # ... as many of them as recent ones (only the first time)
MIN_BEFORE_MESSAGES = 2000  # fewer earlier messages than this and older videos are read
MAX_PARTS = 3  # parts (分P) of a video whose danmu are read; match videos have one per game
COMMENT_PAGES = 3  # pages of hot comments read under each video (20 per page)
MIN_PEOPLE = 3  # different people who must send a phrase on its own before it counts
MIN_VIDEOS = 2  # videos it must be sent in, so one video's own catchphrase doesn't count
MIN_ALONE = 0.5  # the share of the people using a phrase who must send it on its own
MAX_SHORT = 12  # characters a whole danmu (said once) can have and still be a phrase
RISE = 3.0  # how many times as common as before a phrase must be to count as rising (or as
            # rare, to count as older)
SURE = 2  # how sure a change must be, in powers of ten: 2 is a 1 in 100 chance of luck
MIN_EXPECTED = 5  # a phrase is only steady when, had it been as common all along, at least
                  # this many of its senders would be from before and from the last DAYS days
TOP = 30  # phrases kept for each game
EXAMPLES = 8  # example lines kept for each phrase
ALONE_EXAMPLES = 2  # ... of them the phrase on its own, after the longer lines that use it
VARIANTS = 30  # variants kept with a phrase (chovy同意了 and 三叔不同意 with 超威同意了)
DAY = 86400

EMOTE = re.compile(r"\[[^\[\]\s]{1,12}\]")  # [doge] [笑哭]: emotes in comments
REPLY = re.compile(r"^回复\s*@[^:：\s]+\s*[:：]")  # "回复 @someone :" at the start of a reply
MENTION = re.compile(r"@[^\s@:：,，。!！?？]+")  # @someone: a user name, not a meme
LINK = re.compile(r"https?://\S+|www\.\S+|bv[0-9a-z]{10}|av\d+")
NOT_TEXT = re.compile(r"[^0-9a-z一-鿿]+")


# -- reading Bilibili ---------------------------------------------------------------------

def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def sources():
    """{game: {"words": [...], "seeds": [...]}} from data/surf_sources.json: the words a
    title must contain to be about the game, and links to videos to start from."""
    games = _load_json(SOURCES_FILE, {}).get("bilibili", {})
    found = {}
    for game, source in games.items():
        if game.startswith("_"):
            continue
        if isinstance(source, list):  # the first version of the file only had search words
            source = {"words": source}
        found[game] = {"words": list(source.get("words") or []),
                       "seeds": list(source.get("seeds") or [])}
    return found


def messages_path(bvid):
    return os.path.join(SURF_DIR, "bilibili", f"{bvid}.jsonl")


def about(title, words):
    """Whether a title names the game: it contains one of the game's words, in any case."""
    title = unicodedata.normalize("NFKC", title or "").lower()
    return any(unicodedata.normalize("NFKC", word).lower() in title for word in words)


def find_videos(game, source, videos, now, days, count, search=None):
    """The videos to read for a game: (recent, older, seeds).

    recent: the count busiest videos posted in the last days days (topped up with ones from
    the last FILL_DAYS days when there are too few); older: the busiest ones posted 30 to 90
    days ago, to compare with; seeds: the game's seeds in surf_sources.json, read as they are.

    They come from related videos: the lists Bilibili shows beside a video, which a visitor
    gets without a captcha. It follows the seeds first, then the newest videos found, then
    the newest ones read before (videos, from videos.json), up to EXPAND lists. The search is
    tried once per run (search is a dict shared by the games: {"refused": bool}). Videos in
    those lists that explain memes are kept for explainers.py.
    """
    from . import explainers  # here: explainers.py uses this module
    since = now - days * DAY
    search = {} if search is None else search
    found = {}

    def add(video):
        if video["bvid"] not in found and about(video["title"], source["words"]):
            found[video["bvid"]] = video

    for word in source["words"]:
        if search.get("refused"):
            break
        try:
            for video in bilibili.search_videos(word, since, now):
                add(video)
        except bilibili.Captcha:
            search["refused"] = True
            print("  Bilibili wants a captcha for the search, so it's skipped this time")
        except bilibili.Error as error:
            print(f"  search for {word} failed: {error}")

    seeds = []
    for link in source["seeds"]:
        try:
            bvid = bilibili.bvid_of(link)
        except OSError as error:  # a b23.tv link that doesn't lead anywhere
            bvid = None
            print(f"  couldn't open the seed {link}: {error}")
        if bvid and bvid not in seeds:
            seeds.append(bvid)
    read_before = [bvid for bvid, video in sorted(videos.items(),
                                                  key=lambda item: -(item[1].get("pubdate") or 0))
                   if video.get("game") == game]
    followed = set()
    for _ in range(EXPAND):
        newest = [video["bvid"] for video in sorted(found.values(), key=lambda v: -v["pubdate"])
                  if video["pubdate"] >= since]
        starts = seeds[:EXPAND // 2] + newest + read_before + seeds  # a few seeds first
        bvid = next((b for b in starts if b not in followed), None)
        if bvid is None:
            break
        followed.add(bvid)
        try:
            related = bilibili.related_videos(bvid)
        except bilibili.Captcha:
            print("  Bilibili wants a captcha for related videos, so they're skipped this time")
            break
        except bilibili.Error as error:
            print(f"  couldn't list the videos related to {bvid}: {error}")
            continue
        explainers.remember(related)  # videos that explain memes, for explain to look through
        for video in related:
            add(video)

    def busiest(start, end):
        posted = (v for v in found.values() if start <= v["pubdate"] <= end)
        return sorted(posted, key=lambda v: -v["danmu"])

    week = sum(video["pubdate"] >= since for video in found.values())
    print(f"  followed {len(followed)} related-video lists: {len(found)} videos about {game}, "
          f"{week} posted in the last {days} days")
    recent = busiest(since, now)[:count]
    if len(recent) < count:
        recent += busiest(now - FILL_DAYS * DAY, since - 1)[:count - len(recent)]
    first, last = BASELINE_DAYS
    return recent, busiest(now - first * DAY, now - last * DAY), seeds


def read_video(video, game, videos):
    """Read a video's danmu and hot comments; append the new ones to its file in surf/.

    Returns (new danmu, new comments). The danmu list only holds the newest danmu, so
    reading a video again later adds the ones sent since.
    """
    info = bilibili.video_info(video["bvid"])
    path = messages_path(video["bvid"])
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            seen = {json.loads(line)["id"] for line in f if line.strip()}
    found = []
    for cid in info["parts"][:MAX_PARTS]:
        found += bilibili.danmu(cid)
    try:
        found += bilibili.comments(info["aid"], COMMENT_PAGES)
    except (bilibili.Error, bilibili.Captcha):
        pass  # comments closed, hidden or behind a captcha: the danmu are still worth keeping
    new = []
    for message in found:
        if message["id"] not in seen:
            seen.add(message["id"])
            new.append(message)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for message in new:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
    entry = videos.setdefault(video["bvid"], {"game": game, "read": []})
    entry.update(title=info["title"], author=info["author"], pubdate=info["pubdate"],
                 url=f"https://www.bilibili.com/video/{video['bvid']}",
                 baseline=video.get("baseline", False))
    entry["read"].append(time.strftime("%Y-%m-%d %H:%M"))
    return (sum(m["kind"] == "danmu" for m in new), sum(m["kind"] == "comment" for m in new))


def fetch(games, sources, days, videos_per_game, now):
    """Steps 1 and 2: find each game's busiest recent videos and read them."""
    videos = _load_json(VIDEOS_FILE, {})
    before = collections.Counter(game for game, _, message in load_messages(videos)
                                 if 0 < message["sent"] < now - days * DAY)
    search = {}  # shared by the games: once the search asks for a captcha, it's skipped
    for game in games:
        print(f"{game}: looking for videos ...")
        recent, older, seeds = find_videos(game, sources[game], videos, now, days,
                                           videos_per_game, search)
        found = list(recent)
        if before[game] < MIN_BEFORE_MESSAGES:
            # Nothing much to compare with yet: also read some older videos
            have = {video["bvid"] for video in found}
            found += [dict(video, baseline=True) for video in older
                      if video["bvid"] not in have][:max(2, videos_per_game // BASELINE_SHARE)]
        have = {video["bvid"] for video in found}
        found += [{"bvid": bvid} for bvid in seeds if bvid not in videos and bvid not in have]
        if not found:
            print(f"{game}: found no videos. Add a link to a recent {game} video under "
                  f'"seeds" in {SOURCES_FILE}')
            continue
        print(f"{game}: reading {len(found)} videos ...")
        for video in found:
            try:
                danmu, comments = read_video(video, game, videos)
            except bilibili.Captcha as error:
                print(f"  skipped {video['bvid']}: {error}")
                continue
            except bilibili.Error as error:
                print(f"  skipped {video['bvid']}: {error}")
                continue
            except (urllib.error.URLError, OSError) as error:
                print(f"  skipped {video['bvid']} (network trouble: {error})")
                continue
            except (KeyError, TypeError, ValueError) as error:
                print(f"  skipped {video['bvid']} (Bilibili answered in an unexpected way: "
                      f"{error!r})")
                continue
            finally:
                _save_json(VIDEOS_FILE, videos)  # keep what was read if the run stops
            print(f"  {video['bvid']}  {danmu} new danmu, {comments} new comments  "
                  f"{videos[video['bvid']]['title'][:40]}")


def load_messages(videos=None, live=True):
    """Yield (game, bvid, message) for everything read so far: the videos' danmu and
    comments, then (with live) the live rooms recorded (live.py), where each hour counts as
    a video ("live:6-20261103-1905#2")."""
    videos = _load_json(VIDEOS_FILE, {}) if videos is None else videos
    for bvid, video in videos.items():
        try:
            with open(messages_path(bvid), encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        yield video["game"], bvid, json.loads(line)
        except OSError:
            continue
    if live:
        from . import live as rooms  # live.py uses this module too
        yield from rooms.slices(rooms.recordings())


# -- finding phrases ----------------------------------------------------------------------

def clean(text):
    """Lowercase, half-width text without emotes, @names, links or long repeats (哈哈哈哈 ->
    哈哈哈), so the same thing written slightly differently counts as the same."""
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = REPLY.sub(" ", text)
    text = LINK.sub(" ", MENTION.sub(" ", EMOTE.sub(" ", text)))
    text = re.sub(r"(.)\1{3,}", r"\1\1\1", text)
    return " ".join(text.split())


UNIT = re.compile(r"[0-9a-z]+|[一-鿿]")


def units(text):
    """A cleaned text as units: each Chinese character, and each word of Latin letters and
    digits (ig, yyds, 666). Spaces, punctuation and emoji are left out."""
    return UNIT.findall(text)


def said_once(parts):
    """The units of a line that repeats itself (正义必胜正义必胜, 阿一古 阿一古 阿一古), said once.
    Only repeats of two units or more: 哈哈哈 and 好好好 stay as they are."""
    for period in range(2, len(parts) // 2 + 1):
        if all(parts[i] == parts[i - period] for i in range(period, len(parts))):
            return parts[:period]
    return parts


def phrase_of(parts, english_words=()):
    """(form, key) for a whole line, given as units: (None, None) when it is too long to be
    a phrase people send on its own.

    form is the line said once, as text (阿一古！！！ and 阿一古阿一古 are 阿一古). key is the
    form's units without the mood words at its end, so 怎么翻的 and 怎么翻 count as one; it is
    None when nothing is left that could be a meme: one character, digits only (666), or
    English words only (nice).
    """
    form = said_once(parts)
    text = "".join(form)
    if not form or len(text) > MAX_SHORT:
        return None, None
    key = list(form)
    while key and key[-1] in PARTICLES:
        key.pop()
    joined = "".join(key)
    if len(joined) < 2 or joined.isdigit() or all(part in english_words for part in key):
        return text, None
    return text, key


def key_of(text):
    """The key a line or a phrase counts under when it is sent on its own (phrase_of()), as
    text, or None."""
    key = phrase_of(units(clean(text)))[1]
    return "".join(key) if key else None


def contains(parts, wanted):
    """Whether the units wanted come one after another in parts: ig is in ig牛逼 and in
    "ig 赢了", not in big."""
    n = len(wanted)
    return any(parts[i:i + n] == wanted for i in range(len(parts) - n + 1))


def shared(a, b):
    """How many units two phrases share at their start and their end together
    (千言万语感谢现役 and 千言万语赶紧退役: 千言万语 and 役, 5)."""
    start = 0
    while start < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    end = 0
    while end < min(len(a), len(b)) - start and a[-1 - end] == b[-1 - end]:
        end += 1
    return start + end


def variant(a, b):
    """Whether two phrases are variants of one pattern: they share at least two units at
    their start and end, and more than half of the longer one (我们是冠军 and 我们是路边; not
    怎么翻 and 怎么回事)."""
    both = shared(a, b)
    return both >= 2 and 2 * both > max(len(a), len(b))


Line = collections.namedtuple("Line", "game bvid who recent parts form key message")


def surprise(recent, before, people_recent, people_before):
    """How surprising it is that a phrase has `recent` users now and only `before` earlier,
    if it had been just as common all along: -log10 of the chance of `before` or fewer
    earlier users (a binomial test). 2 means a 1 in 100 chance, 6 one in a million."""
    n = recent + before
    q = people_before / max(1, people_recent + people_before)  # an "earlier" person's share
    if n == 0 or q <= 0:
        return 0.0
    if q >= 1:
        return float("inf") if recent else 0.0
    terms = [math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
             + k * math.log(q) + (n - k) * math.log(1 - q) for k in range(before + 1)]
    top = max(terms)
    chance = top + math.log(sum(math.exp(t - top) for t in terms))  # log of the sum
    return max(0.0, -chance / math.log(10))


def read_phrases(paths):
    """The phrases in some files, lowercase: a .json file's keys and values at any depth
    (another app's word tables, like the translator's data/*.json and its settings.json), a
    .txt file's lines (lines starting with # are notes). A folder means the .json and .txt
    files right in it, not in its subfolders."""
    known = set()

    def add(value):
        if isinstance(value, str):
            known.add(store.normal(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                if not str(key).startswith("_"):
                    add(key)
                    add(item)
        elif isinstance(value, list):
            for item in value:
                add(item)

    files = []
    for path in paths:
        if os.path.isdir(path):
            files += sorted(os.path.join(path, name) for name in os.listdir(path)
                            if name.endswith((".json", ".txt")))
        else:
            files.append(path)
    for path in files:
        if path.endswith(".json"):
            add(_load_json(path, {}))
        else:
            try:
                with open(path, encoding="utf-8") as f:
                    add([line.strip() for line in f if line.strip() and not line.startswith("#")])
            except OSError:
                pass
    known.discard("")
    return known


def known_phrases(extra=(), files=()):
    """The phrases that aren't news, lowercase: everyday ones in data/surf_common.txt, the
    memes already learned or turned down (data/memes.json), the phrases in the files listed
    in MEMELEARNER_KNOWN and in files (as for read_phrases()), and extra, phrases another
    app already knows."""
    paths = [COMMON_FILE] + [p for p in os.environ.get(KNOWN_ENV, "").split(os.pathsep) if p]
    known = read_phrases(paths + list(files)) | store.phrases()
    known |= {store.normal(phrase) for phrase in extra}
    known.discard("")
    return known


def _english_words():
    with open(WORDS_FILE, encoding="utf-8") as f:
        return {line.strip().lower() for line in f if line.strip()}


def find_candidates(messages, days=DAYS, now=None, min_people=MIN_PEOPLE,
                    min_videos=MIN_VIDEOS, min_before=MIN_BEFORE_MESSAGES, known=None,
                    english_words=None, top=TOP):
    """Steps 3 and 4: the phrases people send on their own, for each game, and how new they
    are.

    messages is an iterable of (game, bvid, message). Returns (candidates, summary): the
    candidates sorted best first (top of them for each game), and per game {"videos", "recent", "before", "baseline"}
    (line counts, and whether there were enough earlier lines to compare with). A candidate:

        {"phrase": "阿一古", "game": "lol",
         "status": "new", "rising", "steady", "older" or "first look",
         "people": 52,            different people who sent it on its own (at any time)
         "people_recent": 50, "people_before": 2,    ... in the last days days, and before
         "alone": 0.98,           the share of the people using it who sent it on its own
         "videos": 5,             videos it was sent in on its own
         "rise": 9.1,             how many times as common as before (None for a first look)
         "score": 172.7,          what they are sorted by: people * (1 + log2(videos))
         "variants": [{"phrase": "阿古", "people": 4}],   other ways to write the same meme
         "pattern": "阿一古",      the meme's pattern, a name in it as XX (XX同意了)
         "names": [{"name", "player"}],   the names in it and its variants (variants.py)
         "examples": [{"text", "video", "kind", "sent"}]}  lines that use it
    """
    now = time.time() if now is None else now
    cut = now - days * DAY
    known = known_phrases() if known is None else known
    english_words = _english_words() if english_words is None else english_words
    by_game = collections.defaultdict(list)
    for game, bvid, message in messages:
        text = clean(message.get("text", ""))
        parts = units(text)
        if parts and message.get("sent"):  # a line with no time can't be placed: skip it
            form, key = phrase_of(parts, english_words)
            by_game[game].append(Line(game, bvid, message["who"], message["sent"] >= cut,
                                      parts, form, key, message))
    candidates, summary = [], {}
    for game, lines in by_game.items():
        found, summary[game] = _game_candidates(game, lines, min_people, min_videos,
                                                min_before, known, top)
        candidates += found
    candidates.sort(key=lambda c: -c["score"])
    return candidates, summary


def _keep(pool, line):
    """Keep a line as a possible example in {bvid: {text: message}}: up to EXAMPLES different
    ones from each video."""
    texts = pool[line.bvid]
    if len(texts) < EXAMPLES and line.message["text"] not in texts:
        texts[line.message["text"]] = line.message


def _game_candidates(game, lines, min_people, min_videos, min_before, known, top=TOP):
    """find_candidates() for the lines of one game."""
    recent_people = {line.who for line in lines if line.recent}
    before_people = {line.who for line in lines if not line.recent}
    recent_lines = sum(1 for line in lines if line.recent)
    summary = {"videos": len({line.bvid for line in lines}), "recent": recent_lines,
               "before": len(lines) - recent_lines,
               "baseline": len(lines) - recent_lines >= min_before}

    # Who sends each phrase on its own, and how they write it
    alone = {}
    for line in lines:
        if line.key is None:
            continue
        key = "".join(line.key)
        entry = alone.get(key)
        if entry is None:
            entry = alone[key] = {"parts": line.key, "recent": set(), "before": set(),
                                  "videos": set(), "forms": collections.defaultdict(set),
                                  "examples": collections.defaultdict(dict)}
        entry["recent" if line.recent else "before"].add(line.who)
        entry["videos"].add(line.bvid)
        entry["forms"][line.form].add(line.who)
        _keep(entry["examples"], line)
    wanted, popular = {}, set()
    for key, entry in alone.items():
        senders = entry["recent"] | entry["before"]
        if len(senders) < min_people:
            continue
        popular.add(key)
        if key not in known and not any(form in known for form in entry["forms"]) and \
                not nicknames.is_name(key):  # chooovy alone is a name
            wanted[key] = (entry, senders)

    # ... and who writes them inside a longer line, which is how names and game words are
    # used. A line that many send on its own is no discussion, so it doesn't count against
    # a phrase in it: 这怎么翻的 is a variant of 怎么翻的
    sizes = sorted({len(entry["parts"]) for entry, _ in wanted.values()})
    inside = collections.defaultdict(set)
    inside_examples = collections.defaultdict(lambda: collections.defaultdict(dict))
    for line in lines:
        if line.key and "".join(line.key) in popular:
            continue
        text = "".join(line.parts)
        ends = [0]
        for part in line.parts:
            ends.append(ends[-1] + len(part))
        used = set()
        for n in sizes:
            for start in range(len(line.parts) - n + 1):
                piece = text[ends[start]:ends[start + n]]
                if piece in wanted:
                    used.add(piece)
        for piece in used:
            inside[piece].add(line.who)
            _keep(inside_examples[piece], line)

    # The phrases mostly sent on their own
    found = {}
    for key, (entry, senders) in wanted.items():
        share = len(senders) / len(senders | inside[key])
        if share >= MIN_ALONE:
            found[key] = (entry, senders, share)
    score = {key: len(senders) * (1 + math.log2(len(entry["videos"])))
             for key, (entry, senders, _) in found.items()}
    heads, variants, about = _group(found, score, min_videos)

    # How new each one is: a change only counts when luck would explain it less than once
    # in 10^SURE times
    now_people, then_people = len(recent_people), len(before_people)

    def freshness(entry):
        now, then = len(entry["recent"]), len(entry["before"])
        if not summary["baseline"]:
            return "first look", None
        if not now_people:
            return "older", 0.0
        # How many times as many of the people send it now as before. One person is added
        # to each side, so a phrase is only new when the earlier lines were enough to show
        # it, had it been as common then
        rise = (now / now_people) / ((then + 1) / then_people)
        if rise >= RISE and surprise(now, then, now_people, then_people) >= SURE:
            return ("new" if then == 0 else "rising"), rise
        fall = (then / then_people) / ((now + 1) / now_people)
        if fall >= RISE and surprise(then, now, then_people, now_people) >= SURE:
            return "older", rise
        # No clear change. That only makes it steady when the lines could have shown one:
        # had it been as common all along, at least MIN_EXPECTED of its senders would be on
        # each side. With too few lines on one side (one game's last days had 591), it's
        # older when it was more common before, else a first look
        share_now = now_people / (now_people + then_people)
        if min(share_now, 1 - share_now) * (now + then) >= MIN_EXPECTED:
            return "steady", rise
        return ("older" if then / then_people > now / now_people else "first look"), rise

    candidates = []
    for key in heads:
        entry, senders, share = found[key]
        status, rise = freshness(entry)
        candidates.append({
            "phrase": _written(entry), "game": game, "status": status,
            "people": len(senders), "people_recent": len(entry["recent"]),
            "people_before": len(entry["before"]), "alone": round(share, 2),
            "videos": len(entry["videos"]),
            "rise": None if rise is None else round(rise, 2 if rise < 1 else 1),
            "score": round(score[key], 1),
            "variants": [{"phrase": _written(found[other][0]), "people": len(found[other][1])}
                         for other in sorted(variants[key], key=lambda k: -len(found[k][1]))
                         ][:VARIANTS],
            "pattern": about[key]["pattern"], "names": about[key]["names"],
            "examples": _pick_examples(entry["examples"], inside_examples[key]),
        })
    candidates.sort(key=lambda c: -c["score"])
    return candidates[:top], summary


def _written(entry):
    """How most of the people sending a phrase write it (怎么翻的, rather than 怎么翻)."""
    forms = entry["forms"]
    return min(forms, key=lambda form: (-len(forms[form]), len(form), form))


def _group(found, score, min_videos):
    """Put the variants of one meme together (variants.groups()): (heads, {head: [variant,
    ...]}, {head: {"pattern", "names"}}). The one with the best score leads its group, unless
    it was sent in fewer than min_videos videos (one video's catchphrase): such a phrase can
    only be another one's variant, and a group of only those isn't listed."""
    from . import variants  # here: variants.py uses this module
    written = {key: _written(found[key][0]) for key in found}
    key_of = {text: key for key, text in written.items()}
    heads, members, about = [], {}, {}
    for group in variants.groups({written[key]: score[key] for key in found}):
        keys = [key_of[phrase] for phrase in group["phrases"]]
        leaders = [key for key in keys if len(found[key][0]["videos"]) >= min_videos]
        if leaders:
            heads.append(leaders[0])
            members[leaders[0]] = [key for key in keys if key != leaders[0]]
            about[leaders[0]] = {"pattern": group["pattern"], "names": group["names"]}
    return heads, members, about


def _pick_examples(alone, inside):
    """Up to EXAMPLES different lines with a phrase, from {bvid: {text: message}} of lines
    that are the phrase on its own (alone) and of longer lines that use it (inside): the
    longer lines first, with some context (up to 60 characters) before short ones, then
    ALONE_EXAMPLES of the others, newest first; each kind taking turns between the videos."""
    def queues(pools, order):
        return [[(bvid, m) for m in sorted(messages.values(), key=order)]
                for bvid, messages in sorted(pools.items())]

    picked, seen = [], set()

    def take(queues, limit):
        while len(picked) < limit and any(queues):
            for queue in queues:  # take turns, so the examples span the videos
                while queue and len(picked) < limit:
                    bvid, message = queue.pop(0)
                    same = NOT_TEXT.sub("", clean(message["text"]))
                    if same not in seen:  # 阿一古！！！ is the same line as 阿一古
                        seen.add(same)
                        picked.append({"text": message["text"], "video": bvid,
                                       "kind": message["kind"],
                                       "sent": time.strftime("%Y-%m-%d",
                                                             time.localtime(message["sent"]))})
                        break

    longer = queues(inside, lambda m: (len(m["text"]) > 120, -min(len(m["text"]), 60),
                                       -m["sent"]))
    short = queues(alone, lambda m: -m["sent"])
    take(longer, EXAMPLES - ALONE_EXAMPLES)
    take(short, len(picked) + ALONE_EXAMPLES)
    take(longer, EXAMPLES)
    take(short, EXAMPLES)
    return picked


# -- a whole run --------------------------------------------------------------------------

def save(candidates, summary, days, **extra):
    """Write surf/candidates.json: the phrases, their examples and the videos they're from
    (and extra, like what a live room's run compared)."""
    from . import live  # live.py uses this module too
    videos = _load_json(VIDEOS_FILE, {})
    used = {example["video"] for c in candidates for example in c["examples"]}
    for bvid in used:
        if bvid not in videos and bvid.startswith("live:"):
            videos[bvid] = live.video_entry(bvid)
    _save_json(CANDIDATES_FILE, dict({
        "made": time.strftime("%Y-%m-%d %H:%M"),
        "days": days,
        "games": summary,
        "videos": {bvid: {k: videos[bvid].get(k) for k in ("game", "title", "author", "url")}
                   for bvid in sorted(used) if bvid in videos},
        "candidates": candidates,
    }, **extra))


def learn(games=None, days=DAYS, videos=VIDEOS_PER_GAME, people=MIN_PEOPLE, fetching=True,
          known=(), known_files=()):
    """Steps 1 to 5 for some games (all of surf_sources.json by default): read Bilibili
    (unless fetching is False), find the new and rising phrases and save them. known and
    known_files are more phrases to leave out (known_phrases()). Returns (candidates,
    summary), like find_candidates(); raises ValueError for a game it doesn't know."""
    where = sources()
    games = list(where) if games is None else [game.lower() for game in games]
    unknown = [game for game in games if game not in where]
    if unknown or not games:
        raise ValueError(f"Unknown game {', '.join(unknown) or '(none given)'}; "
                         f"data/surf_sources.json has: {', '.join(where)}")

    now = time.time()
    if fetching:
        print("Reading Bilibili (read only, a request every 5 to 7 seconds) ...")
        try:
            fetch(games, where, days, videos, now)
        except bilibili.Blocked as error:
            print(error)
            print("Listing phrases from what was read so far.")
        except OSError as error:  # no internet, or Bilibili can't be reached from here
            print(f"Couldn't reach Bilibili ({getattr(error, 'reason', error)}).")
            print("Listing phrases from what was read so far.")
        except KeyboardInterrupt:
            print("Stopped reading. Listing phrases from what was read so far.")

    candidates, summary = find_candidates(
        ((game, bvid, message) for game, bvid, message in load_messages() if game in games),
        days=days, now=now, min_people=people, known=known_phrases(known, known_files))
    save(candidates, summary, days)
    return candidates, summary


def main(argv=None):
    """py surf.py [options]: short for py -m memelearner learn [options] (see cli.py)."""
    from . import cli
    cli.main(["learn"] + list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    main()
