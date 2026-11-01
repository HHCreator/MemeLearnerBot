"""The meme bot: read Bilibili for the newest memes of each game, for you to look through.

    py surf.py                        # read Bilibili for every game, then list new phrases
    py surf.py --games cs2 dota2      # only some games (cs2, lol, valorant, dota2)
    py surf.py --videos 3             # fewer videos per game: a quick test
    py surf.py --days 3               # "new" means the last 3 days instead of 7
    py surf.py --no-fetch             # don't visit Bilibili; list again from what was read

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
    3. Find phrases    A phrase counts when at least MIN_PEOPLE different people use it, in at
                       least MIN_VIDEOS videos. Three kinds: a whole short danmu (绷不住了), a
                       piece of longer lines that people write on its own and not only as part
                       of a longer phrase (freedom()), and a Latin-letter word that isn't an
                       English word (yyds).
    4. New or rising   Each phrase's share of the people writing in the last DAYS days is
                       compared with its share before that: nobody used it before = new, at
                       least RISE times as common = rising. Phrases the translator already
                       knows, and everyday ones in data/surf_common.txt, are left out.
    5. Save            The phrases, with example lines and the videos they came from, go to
                       surf/candidates.json in the cache folder. Nothing is added to the
                       translator's data files.
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

from . import bilibili
from .phrases import PARTICLES
from .translator import CACHE_FILE, WORDS_FILE

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "data", "surf_sources.json")
COMMON_FILE = os.path.join(HERE, "data", "surf_common.txt")
SURF_DIR = os.path.join(os.path.dirname(CACHE_FILE), "surf")  # what was read; never committed
VIDEOS_FILE = os.path.join(SURF_DIR, "videos.json")
CANDIDATES_FILE = os.path.join(SURF_DIR, "candidates.json")
# The translator's own tables: a phrase in any of them is already known, so it isn't news
KNOWN_FILES = ["memes.json", "memes_learned.json", "memes_chat.json", "phrases.json",
               "nicknames.json", "chat_meanings.json", "stupid_words.json", "players.txt"]

DAYS = 7  # "new" means used in the last DAYS days; videos posted in that time are read
VIDEOS_PER_GAME = 10  # the videos with the most danmu are read for each game
EXPAND = 8  # related-video lists read for each game to find its videos (a request each)
FILL_DAYS = 30  # too few videos from the last DAYS days: add ones from the last FILL_DAYS
BASELINE_DAYS = (90, 30)  # older videos (posted 90 to 30 days ago) show what's not new
BASELINE_SHARE = 1  # ... as many of them as recent ones (only the first time)
MIN_BEFORE_MESSAGES = 2000  # fewer earlier messages than this and older videos are read
MAX_PARTS = 3  # parts (分P) of a video whose danmu are read; match videos have one per game
COMMENT_PAGES = 3  # pages of hot comments read under each video (20 per page)
MIN_PEOPLE = 3  # different people who must use a phrase before it counts
MIN_VIDEOS = 2  # videos it must show up in, so one video's own catchphrase doesn't count
RISE = 3.0  # how many times more common than before a phrase must be to count as rising
SURE = 2  # how sure (in powers of ten, on top of the number of phrases checked) a rise must be
MAX_SHORT = 8  # a whole danmu up to this long is one phrase (like learn_chat.py)
PIECE_LENGTHS = range(2, 7)  # lengths of the phrases looked for inside longer lines
MIN_FREEDOM = 1.0  # how varied the characters on each side of a phrase must be (freedom())
CONTAINED = 0.9  # a phrase mostly used inside a longer one (this share of its people) is dropped
TOP = 25  # phrases kept for each game
EXAMPLES = 8  # example lines kept for each phrase
DAY = 86400

EMOTE = re.compile(r"\[[^\[\]\s]{1,12}\]")  # [doge] [笑哭]: emotes in comments
REPLY = re.compile(r"^回复\s*@[^:：\s]+\s*[:：]")  # "回复 @someone :" at the start of a reply
MENTION = re.compile(r"@[^\s@:：,，。!！?？]+")  # @someone: a user name, not a meme
LINK = re.compile(r"https?://\S+|www\.\S+|bv[0-9a-z]{10}|av\d+")
CJK_RUN = re.compile(r"[一-鿿]+")
LATIN_WORD = re.compile(r"[a-z][a-z0-9]*")
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
    tried once per run (search is a dict shared by the games: {"refused": bool}).
    """
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


