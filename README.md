# MemeLearnerBot
A bot that reads Bilibili by itself for the newest gaming memes (CS2, League of Legends,
Valorant, Dota 2), has an AI model tell the memes from names and explain them, and
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
(ig加油) and everyday words (笑死), and the bot can't tell them apart by itself. An AI
model can judge them first (see [The judge](#the-judge)), and you decide:

```
py -m memelearner judge                             # the model judges what the last run found
py -m memelearner explain                           # read videos that explain those memes
py -m memelearner candidates                        # what the last run found, with verdicts
py -m memelearner accept 阿一古                       # learn it as a meme, with the judge's meaning
py -m memelearner accept 阿一古 --meaning "..."      # ... or with your own
py -m memelearner reject 笑死 --reason "everyday"    # not a meme: never list it again
py -m memelearner forget 笑死                        # undo accept or reject
py -m memelearner memes                             # the memes learned so far
```

`accept` takes the games, a few example lines and their videos from the last run, and the
judge's meaning and English gloss when it found the phrase a meme, unless that meaning is
only the model's guess; `--meaning` and `--english` give your own instead. The learned memes are kept in
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
memelearner.judge()                     # a model judges the candidates: minutes too
memelearner.explain()                   # read videos that explain those memes: minutes
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
said it is, its `meaning`, `english` and `literal` meaning, whether that meaning is only a
`guess`, the `explanations` it was shown, what the `videos` explaining it said, `why`, the
blind `test`, and the `model` that judged it; see `judge()` in `memelearner/judging.py`.
When the online model can't be reached, qwen3:14b on this computer judges instead (see
Which model below), and sums up the videos for `explain()`; both raise
`memelearner.ModelUnavailable` only when no model can be asked (Ollama isn't running or
doesn't have it either).

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

## Live rooms

A live stream's danmu show new memes as they start, before they reach the replays. The bot
can record a live room the way a viewer who isn't logged in sees it, for hours, on its own:

```
py -m memelearner live https://live.bilibili.com/6 --until 22:55   # record until 10:55 pm
py -m memelearner live 6 --hours 3                                 # ... or for 3 hours
py -m memelearner live-phrases                 # the phrases in the last day's recording, new first
py -m memelearner live-phrases --all           # every recording together
```

`live` never logs in and never sends anything. It joins the room's danmu feed like the
live page does (a WebSocket, a heartbeat every 30 seconds), joins again by itself when the
connection drops, keeps Windows awake, and stops at `--until` (this computer's clock), after
`--hours` or on Ctrl+C. Each danmu goes to `memelearner/.cache/surf/live/ROOM-DATE-TIME.jsonl`
as it comes, and the room, the counts and the first messages as Bilibili sent them (with who
sent them blanked) to a `.json` beside it. It prints a status line a minute.

**Who sent it.** On the first real run (room 6, 7 November 2026) a visitor got each sender's
account number and name in full. The bot keeps neither: as for a replay, it keeps a short hash
of the crc32 of the account number (the code a replay's danmu carry), so people are counted
the same way in both. If Bilibili hides them from visitors again (张***, account 0), each
danmu still carries that crc32. After 100 danmu it says whether the codes came; without them,
senders are guessed from the hidden name, fan medal and level, and the people counts are
rough. Danmu Bilibili sends for gifts and lotteries, and stickers,
are marked and not counted; combos (他们都在说 正义必胜 x120) are kept beside them.

**What counts as a video.** A phrase must be sent in at least 2 videos, so one moment's
spam doesn't count. In a recording each hour counts as a video (about a game and its break;
`--slice 30` for half hours), so a phrase has to come back in a later hour.

`live-phrases` takes the last day's recording (all of it, if the recorder was started
again) and compares it with everything read before it began: the replays of
the same game and earlier recordings. `new` means nobody sent it there. It lists the new,
rising and first-look phrases (at least 5 people each, `--people`), then the 10 most sent old
ones, and saves them as the candidates, so `judge` and `compare` work on them; `learn
--no-fetch` lists the usual ones again. The recordings are part of what the bot has read
from then on, so the judge sees their lines too, and `learn` counts them with the last 7 days.

## The judge

The bot only counts, so it lists cheers (ig加油) and everyday words (笑死) beside the memes.
An AI model looks through the candidates first: is each a meme, a name (a team, a
player, a champion), a game word (a role, a seed) or an everyday word, and what does a
meme mean?

```
py -m memelearner judge             # judge what the last run found (seconds per phrase)
py surf.py --judge                  # read Bilibili, then judge what it found
py -m memelearner watching          # what the model wasn't sure of yet
py -m memelearner judge --again     # judge everything again, say with another model
```

**Which model.** By default it asks deepseek-flash (DeepSeek-V4.1-Flash), one of DeepSeek's
online models, which are far bigger than a model that fits on a graphics card. Of five
models compared on the 60 phrases of a real run, it scored best (see Picking a model
below). It needs an API key:

1. Make an account on [DeepSeek's platform](https://platform.deepseek.com), top it up (a few
   dollars go a long way, see below) and create an API key.
2. Put the key in an environment variable on your computer, never in a file in this folder,
   which could end up on GitHub: run `setx DEEPSEEK_API_KEY sk-...` once, then open a new
   terminal.

When DeepSeek can't be reached (no internet, no key, a key it refuses, or no balance left),
the judge switches by itself to qwen3:14b, the best of the compared models that runs on this
computer, and says so: the run ends with which model judged, and `candidates` marks the
verdicts a model other than the default gave ("judged by qwen3:14b"). Once DeepSeek can be
reached again, it judges those phrases again. For that, have
[Ollama](https://ollama.com) running and run `ollama pull qwen3:14b` once; without it, a
judge run that can't reach DeepSeek stops and says why.

Another model: add `--model deepseek/deepseek-v4-pro` (DeepSeek-V4-Pro: bigger, about 4 times
the price), or a model on this computer through Ollama, like `--model qwen3:14b`. A local
model is free and works offline, but it knows far less slang. The `MEMELEARNER_MODEL`
environment variable changes the default.

The judge sends DeepSeek the lines and video titles it shows the model, never who sent
them, and asks without DeepSeek's "thinking", which is slower and costs more. `explain`
sends it what it read of each explainer video: the title, description, tags, subtitles or
what Whisper heard, and some hot comments and danmu, again never who wrote them. Judging the
60 phrases of a run takes about 100 answers and 60,000 tokens: about 1.3 US cents with
Flash and 5 with Pro at full price (2026-11-02). The prices are on DeepSeek's site, and they
halve outside 9:00-12:00 and 14:00-18:00 Beijing time on weekdays. The judge says how many
tokens it used.

**Picking a model.** `compare` judges the same phrases with several models, from the same
lines, and writes each phrase with every model's verdict to
`memelearner/.cache/surf/compare.md`, to read side by side. The verdicts `judge` keeps don't
change. The models are asked one after another, so Ollama loads each once. While it runs
(and while `judge` runs) Windows doesn't go to sleep, and it saves after every phrase: if a
comparison stops anyway, run the same command with `--add` and it carries on where it stopped.

```
py -m memelearner compare deepseek-r1:14b qwen3:14b qwen3.5:9b
py -m memelearner compare deepseek/deepseek-flash --add   # add a model to the last comparison
py -m memelearner compare qwen3:14b deepseek/deepseek-flash --top 10   # each game's 10 best
```

The comparison behind the default (2026-11-02): five models judged the 60 phrases of a run
(LoL and Valorant), and each answer was marked by hand on 59 of them (nobody was sure what
屏蔽w is): right (1 point: the right kind of phrase, and for a meme the right meaning), half
right (½: the right kind, the meaning partly wrong), a meme without a meaning (½: called a
meme, but with no meaning, or one the blind test threw out), no point (0: it couldn't tell
the kind) and wrong (−1: the wrong kind, or a wrong meaning).

| Model | Points | Without the ½ for a meme without a meaning | Right | Wrong |
|---|--:|--:|--:|--:|
| deepseek-flash | 49 | 46.5 | 43 | 1 |
| deepseek-v4-pro | 44.5 | 39 | 36 | 1 |
| qwen3:14b | 22.5 | 8 | 14 | 9 |
| qwen3.5:9b | 21 | −2 | 3 | 5 |
| deepseek-r1:14b | 3.5 | −3.5 | 12 | 21 |

The local models filed memes like 阿一古 and 得分通道关闭 as names or game words, and
qwen3.5:9b said "meaning unknown" for 45 of the 60 phrases.

**Why trust it.** A model only knows the memes from before it was trained, and memes change
faster than that, so the judge never goes by what the model remembers:

- It takes up to 16 different lines that use the phrase from what the bot read: first up
  to 4 where people explain it (出自…, 谐音…, 阿一古的意思是…; questions like 是什么梗 and
  opinions like 我的意思是 don't count), then the others that say more than the phrase
  (主播又在阿一古了), then a few that are just the phrase. The model gets the explaining
  lines, half of the other longer lines (all of them when there are too few to test on), a
  few short ones, how many people send it on its own, its variants and the titles of its
  videos. It says what kind of phrase it is and, for a meme, writes what it means there,
  only from what it got, or no meaning when it can't tell.
- Then a blind test: a second question, which never saw those lines, gets the other longer
  lines and two meanings, the model's and the literal one, and says which one each line
  means. The meaning passes when at least 2 lines, and three quarters of the lines that
  picked a meaning, pick it. The test only runs with at least 4 lines to test on: on the
  real run, tests on 2 or 3 lines threw out more right meanings than wrong ones.
- Many memes are only ever sent on their own, so few lines show what they mean. When fewer
  than 8 lines say more than the phrase (so fewer than 4 would be left to test on), its
  meaning isn't tested: the verdict is "untested". If some lines explain it, or a video
  does (see Videos that explain memes), the meaning comes from them, and `candidates`
  shows one of them. If none does, the meaning is only the
  model's guess: it is shown as one, and `accept` doesn't take it, so give the meaning
  yourself with `--meaning`.
- What the model can't tell, or a meaning that doesn't pass, goes on the watching list.
  Untested and watching phrases are judged again once 50% more people send them.

So "meme" means the meaning fits real lines it wasn't written from. Names, game words and
everyday words come out as "not a meme". The judge never learns or turns down anything
itself: you do, with `accept` and `reject`. Verdicts are kept in
`memelearner/.cache/surf/judge.json`, so a phrase is only asked about again when there's
more to go on.

It doesn't look phrases up in online encyclopedias. The first version did, but on a real
run 萌娘百科 refused programs that aren't logged in, and 维基百科's search found unrelated
pages (奥拉夫 led to Olaf Scholz), which misled the model more than they helped. Videos made
to explain a meme work better: see the next part.

## Videos that explain memes

Bilibili is full of videos made to explain new memes (阿一古是什么梗？). They say what a meme
means and where it comes from, which the danmu rarely do: most memes are sent on their own.
So the bot reads them for the memes the judge found, and the judge goes by what they say:

```
py -m memelearner explain                    # the 10 memes whose meaning is least sure
py -m memelearner explain 阿一古 怎么翻的      # only these
py -m memelearner explain 阿一古 --video https://b23.tv/xxxx   # this video explains it
py surf.py --explain                         # read Bilibili, judge, read explainers, judge again
```

For each phrase it:

1. **Finds** videos whose titles name the phrase (or a variant) and look made to explain
   something (是什么梗, 什么意思, 出处, 由来). They come from the links you give, from the
   explainer videos the bot saw in related-video lists (the collector's and those beside
   every explainer it reads, which a visitor gets without a captcha), and from Bilibili's
   search for "阿一古是什么梗". The search is tried until Bilibili asks for a captcha once in
   a run, then skipped (the bot never solves captchas), and for each phrase at most once a
   week.
2. **Reads** up to 2 of them per phrase, each video once, as any visitor who isn't logged in
   sees them: the page (title, description, tags, the subtitles a visitor gets, related
   videos), the hot comments and the danmu. That is 3 or 4 requests, 5 to 7 seconds apart.
3. **Hears** a video when it has no subtitles, if Whisper is installed (see below): it saves
   the smallest audio stream (about half a megabyte a minute, only for videos up to 10
   minutes), a Whisper model on this computer writes down what is said, and the audio is
   deleted. Nothing is uploaded anywhere.
4. **Sums up** each video with the judge's model (qwen3:14b when DeepSeek can't be
   reached, as for the judge): does it explain the phrase, what does it say the phrase
   means and where it comes from, and a line quoted from it (a quote that isn't in the
   video is dropped). The model is told to write only what the video says.
5. **Judges again** the phrases a video explains: the model gets the videos' summaries
   beside the danmu, and a meaning a video backs isn't only a guess any more, so `accept`
   takes it. `candidates` shows which video it came from.

What it read stays in `memelearner/.cache/surf/explainers.json` and `explainers/`. A phrase
no video explains is looked up again after a week; `--again` looks up everything again.

**Hearing the videos (optional).** Most explainer videos are spoken, and Bilibili only gives
its AI subtitles to logged-in viewers, which the bot never is. To hear them, install Whisper:

```
py -m pip install faster-whisper
py -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12   # to use the graphics card
```

The first video heard downloads the Whisper model, `large-v3-turbo` (about 1.6 GB), into
`memelearner/.cache/whisper`. It uses the graphics card when it can, else the processor (a
few times slower). Set `MEMELEARNER_WHISPER` for another model (`medium`, `small`), or add
`--no-audio` to read only the pages, comments and danmu. Without Whisper, that's all it does.

**Why not BiliNote or SummifyAI.** Both (MIT) turn one video you give them into notes, and
neither finds the videos for a phrase. BiliNote is a whole app (web pages, a database, a
desktop client) that reads Bilibili's subtitles with a logged-in viewer's cookie and
downloads with yt-dlp and ffmpeg; SummifyAI needs PyTorch and an online model. The bot does
what they do for audio, with the same Whisper engine as BiliNote, and stays free of extra
packages unless you want it to hear.

## Files

| File | What it is |
|---|---|
| `memelearner/__init__.py` | the entry point other apps use |
| `memelearner/cli.py` | the command line (`py -m memelearner`) |
| `memelearner/surf.py` | the bot: find videos, read them, find the phrases people send on their own |
| `memelearner/judging.py` | the judge: a model tells memes from names and explains them; compare |
| `memelearner/explainers.py` | finds, reads and sums up the videos that explain memes |
| `memelearner/bilibili.py` | reads Bilibili, read only and anonymous |
| `memelearner/store.py` | the learned and turned-down phrases in `data/memes.json` |
| `memelearner/data/surf_sources.json` | each game's title words and seed videos |
| `memelearner/data/surf_common.txt` | everyday phrases the bot never lists |
| `surf.py` | `py surf.py`, short for `py -m memelearner learn` |

The design notes, in Chinese like the translator's, are in [docs/DESIGN.md](docs/DESIGN.md).

## Tests

`py -m unittest` runs the offline tests; they never visit Bilibili nor ask a real model
(fakes of Ollama and of DeepSeek's API answer instead).
GitHub runs them on each push and pull request, on Windows and Linux.
