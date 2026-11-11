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
py -m memelearner accept 下雨了 --note "..."         # ... or merged with your own note
py -m memelearner reject 笑死 --reason "everyday"    # not a meme: never list it again
py -m memelearner forget 笑死                        # undo accept or reject
py -m memelearner memes                             # the memes learned so far
py -m memelearner report                            # or mark, decide and learn in your browser
```

`accept` takes the games, a few example lines and their videos from the last run, and the
judge's meaning and English gloss when it found the phrase a meme, unless that meaning is
only the model's guess; `--meaning` and `--english` give your own instead. The learned memes are kept in
`memelearner/data/memes.json`, which is committed: push it, and every app that uses the bot
gets them.

`--note` is for what you'd add to a meaning rather than a whole one: "这个梗一般只用在theshy
选手的比赛", or "chovy就是超威，和超威同意了是同一个梗". The model merges it with the meaning
(yours, the one learned before, or the judge's) into one whole meaning that someone who
doesn't know the meme understands, and where they disagree, your note wins. When your note
says the phrase is the same meme as a learned one (和X是同一个梗, 来源同"X"), or a variant of a
pattern of them ("XX同意了"的变体), their meaning is what it means. Every name and quoted word
of your note must be in the merged meaning: the model is asked again when one isn't, and
`accept` says what it still leaves out. With nothing to merge it with, your note is the
meaning, word for word. Your notes are kept as you wrote them, as `notes` in `memes.json`;
the merging is in `memelearner/meanings.py`.

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
 "sources": ["https://www.bilibili.com/video/BV17XxP6CE7n"],
 "variants": []}
```