def load_messages(videos=None):
    """Yield (game, bvid, message) for everything read so far."""
    videos = _load_json(VIDEOS_FILE, {}) if videos is None else videos
    for bvid, video in videos.items():
        try:
            with open(messages_path(bvid), encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        yield video["game"], bvid, json.loads(line)
        except OSError:
            continue


# -- finding phrases ----------------------------------------------------------------------

def clean(text):
    """Lowercase, half-width text without emotes, @names, links or long repeats (哈哈哈哈 ->
    哈哈哈), so the same thing written slightly differently counts as the same."""
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = REPLY.sub(" ", text)
    text = LINK.sub(" ", MENTION.sub(" ", EMOTE.sub(" ", text)))
    text = re.sub(r"(.)\1{3,}", r"\1\1\1", text)
    return " ".join(text.split())


Line = collections.namedtuple("Line", "game bvid who recent whole runs latin message")


def _prepare(text, english_words):
    """(whole, runs, latin) for a cleaned line: the line itself when it is short enough to be
    one phrase, its runs of Chinese characters, and its Latin-letter words that aren't
    English words (yyds)."""
    compact = NOT_TEXT.sub("", text)
    short = 0 < len(compact) <= MAX_SHORT and (CJK_RUN.search(compact) or compact.isdigit())
    latin = {w for w in LATIN_WORD.findall(text) if 2 <= len(w) <= 15 and w not in english_words}
    return (compact if short else None), CJK_RUN.findall(text), latin


def pieces(line, wanted):
    """Yield (phrase, left neighbour, right neighbour) for the phrases of a line in wanted.

    The neighbours are the characters next to the phrase; None is the edge of the line or of
    a run of Chinese characters, and "" marks a Latin-letter word, whose neighbours don't
    matter. A short line counts as a whole, with edges on both sides.
    """
    if line.whole in wanted:
        yield line.whole, None, None
    for run in line.runs:
        for length in PIECE_LENGTHS:
            for start in range(len(run) - length + 1):
                piece = run[start:start + length]
                if piece in wanted and piece != line.whole:
                    yield (piece, run[start - 1] if start else None,
                           run[start + length] if start + length < len(run) else None)
    for word in line.latin:
        if word in wanted:
            yield word, "", ""


def common_phrases(lines, min_people):
    """The phrases that at least min_people of the lines use: a first cut, before people
    are counted.

    Pieces are counted one length at a time, and a piece is only counted when both of its
    shorter pieces (without its last or without its first character) were common, since a
    phrase can't be in more lines than its parts. That keeps the counting small.
    """
    keep = lambda counts: {phrase for phrase, n in counts.items() if n >= min_people}
    common = keep(collections.Counter(line.whole for line in lines if line.whole))
    common |= keep(collections.Counter(word for line in lines for word in line.latin))
    shorter = None
    for length in PIECE_LENGTHS:
        counts = collections.Counter()
        for line in lines:
            found = set()
            for run in line.runs:
                for start in range(len(run) - length + 1):
                    piece = run[start:start + length]
                    if shorter is None or (piece[:-1] in shorter and piece[1:] in shorter):
                        found.add(piece)
            counts.update(found)
        shorter = keep(counts)
        common |= shorter
        if not shorter:
            break
    return common


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


def freedom(neighbours):
    """How varied the characters next to a phrase are: their entropy in bits.

    Every time a phrase stands at the edge of a line counts as a different neighbour, so a
    phrase people write on its own scores high (绷不住了), and a piece of a longer phrase
    scores low on the side where it is always attached (不住了, always after 绷).
    """
    total = sum(neighbours.values())
    if not total:
        return 0.0
    bits = 0.0
    for neighbour, count in neighbours.items():
        if neighbour is None:
            bits += count * (1 / total) * math.log2(total)  # each edge: its own neighbour
        else:
            bits -= count / total * math.log2(count / total)
    return bits


def known_phrases():
    """Everything in the translator's tables (the files in data/ and your own entries from
    the overlay's ⚙ window) and in data/surf_common.txt, lowercase."""
    from . import settings
    known = set()

    def add(value):
        if isinstance(value, str):
            known.add(NOT_TEXT.sub("", unicodedata.normalize("NFKC", value).lower()))
        elif isinstance(value, dict):
            for key, item in value.items():
                if not str(key).startswith("_"):
                    add(key)
                    add(item)
        elif isinstance(value, list):
            for item in value:
                add(item)

    for name in KNOWN_FILES + [os.path.basename(COMMON_FILE)]:
        path = os.path.join(HERE, "data", name)
        if name.endswith(".json"):
            add(_load_json(path, {}))
        else:
            try:
                with open(path, encoding="utf-8") as f:
                    add([line.strip() for line in f if line.strip() and not line.startswith("#")])
            except OSError:
                pass
    for table in settings.TABLES:
        add(settings.words(table))
    known.discard("")
    return known


def _english_words():
    with open(WORDS_FILE, encoding="utf-8") as f:
        return {line.strip().lower() for line in f if line.strip()}


def find_candidates(messages, days=DAYS, now=None, min_people=MIN_PEOPLE,
                    min_videos=MIN_VIDEOS, min_before=MIN_BEFORE_MESSAGES, known=None,
                    english_words=None):
    """Steps 3 and 4: the phrases that are new or rising, for each game.

    messages is an iterable of (game, bvid, message). Returns (candidates, summary): the
    candidates sorted best first, and per game {"videos", "recent", "before", "baseline"}
    (line counts, and whether there were enough earlier lines to compare with).
    """
    now = time.time() if now is None else now
    cut = now - days * DAY
    known = known_phrases() if known is None else known
    english_words = _english_words() if english_words is None else english_words
    by_game = collections.defaultdict(list)
    for game, bvid, message in messages:
        text = clean(message.get("text", ""))
        if text and message.get("sent"):  # a line with no time can't be placed: skip it
            by_game[game].append(Line(game, bvid, message["who"], message["sent"] >= cut,
                                      *_prepare(text, english_words), message))
    candidates, summary = [], {}
    for game, lines in by_game.items():
        found, summary[game] = _game_candidates(game, lines, min_people, min_videos,
                                                min_before, known)
        candidates += found
    candidates.sort(key=lambda c: -c["score"])
    return candidates, summary


def _game_candidates(game, lines, min_people, min_videos, min_before, known):
    """find_candidates() for the lines of one game."""
    recent_lines = [line for line in lines if line.recent]
    wanted = common_phrases(recent_lines, min_people) - known

    # People, videos, neighbours and example lines of each common phrase
    info = collections.defaultdict(lambda: {
        "recent": set(), "before": set(), "videos": set(), "left": collections.Counter(),
        "right": collections.Counter(), "examples": collections.defaultdict(dict)})
    people_recent, people_before = set(), set()
    for line in lines:
        (people_recent if line.recent else people_before).add(line.who)
        used = set()
        for phrase, left, right in pieces(line, wanted):
            entry = info[phrase]
            if line.recent:  # how it is used now decides whether it stands on its own
                entry["left"][left] += 1
                entry["right"][right] += 1
            if phrase in used:
                continue
            used.add(phrase)
            if not line.recent:
                entry["before"].add(line.who)
                continue
            entry["recent"].add(line.who)
            entry["videos"].add(line.bvid)
            pool = entry["examples"][line.bvid]  # different lines only, so spam fills one place
            if len(pool) < EXAMPLES and line.message["text"] not in pool:
                pool[line.message["text"]] = line.message

    before_lines = len(lines) - len(recent_lines)
    baseline = before_lines >= min_before
    checked = []
    for phrase, entry in info.items():
        people, videos = len(entry["recent"]), len(entry["videos"])
        if people < min_people or videos < min_videos:
            continue
        if phrase.isdigit() and len(phrase) < 3:
            continue  # scores and counts, not memes
        latin = "" in entry["left"]
        if not latin and min(freedom(entry["left"]), freedom(entry["right"])) < MIN_FREEDOM:
            continue  # a piece of a longer phrase, not a phrase of its own
        checked.append((phrase, entry))
    # Thousands of phrases are checked, so some look like they rose by pure chance. A rise
    # only counts when chance would explain it less than once in 10^SURE such runs
    enough = math.log10(max(1, len(checked))) + SURE
    candidates = []
    for phrase, entry in checked:
        people, videos, before = len(entry["recent"]), len(entry["videos"]), len(entry["before"])
        rise = None
        if baseline:
            # How many times more of the people use it now than before. One person is
            # added to "before", so a phrase is only new when the earlier lines were enough
            # to show it, had it been as common then
            rise = (people / len(people_recent)) / ((before + 1) / max(1, len(people_before)))
            if rise < RISE or surprise(people, before, len(people_recent),
                                       len(people_before)) < enough:
                continue  # about as common before, or too few people to tell
            status = "new" if before == 0 else "rising"
        else:
            status = "first look"  # nothing to compare with yet
        candidates.append({
            "phrase": phrase, "game": game, "status": status, "people": people,
            "videos": videos, "people_before": before,
            "rise": round(rise, 1) if rise else None,
            "score": round(people * (1 + math.log2(videos)), 1),
            "examples": _pick_examples(entry["examples"]),
        })

    # A candidate plus mood words (下饭了, 下饭吧) is the same phrase as the candidate
    mood = "".join(PARTICLES)
    phrases = {c["phrase"] for c in candidates}
    candidates = [c for c in candidates if c["phrase"].rstrip(mood) == c["phrase"]
                  or c["phrase"].rstrip(mood) not in phrases]
    # A phrase mostly used inside a longer candidate is that one's piece: drop it
    kept = []
    for candidate in sorted(candidates, key=lambda c: -len(c["phrase"])):
        if not any(candidate["phrase"] in other["phrase"]
                   and other["people"] >= CONTAINED * candidate["people"] for other in kept):
            kept.append(candidate)
    kept = sorted(kept, key=lambda c: -c["score"])[:TOP]
    return kept, {"videos": len({line.bvid for line in lines}), "recent": len(recent_lines),
                  "before": before_lines, "baseline": baseline}


def _pick_examples(by_video):
    """Up to EXAMPLES different lines using a phrase, from {bvid: {text: message}}: one from
    each video first, and lines with some context (up to 60 characters) before bare ones."""
    order = lambda m: (len(m["text"]) > 120, -min(len(m["text"]), 60), -m["sent"])
    queues = [[(bvid, m) for m in sorted(messages.values(), key=order)]
              for bvid, messages in sorted(by_video.items())]
    picked, texts = [], set()
    while len(picked) < EXAMPLES and any(queues):
        for queue in queues:  # take turns, so the examples span the videos
            while queue and len(picked) < EXAMPLES:
                bvid, message = queue.pop(0)
                if message["text"] not in texts:
                    texts.add(message["text"])
                    picked.append({"text": message["text"], "video": bvid,
                                   "kind": message["kind"],
                                   "sent": time.strftime("%Y-%m-%d",
                                                         time.localtime(message["sent"]))})
                    break
    return picked


# -- command line -------------------------------------------------------------------------

def save(candidates, summary, days):
    """Write surf/candidates.json: the phrases, their examples and the videos they're from."""
    videos = _load_json(VIDEOS_FILE, {})
    used = {example["video"] for c in candidates for example in c["examples"]}
    _save_json(CANDIDATES_FILE, {
        "made": time.strftime("%Y-%m-%d %H:%M"),
        "days": days,
        "games": summary,
        "videos": {bvid: {k: videos[bvid].get(k) for k in ("game", "title", "author", "url")}
                   for bvid in sorted(used) if bvid in videos},
        "candidates": candidates,
    })


def main():
    """py surf.py [--games cs2 lol ...] [--videos N] [--days N] [--people N] [--no-fetch]"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # print Chinese in the Windows console
    args = sys.argv[1:]
    where = sources()
    games, days, count, people, fetching = list(where), DAYS, VIDEOS_PER_GAME, MIN_PEOPLE, True
    try:
        while args:
            option = args.pop(0)
            if option == "--games":
                games = []
                while args and not args[0].startswith("--"):
                    games.append(args.pop(0).lower())
            elif option in ("--days", "--videos", "--people"):
                value = int(args.pop(0))
                if option == "--days":
                    days = value
                elif option == "--videos":
                    count = value
                else:
                    people = value
            elif option == "--no-fetch":
                fetching = False
            else:
                raise ValueError(option)
    except (ValueError, IndexError):
        sys.exit(__doc__.split("It only reads")[0].strip())
    unknown = [game for game in games if game not in where]
    if unknown or not games:
        sys.exit(f"Unknown game {', '.join(unknown) or '(none given)'}; "
                 f"data/surf_sources.json has: {', '.join(where)}")

    now = time.time()
    if fetching:
        print("Reading Bilibili (read only, a request every 5 to 7 seconds) ...")
        try:
            fetch(games, where, days, count, now)
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
        days=days, now=now, min_people=people)
    save(candidates, summary, days)

    for game in games:
        s = summary.get(game)
        if not s:
            print(f"{game}: nothing read yet")
            continue
        print(f"{game}: {s['videos']} videos, {s['recent']} lines from the last {days} days, "
              f"{s['before']} from before" + ("" if s["baseline"] else
                                              " (too few to compare with: all are a first look)"))
    print(f"\n{len(candidates)} phrases are new or rising (used by at least {people} people "
          f"in at least {MIN_VIDEOS} videos):")
    for c in candidates:
        how = c["status"] if c["status"] != "rising" else f"rising x{c['rise']}"
        example = c["examples"][0]["text"] if c["examples"] else ""
        print(f"  {c['game']:<9} {how:<12} {c['phrase']}  ({c['people']} people, "
              f"{c['videos']} videos)  e.g. {example[:40]}")
    print(f"\nSaved them with example lines to {CANDIDATES_FILE}")


if __name__ == "__main__":
    main()
