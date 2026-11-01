# MemeLearnerBot
A bot that reads Bilibili by itself for the newest gaming memes (CS2, League of Legends,
Valorant, Dota 2), has a local AI model tell the memes from names and explain them, and
gives other apps an entry point to learn the memes you approve. The first
app meant to use it is [StupidTransilator](https://github.com/HHCreator/StupidTransilator),
the stupid English-Chinese translator; the bot started there and moved here so the two can
be developed separately.

## Usage

Needs Python 3.8 or newer and an internet connection. No extra packages.

```
py surf.py                        # read Bilibili for every game, then list the phrases (~20 min)
py surf.py --games lol valorant   # only some games (cs2, lol, valorant, dota2)
py surf.py --videos 3             # 3 videos per game: a quick test
py surf.py --days 3               # "new" means the last 3 days instead of 7
py surf.py --no-fetch             # don't visit Bilibili; list again from what was read
```

`py surf.py` is short for `py -m memelearner learn`. What it lists are candidates: short
danmu that many people send on their own. Most memes are like that, but so are cheers
(ig加油) and everyday words (笑死), and the bot can't tell them apart by itself. A local AI
model can judge them first (see [The judge](#the-judge)), and you decide:

```
py -m memelearner judge                             # the model judges what the last run found
py -m memelearner candidates                        # what the last run found, with verdicts
py -m memelearner accept 阿一古                       # learn it as a meme, with the judge's meaning
py -m memelearner accept 阿一古 --meaning "..."      # ... or with your own
py -m memelearner reject 笑死 --reason "everyday"    # not a meme: never list it again
py -m memelearner forget 笑死                        # undo accept or reject
py -m memelearner memes                             # the memes learned so far
```

`accept` takes the games, a few example lines and their videos from the last run, and the
judge's meaning and English gloss when it found the phrase a meme (tested or not);
`--meaning` and `--english` give your own instead. The learned memes are kept in
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
memelearner.judge()                     # a local model judges the candidates: minutes too
memelearner.watching()                  # what the model wasn't sure of yet
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
`english` are "" until someone fills them in. A candidate has the phrase, its game, how new
it is (`new`, `rising`, `steady`, `older`, or a `first look` when there's too little to
compare with yet), how many `people` send it on its own and in how many `videos`, the share of the
people using it who send it on its own (`alone`), its `variants` and example lines (see
`find_candidates()` in `memelearner/surf.py`). Once judged it also has `judge`: the
`verdict` (`meme`, `untested`, `not a meme` or `watching`), what `kind` of phrase the model
said it is, its `meaning`, `english` and `literal` meaning, `why`, and the blind `test`; see
`judge()` in `memelearner/judging.py`. `judge()` raises `memelearner.ModelUnavailable` when
Ollama isn't running or doesn't have the model.

**Any language: the command line.** `memes`, `lookup`, `find`, `candidates` and `watching`
take `--json` and print what the Python functions return, in UTF-8. `lookup` exits with 1 when
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
`API_VERSION` 2 (November 2026): candidates are the phrases people send on their own, new or
not, and their `people` counts the people who send them on their own.

**Telling the bot what your app knows.** Phrases your app knows already aren't news to it.
Pass them to `learn(known=...)`, or name your app's word files:
`py surf.py --known G:\SBtranslator\sbtranslator\data` (a .json file: every key and value;
a .txt file: every line; a folder: the .json and .txt files right in it). To use the same
files on every run, put them in the `MEMELEARNER_KNOWN` environment variable, separated by
`;` on Windows.

## How it finds memes

Wikipedia is slow to pick up memes, and live chat only covers the room you listen to, so
the bot reads Bilibili by itself. For each game it finds the busiest videos posted in the
last 7 days, reads their danmu and hot comments, and lists the phrases that many people
send on their own. It only reads, like a visitor who isn't logged in: it never posts, likes or
replies, it waits 5 to 7 seconds between requests (ask faster and Bilibili stops answering
for an hour), and it keeps what it read in `memelearner/.cache/surf/` with every sender
replaced by a short hash. That folder is never committed; set `MEMELEARNER_HOME` to keep it
somewhere else.

**What counts.** Memes are sent on their own. On the first real run (about 100,000 danmu
from LoL and Valorant videos), ig牛逼, 正义必胜, 阿一古 and 屏蔽w were the whole danmu for 95
to 100% of the people who sent them, while champions, teams and game words (奥拉夫, blg,
号种子) almost never were: they sit inside longer lines, which is discussion. So a phrase
is a whole danmu of up to 12 characters that at least 3 different people send, in at least
2 videos, and that at least half of the people who use it at all send on its own (a danmu
that many send on their own, like 这怎么翻的, doesn't count as using 怎么翻的). A danmu
that repeats itself counts once (正义必胜正义必胜 is 正义必胜, 阿一古！！！ is 阿一古), and so
does one with a mood word at its end (怎么翻的 and 怎么翻). Digits only (666), English words
(nice) and single characters don't count. Variants of one pattern, like 千言万语感谢现役 and
千言万语赶紧退役, or 我们是冠军 and 我们是路边, are listed together under the one most
people send. A slang word people only ever write inside longer lines isn't found this way.

**How new.** Each phrase's share of the people writing in the last 7 days is compared with
its share before that: `new` (nobody sent it before), `rising` (at least 3 times as common),
`older` (3 times rarer now) or `steady`, and only by more than chance. `steady` also needs
enough lines on both sides to show a change: when too few people would have sent it on one
side, had it been as common there, it is a `first look` (a phrase only a handful of people
send yet), or `older` when it was more common before (most of a game's danmu are old). The first run has nothing to compare
with, so it also reads as many older videos (posted 30 to 90 days ago) to learn what
people were already saying; after that, each run's lines become the next runs' "before".
Phrases already learned or turned down, everyday ones in
`memelearner/data/surf_common.txt`, and your app's words (`--known`) are left out.

**Finding videos.** Bilibili asks a visitor's search for a captcha more often than not,
and the bot never solves captchas, so it finds videos the way a viewer does: in the related
videos shown beside a video. For each game it starts from a few seed videos, follows the
related videos to the newest busy ones whose titles name the game, and next time carries on
from what it read, so seeds are only needed to get going. It still tries the search once a
run and uses it when Bilibili answers. Each game's title words and seed links are in
`memelearner/data/surf_sources.json`: add teams, players or tournaments to the words to
catch more videos, and a link to a recent video (a BV link or a b23.tv share link) to the
seeds when a game runs dry. The bot says when it found nothing for a game.

The 30 phrases with the most people for each game, with up to 8 example lines each (lines
that use it inside something longer first) and the videos they came from, are saved to
`memelearner/.cache/surf/candidates.json` for `py -m memelearner candidates`.

## The judge

The bot only counts, so it lists cheers (ig加油) and everyday words (笑死) beside the memes.
A local AI model looks through the candidates first: is each a meme, a name (a team, a
player, a champion), a game word (a role, a seed) or an everyday word, and what does a
meme mean?

```
py -m memelearner judge             # judge what the last run found (seconds per phrase)
py surf.py --judge                  # read Bilibili, then judge what it found
py -m memelearner watching          # what the model wasn't sure of yet
py -m memelearner judge --again     # judge everything again, say with another model
```

It needs [Ollama](https://ollama.com) running on this computer, with the model downloaded:
`ollama pull deepseek-r1:14b`. To use another model, add `--model qwen3:14b` or set the
`MEMELEARNER_MODEL` environment variable.

**Why trust it.** A model only knows the memes from before it was trained, and memes change
faster than that, so the judge never goes by what the model remembers:

- It takes up to 16 different lines that use the phrase from what the bot read: the ones
  that say more than the phrase first (主播又在阿一古了), then a few that are just the
  phrase. The model gets half of the longer lines, a few short ones, how many people send
  it on its own, its variants and the titles of its videos. It says what kind of phrase it
  is and, for a meme, writes what it means there, only from what it got.
- Then a blind test: a second question, which never saw those lines, gets the other longer
  lines and two meanings, the model's and the literal one, and says which one each line
  means. The meaning passes when at least 2 lines, and three quarters of the lines that
  picked a meaning, pick it.
- Many memes are only ever sent on their own, so no line shows what they mean. When fewer
  than 3 lines say more than the phrase, its meaning can't be tested: the verdict is
  "untested", with the model's meaning for you to check.
- What the model can't tell, or a meaning that doesn't pass, goes on the watching list.
  Untested and watching phrases are judged again once 50% more people send them.

So "meme" means the meaning fits real lines it wasn't written from. Names, game words and
everyday words come out as "not a meme". The judge never learns or turns down anything
itself: you do, with `accept` and `reject`. Verdicts are kept in
`memelearner/.cache/surf/judge.json`, so a phrase is only asked about again when there's
more to go on.

It doesn't look phrases up online. The first version did, but on a real run 萌娘百科
refused programs that aren't logged in, and 维基百科's search found unrelated pages (奥拉夫
led to Olaf Scholz), which misled the model more than they helped.

## Files

| File | What it is |
|---|---|
| `memelearner/__init__.py` | the entry point other apps use |
| `memelearner/cli.py` | the command line (`py -m memelearner`) |
| `memelearner/surf.py` | the bot: find videos, read them, find the phrases people send on their own |
| `memelearner/judging.py` | the judge: a local model tells memes from names and explains them |
| `memelearner/bilibili.py` | reads Bilibili, read only and anonymous |
| `memelearner/store.py` | the learned and turned-down phrases in `data/memes.json` |
| `memelearner/data/surf_sources.json` | each game's title words and seed videos |
| `memelearner/data/surf_common.txt` | everyday phrases the bot never lists |
| `surf.py` | `py surf.py`, short for `py -m memelearner learn` |

The design notes, in Chinese like the translator's, are in [docs/DESIGN.md](docs/DESIGN.md).

## Tests

`py -m unittest` runs the offline tests; they never visit Bilibili nor ask a real model.
GitHub runs them on each push and pull request, on Windows and Linux.