`phrase` is lowercase and half-width, the way the bot finds it; `meaning` (Chinese) and
`english` are "" until someone fills them in. `variants` are the other ways the same meme
is written (超威同意了 has chovy同意了, 大超同意了 and 三叔不同意: see Variants of one meme
below); `lookup()` and `find()` find a meme by any of them. A meme made from another one
also has a `source`: `{"phrase": "我们是冠军", "how": "learned"}`, where it comes from and
how it is known (`video`, `explanation`, `learned`, or `guess`, see The judge; `note`
when your note in the report says so). A candidate has the phrase, its game, how new
it is (`new`, `rising`, `steady`, `older`, or a `first look` when there's too little to
compare with yet), how many `people` send it on its own and in how many `videos`, the share of the
people using it who send it on its own (`alone`), its `variants` with how many send each,
its `pattern` (`XX同意了`), the `names` in it and who they are, and example lines (see
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
`memelearner/data/surf_common.txt`, your app's words (`--known`) and players' nicknames on
their own (chooovy, see below) are left out.

**Players' nicknames.** Danmu call players by nicknames the model often doesn't know:
Chovy is 超威, 大超 or chooovy, Canyon is 三叔. `memelearner/data/nicknames.txt` says who
each one is, and you can edit it in Notepad, one player a line:

```
Chovy = 超威, 大超, chooovy
Canyon = 三叔, 三姆斯
```

The player is on the left of `=`, the nicknames on the right, apart by commas (`,` `，` `、`
or spaces); the player's own ID counts as one, and `#` starts a note. Case and width don't
matter, nor how many times a letter is written (chooooovy is chovy). A team works the same
way (`IG = 埃及`). A line the bot can't read, or a nickname given to two players, stops it
with the line's number.

**Variants of one meme.** 超威同意了, chovy同意了, 大超同意了 and 三叔不同意 are one meme,
「XX同意了」, and 超威今年真不一样了 and 真不一样了 another, 「XX今年真不一样了」.
`memelearner/variants.py` tells them: two phrases are the same meme when they are the same
once these differences are taken away:

- **who**: a name from the nickname table, or any word in letters (ruler, kiin), is just
  someone, XX. So is a Chinese name of two or three characters that isn't in the table
  (兰子同意了), where at least two other names go in the same place, unless far more
  people send it than any of those (正义必胜 is a meme of its own, not ig必胜 with 正义).
- **left out**: one or two parts of one are left out (真不一样了 is 超威今年真不一样了
  without 超威今年), keeping at least 2 characters that aren't names and leaving out at
  most 2 others besides names (感谢多兰 isn't 千言万语感谢多兰). Two characters alone need
  a name with them for that: wei发狂了 is 发狂了, but 退役 isn't 感谢退役.
- **turned around**: 不 or 没 added (三叔不同意).
- **written longer**: a character written more than once in a row (同意意意了, chooooovy,
  ggg and gg), and mood words at the end. 啊 anywhere else is 阿 (啊一占 is 阿一占).

Anything else keeps them apart: 我们是冠军 and 我们是路边, or 目前不知道怎么赢 and
目前不知道怎么输, mean different things. A phrase that is only a name or a word in letters
(ggg) is nobody's variant. Every phrase in a group shares at least 2 characters that
aren't names with its most sent one: a phrase with two memes in it (阿一古加油, ig加油必胜)
doesn't make them one. Each group is named by its most sent phrase that has a name, with
the name as XX, when at least half as many send it as the most sent one
(「XX今年真不一样了」); else by the most sent one (让二追三).

The bot finds, judges and learns a meme as one group: the candidate is its most sent
phrase, with the others as its `variants`; the judge sees them all, with who the names in
them are; the report shows it as one row, so you decide once; and `memes.json` keeps it as
one entry with its `variants`. A new variant of a learned meme (ruler同意了, once
超威同意了 is learned) says so in the report, and Add puts it into that meme.

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
py -m memelearner live-report                  # live-phrases, judge, explain, then the report
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

`live-report` does the rest of a recording in one go: `live-phrases` (with the same
options), `judge`, `explain` for the memes that need it most, and then the report in your
browser (see [The report](#the-report)).

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
  few short ones, how many people send it on its own, its variants with how many send each,
  its pattern (XX同意了), the names in it with who they are by the nickname table (三叔 =
  Canyon), and the titles of its videos. It says what kind of phrase it is and, for a meme,
  writes what it means there, only from what it got, or no meaning when it can't tell; for
  a meme with variants, what the meme means, not one player's version of it.
- It also gets what you taught the bot before: up to 5 learned memes that look like the
  phrase, with their meanings and your notes, and up to 3 phrases you turned down that look
  like it, with your reasons. Two phrases look alike when a pattern in your notes fits the
  phrase (your note on wei出 has "X出", which kanavi出 fits), or when they share a piece
  (同意了, 加油), the longest first (`related()` in meanings.py); a learned meme it is a
  variant of comes first, marked `same`. So a variant of a learned meme is explained the
  way your notes say, and a cheer like the ones you turned down (blg加油, after ig加油) is
  likely no meme either: you don't correct the same mistake twice. When that changes (a
  meme like it learned, a meaning or note of yours changed, a phrase like it turned down,
  or a nickname in it added to the table), the next `judge` judges the phrase again.
- For a meme made from another one (我们是路边 from 我们是冠军, 千言万语感谢多兰 from
  千言万语感谢现役), it says which one: its **source**, from what it was shown (the learned
  memes, the videos, the lines, and up to 3 phrases of the same run on its template that
  are another meme, `alike`), never one it makes up; a variant of the meme itself is no
  source. It also says how it knows, and the bot checks that: `video`, `explanation` or
  `learned` only when a video, a line, or a learned meme's meaning or your notes name that
  source (your note on 我们是冠军 saying 我们是路边 comes from it counts too); else it is a
  `guess`. When you write in the report where it comes from, that is its source (`note`).
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

## Videos and web pages that explain memes

Bilibili is full of videos made to explain new memes (阿一古是什么梗？), and wikis and Q&A
sites have pages that do. They say what a meme means and where it comes from, which the
danmu rarely do: most memes are sent on their own. So the bot reads them for the memes the
judge found, and the judge goes by what they say:

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
   every explainer it reads, which a visitor gets without a captcha), from a search engine's
   results for "阿一古是什么梗" (Bing, or DuckDuckGo or Baidu when Bing doesn't answer:
   `memelearner/web.py`), and from Bilibili's own search for it. Bilibili's search is tried
   until it asks for a captcha once in a run, then skipped (the bot never solves captchas);
   both searches happen for each phrase at most once a week. When no video names a meme the judge says comes from another one (see Where a meme
   comes from), it looks the same way for videos about that one: 千言万语感谢现役是什么梗
   for 千言万语感谢多兰, 超威同意了 for the pattern XX同意了.
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
5. **Reads the web** when no video explains the phrase: 萌娘百科's entry for it (the page
   its mobile site's search goes to; its API wants a login since November 2026), 百度百科's
   and what 小鸡词典's search page shows, and up
   to 3 of the other pages the search engine found that name it, explainer sites first
   (萌娘百科, 百度百科, 知乎, 贴吧 ...); for the rest, the search results' snippets. Only the
   lines around the phrase go to the model, which sums them up as it does a video, names
   the page it went by and quotes it (a quote that page doesn't contain is dropped). A page
   may be about something else of the same name, so the model is told which game the phrase
   is from. 小鸡词典's own API wants an encrypted header that only its page makes; the bot
   doesn't make one, so it only reads what anyone sees there.
6. **Judges again** the phrases a video or a page explains: the model gets their summaries
   beside the danmu, and a meaning a video or a page backs isn't only a guess any more, so
   `accept` takes it. `candidates` shows which video or page it came from. A video about
   where a meme comes from shows the model that source, marked as such, but doesn't back
   the meme's own meaning.

What it read stays in `memelearner/.cache/surf/explainers.json` and `explainers/`. A phrase
nothing explains is looked up again after a week; `--again` looks up everything again. A
site that wants a captcha, refuses the bot (HTTP 403, 429) or can't be reached twice is left
alone for the rest of the run; there are a few seconds between two requests to the same site.

Which search engines and wikis answer depends on where your computer is, so you can try them:

```
py -m memelearner sources 阿一古 正义必胜 --explain   # ask each one, then look them up as explain does
py -m memelearner sources --file phrases.txt --raw   # phrases one a line; keep every page as it came
```

It writes what each place found to `.cache/surf/sources/` (`--out` for another folder):
`sources.md` to read, beside what `memes.json` says the learned ones mean, `sources.json`,
and `links.json`, the Bilibili videos found for each phrase.

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

## The report

`report` shows the last run's phrases and the judge's answers as one table in your browser,
like the meme reports in Claude Docs, and learns what you decide there:

```
py -m memelearner report          # the last run's phrases (learn or live-phrases), judged
py -m memelearner live-report     # the last live recording: live-phrases, judge, explain, report
```

One row per meme, the likeliest memes first (a meaning that passed its blind test, then
one that lines, videos or web pages explain, the other memes, what the model couldn't tell, and last
names, game words and everyday words): its pattern and the way most people write it
(「XX同意了」, 超威同意了), how many people sent that on its own, two lines that use it, and
the model's answer (its kind and meaning, marked when it is only a guess or failed its
test). Under it are its variants, with how many people sent each, and who the names in
them are (Chovy = 超威、chovy、大超); click a variant that isn't this meme to keep it out
of it (click again to take that back). A row that is a new variant of a learned meme says
which one, and a meme made from another one says where it comes from (来源：「我们是冠军」),
found (a video, a web page, a line or a learned meme says so) or only guessed. Under each
meme's meaning, 解释来源 says where it comes from: the video or web page that explains it
(with its link), a line explaining it (观众弹幕里的解释), the learned meme it is, or else the
model (模型猜的, or 模型猜的，盲测通过 when it passed its blind test); 没查到解释 marks a
meme the bot looked up and found nothing that explains. You fill in, once for the whole
meme:

- **Mark**: how right the answer is: Correct, Half correct, Correct, no meaning, No point,
  Wrong, or I don't know.
- **Decision**: Add (learn it), Not a meme (never list it again) or Not sure yet (next time).
- **My meaning**: what the model's meaning leaves out or gets wrong, a whole meaning,
  that it is the same meme as a learned one (和超威同意了是同一个梗), or where it comes from
  (”阿一古“的变体).

Every choice is saved as you make it, in `memelearner/.cache/surf/review.json`: close the
page or the window, and `report` opens it again as you left it. A Mark goes with the answer
it is on: when the phrase is judged again and the model says something else, its Mark is
empty again. Click **完成** when you're done. The bot then:

- learns the memes marked Add, each as one entry with its variants (not the ones you
  clicked out: those are kept as `apart`, so they aren't taken for it again), with the
  judge's meaning; with My meaning, the model merges the two into one whole meaning in
  which yours wins, as `accept --note` does. A meaning you marked Wrong isn't used, nor a
  guess unless you marked it Correct or Half correct. Where it comes from is kept as its
  `source`: the one My meaning names (”阿一古“的变体, 来源于“搏至无憾”; not a pattern like
  “XX翻了”, nor another way of writing the meme), else one that was found, unless you
  marked the answer Wrong, or a guessed one when you marked it Correct or Half correct.
  Two memes never come from each other: of two guesses that would, neither is kept. A new
  variant of a learned meme goes into that meme, which keeps its meaning (merged with My
  meaning, if you wrote one).
- turns down the ones marked Not a meme, with their variants, and My meaning as the reason.
  The judge is shown it for phrases that look alike, so a cheer like ig加油 isn't taken for
  a meme again.
- leaves Not sure yet and the rest for the next report.
- scores the model from your Marks, as on the first scorecard: Correct 1, Half correct and
  Correct, no meaning ½, No point 0, Wrong −1, I don't know not counted; an answer counts
  once. It shows what it learned and the score on the page and in the window, and stops.

When no model can merge your notes (no internet and no Ollama), it learns nothing and says
so; your choices stay saved, so click 完成 again later. The page is served by the bot on this
computer only (127.0.0.1), through a link with a random token, so no other web page can
change your choices; `--port` picks the port, and `--no-browser` only prints the link.

`live-report` runs `live-phrases` (with its options), `judge`, `explain` for the 10 memes
that need it most (`--explain-top`, `--no-explain`, `--no-audio`) and then `report`; `--model`
is the model for all of it (deepseek-flash, with qwen3:14b standing in when it can't be
reached).

## Running by itself for days

On Windows, the bot can run by itself for some days with Task Scheduler while you're away,
and you look at what it found at the end. Double-click, in the bot's folder:

| | |
|---|---|
| `开始自动跑.bat` | `py -m memelearner auto start`: makes the scheduled tasks that run it for 3 days |
| `看看进度.bat` | `py -m memelearner auto status`: the tasks, each run's summary line and the end of the log |
| `打开报告.bat` | `py -m memelearner auto report`: what every run found, as the report |
| `停止自动跑.bat` | `py -m memelearner auto stop`: ends and deletes the tasks (what was found stays) |

To have it run longer, `py -m memelearner auto extend --days 4` makes the tasks again so that
they stop 4 days after the start instead (no extra run starts: the next daily run is at the
usual time, never a second one on the day it started; `--every 10` changes how often the
live rooms are looked at).

`auto start` makes these tasks for you, which run while you're logged on (the screen may be
locked), in the background without a window:

- **MemeLearnerBot daily** reads the newest LoL and Valorant videos once a day, as `learn`
  does: at 12:00 (`--at 09:30`), the first time at once (`--games` picks the games).
- **MemeLearnerBot live 6**, one for each live room, looks at its room every 10 minutes
  (`--every`). When it's live, it records its danmu until it goes offline, or for 6 hours
  (`--hours`), as `live` does, then finds its phrases as `live-phrases` does. The rooms are
  given as `--rooms 6 21144080:valorant`: a room's number or link, and the game after a
  colon (lol when there's none); room 6, the LPL's, by default. Two rooms live at the same
  time are both recorded. A look starts on time even while the last one still runs, and a
  run whose recording stopped after 6 hours (the room still live) has its task look again at
  once, so while it looks up what it recorded, the next part is already being recorded,
  seconds later (a room whose recording is still being written is left to the run writing
  it): a long match isn't missed in between.

After reading, each run judges the phrases, looks up every meme found (`explain`: the videos
and web pages that explain it, then it is judged again) and keeps them in the pool,
`memelearner/.cache/surf/auto/pool.json`, so the report has what every run found, each
phrase with its newest verdict. A run missed while the computer was off or asleep runs as
soon as it's back; two runs take turns judging and looking up; and the tasks stop by
themselves after 3 days (`--days`). With `--pull`, each daily run first pulls the newest code
of the folder's branch (`git pull --ff-only`), so a fix pushed meanwhile is used from then on.

What the runs print goes to `memelearner/.cache/surf/auto/log.txt`. Each run ends there with
a summary line, `== 总结：…`: what it read and found, how many memes it found explained,
the ones nothing explains, and the problems it ran into: Bilibili's rate limit (412) or
captcha, Bilibili out of reach, the online model out of reach (DeepSeek with no key or no
balance) so that qwen3:14b stood in, no model at all, sites that refused the bot, or an
error (logged with its traceback; the next run goes on). A look at rooms that aren't live
isn't logged, only kept in `rooms.json`, which `auto status` shows. A live run's lines start
with `[直播 6]`, as they come between a daily run's while it records, and its status line
comes every half hour.

`auto report` opens [the report](#the-report) with every phrase the runs found that isn't
learned or turned down yet; its first line sums the days up: the runs, the phrases and memes
found, how many memes were explained, and the problems.

## Files

| File | What it is |
|---|---|
| `memelearner/__init__.py` | the entry point other apps use |
| `memelearner/cli.py` | the command line (`py -m memelearner`) |
| `memelearner/surf.py` | the bot: find videos, read them, find the phrases people send on their own |
| `memelearner/judging.py` | the judge: a model tells memes from names and explains them; compare |
| `memelearner/explainers.py` | finds, reads and sums up the videos and web pages that explain memes |
| `memelearner/web.py` | search engines and wikis, for what explains a meme (read only) |
| `memelearner/live.py` | records a live room's danmu, and finds its phrases |
| `memelearner/auto.py` | runs by itself for days with Windows Task Scheduler (`auto`) |
| `memelearner/meanings.py` | merges your notes with the model's meanings |
| `memelearner/review.py` | the report in your browser: your marks and decisions, and what 完成 learns |
| `memelearner/bilibili.py` | reads Bilibili, read only and anonymous |
| `memelearner/store.py` | the learned and turned-down phrases in `data/memes.json` |
| `memelearner/data/surf_sources.json` | each game's title words and seed videos |
| `memelearner/data/surf_common.txt` | everyday phrases the bot never lists |
| `memelearner/data/nicknames.txt` | players' nicknames, which you edit (`nicknames.py` reads it) |
| `memelearner/variants.py` | tells the variants of one meme (XX同意了) |
| `surf.py` | `py surf.py`, short for `py -m memelearner learn` |
| `开始自动跑.bat` and the three beside it | `auto start`, `stop`, `status` and `report`, to double-click |

The design notes, in Chinese like the translator's, are in [docs/DESIGN.md](docs/DESIGN.md).

## Tests

`py -m unittest` runs the offline tests; they never visit Bilibili nor ask a real model
(fakes of Ollama and of DeepSeek's API answer instead).
GitHub runs them on each push and pull request, on Windows and Linux.
