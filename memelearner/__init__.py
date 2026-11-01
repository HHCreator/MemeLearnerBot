"""MemeLearnerBot: a bot that reads Bilibili for the newest gaming memes, and the way other
apps learn them from it. Other apps only need what is in this file.

    import memelearner

    memelearner.memes()                     # the memes learned so far, newest first
    memelearner.memes(game="lol")           # only one game's
    memelearner.lookup("绷不住了")            # one learned meme, or None
    memelearner.find("这下真的绷不住了")       # the learned memes in a text, in order
    memelearner.candidates()                # what the last run found, not looked at yet
    memelearner.learn(games=["lol"])        # read Bilibili now (takes minutes)
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

A candidate is a phrase the bot found new or rising, with its numbers and example lines
(see surf.find_candidates()). Apps that aren't written in Python get the same through the
command line with --json (cli.py), or read data/memes.json, which holds the learned memes
(its format is in store.py). API_VERSION changes when a field changes meaning or goes away;
a new field doesn't change it.
"""

import contextlib
import io

from . import store, surf
from .store import find, forget, lookup, memes, reject

__version__ = "0.1.0"
API_VERSION = 1

__all__ = ["memes", "lookup", "find", "candidates", "learn", "accept", "reject", "forget",
           "API_VERSION"]


def candidates(game=None):
    """The phrases the last run found, best first, leaving out the ones accepted or turned
    down since; only one game's when game is given. [] before the first run."""
    found = surf._load_json(surf.CANDIDATES_FILE, {}).get("candidates") or []
    done = store.phrases()
    return [c for c in found if c["phrase"] not in done and game in (None, c["game"])]


def learn(games=None, videos=surf.VIDEOS_PER_GAME, days=surf.DAYS, people=surf.MIN_PEOPLE,
          fetch=True, known=(), quiet=False):
    """Read Bilibili for new memes now, and return the candidates it found (also saved for
    candidates()). It waits 5 to 7 seconds between requests, so this takes minutes: about
    half a minute per video, 20 minutes for all games. Bilibili refusing or not answering
    stops the reading, not the call.

    games: some of cs2, lol, valorant, dota2 (all by default); videos: videos read per game;
    days: how recent "new" is; people: different people who must use a phrase; fetch=False
    finds phrases again in what was read before, without visiting Bilibili; known: phrases
    your app knows already, left out like the learned ones; quiet: print nothing.
    """
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        found, _ = surf.learn(games, days, videos, people, fetch, known)
    return found


def accept(phrase, meaning="", english="", game=None):
    """Learn a phrase as a meme, usually one of candidates(), and return the meme.

    When the phrase was a candidate in the last run, its games, example lines and videos
    come from there. Accepting it again updates it: a meaning or English gloss given
    replaces the old one.
    """
    key = store.normal(phrase)
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    urls = {bvid: video.get("url") for bvid, video in (last.get("videos") or {}).items()}
    found = [c for c in last.get("candidates") or [] if c["phrase"] == key]
    examples = [example for c in found for example in c["examples"]][:store.EXAMPLES]
    return store.accept(key, meaning, english,
                        games=[c["game"] for c in found] + ([game] if game else []),
                        examples=[example["text"] for example in examples],
                        sources=[urls[e["video"]] for e in examples if urls.get(e["video"])])
