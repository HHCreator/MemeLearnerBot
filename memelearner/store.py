"""The memes the bot has learned: data/memes.json, the file other apps read.

The file is what the bot makes, so it is committed with the code (unlike what the bot read,
which stays in the cache folder, see surf.py), and an app can read it straight from GitHub
too. It looks like this:

    {"version": 2,
     "memes": {"绷不住了": {"meaning": "笑得忍不住了", "english": "can't keep a straight face",
                          "games": ["lol"], "added": "2026-11-01",
                          "examples": ["这下真的绷不住了"],
                          "sources": ["https://www.bilibili.com/video/BV17XxP6CE7n"]}},
     "rejected": {"下路": {"reason": "an everyday word", "date": "2026-11-01"}}}

"memes" are the learned memes, by phrase; "rejected" are the phrases you turned down (names,
everyday words), so the bot doesn't list them again. A meme you wrote notes on has them too,
word for word, as "notes": ["chovy就是超威，和超威同意了是同一个梗"]; its meaning is then the one
the model merged from them and its own (see meanings.py). A meme people write in more than one
way (超威同意了, chovy同意了 and 三叔不同意 are one meme, see variants.py) is one entry, under
the phrase sent most, with the others as "variants": ["chovy同意了", "三叔不同意"], and
lookup() and find() find it by any of them; it keeps EXAMPLES example lines for each. "apart"
holds the phrases you said aren't that meme, though they look like it (in the report). Every
phrase is written the way the bot finds it (normal()): lowercase, half-width, only letters,
digits and Chinese characters. "version" changes when a field changes meaning or goes away; a
new field doesn't change it.
"""

