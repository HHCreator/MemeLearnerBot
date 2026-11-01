"""The memes the bot has learned: data/memes.json, the file other apps read.

The file is what the bot makes, so it is committed with the code (unlike what the bot read,
which stays in the cache folder, see surf.py), and an app can read it straight from GitHub
too. It looks like this:

    {"version": 1,
     "memes": {"绷不住了": {"meaning": "笑得忍不住了", "english": "can't keep a straight face",
                          "games": ["lol"], "added": "2026-11-01",
                          "examples": ["这下真的绷不住了"],
                          "sources": ["https://www.bilibili.com/video/BV17XxP6CE7n"]}},
     "rejected": {"下路": {"reason": "an everyday word", "date": "2026-11-01"}}}

"memes" are the learned memes, by phrase; "rejected" are the phrases you turned down (names,
everyday words), so the bot doesn't list them again. Every phrase is written the way the bot
finds it (normal()): lowercase, half-width, only letters, digits and Chinese characters.
"version" changes when a field changes meaning or goes away; a new field doesn't change it.
"""

import json
import os
import re
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
MEMES_FILE = os.path.join(HERE, "data", "memes.json")
VERSION = 1
EXAMPLES = 3  # example lines (and the videos they're from) kept for each meme
NOTE = ("Memes learned by MemeLearnerBot (https://github.com/HHCreator/MemeLearnerBot). "
        "Change them with py -m memelearner accept, reject or forget; the format is in "
        "memelearner/store.py and the README.")

NOT_TEXT = re.compile(r"[^0-9a-z一-鿿]+")
LATIN = re.compile(r"[0-9a-z]")


def normal(text):
    """A phrase the way the bot writes it: ＹＹＤＳ -> yyds, 绷不住了！ -> 绷不住了."""
    return NOT_TEXT.sub("", unicodedata.normalize("NFKC", text or "").lower())


def load():
    """The file's content: {"version", "memes", "rejected"}; empty when there's no file.

    A file that isn't valid JSON raises ValueError instead of counting as empty, so a broken
    file (a merge gone wrong) is never saved over with nothing.
    """
    try:
        with open(MEMES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    return {"version": data.get("version", VERSION), "memes": dict(data.get("memes") or {}),
            "rejected": dict(data.get("rejected") or {})}


def save(data):
    """Write the file, in a fixed order so its changes are easy to read in git."""
    content = {"_comment": NOTE, "version": VERSION,
               "memes": dict(sorted(data["memes"].items())),
               "rejected": dict(sorted(data["rejected"].items()))}
    os.makedirs(os.path.dirname(MEMES_FILE), exist_ok=True)
    temporary = MEMES_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(temporary, MEMES_FILE)  # all at once, so a reader never sees half a file


def _meme(phrase, entry):
    """A meme as the API gives it out (see __init__.py)."""
    return {"phrase": phrase, "meaning": entry.get("meaning", ""),
            "english": entry.get("english", ""), "games": list(entry.get("games") or []),
            "added": entry.get("added", ""), "examples": list(entry.get("examples") or []),
            "sources": list(entry.get("sources") or [])}


def memes(game=None):
    """The learned memes, newest first (in the order of their phrases within a day); only
    one game's when game is given."""
    found = [_meme(phrase, entry) for phrase, entry in load()["memes"].items()
             if game is None or game in (entry.get("games") or [])]
    found.sort(key=lambda meme: meme["phrase"])
    return sorted(found, key=lambda meme: meme["added"], reverse=True)


def lookup(phrase):
    """The learned meme for a phrase, written in any case or width, or None."""
    key = normal(phrase)
    entry = load()["memes"].get(key)
    return None if entry is None else _meme(key, entry)


def _whole(text, start, end):
    """Whether text[start:end] isn't part of a longer run of letters and digits (a meme
    like yyds only counts as a whole word; Chinese has no spaces, so it always does)."""
    if LATIN.match(text[start]) and start > 0 and LATIN.match(text[start - 1]):
        return False
    return not (LATIN.match(text[end - 1]) and end < len(text) and LATIN.match(text[end]))


def find(text):
    """The learned memes in a text, in the order they come, each once.

    Where two overlap, the longer one wins (这下真的绷不住了 has 绷不住了, not 不住), and
    a meme of letters or digits only counts as a whole word: "zywoo yyds" has yyds, "yydsx"
    doesn't. The text may be in any case or width.
    """
    known = load()["memes"]
    text = unicodedata.normalize("NFKC", text or "").lower()
    longest = max(map(len, known), default=0)
    found, seen, start = [], set(), 0
    while start < len(text):
        for length in range(min(longest, len(text) - start), 0, -1):
            piece = text[start:start + length]
            if piece in known and _whole(text, start, start + length):
                if piece not in seen:
                    seen.add(piece)
                    found.append(_meme(piece, known[piece]))
                start += length
                break
        else:
            start += 1
    return found


def phrases():
    """Every phrase learned or turned down: the bot doesn't list them again."""
    data = load()
    return set(data["memes"]) | set(data["rejected"])


def _key(phrase):
    key = normal(phrase)
    if not key:
        raise ValueError(f"{phrase!r} has no letters, digits or Chinese characters")
    return key


def _merge(old, new):
    """old, then the items of new that aren't in it yet."""
    merged = list(old or [])
    for item in new:
        if item and item not in merged:
            merged.append(item)
    return merged


def accept(phrase, meaning="", english="", games=(), examples=(), sources=()):
    """Learn a phrase as a meme; returns it. Accepting it again updates it: a meaning or an
    English gloss given replaces the old one, and games, examples and sources are added."""
    key = _key(phrase)
    data = load()
    entry = data["memes"].get(key, {})
    entry.update(meaning=meaning or entry.get("meaning", ""),
                 english=english or entry.get("english", ""),
                 games=_merge(entry.get("games"), games),
                 added=entry.get("added") or time.strftime("%Y-%m-%d"),
                 examples=_merge(entry.get("examples"), examples)[:EXAMPLES],
                 sources=_merge(entry.get("sources"), sources)[:EXAMPLES])
    data["memes"][key] = entry
    data["rejected"].pop(key, None)
    save(data)
    return _meme(key, entry)


def reject(phrase, reason=""):
    """Turn a phrase down (a name, an everyday word): the bot won't list it again, and if
    it was a learned meme, it isn't any more."""
    key = _key(phrase)
    data = load()
    data["rejected"][key] = {"reason": reason, "date": time.strftime("%Y-%m-%d")}
    data["memes"].pop(key, None)
    save(data)


def forget(phrase):
    """Undo accept() or reject(): the phrase is neither learned nor turned down, so the bot
    may list it again. Returns whether it was either."""
    key = _key(phrase)
    data = load()
    was = data["memes"].pop(key, None) is not None
    was = data["rejected"].pop(key, None) is not None or was
    if was:
        save(data)
    return was
