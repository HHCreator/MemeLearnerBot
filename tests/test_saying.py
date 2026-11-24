"""Offline tests for saying memes by itself (saying.py), its page and its command.

A fake model answers in the shape a real one does, so these check what the model is shown,
which memes are drawn, which lines pass the check, and what the ratings change. Whether a
real model says funny, fitting lines is only shown by a real run.
"""

import collections
import contextlib
import io
import json
import os
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import memelearner
from memelearner import cli, judging, nicknames, saying, store, uses

OCCASIONS = """秀了 = 打得很好
打得烂 = 失误、送人头
要输了 = 局势崩了
输了 = 刚输掉
离谱 = 看不懂的操作
翻盘 = 逆转
加油 = 打气
要打团 = 大团战要来了
BP = 选英雄
名场面 = 想起以前的比赛
解说 = 在议论解说
"""
NICKNAMES = "Chovy = 超威, 大超, chooovy\nCanyon = 三叔, 三姆斯\n"


def use(when, tone="调侃", how="随便说说", template="", who=""):
    return {"when": when, "tone": tone, "how": how, "template": template, "who": who,
            "by": "deepseek/deepseek-flash", "of": "x", "checked": True}


MEMES = {
    "wei出": {"meaning": "「X出」：出和畜同音，骂选手打得烂。", "games": ["lol"],
             "variants": ["kanavi出"], "examples": ["wei出", "wei出早打完了", "kanavi出"],
             "use": use(["打得烂", "要输了"], "骂", "选手打得烂时刷他的名字加出", "XX出", "选手")},
    "超威同意了": {"meaning": "同意是认输：调侃 Chovy（超威）输掉。", "games": ["lol"],
               "variants": ["chovy同意了", "三叔不同意"], "examples": ["超威同意了吗"],
               "use": use(["输了", "要输了"], "嘲讽", "要输的时候刷", "XX同意了", "选手")},
    "阿一古": {"meaning": "韩语「아이고」的音译，表示哎呀、无奈。", "games": ["lol", "valorant"],
             "examples": ["阿一古", "主播又在阿一古了"], "use": use(["离谱", "打得烂"], "无奈")},
    "阿一占": {"meaning": "嘲讽 TheShy 手伤。", "games": ["lol"], "examples": ["阿一占别说"],
             "use": use(["打得烂"], "嘲讽", "TheShy 打得差的时候刷")},
    "把光借给ig": {"meaning": "给 IG 加油。", "games": ["lol"], "examples": ["把光借给IG"],
                "use": use(["加油", "要打团"], "加油", "给战队加油", "把光借给XX", "战队")},
    "复刻s7": {"meaning": "想再来一次 2017 年那样的比赛。", "games": ["lol"],
              "examples": ["复刻S7"], "use": use(["名场面"], "惊叹", "想起老比赛", "复刻XX", "别的")},
    "这能输啊": {"meaning": "优势被翻盘的感叹。", "games": ["valorant"], "examples": ["这能输啊"],
              "use": use(["翻盘", "要输了"], "无奈")},
    "41翻": {"meaning": "41 是世一，41翻是世界第一翻译。", "games": ["lol"], "examples": ["41中"],
            "use": use(["解说"], "调侃", "夸翻译", "41XX", "位置")},
    "没用法": {"meaning": "还没写用法的梗。", "games": ["lol"], "examples": ["没用法"]},
}
THESHY = "TheShy 一级被抓，送了两个"
JUDGED = {"when": ["打得烂", "要输了"], "game": "lol", "names": [{"name": "TheShy", "who": "选手"}]}
# What fits THESHY, the heaviest first: about TheShy; 打得烂 and 要输了, with a name for XX;
# 打得烂 second; 要输了 second, with a name; and only seen in valorant
DRAWN = ["阿一占", "wei出", "阿一古", "超威同意了", "这能输啊"]
ASK = judging._ask  # the real one, before a test replaces it