import json
import os
import re
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
MEMES_FILE = os.path.join(HERE, "data", "memes.json")
VERSION = 2  # 2: a meme written in more than one way is one entry, the others in "variants"
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
    # newline="\n": the same line ends on Windows, so git shows only the lines that changed
    with open(temporary, "w", encoding="utf-8", newline="\n") as f:
        json.dump(content, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(temporary, MEMES_FILE)  # all at once, so a reader never sees half a file


def _meme(phrase, entry):
    """A meme as the API gives it out (see __init__.py)."""
    return {"phrase": phrase, "meaning": entry.get("meaning", ""),
            "english": entry.get("english", ""), "games": list(entry.get("games") or []),
            "added": entry.get("added", ""), "examples": list(entry.get("examples") or []),
            "sources": list(entry.get("sources") or []),
            "variants": list(entry.get("variants") or [])}


def owners(memes):
    """{phrase: the learned meme it is}: each meme's phrase, and each of its variants."""
    owner = {}
    for phrase, entry in memes.items():
        for variant in entry.get("variants") or []:
            owner.setdefault(variant, phrase)
    owner.update((phrase, phrase) for phrase in memes)  # a meme's own phrase is its own
    return owner


def memes(game=None):
    """The learned memes, newest first (in the order of their phrases within a day); only
    one game's when game is given."""
    found = [_meme(phrase, entry) for phrase, entry in load()["memes"].items()
             if game is None or game in (entry.get("games") or [])]
    found.sort(key=lambda meme: meme["phrase"])
    return sorted(found, key=lambda meme: meme["added"], reverse=True)


def lookup(phrase):
    """The learned meme for a phrase, written in any case or width, or None. A variant of a
    meme gives that meme: chovy同意了 gives 超威同意了's."""
    known = load()["memes"]
    owner = owners(known).get(normal(phrase))
    return None if owner is None else _meme(owner, known[owner])


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
    owner = owners(known)  # a meme's variants count as the meme
    text = unicodedata.normalize("NFKC", text or "").lower()
    longest = max(map(len, owner), default=0)
    found, seen, start = [], set(), 0
    while start < len(text):
        for length in range(min(longest, len(text) - start), 0, -1):
            piece = text[start:start + length]
            if piece in owner and _whole(text, start, start + length):
                meme = owner[piece]
                if meme not in seen:
                    seen.add(meme)
                    found.append(_meme(meme, known[meme]))
                start += length
                break
        else:
            start += 1
    return found


def phrases():
    """Every phrase learned (a meme's variants too) or turned down: the bot doesn't list them
    again."""
    data = load()
    return set(owners(data["memes"])) | set(data["rejected"])


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


def accept(phrase, meaning="", english="", games=(), examples=(), sources=(), note="",
           variants=(), apart=()):
    """Learn a phrase as a meme; returns it. Accepting it again updates it: a meaning or an
    English gloss given replaces the old one, and games, examples and sources are added.
    note: your own note on it, kept word for word in its "notes" (meanings.py merges it
    with the meaning; the meaning given here is the merged one).

    variants: other phrases of the same meme (variants.py). A learned meme among them joins
    this one, with its variants, notes, games, examples and sources (its meaning only when
    this one has none), and its own entry goes: one meme, one entry. One that was another
    meme's variant moves here, and one you turned down isn't turned down any more. A phrase
    that is a variant of a learned meme already updates that meme (chovy同意了: 超威同意了).
    apart: phrases you said aren't this meme, though they look like it."""
    data = load()
    memes = data["memes"]
    key = _key(phrase)
    key = owners(memes).get(key, key)
    entry = memes.setdefault(key, {})
    away = [p for p in dict.fromkeys(normal(p) for p in apart) if p and p != key]
    joining = [p for p in dict.fromkeys(normal(p) for p in variants)
               if p and p != key and p not in away]
    joined, notes = [], []
    for other in joining:
        owner = owners(memes).get(other)
        if owner == key:
            joined.append(other)
            continue
        if owner and owner != other:  # another meme's variant: it moves here
            _drop_variant(memes[owner], other)
        elif owner:  # a learned meme: it joins this one
            folded = memes.pop(other)
            meaning = meaning or entry.get("meaning") or folded.get("meaning", "")
            english = english or entry.get("english") or folded.get("english", "")
            games = list(games) + list(folded.get("games") or [])
            examples = list(examples) + list(folded.get("examples") or [])
            sources = list(sources) + list(folded.get("sources") or [])
            notes += list(folded.get("notes") or [])
            joined += [p for p in folded.get("variants") or [] if p != key and p not in away]
            away += [p for p in folded.get("apart") or [] if p not in away]
        joined.append(other)
    kept = [p for p in entry.get("variants") or [] if p not in away]
    entry["variants"] = [p for p in _merge(kept, joined) if p != key]
    entry["apart"] = [p for p in _merge(entry.get("apart"), away) if p not in entry["variants"]]
    limit = EXAMPLES * (1 + len(entry["variants"]))  # EXAMPLES for each way it's written
    entry.update(meaning=meaning or entry.get("meaning", ""),
                 english=english or entry.get("english", ""),
                 games=_merge(entry.get("games"), games),
                 added=entry.get("added") or time.strftime("%Y-%m-%d"),
                 examples=_merge(entry.get("examples"), examples)[:limit],
                 sources=_merge(entry.get("sources"), sources)[:limit])
    notes += [" ".join(note.split())] if note.strip() else []
    if notes:
        entry["notes"] = _merge(entry.get("notes"), notes)
    for field in ("variants", "apart"):
        if not entry[field]:
            del entry[field]
    for p in [key] + entry.get("variants", []):
        data["rejected"].pop(p, None)
    memes[key] = dict(sorted(entry.items(), key=lambda item: FIELDS.index(item[0])
                             if item[0] in FIELDS else len(FIELDS)))
    save(data)
    return _meme(key, memes[key])


FIELDS = ("meaning", "english", "games", "added", "examples", "sources", "notes", "variants",
          "apart")  # the order of a meme's fields in the file


def _drop_variant(entry, phrase):
    entry["variants"] = [p for p in entry.get("variants") or [] if p != phrase]
    if not entry["variants"]:
        del entry["variants"]


def reject(phrase, reason=""):
    """Turn a phrase down (a name, an everyday word): the bot won't list it again. A learned
    meme isn't one any more, and its variants are turned down with it; a variant of one is
    only taken out of it."""
    key = _key(phrase)
    data = load()
    memes = data["memes"]
    owner = owners(memes).get(key)
    turned = [key]
    if owner and owner != key:
        _drop_variant(memes[owner], key)
    elif owner:
        turned += list(memes.pop(key).get("variants") or [])
    for p in turned:
        data["rejected"][p] = {"reason": reason, "date": time.strftime("%Y-%m-%d")}
    save(data)


def forget(phrase):
    """Undo accept() or reject(): the phrase is neither learned nor turned down, so the bot
    may list it again (a learned meme's variants too). Returns whether it was either."""
    key = _key(phrase)
    data = load()
    memes = data["memes"]
    owner = owners(memes).get(key)
    if owner and owner != key:
        _drop_variant(memes[owner], key)
    elif owner:
        del memes[key]
    was = data["rejected"].pop(key, None) is not None or owner is not None
    if was:
        save(data)
    return was
