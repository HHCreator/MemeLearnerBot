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
     "sources": ["https://www.bilibili.com/video/BV17XxP6CE7n"]}   videos those came from

A candidate is a short danmu that many people send on their own, with how new it is, its
numbers, its variants and example lines (see surf.find_candidates()), and once judged, the
model's verdict and meaning ("judge", see judging.judge()). Apps that aren't written
in Python get the same through the command line with --json (cli.py), or read
data/memes.json, which holds the learned memes (its format is in store.py). API_VERSION
changes when a field changes meaning or goes away; a new field doesn't change it.
"""

import contextlib
import io

from . import judging, store, surf
from .judging import ModelUnavailable, watching
from .store import find, forget, lookup, memes, reject

__version__ = "0.3.0"
API_VERSION = 2  # 2: a candidate is a phrase people send on their own, new or not

__all__ = ["memes", "lookup", "find", "candidates", "learn", "judge", "watching", "accept",
           "reject", "forget", "ModelUnavailable", "API_VERSION"]


def candidates(game=None):
    """The phrases the last run found, best first, leaving out the ones accepted or turned
    down since; only one game's when game is given. [] before the first run."""
    found = surf._load_json(surf.CANDIDATES_FILE, {}).get("candidates") or []
    done = store.phrases()
    return [c for c in found if c["phrase"] not in done and game in (None, c["game"])]


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
    your app knows already, left out like the learned ones; judge: then have the local
    model judge them, as judge() does (raises ModelUnavailable when it can't be asked; the
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

    games: only these games' candidates; model: an Ollama model on this computer, or an
    online one named PROVIDER/MODEL like deepseek/deepseek-flash, which reads your key from
    an environment variable (DEEPSEEK_API_KEY) (default: MEMELEARNER_MODEL, or
    deepseek-r1:14b); again=True judges again what was judged before. Raises
    ModelUnavailable when the model can't be asked (Ollama isn't running or doesn't have it;
    an online one has no key, refuses it or doesn't answer); the verdicts given until then
    are kept. quiet: print nothing.
    """
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        return judging.judge(games, model or judging.MODEL, again)


def accept(phrase, meaning="", english="", game=None):
    """Learn a phrase as a meme, usually one of candidates(), and return the meme.

    When the phrase was a candidate in the last run, its games, example lines and videos
    come from there, and when the judge found it a meme and its meaning isn't only a guess
    (it was tested, or lines explained it), so do its meaning and English gloss, unless you
    give them. Accepting it again updates it: a meaning or
    English gloss given replaces the old one.
    """
    key = store.normal(phrase)
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    urls = {bvid: video.get("url") for bvid, video in (last.get("videos") or {}).items()}
    found = [c for c in last.get("candidates") or [] if c["phrase"] == key]
    examples = [example for c in found for example in c["examples"]][:store.EXAMPLES]
    if not (meaning and english) and not store.lookup(key):
        judged = judging.meaning_of(key)
        meaning, english = meaning or judged[0], english or judged[1]
    return store.accept(key, meaning, english,
                        games=[c["game"] for c in found] + ([game] if game else []),
                        examples=[example["text"] for example in examples],
                        sources=[urls[e["video"]] for e in examples if urls.get(e["video"])])
