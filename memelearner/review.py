"""The meme report as a page in your browser: the last run's phrases with the judge's
answers, as one table you work in (like the reports in Claude Docs, 2026-11-08), and what
the bot learns when you click 完成.

    py -m memelearner report        open it for the last run's phrases
    py -m memelearner live-report   find the phrases of the last live recording, judge them,
                                    read the videos that explain them, then open it

One row per meme, the likeliest memes first: its pattern and the way most people write it
(「XX同意了」, 超威同意了), how many people sent that on its own, two lines that use it, the
model's answer (its kind and meaning, and whether that is only a guess or failed its blind
test) with a Mark, then a Decision and My meaning, all empty until you fill them in. Under
it, its variants (chovy同意了, 三叔不同意: variants.py) with how many sent each, and who the
names in them are (超威 = Chovy, by data/nicknames.txt); click a variant that isn't this
meme to keep it out. A meme that is a new variant of a learned one (ruler同意了, once
超威同意了 is learned) says which, and one made from another meme says where it comes from
(我们是路边: 我们是冠军), and whether a video, a line or a learned meme says so or the model
only guessed. Every change is saved at once, in surf/review.json, so
closing the page loses nothing: report opens it again as you left it. A Mark goes with the
answer it is on: when the model answers something else for the phrase later, its Mark is
empty again.

完成 then:

- Add learns the meme (store.accept()), as one entry with its variants, with the judge's
  meaning and where it comes from (a source the model only guessed only when you marked
  its answer Correct or Half correct; none when you marked it Wrong). With My meaning, the
  model merges the two into one whole meaning in which yours wins (meanings.py). A meaning
  you marked Wrong isn't used, nor one that is only a guess unless you marked it Correct or
  Half correct. When My meaning says where the meme comes from (”阿一古“的变体), that is
  its source, found in your note. Two memes don't come from each other: of two guesses
  that would, neither is kept. A new variant of a learned meme joins that meme instead,
  which keeps its meaning (merged with My meaning, if you wrote one).
- Not a meme turns it down with its variants (store.reject()): the bot doesn't list them
  again, and My meaning is kept as the reason, which the judge is shown for phrases like
  it (judging.learned_like()).
- Not sure yet, or no decision, leaves it for the next report.
- The Marks score the model as on the first scorecard: Correct 1, Half correct ½, Correct,
  no meaning ½, No point 0, Wrong −1; I don't know isn't counted. Each answer counts once.

The page is only served to this computer (127.0.0.1), and only through the link the bot
opens, which holds a random token, so another web page can't change your choices.
"""

import collections
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser

from . import judging, meanings, store, surf

MARKS = ("Correct", "Half correct", "Correct, no meaning", "No point", "Wrong", "I don't know")
POINTS = {"Correct": 1, "Half correct": 0.5, "Correct, no meaning": 0.5, "No point": 0,
          "Wrong": -1}  # I don't know isn't counted
DECISIONS = ("Add", "Not a meme", "Not sure yet")
TAKE_GUESS = ("Correct", "Half correct")  # the Marks that let a guessed meaning be learned
SHOWN = 2  # example lines on the page (a learned meme keeps store.EXAMPLES)
LINE = 60  # longest line used as an example
NOTE_LENGTH = 1000  # characters kept of My meaning
SHARED = ("【", "http", "哔哩哔哩", "bilibili")  # a shared video's title, not a danmu line
VERSION = 1  # review.json's format; choices in an older one are dropped
ORDER = ("顺序：盲测通过的梗，有句子或视频解释的梗，别的梗（意思是猜的、没有意思，或者没通过盲测），"
         "看不出是什么的，最后是名字、游戏术语和普通的话；同一类里单独发的人多的在前。People = "
         "单独发它的不同的人数，变体后面的数字也是。Mark 的分数和第一次评分一样：Correct 1，"
         "Half correct 0.5，Correct, no meaning 0.5，No point 0，Wrong −1；I don't know 不算。")


def review_file():
    return os.path.join(surf.SURF_DIR, "review.json")


