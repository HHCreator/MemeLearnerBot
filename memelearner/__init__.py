"""MemeLearnerBot: a bot that reads Bilibili for the newest gaming memes, and the way other
apps learn them from it. Other apps only need what is in this file.

    import memelearner

    memelearner.memes()                     # the memes learned so far, newest first
    memelearner.memes(game="lol")           # only one game's
    memelearner.lookup("绷不住了")            # one learned meme, or None
    memelearner.find("这下真的绷不住了")       # the learned memes in a text, in order
    memelearner.candidates()                # what the last run found, not looked at yet
    memelearner.learn(games=["lol"])        # read Bilibili now (takes minutes)
    memelearner.judge()                     # a model tells memes from names (minutes)
    memelearner.explain()                   # read videos that explain those memes (minutes)
    memelearner.watching()                  # what the model wasn't sure of yet
    memelearner.accept("绷不住了", meaning="笑得忍不住了")   # a candidate becomes a meme
    memelearner.reject("下路")              # not a meme: never list it again

A meme is a dict:

    {"phrase": "绷不住了",              lowercase and half-width, the way the bot finds it
     "meaning": "笑得忍不住了",          what it means, in Chinese ("" when not known yet)
     "english": "can't keep a straight face",    an English gloss, or ""
     "games": ["lol"],                 where it was seen: cs2, lol, valorant, dota2
     "added": "2026-11-01",            when it was learned
     "examples": ["这下真的绷不住了"],    a few lines that used it
     "sources": ["https://www.bilibili.com/video/BV17XxP6CE7n"],   videos those came from
     "variants": [],                   other ways the same meme is written: lookup() and
                                       find() find it by any (超威同意了: chovy同意了 ...)
     "source": None}                   the meme it comes from, and how the judge knows:
                                       {"phrase": "我们是冠军", "how": "learned"} for
                                       我们是路边 (see store.py), or None

A candidate is a short danmu that many people send on their own, with how new it is, its
numbers, its variants (the same meme written differently: variants.py), its pattern
(XX同意了), the names in it and example lines (see surf.find_candidates()), and once
judged, the model's verdict and meaning ("judge", see judging.judge()). Apps that aren't written
in Python get the same through the command line with --json (cli.py), or read
data/memes.json, which holds the learned memes (its format is in store.py). API_VERSION
changes when a field changes meaning or goes away; a new field doesn't change it.
"""

import contextlib
import io

from . import explainers, judging, meanings, store, surf
from .judging import ModelUnavailable, watching
from .store import find, forget, lookup, memes, reject

__version__ = "0.3.0"
API_VERSION = 3  # 2: a candidate is a phrase people send on their own, new or not;
#                 3: a meme written in more than one way is one meme, with its "variants"

__all__ = ["memes", "lookup", "find", "candidates", "learn", "judge", "explain", "watching",
           "accept", "reject", "forget", "ModelUnavailable", "API_VERSION"]


def candidates(game=None):
    """The phrases the last run found, best first, leaving out the ones accepted or turned
    down since; only one game's when game is given. [] before the first run."""
    found = surf._load_json(surf.CANDIDATES_FILE, {}).get("candidates") or []
    done = store.phrases()
    return [c for c in found if store.normal(c["phrase"]) not in done
            and game in (None, c["game"])]


def learn(games=None, videos=surf.VIDEOS_PER_GAME, days=surf.DAYS, people=surf.MIN_PEOPLE,
          fetch=True, known=(), judge=False, quiet=False):
    """Read Bilibili for new memes now, and return the candidates it found (also saved for
    candidates()). It waits 5 to 7 seconds between requests, so this takes minutes: about
    half a minute per video, 20 minutes for all games. Bilibili refusing or not answering
    stops the reading, not the call.

    games: some of cs2, lol, valorant, dota2 (all by default); videos: videos read per game;
    days: how recent "new" is; people: different people who must send a phrase on its own
    (in at least two videos); fetch=False
    finds phrases again in what was read before, without visiting Bilibili; known: phrases
    your app knows already, left out like the learned ones; judge: then have the model
    judge them, as judge() does (raises ModelUnavailable when it can't be asked; the
    candidates are saved all the same); quiet: print nothing.
    """
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        found, _ = surf.learn(games, days, videos, people, fetch, known)
        if judge and found:
            found = judging.judge(games)
    return found