class FakeModel:
    """Stands in for judging._ask: answers a moment's judging with judged, and the writing
    with the next of written (the last again when they run out); keeps what it was asked:
    (kind, question, temperature, model, instructions)."""

    def __init__(self, judged=JUDGED, written=({"lines": []},)):
        self.judged, self.written = judged, list(written)
        self.asked = []

    def __call__(self, messages, model, temperature=None):
        told, question = messages[0]["content"], json.loads(messages[1]["content"])
        kind = "judge" if told.startswith(saying.JUDGE) else "write" if told == saying.WRITE \
            else None
        assert kind, told[:40]
        self.asked.append((kind, question, temperature, model, told))
        if kind == "judge":
            answer = self.judged
        else:
            answer = self.written[min(len(self.of("write")), len(self.written)) - 1]
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)

    def of(self, kind):
        return [question for asked, question, *_ in self.asked if asked == kind]


class Fixed:
    """An rng that draws the memes in the order of their weight, the heaviest first."""

    def random(self):
        return 0.5


class SayingTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        for name, text in (("occasions.txt", OCCASIONS), ("nicknames.txt", NICKNAMES)):
            with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
                f.write(text)
        self.model = FakeModel()
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(self.folder, "memes.json")),
                mock.patch.object(saying, "SAID_FILE", os.path.join(self.folder, "said.json")),
                mock.patch.object(uses, "OCCASIONS_FILE",
                                  os.path.join(self.folder, "occasions.txt")),
                mock.patch.object(nicknames, "NICKNAMES_FILE",
                                  os.path.join(self.folder, "nicknames.txt")),
                mock.patch.object(judging, "_ask", self.model),
                mock.patch.object(judging, "MODEL", "qwen3:14b"),
                mock.patch.object(judging, "_get_json",
                                  lambda path: {"models": [{"name": "qwen3:14b"}]}),
                mock.patch.object(judging, "RETRY_WAIT", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        store.save({"memes": json.loads(json.dumps(MEMES)), "rejected": {}})

    def say(self, situation=THESHY, written=None, judged=JUDGED, **options):
        self.model.judged = judged
        if written is not None:
            self.model.written = list(written)
        options.setdefault("rng", Fixed())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = saying.say(situation, **options)
        self.out = out.getvalue()
        return result

    def moment(self, situation=THESHY, judged=JUDGED):
        return {"when": judged["when"], "game": judged["game"],
                "names": saying.names_in(situation, judged["names"])}

    # -- 1. judge ---------------------------------------------------------------------------

    def test_the_model_judges_the_moment_from_what_you_wrote_and_the_occasions(self):
        judged = {"when": ["打得烂", "没有这个", "要输了", "离谱", "输了"],
                  "game": "英雄联盟（LOL）",
                  "names": [{"name": "TheShy", "who": "选手"}, {"name": "Faker", "who": "选手"},
                            {"name": "三叔", "who": "教练"}, "not a name"]}
        result = self.say("TheShy 一级被抓，三叔来了", written=[{"lines": []}], judged=judged)
        kind, question, temperature, model, told = self.model.asked[0]
        self.assertEqual((kind, question, temperature, model),
                         ("judge", {"situation": "TheShy 一级被抓，三叔来了"}, None, "qwen3:14b"))
        self.assertIn("- 打得烂 = 失误、送人头", told)  # the occasions, from occasions.txt
        self.assertIn("- lol = 英雄联盟（LOL）", told)
        self.assertEqual(result["when"], ["打得烂", "要输了", "离谱"])  # MAX_WHEN, known ones
        self.assertEqual(result["game"], "lol")
        # Faker isn't in what you wrote; 三叔 is Canyon, and 教练 isn't one of NAMES
        self.assertEqual(result["names"], [
            {"name": "TheShy", "who": "选手", "player": "", "nicknames": []},
            {"name": "三叔", "who": "", "player": "Canyon", "nicknames": ["Canyon", "三姆斯"]}])

    def test_the_nickname_table_adds_the_names_the_model_left_out(self):
        result = self.say("大超又被单杀了", written=[{"lines": []}],
                          judged={"when": "打得烂、离谱", "game": "", "names": []})
        self.assertEqual(result["when"], ["打得烂", "离谱"])  # one text of occasions
        self.assertEqual(result["names"], [{"name": "大超", "who": "", "player": "Chovy",
                                            "nicknames": ["Chovy", "超威", "chooovy"]}])
        # The same player twice (Chovy and 超威) is one name
        names = saying.names_in("Chovy 和超威", [{"name": "Chovy", "who": "选手"}])
        self.assertEqual([name["name"] for name in names], ["Chovy"])

    def test_an_answer_that_isnt_json_is_asked_again_and_no_occasion_says_nothing(self):
        result = self.say(judged="I can't tell")
        self.assertEqual([kind for kind, *_ in self.model.asked], ["judge", "judge"])
        self.assertEqual((result["when"], result["lines"], result["drawn"]), ([], [], []))
        self.assertIn("看不出这是比赛里的什么场面", result["why"])
        self.model.asked.clear()
        result = self.say("今天天气不错", judged={"when": [], "game": "", "names": []})
        self.assertEqual([kind for kind, *_ in self.model.asked], ["judge"])  # [] is an answer
        self.assertIn("看不出", result["why"])

    # -- 2. pick ----------------------------------------------------------------------------

    def test_memes_are_weighed_by_their_occasions_names_game_and_template(self):
        memes, moment = store.load()["memes"], self.moment()

        def weight(phrase, moment=moment, said=()):
            return round(saying.weigh(phrase, memes[phrase], moment, said), 3)

        # Both of wei出's occasions fit (1 + 1/2/2), and its XX takes the player
        self.assertEqual(weight("wei出"), round(1.25 * saying.TAKES_NAME, 3))
        self.assertEqual(weight("阿一占"), saying.ABOUT)  # about TheShy: its how names him
        self.assertEqual(weight("阿一古"), 0.5)  # 打得烂 is its second occasion
        self.assertEqual(weight("这能输啊"), round(0.25 * saying.OTHER_GAME, 3))  # valorant
        for phrase in ("把光借给ig", "复刻s7", "41翻", "没用法"):  # other occasions, no use
            self.assertEqual(weight(phrase), 0, phrase)
        nobody = dict(moment, names=[])
        self.assertEqual(weight("wei出", nobody), round(1.25 * saying.NAMELESS, 3))
        self.assertEqual(weight("阿一占", nobody), 1)
        # About someone in it, it may come with other occasions too
        bp = dict(moment, when=["BP"])
        self.assertEqual(weight("阿一占", bp), round(saying.UNFIT_ABOUT * saying.ABOUT, 3))
        chovy = self.moment("超威又送了", {"when": ["打得烂"], "game": "lol", "names": []})
        self.assertEqual(weight("超威同意了", chovy),
                         round(saying.UNFIT_ABOUT * saying.ABOUT * saying.TAKES_NAME, 3))

    def test_your_ratings_in_such_moments_weigh_a_meme_more_or_less(self):
        memes, moment = store.load()["memes"], self.moment()

        def rated(rating, when, n=1):
            return [{"meme": "阿一古", "line": f"阿一古{i}", "situation": "x", "when": when,
                     "rating": rating} for i in range(n)]

        def weight(said):
            return saying.weigh("阿一古", memes["阿一古"], moment, said)

        self.assertAlmostEqual(weight(rated("funny", ["打得烂"])), 0.5 * saying.FUNNY)
        self.assertAlmostEqual(weight(rated("funny", ["打得烂"], 5)),
                               0.5 * saying.FUNNY ** saying.RATED)  # RATED at most
        self.assertAlmostEqual(weight(rated("wrong", ["要输了"], 2)), 0.5 * saying.WRONG ** 2)
        self.assertAlmostEqual(weight(rated("wrong", ["秀了"])), 0.5)  # another moment

    def test_the_heavier_a_meme_the_likelier_it_is_drawn_and_never_one_that_doesnt_fit(self):
        memes, moment = store.load()["memes"], self.moment()
        self.assertEqual(saying.draw(memes, moment, rng=Fixed()), DRAWN)
        self.assertEqual(saying.draw(memes, moment, rng=Fixed(), limit=2), ["阿一占", "wei出"])
        drawn = collections.Counter()
        rng = random.Random(5)
        for _ in range(300):
            drawn.update(saying.draw(memes, moment, rng=rng, limit=1))
        self.assertGreater(drawn["阿一占"], drawn["wei出"])
        self.assertGreater(drawn["wei出"], drawn["阿一古"])
        self.assertEqual(set(drawn) - set(DRAWN), set())
        # No meme is for 秀了: only the one about TheShy may still come
        self.assertEqual(saying.draw(memes, dict(moment, when=["秀了"]), rng=rng), ["阿一占"])
        self.assertEqual(saying.draw(memes, dict(moment, when=["秀了"], names=[]), rng=rng), [])

    # -- 3. write and 4. check --------------------------------------------------------------

    def says(self, line, phrase, situation=THESHY, judged=JUDGED):
        memes = store.load()["memes"]
        said, why = saying.says(store.normal(line), phrase, memes[phrase],
                                self.moment(situation, judged), situation)
        return said if said else why

    def test_a_line_must_say_its_meme_as_learned_or_on_its_template(self):
        nobody = {"when": ["打得烂"], "game": "lol", "names": []}
        self.assertEqual(self.says("TheShy出！", "wei出"), {"template": "XX出", "name": "theshy"})
        self.assertEqual(self.says("这把 wei出", "wei出", "一级被抓", nobody),
                         {"template": "", "name": ""})  # nobody in it: as it was learned
        self.assertEqual(self.says("kanavi出", "wei出", "一级被抓", nobody),
                         {"template": "", "name": ""})  # a variant
        self.assertIn("wei 不在场面里", self.says("wei出", "wei出"))  # TheShy is in it
        self.assertIn("没有照原样说「wei出」或「kanavi出」或「XX出」", self.says("出", "wei出"))
        self.assertEqual(self.says("theshy出了", "wei出"), {"template": "XX出", "name": "theshy"})
        self.assertIn("没有照原样说", self.says("shy出", "wei出", "一级被抓", nobody))
        # A nickname from the table, a part of a name, and a team in a player's place
        chovy = {"when": ["输了"], "game": "lol", "names": [{"name": "Chovy", "who": "选手"}]}
        self.assertEqual(self.says("大超同意了", "超威同意了", "Chovy 又输了", chovy),
                         {"template": "XX同意了", "name": "大超"})
        self.assertEqual(self.says("超威同意了吗", "超威同意了", "Chovy 又输了", chovy),
                         {"template": "", "name": ""})
        self.assertEqual(self.says("阿一古 绷不住了", "阿一古"), {"template": "", "name": ""})
        self.assertIn("没有照原样说「阿一古」", self.says("阿一", "阿一古"))
        # Something else (别的) may be anything of what's going on; a position, a position
        old = {"when": ["名场面"], "game": "lol", "names": []}
        self.assertEqual(self.says("复刻s8吧", "复刻s7", "像 S8 那年一样", old),
                         {"template": "复刻XX", "name": "s8"})
        self.assertIn("没有照原样说", self.says("复刻s9", "复刻s7", "像 S8 那年一样", old))
        self.assertEqual(self.says("41中", "41翻", "解说在吹", old),
                         {"template": "41XX", "name": "中"})

    def test_lines_are_checked_and_the_model_is_asked_again_for_the_ones_that_failed(self):
        written = [{"lines": [
            {"meme": "wei出", "line": "TheShy出"},
            {"meme": "wei出", "line": "theshy出出"},  # wei出 again
            {"meme": "阿一古", "line": "wei出"},  # another meme than it says, and someone else
            {"meme": "编的梗", "line": "阿一占 别说了"},  # names none drawn, but says one
            {"meme": "阿一古", "line": "阿一古" * 11},  # too long
            {"meme": "没给的", "line": "我编了一个梗"},
            "not a line"]},
            {"lines": [{"meme": "阿一古", "line": "阿一古 一级就送"}]}]
        result = self.say(written=written, count=4)
        self.assertEqual(result["lines"], [
            {"line": "TheShy出", "meme": "wei出", "template": "XX出", "name": "theshy"},
            {"line": "阿一占 别说了", "meme": "阿一占", "template": "", "name": ""},
            {"line": "阿一古 一级就送", "meme": "阿一古", "template": "", "name": ""}])
        whys = [(line["line"], line["why"]) for line in result["rejected"]]
        self.assertEqual(whys[0], ("theshy出出", "「wei出」前面一句用过了"))
        self.assertEqual(whys[1], ("wei出", "没有照原样说「阿一古」"))
        self.assertIn("太长了", whys[2][1])
        self.assertEqual(whys[3], ("我编了一个梗", "「没给的」不是给你的梗"))
        self.assertEqual(whys[4], ("", "没有写弹幕"))
        # Asked again for 2 lines (4, less the 2 that passed), with the memes not said yet
        write, again = self.model.of("write")
        self.assertEqual((write["count"], again["count"]), (4, 2))
        self.assertEqual([m["phrase"] for m in again["memes"]], ["阿一古", "超威同意了", "这能输啊"])
        self.assertEqual(again["rejected"][0], {"line": "theshy出出", "meme": "wei出",
                                                "why": "「wei出」前面一句用过了"})
        self.assertEqual({temperature for kind, _, temperature, *_ in self.model.asked
                          if kind == "write"}, {saying.WRITE_TEMPERATURE})

    def test_the_model_is_shown_the_moment_and_each_meme_with_its_use_and_lines(self):
        self.say(written=[{"lines": []}])
        write = self.model.of("write")[0]
        self.assertEqual((write["situation"], write["when"], write["count"]),
                         (THESHY, ["打得烂", "要输了"], saying.COUNT))
        self.assertEqual(write["names"], [{"name": "TheShy", "who": "选手", "nicknames": []}])
        wei = write["memes"][1]
        self.assertEqual(wei, {"phrase": "wei出", "variants": ["kanavi出"],
                               "meaning": MEMES["wei出"]["meaning"],
                               "how": "选手打得烂时刷他的名字加出", "tone": "骂",
                               "template": "XX出", "who": "选手",
                               "lines": ["wei出", "wei出早打完了", "kanavi出"],
                               "funny": [], "wrong": []})
        # It wrote no line, as no meme fitted: it isn't asked again
        self.assertEqual(len(self.model.of("write")), 1)

    def test_what_say_returns(self):
        result = self.say(written=[{"lines": [{"meme": "阿一占", "line": "阿一占"}]}])
        self.assertEqual(result, {
            "situation": THESHY, "when": ["打得烂", "要输了"], "game": "lol",
            "names": [{"name": "TheShy", "who": "选手", "player": "", "nicknames": []}],
            "lines": [{"line": "阿一占", "meme": "阿一占", "template": "", "name": ""}],
            "rejected": [], "drawn": DRAWN,
            "model": "qwen3:14b", "spent": "", "why": ""})
        shown = saying.show(result)
        self.assertIn("打得烂、要输了 · 英雄联盟（LOL） · TheShy（选手）", shown)
        self.assertIn("1. 阿一占    （阿一占）", shown)
        # The game you give wins over the model's
        self.assertEqual(self.say(written=[{"lines": []}], game="valorant")["game"],
                         "valorant")
        result = self.say(judged={"when": ["秀了"], "game": "", "names": []})
        self.assertEqual((result["lines"], result["why"]), ([], "学过的梗里，没有秀了的时候说的"))

    def test_what_cant_be_said(self):
        for situation, options, message in (
                ("  ", {}, "先说说"), (THESHY, {"count": 0}, "count"),
                (THESHY, {"count": saying.CANDIDATES + 1}, "count"),
                (THESHY, {"count": True}, "count"), (THESHY, {"game": "chess"}, "no such game")):
            with self.assertRaisesRegex(ValueError, message):
                saying.say(situation, **options)
        with open(uses.OCCASIONS_FILE, "w", encoding="utf-8") as f:
            f.write("# none\n")
        with self.assertRaisesRegex(uses.TableError, "has no occasions"):
            saying.say(THESHY)
        self.assertEqual(self.model.asked, [])

    # -- what you rated ---------------------------------------------------------------------

    def test_your_ratings_are_kept_and_the_model_is_shown_them_next_time(self):
        result = self.say(written=[{"lines": [{"meme": "wei出", "line": "theshy出"},
                                              {"meme": "阿一古", "line": "阿一古"}]}])
        self.assertEqual(saying.rated(), [])
        kept = saying.rate(result, 0, "funny")
        self.assertEqual({k: v for k, v in kept.items() if k != "at"}, {
            "line": "theshy出", "meme": "wei出", "template": "XX出", "name": "theshy",
            "situation": THESHY, "when": ["打得烂", "要输了"], "game": "lol",
            "rating": "funny", "by": "qwen3:14b"})
        saying.rate(result, 1, "wrong")
        saying.rate(result, 1, "funny")  # changed your mind: one rating, the last
        saying.rate(result, 1, "")  # ... and took it back
        saying.rate(result, 1, "wrong")
        self.assertEqual([(line["line"], line["rating"]) for line in saying.rated()],
                         [("theshy出", "funny"), ("阿一古", "wrong")])
        with open(saying.SAID_FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual((data["version"], len(data["lines"])), (saying.VERSION, 2))
        for index, rating in ((2, "funny"), (-1, "funny"), (True, "funny"), (0, "good")):
            with self.assertRaises(ValueError):
                saying.rate(result, index, rating)

        self.say(written=[{"lines": []}])
        memes = {m["phrase"]: m for m in self.model.of("write")[-1]["memes"]}
        self.assertEqual(memes["wei出"]["funny"], [{"line": "theshy出", "situation": THESHY}])
        self.assertEqual(memes["阿一古"]["wrong"], [{"line": "阿一古", "situation": THESHY}])
        self.assertEqual((memes["wei出"]["wrong"], memes["阿一古"]["funny"]), ([], []))

    def test_a_broken_ratings_file_is_never_saved_over(self):
        with open(saying.SAID_FILE, "w", encoding="utf-8") as f:
            f.write('{"lines": [<<<<<<< HEAD')
        with self.assertRaises(ValueError):
            saying.rated()
        with open(saying.SAID_FILE, "w", encoding="utf-8") as f:
            json.dump({"lines": [{"line": "x", "meme": "阿一古", "situation": "y",
                                  "rating": "funny"}, {"line": "no rating"}, "junk"]}, f)
        self.assertEqual([line["line"] for line in saying.rated()], ["x"])

    # -- the page, the command and the API ---------------------------------------------------

    def test_the_page_says_rates_and_stops_when_done(self):
        self.model.written = [{"lines": [{"meme": "wei出", "line": "theshy出"}]}]
        page = saying.SayPage()
        base = page.url.split("?")[0].rstrip("/")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy here

        def ask(url, body=None, token=page.token):
            request = urllib.request.Request(url, None if body is None else json.dumps(
                body, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json", "X-Token": token})
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, json.loads(response.read().decode("utf-8")) \
                        if body is not None else response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read().decode("utf-8"))

        ran = {}
        thread = threading.Thread(target=lambda: ran.update(result=page.run(browser=False)))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            thread.start()
            status, html = ask(page.url)
            self.assertEqual(status, 200)
            start = html.index('<script type="application/json" id="data">') + 42
            shown = json.loads(html[start:html.index("</script>", start)])
            self.assertEqual((shown["token"], shown["games"][0], shown["count"]),
                             (page.token, {"key": "lol", "name": "英雄联盟"}, saying.COUNT))
            self.assertEqual(ask(base + "/save", {"action": "say"}, token="wrong")[0], 403)
            status, said = ask(base + "/save", {"action": "say", "situation": THESHY,
                                                "game": "", "count": 3})
            self.assertEqual(status, 200)
            self.assertEqual((said["said"], said["lines"][0]["line"], said["lines"][0]["rating"]),
                             (0, "theshy出", ""))
            self.assertEqual(said["lines"][0]["meaning"], MEMES["wei出"]["meaning"])
            self.assertEqual(ask(base + "/save", {"action": "say", "situation": " "})[0], 400)
            status, answer = ask(base + "/save", {"action": "rate", "said": 0, "index": 0,
                                                  "rating": "funny"})
            self.assertEqual((status, answer), (200, {"rating": "funny"}))
            self.assertEqual(saying.rated()[0]["line"], "theshy出")
            for wrong in ({"action": "rate", "said": 1, "index": 0, "rating": "funny"},
                          {"action": "rate", "said": 0, "index": 5, "rating": "funny"},
                          {"action": "rate", "said": 0, "index": 0, "rating": "meh"},
                          {"action": "dance"}):
                self.assertEqual(ask(base + "/save", wrong)[0], 400, wrong)
            with mock.patch.object(judging, "_get_json", mock.Mock(side_effect=OSError)):
                page.ask = saying.meanings._Asker(None, saying.JUDGE)  # checks the model again
                status, answer = ask(base + "/save", {"action": "say", "situation": THESHY})
            self.assertEqual(status, 503)
            self.assertIn("没有模型能用", answer["error"])
            status, answer = ask(base + "/done", {})
            thread.join(10)
        self.assertEqual(status, 200)
        self.assertFalse(thread.is_alive())
        self.assertEqual(answer, {"said": 1, "lines": 1, "funny": 1, "wrong": 0})
        self.assertEqual(ran["result"], answer)
        self.assertIn(page.url, printed.getvalue())
        self.assertIn("theshy出", printed.getvalue())  # what it said, in the window too

    def test_the_command_says_or_opens_the_page(self):
        self.model.written = [{"lines": [{"meme": "阿一占", "line": "阿一占"}]}]
        opened = []

        class FakePage:
            def __init__(self, port, model):
                opened.append(("page", port, model))

            def run(self, browser):
                opened.append(("open", browser))
                return {"said": 2, "lines": 7, "funny": 3, "wrong": 1}

        def run(*args):
            with mock.patch.object(saying, "SayPage", FakePage), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                try:
                    cli.main(list(args))
                    code = 0
                except SystemExit as stop:
                    code = stop.code
            return code, printed.getvalue()

        code, printed = run("say", "TheShy", "一级被抓，送了两个")
        self.assertEqual(code, 0)
        self.assertEqual(self.model.of("judge")[-1], {"situation": THESHY})
        self.assertIn("1. 阿一占", printed)
        code, printed = run("say", THESHY, "--json", "-n", "2", "--game", "lol")
        self.assertEqual((code, json.loads(printed)["lines"][0]["meme"]), (0, "阿一占"))
        self.assertEqual(self.model.of("write")[-1]["count"], 2)
        code, printed = run("say", "--no-browser", "--port", "8765")
        self.assertEqual((code, opened), (0, [("page", 8765, "qwen3:14b"), ("open", False)]))
        self.assertIn("It said 7 lines in 2 moments; you rated 3 funny and 1 wrong", printed)
        self.assertEqual(run("say", THESHY, "-n", "0")[0], f"count: a whole number from 1 to "
                                                           f"{saying.CANDIDATES}")
        with mock.patch.object(judging, "_get_json", mock.Mock(side_effect=OSError)):
            code, printed = run("say", THESHY)
        self.assertEqual(code, 1)
        self.assertIn("Couldn't ask a model: Ollama isn't running", printed)

    def test_another_app_says_and_rates_quietly(self):
        self.model.written = [{"lines": [{"meme": "阿一占", "line": "阿一占"}]}]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            said = memelearner.say(THESHY, quiet=True)
        self.assertEqual((out.getvalue(), said["lines"][0]["line"]), ("", "阿一占"))
        self.assertEqual(memelearner.rate(said, 0, "funny")["meme"], "阿一占")
        self.assertEqual(saying.rated()[0]["rating"], "funny")

    def test_an_online_model_judges_carefully_and_writes_freely(self):
        sent = []

        def api(request, timeout=None):
            body = json.loads(request.data.decode("utf-8")) if request.data else None
            if request.full_url.endswith("/models"):
                answer = {"data": [{"id": "deepseek-flash"}]}
            else:
                sent.append(body)
                reply = self.model(body["messages"], "deepseek/deepseek-flash",
                                   body["temperature"])
                answer = {"choices": [{"message": {"content": reply}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 900, "completion_tokens": 60,
                                    "prompt_cache_hit_tokens": 800}}
            return io.BytesIO(json.dumps(answer, ensure_ascii=False).encode("utf-8"))

        self.model.written = [{"lines": [{"meme": "阿一占", "line": "阿一占"}]}]
        with mock.patch.object(judging, "_ask", ASK), \
                mock.patch.object(urllib.request, "urlopen", api), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            result = self.say(model="deepseek/deepseek-flash")
        self.assertEqual([body["temperature"] for body in sent],
                         [judging.TEMPERATURE, saying.WRITE_TEMPERATURE])
        self.assertEqual((result["model"], result["lines"][0]["line"]),
                         ("deepseek/deepseek-flash", "阿一占"))
        self.assertIn("read 1,800 tokens (1,600 of them cached", result["spent"])


if __name__ == "__main__":
    unittest.main()
