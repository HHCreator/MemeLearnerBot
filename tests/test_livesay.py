"""Offline tests for saying memes by itself while a live room is on (livesay.py) and its
command. A fake model answers in the shape a real one does, and the danmu are made up, so
these check what the model is shown, when it looks, what it says and who gets it. Whether a
real room and a real model give fitting lines is only shown by a real run."""

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from memelearner import cli, judging, live, livesay, nicknames, saying, store, uses

from .test_saying import MEMES, NICKNAMES, OCCASIONS, Fixed

ROOM = {"name": 6, "title": "TES vs IG", "link": "https://live.bilibili.com/6"}
SEEN = {"situation": "TheShy 一级被抓，送了两个", "when": ["打得烂", "要输了"], "game": "lol",
        "names": [{"name": "TheShy", "who": "选手"}]}
WRITTEN = {"lines": [{"meme": "wei出", "line": "TheShy出"},
                     {"meme": "阿一古", "line": "阿一古 一级就送"}]}


def danmu(text, who="a", **marks):
    return dict({"id": f"{who}{text}", "kind": "live", "who": who, "text": text,
                 "sent": 0}, **marks)


def crowd(n=10, text="theshy又被抓了"):
    """n danmu from n people."""
    return [danmu(text if i % 2 else f"{text}{i}", f"p{i}") for i in range(n)]