def judge(games=None, model=None, again=False, quiet=False):
    """Have a model judge the last run's candidates: is each a meme, a name, a game word or
    an everyday word, and what does a meme mean? Returns the candidates, each with its
    verdict as "judge" (also saved for candidates()); see judging.py for how it works and
    what a verdict holds. It takes a few seconds per candidate.

    games: only these games' candidates; model: an online model named PROVIDER/MODEL, which
    reads your key from an environment variable (DEEPSEEK_API_KEY), or an Ollama model on
    this computer like qwen3:14b (default: MEMELEARNER_MODEL, or deepseek/deepseek-flash);
    again=True judges again what was judged before. When an online model can't be reached
    (no internet, no key, a key it refuses or no balance left), qwen3:14b on this computer
    judges instead (judging.LOCAL_MODEL), and each verdict's "model" says which one judged.
    Raises ModelUnavailable when no model can be asked (Ollama isn't running or doesn't
    have it, either); the verdicts given until then are kept. quiet: print nothing.
    """
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        return judging.judge(games, model or judging.MODEL, again)


def explain(phrases=None, games=None, videos=explainers.VIDEOS, top=explainers.TOP, links=None,
            model=None, again=False, hearing=True, judge=True, quiet=False):
    """Find and read the Bilibili videos that explain memes (阿一古是什么梗？), have the model
    sum up what each says, and judge those memes again with it (see explainers.py). It reads
    as a visitor, 5 to 7 seconds between requests, so it takes minutes: about half a minute
    to a minute per video. Returns {"game:phrase": [summary, ...]} for the videos read now;
    a summary is {"bvid", "url", "title", "author", "about", "meaning", "origin",
    "english", "quote", "heard", "model", "summarized"}.

    phrases: only these (default: the memes the judge found, the ones whose meaning is
    least sure first); games: only these games' phrases; videos: read per phrase; top:
    phrases looked up; links: {phrase: [video link, ...]} to read instead of looking;
    model: as for judge(), and qwen3:14b sums the videos up too when an online model can't
    be reached; again: also phrases looked up before; hearing=False: don't hear videos even
    with Whisper installed (py -m pip install faster-whisper); judge=False: don't judge them
    again. Raises ModelUnavailable when no model can be asked; what was read until then is
    kept. quiet: print nothing.
    """
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        return explainers.explain(phrases, games, links, model or judging.MODEL, videos, top,
                                  again, hearing, judge)


def accept(phrase, meaning="", english="", game=None, note="", model=None):
    """Learn a phrase as a meme, usually one of candidates(), and return the meme.

    When the phrase was a candidate in the last run, its games, example lines and videos
    come from there, and when the judge found it a meme and its meaning isn't only a guess
    (it was tested, or lines explained it), so do its meaning and English gloss, unless you
    give them. Accepting it again updates it: a meaning or
    English gloss given replaces the old one.

    note: your own note on it, like something to add to the meaning (一般只用在theshy选手的
    比赛), or that it is the same meme as a learned one (和超威同意了是同一个梗). A model merges
    it with the meaning (the one given, else the one learned before, else the judge's) into
    one whole meaning, in which your note wins, and the note is kept as you wrote it (see
    meanings.py). That asks model (default: the judge's, with qwen3:14b standing in when an
    online one can't be reached) and takes seconds; it raises ModelUnavailable, learning
    nothing, when no model can be asked.
    """
    key = store.normal(phrase)
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    urls = {bvid: video.get("url") for bvid, video in (last.get("videos") or {}).items()}
    found = [c for c in last.get("candidates") or [] if c["phrase"] == key]
    examples = [example for c in found for example in c["examples"]][:store.EXAMPLES]
    learned = store.lookup(key)
    if not (meaning and english) and not learned:
        judged = judging.meaning_of(key)
        meaning, english = meaning or judged[0], english or judged[1]
    if note.strip():
        base = meaning or (learned or {}).get("meaning", "")
        gloss = english or ("" if meaning else (learned or {}).get("english", ""))
        merged = meanings.merge(key, note, base, gloss, model)
        meaning, english = merged["meaning"], merged["english"]
    return store.accept(key, meaning, english,
                        games=[c["game"] for c in found] + ([game] if game else []),
                        examples=[example["text"] for example in examples],
                        sources=[urls[e["video"]] for e in examples if urls.get(e["video"])],
                        note=note)
