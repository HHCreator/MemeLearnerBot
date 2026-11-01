# MemeLearnerBot
A bot that reads Bilibili by itself for the newest gaming memes (CS2, League of Legends,
Valorant, Dota 2), and an entry point other apps use to learn the memes it found. The first
app meant to use it is [StupidTransilator](https://github.com/HHCreator/StupidTransilator),
the stupid English-Chinese translator; the bot started there and moved here so the two can
be developed separately.

## Usage

Needs Python 3.8 or newer and an internet connection. No extra packages.

```
py surf.py                        # read Bilibili for every game, then list new phrases (~20 min)
py surf.py --games lol valorant   # only some games (cs2, lol, valorant, dota2)
py surf.py --videos 3             # 3 videos per game: a quick test
py surf.py --days 3               # "new" means the last 3 days instead of 7
py surf.py --no-fetch             # don't visit Bilibili; list again from what was read
```

`py surf.py` is short for `py -m memelearner learn`. What it lists are candidates: it can't
tell a meme from a team, a player or a champion (金克丝) by itself, so you look through them
and decide:

```
py -m memelearner candidates                        # what the last run found
py -m memelearner accept 阿一古 --meaning "..."      # learn it as a meme
py -m memelearner reject 金克丝 --reason "a champion"  # not a meme: never list it again
py -m memelearner forget 金克丝                      # undo accept or reject
py -m memelearner memes                             # the memes learned so far
```

`accept` takes the games, a few example lines and their videos from the last run, and can
take an English gloss too (`--english`). The learned memes are kept in
`memelearner/data/memes.json`, which is committed: push it, and every app that uses the bot
gets them.

## Using it from another app

Three ways, from the most code to none:

**Python.** Install it with `pip install git+https://github.com/HHCreator/MemeLearnerBot`
(the memes learned up to then come with it), or with `pip install -e G:\MemeLearnerBot` to
use your own copy: the memes you accept, and the candidates of your own runs, right away.
Then:

```python
import memelearner

memelearner.memes()                     # the memes learned so far, newest first
memelearner.memes(game="lol")           # only one game's
memelearner.lookup("绷不住了")            # one learned meme, or None (any case or width)
memelearner.find("这下真的绷不住了")       # the learned memes in a text, in order
memelearner.candidates()                # what the last run found, not looked at yet
memelearner.learn(games=["lol"], known=my_words)  # read Bilibili now: takes minutes
memelearner.accept("绷不住了", meaning="笑得忍不住了")   # a candidate becomes a meme
memelearner.reject("下路")              # not a meme: never list it again
```

A meme is a dict:

```json
{"phrase": "绷不住了",
 "meaning": "笑得忍不住了",
 "english": "can't keep a straight face",
 "games": ["lol"],
 "added": "2026-11-01",
 "examples": ["这下真的绷不住了"],
 "sources": ["https://www.bilibili.com/video/BV17XxP6CE7n"]}
```

`phrase` is lowercase and half-width, the way the bot finds it; `meaning` (Chinese) and
`english` are "" until someone fills them in. A candidate has the phrase, its game, whether
it is `new`, `rising` or a `first look` (nothing to compare with yet), how many people and
videos used it, and example lines (see `find_candidates()` in `memelearner/surf.py`).

**Any language: the command line.** `memes`, `lookup`, `find` and `candidates` take
`--json` and print what the Python functions return, in UTF-8. `lookup` exits with 1 when
the phrase isn't a learned meme.

```
py -m memelearner memes --json
py -m memelearner find "这下真的绷不住了" --json
```

**No code: the file.** `memelearner/data/memes.json` holds the learned memes by phrase
(`{"version": 1, "memes": {"绷不住了": {...}}, "rejected": {...}}`, the same fields as
above). An app can read it straight from GitHub:
https://raw.githubusercontent.com/HHCreator/MemeLearnerBot/main/memelearner/data/memes.json

`version` in the file, and `memelearner.API_VERSION`, only change when a field changes
meaning or goes away. New fields can appear any time, so ignore the ones you don't know.

**Telling the bot what your app knows.** Phrases your app knows already aren't news to it.
Pass them to `learn(known=...)`, or name your app's word files:
`py surf.py --known G:\SBtranslator\sbtranslator\data` (a .json file: every key and value;
a .txt file: every line; a folder: the .json and .txt files right in it). To use the same
files on every run, put them in the `MEMELEARNER_KNOWN` environment variable, separated by
`;` on Windows.

## How it finds memes

Wikipedia is slow to pick up memes, and live chat only covers the room you listen to, so
the bot reads Bilibili by itself. For each game it finds the busiest videos posted in the
last 7 days, reads their danmu and hot comments, and lists the phrases that are new or
rising. It only reads, like a visitor who isn't logged in: it never posts, likes or
replies, it waits 5 to 7 seconds between requests (ask faster and Bilibili stops answering
for an hour), and it keeps what it read in `memelearner/.cache/surf/` with every sender
replaced by a short hash. That folder is never committed; set `MEMELEARNER_HOME` to keep it
somewhere else.

**What counts.** A phrase must be used by at least 3 different people in at least 2 videos:
a whole short danmu (绷不住了), a piece of longer lines that people also write on its own
(not 不住了, which only ever comes after 绷), or a Latin-letter word that isn't English
(yyds). 下饭了 and 下饭吧 count as 下饭.

**New or rising** means its share of the people writing in the last 7 days is at least 3
times its share before that, and by more than chance: with thousands of phrases checked,
some always look like they rose by luck, so a phrase only a handful of people use waits
until more do. The first run has nothing to compare with, so it also reads as many older
videos (posted 30 to 90 days ago) to learn what people were already saying; after that,
each run's lines become the next runs' "before". Phrases already learned or turned down,
everyday ones in `memelearner/data/surf_common.txt`, and your app's words (`--known`) are
left out.

**Finding videos.** Bilibili asks a visitor's search for a captcha more often than not,
and the bot never solves captchas, so it finds videos the way a viewer does: in the related
videos shown beside a video. For each game it starts from a few seed videos, follows the
related videos to the newest busy ones whose titles name the game, and next time carries on
from what it read, so seeds are only needed to get going. It still tries the search once a
run and uses it when Bilibili answers. Each game's title words and seed links are in
`memelearner/data/surf_sources.json`: add teams, players or tournaments to the words to
catch more videos, and a link to a recent video (a BV link or a b23.tv share link) to the
seeds when a game runs dry. The bot says when it found nothing for a game.

The phrases, with up to 8 example lines each and the videos they came from, are saved to
`memelearner/.cache/surf/candidates.json` for `py -m memelearner candidates`.

## Files

| File | What it is |
|---|---|
| `memelearner/__init__.py` | the entry point other apps use |
| `memelearner/cli.py` | the command line (`py -m memelearner`) |
| `memelearner/surf.py` | the bot: find videos, read them, find new and rising phrases |
| `memelearner/bilibili.py` | reads Bilibili, read only and anonymous |
| `memelearner/store.py` | the learned and turned-down phrases in `data/memes.json` |
| `memelearner/data/surf_sources.json` | each game's title words and seed videos |
| `memelearner/data/surf_common.txt` | everyday phrases the bot never lists |
| `surf.py` | `py surf.py`, short for `py -m memelearner learn` |

The design notes, in Chinese like the translator's, are in [docs/DESIGN.md](docs/DESIGN.md).

## Tests

`py -m unittest` runs the offline tests; they never visit Bilibili. GitHub runs them on each
push and pull request, on Windows and Linux.