class FakeModel:
    """Stands in for judging._ask: answers a look at the danmu with seen, and the writing
    with the next of written (the last again when they run out)."""

    def __init__(self, seen=SEEN, written=(WRITTEN,)):
        self.seen, self.written, self.asked = seen, list(written), []

    def __call__(self, messages, model, temperature=None):
        told, question = messages[0]["content"], json.loads(messages[1]["content"])
        kind = "look" if told.startswith(livesay.JUDGE) else "write" if told == saying.WRITE \
            else None
        assert kind, told[:40]
        self.asked.append((kind, question, told))
        if kind == "look":
            return json.dumps(self.seen, ensure_ascii=False)
        answer = self.written[min(len(self.of("write")), len(self.written)) - 1]
        return json.dumps(answer, ensure_ascii=False)

    def of(self, kind):
        return [question for asked, question, _ in self.asked if asked == kind]


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class LiveSayTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name, text in (("occasions.txt", OCCASIONS), ("nicknames.txt", NICKNAMES)):
            with open(os.path.join(folder.name, name), "w", encoding="utf-8") as f:
                f.write(text)
        self.model = FakeModel()
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(folder.name, "memes.json")),
                mock.patch.object(saying, "SAID_FILE", os.path.join(folder.name, "said.json")),
                mock.patch.object(uses, "OCCASIONS_FILE",
                                  os.path.join(folder.name, "occasions.txt")),
                mock.patch.object(nicknames, "NICKNAMES_FILE",
                                  os.path.join(folder.name, "nicknames.txt")),
                mock.patch.object(judging, "_ask", self.model),
                mock.patch.object(judging, "MODEL", "qwen3:14b"),
                mock.patch.object(judging, "_get_json",
                                  lambda path: {"models": [{"name": "qwen3:14b"}]}),
                mock.patch.object(judging, "RETRY_WAIT", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        store.save({"memes": json.loads(json.dumps(MEMES)), "rejected": {}})
        self.clock, self.out = Clock(), io.StringIO()

    def watcher(self, **options):
        options = dict({"count": 2, "rng": Fixed(), "out": self.out, "clock": self.clock},
                       **options)
        return livesay.Watcher(dict(ROOM), **options)

    # -- 1. what the model is shown, and what it tells ----------------------------------------

    def test_the_model_is_shown_each_line_once_with_how_many_people_sent_it(self):
        heard = [danmu("下雨了", "a"), danmu("下雨了", "b"), danmu("下雨了", "b"),  # b twice
                 danmu("666", "c"), danmu("感谢老板的火箭", "d", auto=1),
                 danmu("[大笑]", "e", sticker=1), danmu("   ", "f"), danmu("哈" * 60, "g")]
        self.assertEqual(livesay.window(heard), ["下雨了 ×2", "哈" * livesay.DANMU_LENGTH, "666"])
        self.assertEqual(livesay.window(heard, shown=1), ["下雨了 ×2"])

    def test_the_model_tells_what_is_going_on_from_the_danmu_with_names_from_them(self):
        seen = dict(SEEN, situation="TheShy 和 Faker 对线，TheShy 被抓了",
                    names=[{"name": "TheShy", "who": "选手"}, {"name": "Faker", "who": "选手"}])
        self.model.seen = seen
        ask = saying.meanings._Asker(None, "")
        situation, moment = livesay.judge(crowd(), "TES vs IG", "刚才 IG 拿了小龙", ask,
                                          uses.occasions())
        kind, question, told = self.model.asked[0]
        self.assertEqual(question, {"title": "TES vs IG", "danmu": livesay.window(crowd()),
                                    "before": "刚才 IG 拿了小龙"})
        self.assertIn("- 打得烂 = 失误、送人头", told)  # the occasions come after the question
        self.assertEqual(situation, seen["situation"])
        # Faker is in what the model said, but nobody sent his name
        self.assertEqual((moment["when"], moment["game"], [n["name"] for n in moment["names"]]),
                         (["打得烂", "要输了"], "lol", ["TheShy"]))
        self.model.seen = dict(SEEN, situation=" ")  # the danmu don't tell
        self.assertEqual(livesay.judge(crowd(), "", "", ask, uses.occasions()),
                         ("", {"when": [], "game": "", "names": []}))

    # -- looking, and saying ----------------------------------------------------------------

    def test_a_look_says_lines_and_other_programs_get_them(self):
        watcher = self.watcher()
        for item in crowd():
            watcher.heard(item)
        said = watcher.look()
        self.assertEqual([(line["line"], line["meme"]) for line in said["lines"]],
                         [("TheShy出", "wei出"), ("阿一古 一级就送", "阿一古")])
        self.assertEqual(said["lines"][0]["meaning"], MEMES["wei出"]["meaning"])
        self.assertEqual((said["id"], said["situation"], said["when"], said["why"]),
                         (1, SEEN["situation"], ["打得烂", "要输了"], ""))
        self.assertEqual(self.model.of("write")[0]["count"], 2)
        self.assertIn("1. TheShy出", self.out.getvalue())
        got = watcher.lines()
        self.assertEqual((got["room"], got["started"], got["every"], got["heard"]),
                         (ROOM, 1_800_000_000.0, livesay.EVERY, 10))
        self.assertEqual(got["rounds"][0]["lines"][1]["line"], "阿一古 一级就送")
        self.assertNotIn("rejected", got["rounds"][0])
        self.assertEqual(watcher.lines(after=1)["rounds"], [])
        self.clock.now += 61  # nobody sent anything for a minute
        self.assertEqual(watcher.lines()["heard"], 0)

    def test_it_looks_only_when_enough_danmu_came_and_is_quiet_when_they_dont_tell(self):
        watcher = self.watcher()
        for item in crowd(livesay.MIN_DANMU - 1) + [danmu("礼物", "x", auto=1)]:
            watcher.heard(item)
        self.assertIsNone(watcher.look())
        self.assertEqual(self.model.asked, [])
        for item in crowd(livesay.MIN_DANMU):
            watcher.heard(item)
        self.model.seen = {"situation": "", "when": [], "game": "", "names": []}
        self.assertEqual(watcher.look()["why"], "弹幕里看不出比赛在发生什么")
        for item in crowd(livesay.MIN_DANMU):
            watcher.heard(item)
        self.model.seen = dict(SEEN, when=["不是场面"])
        self.assertEqual(watcher.look()["why"], f"看不出「{SEEN['situation']}」是什么场面")
        self.assertEqual(self.model.of("write"), [])  # nothing to write about
        self.assertIn("(弹幕里看不出比赛在发生什么)", self.out.getvalue())

    def test_what_it_saw_last_is_before_and_memes_it_said_lately_come_less(self):
        watcher, spoken = self.watcher(), []

        def speak(*args):
            spoken.append((set(args[5]), set(args[6])))
            return real(*args)

        real = saying.speak
        with mock.patch.object(saying, "speak", speak):
            for _ in range(2):
                for item in crowd():
                    watcher.heard(item)
                watcher.look()
                self.clock.now += livesay.EVERY
            self.clock.now += livesay.RECENT
            for item in crowd():
                watcher.heard(item)
            watcher.look()
        # Said a moment ago, and in the last looks; then too long ago, but still in the last looks
        self.assertEqual(spoken, [(set(), set()), ({"wei出", "阿一古"}, {"wei出", "阿一古"}),
                                  (set(), {"wei出", "阿一古"})])
        befores = [question["before"] for question in self.model.of("look")]
        self.assertEqual(befores, ["", SEEN["situation"], ""])  # too long ago the last time
        memes = store.load()["memes"]
        moment = {"when": SEEN["when"], "game": "lol",
                  "names": saying.names_in(SEEN["situation"], SEEN["names"])}
        self.assertEqual(saying.draw(memes, moment, rng=Fixed())[:2], ["阿一占", "wei出"])
        self.assertEqual(saying.draw(memes, moment, rng=Fixed(), recent={"阿一占"})[:2],
                         ["wei出", "阿一占"])  # 5 x AGAIN = 1, under wei出's 1.875
        every = saying.draw(memes, moment, rng=Fixed())
        drawn = saying.draw(memes, moment, rng=Fixed(), last={"阿一占"}, least=len(every) - 1)
        self.assertEqual(drawn, [meme for meme in every if meme != "阿一占"])  # left out
        drawn = saying.draw(memes, moment, rng=Fixed(), last={"阿一占"}, least=len(every))
        self.assertEqual(drawn[-1], "阿一占")  # too few others: after them, however well it fits
        self.assertEqual(sorted(drawn), sorted(every))

    def test_a_model_that_cant_be_asked_is_said_once_and_it_goes_on_listening(self):
        watcher = self.watcher()
        gone = mock.Mock(side_effect=judging.ModelUnavailable("Ollama isn't running"))
        with mock.patch.object(judging, "_ask", gone):
            for _ in range(2):
                for item in crowd():
                    watcher.heard(item)
                self.assertIn("没有模型能用：Ollama isn't running", watcher.look()["why"])
        for item in crowd():  # it is back
            watcher.heard(item)
        self.assertEqual(len(watcher.look()["lines"]), 2)
        self.assertEqual(self.out.getvalue().count("没有模型能用"), 1)

    # -- other programs ---------------------------------------------------------------------

    def test_the_lines_are_served_to_programs_on_this_computer_only(self):
        watcher = self.watcher()
        for item in crowd():
            watcher.heard(item)
        watcher.look()
        server = livesay.Server(watcher, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy here

        def get(path, host=None):
            request = urllib.request.Request(server.url.replace("/lines", path),
                                             headers={"Host": host} if host else {})
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read().decode("utf-8"))

        status, got = get("/lines?after=0")
        self.assertEqual((status, got["rounds"][0]["lines"][0]["line"]), (200, "TheShy出"))
        self.assertEqual(get("/lines?after=1")[1]["rounds"], [])
        self.assertEqual(get("/lines?after=x")[1]["rounds"][0]["id"], 1)
        self.assertEqual(get("/")[0], 404)
        self.assertEqual(get("/lines", host="evil.example:80")[0], 403)

    def test_live_say_listens_to_the_room_says_lines_and_stops_with_it(self):
        made, served = [], []

        class Watcher(livesay.Watcher):
            def __init__(self, *args, **options):
                super().__init__(*args, **dict(options, rng=Fixed()))
                made.append(self)

        def record(link, until=None, game="lol", out=None, heard=None):
            self.assertEqual((link, game), ("https://live.bilibili.com/6", "lol"))
            for item in crowd():
                heard(item)
            for _ in range(200):  # it looks in its own thread
                if made[0].rounds:
                    break
                time.sleep(0.02)
            url = out.getvalue().split("lines at ")[1].split(" ")[0]
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    url, timeout=10) as response:
                served.append(json.loads(response.read().decode("utf-8")))
            return "6-20261124-2200.jsonl"

        found = {"room_id": 7734200, "short_id": 6, "live": True, "title": "TES vs IG"}
        with mock.patch.object(live, "room_info", return_value=found), \
                mock.patch.object(live, "record", record), \
                mock.patch.object(livesay, "Watcher", Watcher):
            watcher = livesay.run("https://live.bilibili.com/6", count=2, every=0.05, port=0,
                                  out=self.out)
        self.assertEqual(served[0]["rounds"][0]["lines"][0]["line"], "TheShy出")
        printed = self.out.getvalue()
        self.assertIn("room 6 (TES vs IG)", printed)
        self.assertIn("1. TheShy出", printed)
        self.assertIn(f"It said 2 lines in the last {len(watcher.rounds)} looks", printed)
        with self.assertRaises(urllib.error.URLError):  # the server stopped with it
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                printed.split("lines at ")[1].split(" ")[0], timeout=2)

    def test_the_command_listens_until_the_time_given(self):
        calls = []

        def run(*args):
            calls.append(args)

        def command(*args):
            with mock.patch.object(livesay, "run", run), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    cli.main(list(args))
                    return 0
                except SystemExit as stop:
                    return stop.code

        self.assertEqual(command("live-say", "6"), 0)
        self.assertEqual(calls[-1], ("6", livesay.COUNT, livesay.EVERY, livesay.PORT,
                                     "qwen3:14b", None, None))
        before = time.time()
        command("live-say", "https://live.bilibili.com/6", "-n", "2", "--every", "20",
                "--port", "9000", "--game", "lol", "--hours", "1")
        link, count, every, port, model, game, until = calls[-1]
        self.assertEqual((link, count, every, port, game), (
            "https://live.bilibili.com/6", 2, 20, 9000, "lol"))
        self.assertAlmostEqual(until, before + 3600, delta=60)
        run = mock.Mock(side_effect=OSError("address in use"))
        with mock.patch.object(livesay, "run", run), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            cli.main(["live-say", "6"])
        self.assertIn("--port", str(stop.exception.code))
        with mock.patch.object(livesay, "run", mock.Mock(side_effect=ValueError("no room"))), \
                self.assertRaises(SystemExit) as stop:
            cli.main(["live-say", "x"])
        self.assertEqual(stop.exception.code, "no room")


if __name__ == "__main__":
    unittest.main()