def _answer(verdict):
    """Which answer a Mark is on: the model, and what it said."""
    said = [verdict.get(k) for k in ("model", "verdict", "kind", "meaning", "guess")]
    return hashlib.sha1(json.dumps(said, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _group(verdict):
    """Where a phrase goes in the report: 0 memes whose meaning passed its blind test, 1
    memes whose meaning lines or videos explain, 2 the other memes (a guessed meaning, none,
    or one that failed its test), 3 what the model couldn't tell, 4 names, game words and
    everyday words, 5 phrases not judged yet."""
    said = verdict.get("verdict")
    if not said:
        return 5
    if said == "meme":
        return 0
    if verdict.get("kind") == "meme":
        sure = said == "untested" and verdict.get("meaning") and not verdict.get("guess")
        return 1 if sure else 2
    return 3 if said == "watching" else 4


def _examples(lines, phrase):
    """Up to store.EXAMPLES different (line, video) of lines, in the order the judge was
    shown them: the ones that say more than phrase first, then phrase on its own; no titles
    of shared videos."""
    usable = [(" ".join(line.split()), video) for line, video in lines]
    usable = [(line, video) for line, video in usable if line and len(line) <= LINE
              and not any(word in line.lower() for word in SHARED)]
    longer = [(line, video) for line, video in usable if store.normal(line) != phrase]
    picked, seen = [], set()
    for line, video in longer + usable:
        if store.normal(line) not in seen:
            seen.add(store.normal(line))
            picked.append((line, video))
        if len(picked) == store.EXAMPLES:
            break
    return picked


def rows():
    """The report's rows, one for each meme: the last run's candidates not learned or turned
    down yet, the likeliest memes first (_group()), and the most people first within a
    group. A row is {"key": "game:phrase", "phrase": the way most people write it, "pattern":
    the meme's, a name in it as XX (XX同意了), "game", "people", "variants": [{"phrase",
    "people"}], its other ways of writing it (variants.py), "names": [{"name", "player"}],
    the names in them and who they are by the nickname table (judging.names_in()),
    "learned": {"phrase", "meaning", "english"} of the learned meme it is a new variant of,
    or None, "examples": up to store.EXAMPLES lines that use it, "sources": the links of
    their videos, "verdict", "kind", "meaning", "english", "why", "guess", "source" (where
    it comes from: {"phrase", "how"} or None): the judge's (judging.judge()), "test": the
    blind test it failed, or None, "model": which model judged it ("" when none did yet),
    "answer": which answer a Mark is on, "group"}."""
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    done = store.phrases()
    found = [c for c in last.get("candidates") or [] if store.normal(c["phrase"]) not in done]
    memes = store.load()["memes"]
    same = judging.same_memes(found, {"memes": memes}) if found else {}
    wanted = {(c["game"], c["phrase"]) for c in found}
    used = judging.uses(wanted) if wanted else {}
    shown = judging.usage_lines(wanted, found=used)  # the lines the judge saw, in its order
    urls = {video: entry.get("url") for video, entry in (last.get("videos") or {}).items()}
    made = []
    for c in found:
        where = (c["game"], c["phrase"])
        videos = {}  # line, as usage_lines() writes it: the video it is from
        for _, _, video, text in used.get(where) or []:
            videos.setdefault(" ".join(text.split())[:judging.LINE_LENGTH], video)
        lines = [(line, videos.get(line)) for line in shown.get(where) or []] or [
            (e.get("text", ""), e.get("video")) for e in c.get("examples") or []]
        examples = _examples(lines, c["phrase"])
        sources = []
        for _, video in examples:
            if urls.get(video) and urls[video] not in sources:
                sources.append(urls[video])
        verdict = c.get("judge") or {}
        key = f"{c['game']}:{c['phrase']}"
        learned = (same.get(key) or [None])[0]
        made.append({
            "key": key, "phrase": c["phrase"], "pattern": c.get("pattern") or c["phrase"],
            "game": c["game"], "people": c.get("people") or 0,
            "variants": [{"phrase": v["phrase"], "people": v.get("people") or 0}
                         for v in c.get("variants") or [] if store.normal(v["phrase"]) not in done],
            "names": judging.names_in(c),
            "learned": {"phrase": learned, "meaning": memes[learned].get("meaning", ""),
                        "english": memes[learned].get("english", "")} if learned else None,
            "source": verdict.get("source"),
            "examples": [line for line, _ in examples],
            "sources": sources, "verdict": verdict.get("verdict", ""),
            "kind": verdict.get("kind", ""), "meaning": verdict.get("meaning", ""),
            "english": verdict.get("english", ""), "why": verdict.get("why", ""),
            "guess": bool(verdict.get("guess")),
            "test": verdict.get("test") if verdict.get("verdict") == "watching" else None,
            "model": verdict.get("model", ""), "answer": _answer(verdict) if verdict else "",
            "group": _group(verdict)})
    made.sort(key=lambda row: (row["group"], -row["people"]))
    return made


def about():
    """What the report is about, in a line: the live recordings, or the run."""
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    live = last.get("live")
    if live:
        return (f"直播录制 {'、'.join(live.get('recordings') or [])}："
                f"{live.get('danmu', 0)} 条弹幕，{live.get('senders', 0)} 个人发的")
    return f"{last.get('made', '')} 那次读 B 站找到的说法"


# -- what you chose ---------------------------------------------------------------------

def _load():
    state = surf._load_json(review_file(), {})
    if state.get("version") != VERSION:
        state = {}
    return {"version": VERSION, "rows": dict(state.get("rows") or {}),
            "scored": dict(state.get("scored") or {}), "done": list(state.get("done") or [])}


def _save(state):
    path = review_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)  # all at once: a click never leaves half a file


def _variants(row):
    return [variant["phrase"] for variant in row.get("variants") or []]


def chosen(rows, state=None):
    """What you chose for each row so far: {key: {"mark", "decision", "note", "apart": the
    variants you said aren't this meme}}, "" (or []) for what you didn't choose; a Mark only
    while the answer it is on stands."""
    state = _load() if state is None else state
    picked = {}
    for row in rows:
        saved = state["rows"].get(row["key"]) or {}
        apart = saved.get("apart") or []
        picked[row["key"]] = {
            "mark": saved.get("mark", "") if saved.get("answer") == row["answer"] else "",
            "decision": saved.get("decision", ""), "note": saved.get("note", ""),
            "apart": [phrase for phrase in _variants(row) if phrase in apart]}
    return picked


def choose(rows, key, field, value):
    """Save one choice: field is "mark", "decision", "note" (My meaning) or "apart" (the
    variants you said aren't this meme, a list of them), and value "" (or []) takes it back.
    Raises ValueError for a row or a choice the report doesn't have."""
    row = next((row for row in rows if row["key"] == key), None)
    allowed = {"mark": MARKS, "decision": DECISIONS}
    if row is None or field not in ("mark", "decision", "note", "apart") or \
            not isinstance(value, list if field == "apart" else str):
        raise ValueError(f"the report has no {field!r} for {key!r}")
    if field in allowed and value and value not in allowed[field]:
        raise ValueError(f"{value!r} isn't a {field}")
    if field == "apart":
        if not all(isinstance(phrase, str) for phrase in value) or \
                set(value) - set(_variants(row)):
            raise ValueError(f"{key!r} has no such variants: {value!r}")
        value = [phrase for phrase in _variants(row) if phrase in value]  # each once
    state = _load()
    saved = state["rows"].setdefault(key, {})
    saved[field] = value[:NOTE_LENGTH] if field == "note" else value
    if field == "mark":
        saved["answer"] = row["answer"]
    saved["changed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(state)


# -- 完成 --------------------------------------------------------------------------------

def _base(row, mark):
    """The meaning and English gloss the bot learns a row with, before your note: a learned
    meme's own, for a new variant of one (it stays that meme's meaning), else the judge's,
    as far as your Mark lets the bot use them."""
    if row.get("learned"):
        return row["learned"]["meaning"], row["learned"]["english"]
    if mark == "Wrong" or row["guess"] and mark not in TAKE_GUESS:
        return "", ""
    return row["meaning"], row["english"]


def _source(row, mark):
    """Where a row's meme comes from, as far as your Mark lets the bot keep it: one a video,
    a line or a learned meme names, unless you marked the answer Wrong; one the model only
    guessed, when you marked it Correct or Half correct. None for a new variant of a learned
    meme, which keeps its own."""
    source = row.get("source")
    if not source or row.get("learned") or mark == "Wrong" or \
            source.get("how") == "guess" and mark not in TAKE_GUESS:
        return None
    return source


def _note_source(row, note, known):
    """Where your note on a row says its meme comes from (”阿一古“的变体): {"phrase", "how":
    "note"}, or None (meanings.source_in(); known: the learned memes and the other rows)."""
    own = [_meme_of(row)] + _variants(row)
    found = meanings.source_in(row["phrase"], note, known, own)
    return {"phrase": found, "how": "note"} if found else None


def _untangle(rows, sources, memes):
    """Take out the sources that go round: when two memes would each come from the other
    (把光借给ig and 把力量借给ig, each the model's guess for the other), a guess gives way
    to one found, and two guesses both go; so does a guess that a learned meme comes from
    this one. sources: {row key: source or None} of the rows learned, changed in place."""
    owner = store.owners(memes)
    ways = {row["key"]: {store.normal(p) for p in [row["phrase"], _meme_of(row)] +
                         _variants(row)} for row in rows}
    row_of = {phrase: key for key, phrases in ways.items() for phrase in phrases}
    dropped = set()
    for key, mine in sources.items():
        if not mine:
            continue
        other = row_of.get(store.normal(mine["phrase"]))
        theirs = sources.get(other) if other not in (None, key) else \
            (memes.get(owner.get(store.normal(mine["phrase"]), "")) or {}).get("source")
        if not theirs or store.normal(theirs.get("phrase")) not in ways[key]:
            continue
        if mine["how"] == "guess":
            dropped.add(key)
        if other not in (None, key) and theirs["how"] == "guess":
            dropped.add(other)
    for key in dropped:
        sources[key] = None


def _meme_of(row):
    """The meme a row is learned as: the learned meme it is a new variant of, or itself."""
    return row["learned"]["phrase"] if row.get("learned") else row["phrase"]


def finish(rows, model=None):
    """What 完成 does (see the top of this file). model merges your notes (default: the
    judge's, with judging.LOCAL_MODEL standing in when an online one can't be reached).

    Returns {"done": when, "added": [{"phrase": the meme as learned, "meaning", "from":
    "merged" (the model merged your note), "note" (your note is the meaning, word for word),
    "judge" (the judge's meaning), "learned" (the meaning it was learned with before) or ""
    (no meaning), "model": the model that merged, "missing": names and quoted words of your
    note the meaning leaves out, "variants": the variants it got, "learned": whether it was
    learned before, "source": where it comes from, as kept (_source())}], "rejected": [{"phrase", "reason", "variants": turned down with it}],
    "left": [{"phrase", "decision"}], "score": {model: {"points", "answers": the answers
    counted, "marks": {mark: how many}}}}. Raises judging.ModelUnavailable, learning
    nothing, when your notes need a model to merge them and none can be asked."""
    state = _load()
    picked = chosen(rows, state)
    decided = lambda row: picked[row["key"]]["decision"]
    note = lambda row: " ".join(picked[row["key"]]["note"].split())
    base = lambda row: _base(row, picked[row["key"]]["mark"])
    kept = lambda row: [p for p in _variants(row) if p not in picked[row["key"]]["apart"]]
    adds = [row for row in rows if decided(row) == "Add"]
    turned = [row for row in rows if decided(row) == "Not a meme"]
    items = [dict(zip(("meaning", "english"), base(row)), phrase=_meme_of(row), note=note(row))
             for row in adds if note(row)]
    merged = {result["phrase"]: result for result in meanings.merge_all(items, model)} \
        if items else {}  # first, so nothing is learned when no model can merge
    memes = store.load()["memes"]
    known = list(dict.fromkeys(list(store.owners(memes)) + [row["phrase"] for row in rows]))
    sources = {row["key"]: _note_source(row, note(row), known) or
               _source(row, picked[row["key"]]["mark"]) for row in adds}
    _untangle(adds, sources, memes)
    added = []
    for row in adds:
        meaning, english = base(row)
        result = merged.get(store.normal(_meme_of(row)))
        if result:
            meaning, english = result["meaning"], result["english"]
        joining = ([row["phrase"]] if row.get("learned") else []) + kept(row)
        meme = store.accept(_meme_of(row), meaning, english, games=[row["game"]],
                            examples=row["examples"], sources=row["sources"], note=note(row),
                            variants=joining, apart=picked[row["key"]]["apart"],
                            source=sources[row["key"]])
        added.append({"phrase": meme["phrase"], "meaning": meaning,
                      "from": ("merged" if result["model"] else "note") if result else
                      (("learned" if row.get("learned") else "judge") if meaning else ""),
                      "model": result["model"] if result else "",
                      "missing": result["missing"] if result else [],
                      "variants": joining, "learned": bool(row.get("learned")),
                      "source": meme["source"]})
    rejected = []
    for row in turned:
        reason = note(row) or f"not a meme (report {time.strftime('%Y-%m-%d')})"
        for phrase in [row["phrase"]] + kept(row):
            store.reject(phrase, reason)
        rejected.append({"phrase": row["phrase"], "reason": reason, "variants": kept(row)})
    gone = {row["key"] for row in adds + turned}
    left = [{"phrase": row["phrase"], "decision": decided(row)}
            for row in rows if row["key"] not in gone]
    score = {}
    for row in rows:
        mark = picked[row["key"]]["mark"]
        if not mark or state["scored"].get(row["key"]) == row["answer"]:
            continue  # not marked, or counted on an earlier 完成
        counted = score.setdefault(row["model"] or "not judged",
                                   {"points": 0, "answers": 0, "marks": {}})
        counted["marks"][mark] = counted["marks"].get(mark, 0) + 1
        if mark in POINTS:
            counted["points"] += POINTS[mark]
            counted["answers"] += 1
        state["scored"][row["key"]] = row["answer"]
    for key in gone:
        state["rows"].pop(key, None)
    result = {"done": time.strftime("%Y-%m-%d %H:%M"), "added": added, "rejected": rejected,
              "left": left, "score": score}
    state["done"].append({"done": result["done"], "added": [a["phrase"] for a in added],
                          "rejected": [r["phrase"] for r in rejected], "left": len(left),
                          "score": score})
    _save(state)
    return result


def summary(result):
    """finish()'s result as lines for the console."""
    added, rejected, left = result["added"], result["rejected"], result["left"]
    lines = [f"Learned {len(added)} meme{'' if len(added) == 1 else 's'}, turned down "
             f"{len(rejected)}, left {len(left)} for the next report."]
    whose = {"merged": "merged with your note", "note": "your note", "judge": "the judge's",
             "learned": "as learned before",
             "": "no meaning yet: add one with py -m memelearner accept PHRASE --note ..."}
    for a in added:
        lines.append(f"  + {a['phrase']}: {a['meaning'] or '-'} ({whose[a['from']]})")
        if a.get("variants"):
            lines.append(f"    {'new variants' if a.get('learned') else 'variants'}: "
                         f"{', '.join(a['variants'])}")
        if a.get("source"):
            lines.append(f"    comes from {a['source']['phrase']} ({a['source']['how']})")
        if a["missing"]:
            lines.append(f"    check it: it leaves out {', '.join(a['missing'])} of your note")
    for r in rejected:
        lines.append(f"  - {r['phrase']} ({r['reason']})")
        if r.get("variants"):
            lines.append(f"    with its variants: {', '.join(r['variants'])}")
    for model, counted in result["score"].items():
        marks = ", ".join(f"{mark} {n}" for mark, n in counted["marks"].items())
        lines.append(f"{model}: {counted['points']:g} points for {counted['answers']} "
                     f"answers ({marks})")
    return lines


# -- the page ---------------------------------------------------------------------------

def _short(model):
    return model.rsplit("/", 1)[-1] if model else ""


def page(rows, picked, token, line=""):
    """The report's HTML, with its rows and your choices in it."""
    models = collections.Counter(row["model"] for row in rows if row["model"])
    model = judging.MODEL if judging.MODEL in models else \
        (models.most_common(1)[0][0] if models else "")  # the column's; others are marked
    data = {"token": token, "about": line, "model": model, "name": _short(model),
            "marks": MARKS, "decisions": DECISIONS, "shown": SHOWN, "order": ORDER,
            "picked": picked,
            "rows": [{k: row.get(k) for k in ("key", "phrase", "pattern", "people", "variants",
                                              "names", "learned", "examples", "kind",
                                              "meaning", "why", "guess", "source", "test",
                                              "model")}
                     for row in rows]}
    text = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")  # never ends the tag
    return PAGE.replace("__DATA__", text)


class Report:
    """The page, served on this computer until 完成 is clicked: report.url is its link."""

    def __init__(self, rows, model=None, port=0, line=""):
        self.rows, self.model, self.line = rows, model, line
        self.token, self.result, self.lock = secrets.token_urlsafe(16), None, threading.Lock()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True
        port = self.server.server_address[1]
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.url = f"http://127.0.0.1:{port}/?t={self.token}"

    def run(self, browser=True):
        """Serve the page (opening it in the browser) until 完成 is clicked, or Ctrl+C.
        Returns finish()'s result, or None when it was stopped before."""
        opened = browser and webbrowser.open(self.url)
        print(("The report is open in your browser: " if opened else
               "Open the report in your browser: ") + self.url)
        print("Every choice is saved as you make it. Click 完成 at the bottom when you're "
              "done; or close this window and run py -m memelearner report to go on later.")
        try:
            self.server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            print(f"Stopped. Your choices so far are saved in {review_file()}.")
        finally:
            self.server.server_close()
        return self.result

    def _handler(self):
        report = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # no line in the console for every click
                pass

            def reply(self, code, body, kind="application/json"):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def error(self, code, text):
                self.reply(code, json.dumps({"error": text}, ensure_ascii=False))

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                if (self.headers.get("Host") not in report.hosts or url.path != "/"
                        or urllib.parse.parse_qs(url.query).get("t") != [report.token]):
                    return self.reply(403, "Open the link the bot printed in its window.",
                                      "text/plain")
                with report.lock:
                    picked = chosen(report.rows)
                self.reply(200, page(report.rows, picked, report.token, report.line),
                           "text/html")

            def do_POST(self):
                if (self.headers.get("Host") not in report.hosts
                        or self.headers.get("X-Token") != report.token):
                    return self.error(403, "not the bot's page")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    asked = json.loads(self.rfile.read(length).decode("utf-8") or "{}") \
                        if 0 <= length <= 100_000 else None
                except (ValueError, UnicodeDecodeError):
                    asked = None
                if not isinstance(asked, dict):
                    return self.error(400, "not JSON")
                if self.path not in ("/save", "/done"):
                    return self.error(404, "no such thing")
                with report.lock:
                    if report.result is not None:
                        return self.error(409, "完成过了：运行 py -m memelearner report 再打开")
                    if self.path == "/save":
                        try:
                            choose(report.rows, asked.get("key"), asked.get("field"),
                                   asked.get("value"))
                        except ValueError as error:
                            return self.error(400, str(error))
                        except OSError as error:  # review.json open elsewhere, a full disk
                            return self.error(500, f"存不进 {review_file()}：{error}")
                        return self.reply(200, '{"saved": true}')
                    try:
                        report.result = finish(report.rows, report.model)
                    except judging.ModelUnavailable as error:
                        print(f"Couldn't ask a model to merge your notes: {error}.")
                        return self.error(503, f"没有模型能合并你的补充（{error}），什么都没学。"
                                          "你的选择都存着，过一会儿再点「完成」。")
                    except (OSError, ValueError) as error:  # memes.json broken or open elsewhere
                        print(f"Couldn't finish: {error!r}")
                        return self.error(500, f"没完成：{error!r}。看一下 bot 的窗口。")
                self.reply(200, json.dumps(report.result, ensure_ascii=False))
                threading.Thread(target=report.server.shutdown, daemon=True).start()

        return Handler


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Meme report</title>
<style>
:root {
  --bg: #f7f7f5; --panel: #ffffff; --text: #1c1c1a; --muted: #65645f; --line: #e2e1dc;
  --head: #efeee9; --accent: #2557c7; --on-accent: #ffffff; --add: #1d7a46; --no: #a33a2f;
  --unsure: #8a6d12;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #151514; --panel: #1d1d1b; --text: #ebeae5; --muted: #a09f99; --line: #34332f;
    --head: #252522; --accent: #7ea2ff; --on-accent: #10131a; --add: #63c98f; --no: #ff8f80;
    --unsure: #e2c25b;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif; }
main { max-width: 1440px; margin: 0 auto; padding: 24px 16px 120px; }
h1 { font-size: 22px; margin: 0 0 6px; }
h2 { font-size: 16px; margin: 16px 0 6px; }
.lead { color: var(--muted); margin: 0 0 18px; max-width: 75em; }
.table { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
@media (max-width: 900px) { .table { overflow-x: auto; } }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
thead th { position: sticky; top: 0; background: var(--head); font-size: 14px; font-weight: 600; z-index: 1; }
thead th:first-child { border-top-left-radius: 10px; }
thead th:last-child { border-top-right-radius: 10px; }
tbody tr:last-child td { border-bottom: 0; }
td.phrase { font-size: 16px; font-weight: 600; word-break: break-all; min-width: 6em; }
td.phrase .written { font-size: 14px; font-weight: 400; }
td.phrase .flag { display: block; font-size: 13px; font-weight: 400; }
tr.group td { border-bottom: 0; padding-bottom: 2px; }
tr.variants td { padding-top: 0; font-size: 14px; color: var(--muted); }
tr.variants .what { margin-right: 6px; }
button.variant { font: inherit; font-weight: 400; color: var(--text); background: var(--bg);
  border: 1px solid var(--line); border-radius: 999px; padding: 0 9px; margin: 2px 6px 2px 0; }
button.variant .n { color: var(--muted); margin-left: 5px; font-variant-numeric: tabular-nums; }
button.variant[aria-pressed="true"] { color: var(--no); border-color: var(--no); text-decoration: line-through; }
tr.variants .names { margin-top: 3px; }
td.answer .source { margin-top: 4px; }
td.people { text-align: right; font-variant-numeric: tabular-nums; }
td.lines { color: var(--muted); min-width: 13em; }
td.lines div + div { margin-top: 4px; }
td.answer { min-width: 17em; }
.kind { font-weight: 600; }
.flag { color: var(--muted); font-style: italic; }
select, textarea { font: inherit; color: var(--text); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; }
select { max-width: 100%; }
td.answer select { margin-bottom: 6px; }
textarea { width: 100%; min-width: 13em; min-height: 3.3em; resize: vertical; }
select:focus-visible, textarea:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
tr[data-decision="Add"] td.phrase { color: var(--add); }
tr[data-decision="Not a meme"] td.phrase { color: var(--no); text-decoration: line-through; }
tr[data-decision="Not sure yet"] td.phrase { color: var(--unsure); }
tr[data-decision] td.phrase .flag { color: var(--muted); text-decoration: none; }
.note { color: var(--muted); font-size: 13px; margin: 10px 2px 0; max-width: 75em; }
footer { position: fixed; left: 0; right: 0; bottom: 0; background: var(--panel); border-top: 1px solid var(--line); }
footer .bar { max-width: 1440px; margin: 0 auto; padding: 10px 16px; display: flex; gap: 8px 16px;
  align-items: center; flex-wrap: wrap; }
#counts { font-variant-numeric: tabular-nums; }
#status { color: var(--muted); flex: 1; min-width: 12em; }
#status.bad { color: var(--no); }
button { font: inherit; font-weight: 600; padding: 8px 24px; border: 0; border-radius: 8px;
  background: var(--accent); color: var(--on-accent); cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
#result { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 2px 18px 14px; margin-bottom: 20px; }
#result ul { margin: 4px 0; padding-left: 1.3em; }
#result li { margin: 3px 0; }
</style>
</head>
<body>
<main>
<h1>Meme report</h1>
<p class="lead" id="lead"></p>
<section id="result" hidden></section>
<div class="table"><table>
<thead><tr><th>Meme</th><th>People</th><th>Example lines</th><th id="model"></th>
<th>Decision</th><th>My meaning</th></tr></thead>
<tbody id="rows"></tbody>
</table></div>
<p class="note" id="order"></p>
</main>
<footer><div class="bar"><span id="counts"></span><span id="status" role="status" aria-live="polite"></span>
<button id="done" type="button">完成</button></div></footer>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);
const KINDS = {meme: "meme", name: "a name", term: "a game word", everyday: "everyday words",
               unclear: "unclear"};
const HOW = {video: "查到的：视频里说的", explanation: "查到的：弹幕里有人说",
             learned: "查到的：已学的梗里写着", note: "查到的：你的补充里写着",
             guess: "猜的"};  // how the source is known
const WHOSE = {merged: "和你的补充合成的", note: "你的原话", judge: "模型的意思",
               learned: "原来学的意思",
               "": "还没有意思：之后用 py -m memelearner accept 它 --note \"...\" 补上"};
const picked = DATA.picked, rowOf = {}, saving = new Set(), typing = new Map();
const statusLine = document.getElementById("status");

function el(tag, props, ...children) {  // children are text or elements, never HTML
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props || {})) {
    if (name === "class") node.className = value; else node.setAttribute(name, value);
  }
  node.append(...children);
  return node;
}

function show(text, bad) {
  statusLine.textContent = text;
  statusLine.classList.toggle("bad", !!bad);
}

function why(error) {
  return error instanceof TypeError
    ? "bot 没在运行。运行 py -m memelearner report 接着弄，存过的都还在。" : error.message;
}

async function post(path, body) {
  const response = await fetch(path, {method: "POST", body: JSON.stringify(body),
    headers: {"Content-Type": "application/json", "X-Token": DATA.token}});
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(answer.error || "HTTP " + response.status);
  return answer;
}

function save(key, field, value) {
  picked[key][field] = value;
  if (field === "decision") { rowOf[key].dataset.decision = value; count(); }
  show("正在存…");
  const job = post("/save", {key, field, value}).then(
    () => { if (saving.size === 1) show("已存 " + new Date().toLocaleTimeString()); },
    error => show("没存上：" + why(error), true));
  saving.add(job);
  job.finally(() => saving.delete(job));
}

function dropdown(options, value, label, key, field) {
  const box = el("select", {"aria-label": label}, el("option", {value: ""}, "—"));
  for (const option of options) box.append(el("option", {value: option}, option));
  box.value = value || "";
  box.addEventListener("change", () => save(key, field, box.value));
  return box;
}

function said(row) {
  const text = el("div");
  if (!row.model) { text.append(el("span", {class: "flag"}, "not judged yet")); return text; }
  text.append(el("span", {class: "kind"}, KINDS[row.kind] || row.kind || "unclear"));
  if (row.kind === "meme") {
    text.append(": " + (row.meaning || "meaning unknown"));
    if (row.guess) text.append(" · ", el("span", {class: "flag"}, "guess"));
    if (row.test) text.append(" · ", el("span", {class: "flag"},
      `failed its test (${row.test.fits} of ${row.test.lines} lines)`));
  } else if (row.why) {
    text.append(": " + row.why);
  }
  if (row.model !== DATA.model) text.append(" · ", el("span", {class: "flag"}, "judged by " + row.model));
  if (row.kind === "meme" && row.source) {
    text.append(el("div", {class: "source"}, "来源：「" + row.source.phrase + "」",
                   el("span", {class: "flag"}, "（" + (HOW[row.source.how] || HOW.guess) + "）")));
  }
  return text;
}

function noteBox(row) {
  const box = el("textarea", {"aria-label": "My meaning: " + row.phrase, rows: "2"});
  box.value = picked[row.key].note || "";
  const now = () => { clearTimeout(typing.get(row.key)); typing.delete(row.key);
                      if (box.value !== picked[row.key].note) save(row.key, "note", box.value); };
  box.addEventListener("input", () => { clearTimeout(typing.get(row.key));
                                        typing.set(row.key, setTimeout(now, 800)); });
  box.addEventListener("change", now);
  box.flush = now;
  return box;
}

function meme(row) {  // the pattern, the way most write it, and the learned meme it is
  const cell = el("td", {class: "phrase"});
  if (row.pattern && row.pattern !== row.phrase) {
    cell.append(el("div", {}, "「" + row.pattern + "」"), el("div", {class: "written"}, row.phrase));
  } else {
    cell.append(row.phrase);
  }
  if (row.learned) cell.append(el("span", {class: "flag"}, "已学的梗「" + row.learned.phrase + "」的新变体：Add 就加进它"));
  return cell;
}

function variants(row) {  // its variants, each one a button that keeps it out, and the names
  const cell = el("td", {colspan: "6"});
  const apart = new Set(picked[row.key].apart || []);
  if (row.variants.length) {
    cell.append(el("span", {class: "what"}, "变体（不是这个梗的点一下）："));
    for (const variant of row.variants) {
      const button = el("button", {type: "button", class: "variant", "aria-pressed": String(apart.has(variant.phrase)),
                                   title: "点一下：不是这个梗 / 是"},
                        variant.phrase, el("span", {class: "n"}, String(variant.people)));
      button.addEventListener("click", () => {
        if (apart.has(variant.phrase)) apart.delete(variant.phrase); else apart.add(variant.phrase);
        button.setAttribute("aria-pressed", String(apart.has(variant.phrase)));
        save(row.key, "apart", row.variants.map(v => v.phrase).filter(p => apart.has(p)));
      });
      cell.append(button);
    }
  }
  if (row.names.length) {  // by player: Chovy = 超威、chovy、大超
    const players = new Map(), unknown = [];
    for (const n of row.names) {
      if (!n.player) { unknown.push(n.name); continue; }
      if (!players.has(n.player)) players.set(n.player, []);
      players.get(n.player).push(n.name);
    }
    const parts = [...players].map(([player, names]) => `${player} = ${names.join("、")}`);
    if (unknown.length) parts.push("外号表里没有的：" + unknown.join("、"));
    cell.append(el("div", {class: "names"}, "名字：" + parts.join("；")));
  }
  return cell;
}

const body = document.getElementById("rows"), notes = [];
for (const row of DATA.rows) {
  const lines = el("td", {class: "lines"});
  for (const line of row.examples.slice(0, DATA.shown)) lines.append(el("div", {}, "「" + line + "」"));
  const answer = el("td", {class: "answer"},
    dropdown(DATA.marks, picked[row.key].mark, "Mark: " + row.phrase, row.key, "mark"), said(row));
  const note = noteBox(row);
  notes.push(note);
  const more = row.variants.length || row.names.length;
  const tr = el("tr", {"data-decision": picked[row.key].decision || "", class: more ? "group" : ""},
    meme(row), el("td", {class: "people"}, String(row.people)), lines,
    answer, el("td", {}, dropdown(DATA.decisions, picked[row.key].decision, "Decision: " + row.phrase,
                                  row.key, "decision")),
    el("td", {}, note));
  rowOf[row.key] = tr;
  body.append(tr);
  if (more) body.append(el("tr", {class: "variants"}, variants(row)));
}
document.getElementById("model").textContent = DATA.name || "Model";
document.getElementById("lead").textContent = (DATA.about ? DATA.about + "。" : "") +
  `${DATA.rows.length} 个梗，最像梗的在前。一行一个梗：上面是句式（XX 是放人名的地方）和发得最多的说法，` +
  "下面是它的变体（换了人名或外号、加了「不」、省了几个字的写法），有不是这个梗的就点一下它。" +
  "给模型的回答选 Mark，给每个梗选 Decision；模型的意思不对或者不全，就在 My meaning 里写你的补充。" +
  "每次选和写都会马上存下来。全部弄完点下面的「完成」：Add 的 bot 连变体一起学会（你的补充和模型的意思" +
  "合成一句，你标了 Wrong 的意思不用；已学的梗的新变体加进那个梗），Not a meme 的连变体以后不再列出" +
  "（你写的当理由），Not sure yet 和没选的留到下次。";
document.getElementById("order").textContent = DATA.order;

function tally() {
  const counts = {"Add": 0, "Not a meme": 0, "Not sure yet": 0, "": 0};
  for (const row of DATA.rows) counts[picked[row.key].decision || ""] += 1;
  return counts;
}

function count() {
  const counts = tally();
  document.getElementById("counts").textContent =
    `Add ${counts["Add"]} · Not a meme ${counts["Not a meme"]} · Not sure yet ${counts["Not sure yet"]} · 没选 ${counts[""]}`;
}
count();

function lock(on) {
  for (const input of document.querySelectorAll("select, textarea, button")) input.disabled = on;
}

function list(title, items) {
  const section = document.getElementById("result");
  section.append(el("h2", {}, title));
  if (items.length) section.append(el("ul", {}, ...items.map(item => el("li", {}, ...item))));
}

function results(result) {
  const section = document.getElementById("result");
  section.replaceChildren();
  list(`学会了 ${result.added.length} 个梗`, result.added.map(a => [
    el("b", {}, a.phrase), "：" + (a.meaning || "—") + "（" + WHOSE[a.from] + "）" +
    (a.variants.length ? (a.learned ? " 加了变体 " : " 变体 ") + a.variants.join("、") : "") +
    (a.source ? ` 来源「${a.source.phrase}」（${HOW[a.source.how] || HOW.guess}）` : "") +
    (a.missing.length ? " 请检查：没写进你补充里的 " + a.missing.join("、") : "")]));
  list(`不再列出 ${result.rejected.length} 个`, result.rejected.map(r => [el("b", {}, r.phrase), "：" + r.reason +
    (r.variants.length ? "（还有变体 " + r.variants.join("、") + "）" : "")]));
  list(`留到下次 ${result.left.length} 个`, []);
  const scores = Object.entries(result.score).map(([model, s]) => [
    el("b", {}, model.split("/").pop()), `：${s.points} 分，算了 ${s.answers} 个回答（` +
    Object.entries(s.marks).map(([mark, n]) => `${mark} ${n}`).join("，") + "）"]);
  list("模型得分", scores.length ? scores : [["这次没有标 Mark"]]);
  section.append(el("p", {}, "bot 已经停了，可以关掉这个页面。下次运行会列出留下的和新的说法。"));
  section.hidden = false;
  window.scrollTo({top: 0, behavior: "smooth"});
}

document.getElementById("done").addEventListener("click", async () => {
  for (const note of notes) note.flush();
  const counts = tally();
  if (!confirm(`学会 ${counts["Add"]} 个，不再列出 ${counts["Not a meme"]} 个，其余 ` +
               `${counts["Not sure yet"] + counts[""]} 个留到下次。现在完成吗？`)) return;
  await Promise.allSettled([...saving]);
  lock(true);
  show("正在学…（要合并你的补充的话要问模型，可能要一两分钟）");
  try {
    results(await post("/done", {}));
    show("完成了。");
  } catch (error) {
    lock(false);
    show("没完成：" + why(error), true);
  }
});
</script>
</body>
</html>
"""
