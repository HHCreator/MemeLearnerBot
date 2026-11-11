"""Offline tests for running by itself for days (auto.py: py -m memelearner auto). Task
Scheduler, git, Bilibili, the live room and the model are all made up: whether Windows takes
the tasks the way they are written is only shown by 开始自动跑.bat on a real computer."""

import contextlib
import datetime
import io
import json
import os
import sys
import time
import unittest
import xml.etree.ElementTree as ElementTree
from unittest import mock

from memelearner import auto, bilibili, cli, explainers, judging, live, review, surf

from .test_explainers import Folder

TASK = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
LOL = {"videos": 4, "recent": 120, "before": 300, "baseline": True}
VALORANT = {"videos": 2, "recent": 30, "before": 80, "baseline": True}


def at(element, path):
    """The element at path (like Settings/StartWhenAvailable) in a task's XML."""
    return element.find("/".join(TASK + part for part in path.split("/")))


def candidate(phrase, people=6, game="lol", status="new"):
    """A phrase as the finder lists it (surf.find_candidates())."""
    return {"phrase": phrase, "game": game, "status": status, "people": people,
            "people_recent": people, "people_before": 0, "alone": 0.9, "videos": 2,
            "rise": None, "score": people * 2.0, "variants": [], "pattern": phrase,
            "names": [], "examples": [{"text": f"{phrase}！", "video": "BV1aaaaaaaaa"}]}


class FakeBot:
    """Stands in for reading Bilibili (surf.learn), the judge and explain. A run finds
    self.found; the judge calls the phrases in self.memes memes (and leaves the ones in
    self.unjudged alone); explain finds a web page for the ones in self.told. Each prints
    what self.said has for it, as the real ones print what goes wrong."""

    def __init__(self):
        self.found = [candidate("阿一古", 9), candidate("正义必胜", 7), candidate("fpx", 5)]
        self.memes = {"阿一古": "主播的口头禅", "正义必胜": "给更受欢迎的一方加油"}
        self.told, self.unjudged, self.down = {"阿一古"}, set(), False
        self.said = {"learn": "", "judge": "", "explain": ""}
        self.steps = []

    def learn(self, games):
        self.steps.append(("learn", list(games)))
        print(self.said["learn"])
        if isinstance(self.found, Exception):
            raise self.found
        counts = {"lol": LOL, "valorant": VALORANT}
        surf._save_json(surf.VIDEOS_FILE, {"BV1aaaaaaaaa": {
            "game": "lol", "title": "英雄联盟 a 组比赛", "author": "",
            "url": "https://www.bilibili.com/video/BV1aaaaaaaaa"}})  # what it read
        surf.save(self.found, counts, 2)
        return self.found, counts

    def judge(self, games, model=None, again=False, phrases=None):
        self.steps.append(("judge", model))
        print(self.said["judge"])
        if self.down:
            raise judging.ModelUnavailable("the model isn't running")
        last = surf._load_json(surf.CANDIDATES_FILE, {})
        for c in last["candidates"]:
            if c["phrase"] in self.unjudged:
                continue
            meaning = self.memes.get(c["phrase"])
            c["judge"] = ({"verdict": "meme", "kind": "meme", "meaning": meaning,
                           "english": "", "why": "", "guess": False, "model": model}
                          if meaning else {"verdict": "not a meme", "kind": "name",
                                           "meaning": "", "why": "a team", "model": model})
        surf._save_json(surf.CANDIDATES_FILE, last)

    def explain(self, phrases=None, games=None, links=None, model=None, videos=None,
                top=explainers.TOP, again=False, hearing=True, judge=True):
        self.steps.append(("explain", model, top))
        print(self.said["explain"])
        data = explainers.load()
        for c in surf._load_json(surf.CANDIDATES_FILE, {})["candidates"]:
            if (c.get("judge") or {}).get("kind") != "meme":
                continue
            entry = data["phrases"].setdefault(f"{c['game']}:{c['phrase']}", {})
            entry["looked"] = time.strftime("%Y-%m-%d %H:%M")
            if c["phrase"] in self.told:
                entry["pages"] = [{"kind": "page", "url": "https://zh.moegirl.org.cn/x",
                                   "site": "moegirl", "title": c["phrase"], "about": True,
                                   "meaning": self.memes[c["phrase"]]}]
        explainers._save(data)
        return {}


class AutoTest(Folder):
    """A test with a cache of its own (test_explainers.Folder), the made-up bot and Task
    Scheduler, and no sleeping."""

    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.tasks = []  # what schtasks was asked, and the XML files it was given
        self.answers = {}  # schtasks' first argument: (exit code, what it prints)
        for patcher in (
                mock.patch.object(surf, "learn", self.bot.learn),
                mock.patch.object(judging, "judge", self.bot.judge),
                mock.patch.object(explainers, "explain", self.bot.explain),
                mock.patch.object(judging, "_execution_state", lambda flags: None),
                mock.patch.object(auto, "_schtasks", self.schtasks),
                mock.patch.object(auto, "_git", side_effect=AssertionError("a test ran git")),
                mock.patch.object(auto, "_again",
                                  side_effect=AssertionError("a test ran the bot again")),
                mock.patch.object(time, "sleep", lambda seconds: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def schtasks(self, *args):
        self.tasks.append(args)
        return self.answers.get(args[0], (0, "SUCCESS"))

    def run_quietly(self, function, *args, **options):
        with contextlib.redirect_stdout(io.StringIO()) as printed, \
                contextlib.redirect_stderr(printed):
            result = function(*args, **options)
        return result, printed.getvalue()

    def task(self, kind):
        """The task XML start() wrote, as elements."""
        return ElementTree.parse(os.path.join(auto.auto_dir(), f"{kind}.xml")).getroot()

    # -- start, stop, status ---------------------------------------------------------------

    def test_start_makes_the_tasks_and_starts_the_first_daily_run_at_once(self):
        before = datetime.datetime.now()
        made, printed = self.run_quietly(auto.start, "9:30", 3, 20,
                                         ["6", "https://live.bilibili.com/21144080:valorant"],
                                         ["lol", "valorant"], 5, True)
        self.assertTrue(made)
        xml = lambda kind: os.path.join(auto.auto_dir(), f"{kind}.xml")
        self.assertEqual(self.tasks, [
            ("/Create", "/TN", "MemeLearnerBot daily", "/XML", xml("daily"), "/F"),
            ("/Create", "/TN", "MemeLearnerBot live 6", "/XML", xml("live-6"), "/F"),
            ("/Create", "/TN", "MemeLearnerBot live 21144080", "/XML", xml("live-21144080"),
             "/F"),
            ("/Run", "/TN", "MemeLearnerBot daily")])
        daily, recording = self.task("daily"), self.task("live-21144080")
        trigger = at(daily, "Triggers/CalendarTrigger")
        tomorrow = before.date() + datetime.timedelta(days=1)
        self.assertEqual(at(trigger, "StartBoundary").text, f"{tomorrow}T09:30:00")
        self.assertEqual(at(trigger, "ScheduleByDay/DaysInterval").text, "1")
        end = datetime.datetime.strptime(at(trigger, "EndBoundary").text, "%Y-%m-%dT%H:%M:%S")
        self.assertLess(abs(end - (before + datetime.timedelta(days=3))),
                        datetime.timedelta(minutes=2))
        self.assertEqual(at(daily, "Actions/Exec/Arguments").text, "-m memelearner auto daily")
        self.assertEqual(at(daily, "Actions/Exec/WorkingDirectory").text, auto.code_folder())
        self.assertEqual(at(daily, "Settings/MultipleInstancesPolicy").text, "IgnoreNew")
        self.assertEqual(at(daily, "Settings/StartWhenAvailable").text, "true")
        self.assertEqual(at(daily, "Settings/ExecutionTimeLimit").text, "PT8H")
        self.assertEqual(at(daily, "Principals/Principal/LogonType").text, "InteractiveToken")
        trigger = at(recording, "Triggers/TimeTrigger")
        self.assertEqual(at(trigger, "Repetition/Interval").text, "PT20M")
        self.assertEqual(at(trigger, "Repetition/Duration").text, "P3D")
        self.assertEqual(at(trigger, "EndBoundary").text,
                         at(at(daily, "Triggers/CalendarTrigger"), "EndBoundary").text)
        self.assertEqual(at(recording, "Actions/Exec/Arguments").text,
                         "-m memelearner auto live --rooms 21144080:valorant")
        self.assertEqual(at(recording, "Settings/ExecutionTimeLimit").text, "PT8H")  # 5 + 3
        first = lambda kind: at(at(self.task(kind), "Triggers/TimeTrigger"), "StartBoundary").text
        self.assertLess(first("live-6"), first("live-21144080"))  # one after the other
        found = auto.settings()
        self.assertEqual((found["at"], found["days"], found["every"], found["hours"],
                          found["rooms"], found["games"], found["pull"]),
                         ("9:30", 3, 20, 5, ["6:lol", "21144080:valorant"],
                          ["lol", "valorant"], True))
        self.assertIn("第一次现在就开始", printed)
        self.assertIn("每次先拉一下这个分支的新代码", printed)
        self.assertIn("停止自动跑.bat", printed)
        # Started again with other rooms: the task of the room left out goes
        self.tasks.clear()
        self.run_quietly(auto.start, rooms=["21144080:valorant"])
        self.assertEqual(self.tasks[0], ("/Delete", "/TN", "MemeLearnerBot live 6", "/F"))
        self.assertEqual([args[2] for args in self.tasks[1:]],
                         ["MemeLearnerBot daily", "MemeLearnerBot live 21144080",
                          "MemeLearnerBot daily"])

    def test_start_makes_nothing_it_cannot_and_says_why(self):
        self.answers["/Create"] = (1, "ERROR: Access is denied.")
        made, printed = self.run_quietly(auto.start)
        self.assertFalse(made)
        self.assertIn("Couldn't make the task MemeLearnerBot daily: ERROR: Access is denied.",
                      printed)
        self.assertNotIn("/Run", [args[0] for args in self.tasks])
        self.assertEqual(auto.settings()["started"], "")  # not started
        for wrong in ({"at": "25:00"}, {"at": "noon"}, {"rooms": ["lpl"]},
                      {"games": ["dota"]}, {"rooms": ["6:dota"]}):
            with self.assertRaises(ValueError):
                auto.start(**wrong)

    def test_stop_ends_and_deletes_the_tasks_and_lets_go_of_the_turn(self):
        os.makedirs(auto.auto_dir())
        open(os.path.join(auto.auto_dir(), "lock"), "w").close()
        self.answers["/Delete"] = (1, "ERROR: The system cannot find the file specified.")
        _, printed = self.run_quietly(auto.stop)
        self.assertEqual(self.tasks, [("/End", "/TN", "MemeLearnerBot daily"),
                                      ("/Delete", "/TN", "MemeLearnerBot daily", "/F"),
                                      ("/End", "/TN", "MemeLearnerBot live 6"),
                                      ("/Delete", "/TN", "MemeLearnerBot live 6", "/F")])
        self.assertFalse(os.path.exists(os.path.join(auto.auto_dir(), "lock")))
        self.assertIn("没删掉（可能本来就没有）", printed)

    def test_status_shows_the_tasks_the_runs_the_rooms_and_the_end_of_the_log(self):
        self.run_quietly(auto.start)
        self.run_quietly(auto.daily)
        with open(auto.log_file(), "w", encoding="utf-8") as f:
            f.write("".join(f"line {n}\n" for n in range(100)))
        surf._save_json(os.path.join(auto.auto_dir(), "rooms.json"),
                        {"6": {"looked": "2026-11-10 20:30", "how": "没开播"}})

        def query(*args):
            self.tasks.append(args)
            if args[2] == "MemeLearnerBot live 6":
                return 1, "ERROR: The system cannot find the file specified."
            return 0, '"\\MemeLearnerBot daily","2026/11/11 12:00:00","就绪"\r\n'

        with mock.patch.object(auto, "_schtasks", query):
            _, printed = self.run_quietly(auto.status, 5)
        self.assertIn("「MemeLearnerBot daily」：下次 2026/11/11 12:00:00，现在 就绪", printed)
        self.assertIn("「MemeLearnerBot live 6」：没有这个任务", printed)
        self.assertIn("直播间 6：2026-11-10 20:30 看的时候没开播", printed)
        self.assertIn("== 总结：第 1 次（每天的）", printed)
        self.assertIn("待审清单：3 个说法，2 个是梗。", printed)
        self.assertTrue(printed.rstrip().endswith("line 95\nline 96\nline 97\nline 98\nline 99"))
        self.assertNotIn("line 94", printed)

    # -- a daily run -----------------------------------------------------------------------

    def test_a_daily_run_reads_judges_looks_every_meme_up_and_keeps_them(self):
        self.bot.said["learn"] = "Bilibili wants a captcha for its search now."
        self.bot.said["explain"] = ("Left alone for the rest of the run: baidu (a captcha); "
                                    "duckduckgo (HTTP 403)")
        _, printed = self.run_quietly(auto.daily, "deepseek/deepseek-flash")
        self.assertEqual(self.bot.steps, [("learn", ["lol", "valorant"]),
                                          ("judge", "deepseek/deepseek-flash"),
                                          ("explain", "deepseek/deepseek-flash", None)])
        pool = auto.load_pool()
        [run] = pool["runs"]
        self.assertEqual((run["n"], run["kind"], run["read"], run["found"], run["memes"],
                          run["explained"], run["unexplained"], run["error"]),
                         (1, "daily", "6 个视频里最近的 150 条弹幕和评论", 3, 2, 1, ["正义必胜"], ""))
        self.assertEqual(run["problems"], {"B 站要验证码，那一步跳过了": 1, "有网站挡住了机器人": 1})
        self.assertIn(f"== Run 1 (daily), {run['started']}", printed)
        self.assertTrue(printed.rstrip().endswith(run["summary"]))
        self.assertIn("找到 3 个说法，2 个是梗；查到解释 1 个，没查到 1 个：正义必胜；"
                      "问题：B 站要验证码，那一步跳过了 1 次；有网站挡住了机器人 1 次", run["summary"])
        self.assertEqual(sorted(pool["candidates"]), ["lol:fpx", "lol:正义必胜", "lol:阿一古"])
        ayigu = pool["candidates"]["lol:阿一古"]
        self.assertEqual((ayigu["runs"], ayigu["first"], ayigu["judge"]["meaning"]),
                         ([1], run["started"], "主播的口头禅"))
        self.assertIn("BV1aaaaaaaaa", pool["videos"])
        self.assertFalse(os.path.exists(os.path.join(auto.auto_dir(), "lock")))

        # The next day: 阿一古 again, sent by fewer people and not judged this time (it keeps
        # its verdict and the most people), and a new phrase
        self.bot.found = [candidate("阿一古", 4), candidate("老霸道了", 6)]
        self.bot.unjudged = {"阿一古"}
        self.bot.said = {"learn": "", "judge": "", "explain": ""}
        self.run_quietly(auto.daily)
        pool = auto.load_pool()
        self.assertEqual([run["n"] for run in pool["runs"]], [1, 2])
        self.assertEqual(pool["runs"][1]["problems"], {})
        self.assertIn("问题：没有", pool["runs"][1]["summary"])
        ayigu = pool["candidates"]["lol:阿一古"]
        self.assertEqual((ayigu["runs"], ayigu["people"], ayigu["judge"]["meaning"]),
                         ([1, 2], 9, "主播的口头禅"))
        self.assertEqual(pool["candidates"]["lol:老霸道了"]["runs"], [2])
        self.assertEqual(len(pool["candidates"]), 4)

    def test_without_a_model_the_phrases_are_kept_unjudged(self):
        self.bot.down = True
        _, printed = self.run_quietly(auto.daily)
        self.assertIn("Couldn't ask the model: the model isn't running", printed)
        self.assertNotIn("explain", [step[0] for step in self.bot.steps])
        pool = auto.load_pool()
        self.assertEqual(len(pool["candidates"]), 3)
        self.assertEqual(pool["runs"][0]["problems"], {"模型都问不了": 1})
        self.assertEqual((pool["runs"][0]["found"], pool["runs"][0]["memes"]), (3, 0))

    def test_a_run_that_goes_wrong_is_logged_and_the_next_one_goes_on(self):
        self.bot.found = RuntimeError("something broke")
        _, printed = self.run_quietly(auto.daily)
        self.assertIn("Traceback", printed)
        [run] = auto.load_pool()["runs"]
        self.assertEqual(run["error"], "RuntimeError: something broke")
        self.assertEqual(run["problems"], {"出错": 1})
        self.assertIn("停在：RuntimeError: something broke", run["summary"])
        self.assertFalse(os.path.exists(os.path.join(auto.auto_dir(), "lock")))
        self.bot.found = [candidate("阿一古", 9)]
        self.run_quietly(auto.daily)
        self.assertEqual([run["n"] for run in auto.load_pool()["runs"]], [1, 2])

    def test_each_problem_is_counted_from_what_the_bot_prints_when_it_happens(self):
        model = "deepseek/deepseek-flash"
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}), \
                self.assertRaises(judging.ModelUnavailable) as no_key:
            judging._key(model)
        printed = "\n".join([
            "== Run 1 (daily), 2026-11-10 12:00",
            "  Bilibili refused a request (HTTP 412, code -412); waiting 60 seconds before "
            "trying again...",  # it tries again: not a problem yet
            "Bilibili keeps refusing requests from this computer (HTTP 412, code -412). Wait "
            "an hour or so and try again, or read fewer videos.",
            "    Bilibili refused https://www.bilibili.com/video/BV1aaaaaaaaa (HTTP 429)",
            "  Bilibili wants a captcha for the search, so it's skipped this time",
            "  skipped BV1bbbbbbbbb (network trouble: timed out)",
            f"{model}: {judging._refusal(model, 402)} (HTTP 402: Insufficient Balance).",
            f"So {judging.LOCAL_MODEL}, the model on this computer, judges instead.",
            f"{no_key.exception}.",
            f"{model}: {judging._refusal(model, 401)} (HTTP 401: invalid key).",
            "Couldn't ask the model: no model. The phrases are kept as they are.",
            "Left alone for the rest of the run: baidu (a captcha)",
            "Traceback (most recent call last):"])
        run = {"n": 1, "kind": "daily", "started": "2026-11-10 12:00", "read": "", "found": 0,
               "memes": 0, "explained": 0, "unexplained": [], "problems": {}, "error": ""}
        self.run_quietly(auto._end, run, printed)
        self.assertEqual(run["problems"], {
            "B 站限速（412）或风控": 2, "B 站要验证码，那一步跳过了": 1, "连不上 B 站": 1,
            "DeepSeek 没余额": 1, "DeepSeek 的密钥没设或不对": 2, "在线模型问不了，本地模型顶上": 1,
            "模型都问不了": 1, "有网站挡住了机器人": 1, "出错": 1})

    def test_runs_take_turns_and_a_turn_left_by_a_run_that_died_is_taken(self):
        os.makedirs(auto.auto_dir())
        lock = os.path.join(auto.auto_dir(), "lock")
        open(lock, "w").close()
        with mock.patch.object(auto, "WAIT_MINUTES", 0):
            _, printed = self.run_quietly(auto.daily)
        self.assertEqual(self.bot.steps, [])  # it waited, then gave up
        [run] = auto.load_pool()["runs"]
        self.assertIn("停在：another run kept its turn for 0 minutes", run["summary"])
        self.assertTrue(os.path.exists(lock))  # still the other run's
        old = time.time() - (auto.LOCK_HOURS + 1) * 3600
        os.utime(lock, (old, old))
        _, printed = self.run_quietly(auto.daily)
        self.assertIn("hours ago was never given back (that run must have stopped halfway): "
                      "taking it.", printed)
        self.assertEqual(self.bot.steps[0], ("learn", ["lol", "valorant"]))
        self.assertFalse(os.path.exists(lock))

    def test_a_daily_run_pulls_the_newest_code_first_and_runs_that(self):
        heads = iter(["aaaaaaa1", "bbbbbbb2"])
        git = mock.Mock(side_effect=lambda folder, *args: mock.Mock(
            returncode=0, stdout=next(heads) if args[0] == "rev-parse" else "", stderr=""))
        again = mock.Mock()
        with mock.patch.object(auto, "_git", git), mock.patch.object(auto, "_again", again):
            _, printed = self.run_quietly(auto.daily, pull=True)
        self.assertIn("Pulled the newest code (aaaaaaa -> bbbbbbb).", printed)
        self.assertEqual(again.call_args[0], ([sys.executable, "-m", "memelearner", "auto",
                                               "daily", "--no-pull"], auto.code_folder()))
        self.assertEqual(self.bot.steps, [])  # the new code reads, not this one
        failed = mock.Mock(side_effect=lambda folder, *args: mock.Mock(
            returncode=1 if args[0] == "pull" else 0, stdout="aaaaaaa1",
            stderr="fatal: Not possible to fast-forward, aborting."))
        with mock.patch.object(auto, "_git", failed):
            _, printed = self.run_quietly(auto.daily, pull=True)
        self.assertIn("Couldn't pull the newest code: fatal: Not possible to fast-forward",
                      printed)
        self.assertEqual(self.bot.steps[0][0], "learn")  # so the code it has runs

    # -- a live run ------------------------------------------------------------------------

    def test_a_live_room_is_recorded_while_it_is_live_then_its_phrases_are_looked_up(self):
        surf._save_json(os.path.join(auto.auto_dir(), "settings.json"),
                        {"rooms": ["6:lol", "21144080:valorant"], "hours": 4})
        live_now, recorded, logged = set(), [], []

        def room_info(room):
            if room == 6:
                raise bilibili.Blocked("Bilibili keeps refusing requests (HTTP 412)")
            return {"room_id": room, "short_id": 0, "live": room in live_now, "title": "VCT"}

        def record(link, until=None, path=None, game="lol", out=None, stop=None):
            recorded.append((link, game, until, stop))
            return os.path.join(live.live_dir(), "21144080-20261110-1900.jsonl")

        phrases = ([candidate(p, game="valorant") for p in ("懂你意思", "未识别到人脸", "噢噢噢")]
                   + [candidate(p, game="valorant", status="steady") for p in ("gg", "666")])
        compared = {"recordings": ["21144080-20261110-1900.jsonl"], "start": int(time.time()),
                    "parts": 3, "danmu": 2400, "senders": 800, "before": 0, "minutes": 60}
        self.bot.found = phrases
        self.bot.memes = {"懂你意思": "懂了", "未识别到人脸": "嘲笑选手没打中"}
        self.bot.told = {"懂你意思"}
        with mock.patch.object(live, "room_info", room_info), \
                mock.patch.object(live, "record", record), \
                mock.patch.object(live, "find",
                                  lambda paths: (phrases, {"valorant": LOL}, compared)), \
                mock.patch.object(auto, "LIVE_TOP", 2), \
                mock.patch.object(auto, "LIVE_KNOWN_TOP", 1):
            path, printed = self.run_quietly(auto.watch, None, lambda: logged.append(1))
            self.assertIsNone(path)
            self.assertEqual(printed, "")  # nothing for the log
            self.assertEqual(auto.load_pool()["runs"], [])
            looked = surf._load_json(os.path.join(auto.auto_dir(), "rooms.json"), {})
            self.assertEqual(looked["21144080"]["how"], "没开播")
            self.assertIn("看不了", looked["6"]["how"])
            live_now.add(21144080)
            before = time.time()  # its own task looks at it
            path, printed = self.run_quietly(auto.watch, "qwen3:14b", lambda: logged.append(1),
                                             ["21144080:valorant"])
        self.assertEqual(logged, [1])
        self.assertTrue(path.endswith("21144080-20261110-1900.jsonl"))
        [(room, game, until, stop)] = recorded
        self.assertEqual((room, game), (21144080, "valorant"))
        self.assertLess(abs(until - (before + 4 * 3600)), 60)
        self.assertIsInstance(stop, auto._Offline)
        kept = [c["phrase"] for c in surf._load_json(surf.CANDIDATES_FILE, {})["candidates"]]
        self.assertEqual(kept, ["懂你意思", "未识别到人脸", "gg"])  # 2 new ones, 1 known
        self.assertEqual(self.bot.steps, [("judge", "qwen3:14b"), ("explain", "qwen3:14b", None)])
        [run] = auto.load_pool()["runs"]
        self.assertEqual((run["kind"], run["read"], run["memes"], run["explained"],
                          run["unexplained"]),
                         ("live", "2400 条直播弹幕（800 个人，21144080-20261110-1900.jsonl）", 2, 1,
                          ["未识别到人脸"]))
        self.assertIn("== 总结：第 1 次（直播）", run["summary"])

    def test_a_recording_without_danmu_has_nothing_to_look_for(self):
        with mock.patch.object(live, "room_info", lambda room: {"live": True}), \
                mock.patch.object(live, "record", lambda *a, **k: "6-20261110-1900.jsonl"), \
                mock.patch.object(live, "find", side_effect=ValueError(
                    "No danmu in 6-20261110-1900.jsonl")):
            path, printed = self.run_quietly(auto.watch)
        self.assertEqual(path, "6-20261110-1900.jsonl")
        self.assertIn("No danmu in 6-20261110-1900.jsonl: nothing to look for.", printed)
        [run] = auto.load_pool()["runs"]
        self.assertEqual((run["found"], run["error"], run["problems"]), (0, "", {}))
        self.assertEqual(self.bot.steps, [])
        self.assertFalse(os.path.exists(os.path.join(auto.auto_dir(), "lock")))

    def test_a_recording_stops_once_the_room_is_offline(self):
        answers = iter([{"live": True}, {"live": False}, bilibili.Error("no answer")])

        def room_info(room):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        stop = auto._Offline(6)
        with mock.patch.object(live, "room_info", room_info):
            self.assertEqual([stop(), stop(), stop()], [False, True, False])  # unsure: go on

    # -- the report ------------------------------------------------------------------------

    def test_the_report_shows_what_every_run_found_with_a_line_about_the_days_on_top(self):
        made = []

        class Report:
            def __init__(self, rows, model, port, line):
                made.append((rows, model, port, line))

            def run(self, browser):
                made.append(browser)
                return {"added": [], "rejected": [], "left": [], "score": {}}

        with mock.patch.object(review, "Report", Report):
            result, printed = self.run_quietly(auto.report)
            self.assertIsNone(result)
            self.assertIn("Nothing to report", printed)
            self.bot.said["learn"] = "Bilibili wants a captcha for its search now."
            self.run_quietly(auto.daily)
            self.bot.found = [candidate("老霸道了", 6)]
            self.bot.memes["老霸道了"] = "很霸道"
            self.bot.said["learn"] = "Bilibili wants a captcha for its search now."
            self.run_quietly(auto.daily)
            self.run_quietly(auto.report, "qwen3:14b", 8765, False)
        rows, model, port, line = made[0]
        self.assertEqual([row["phrase"] for row in rows][:3], ["阿一古", "正义必胜", "老霸道了"])
        self.assertEqual({row["phrase"] for row in rows}, {"阿一古", "正义必胜", "老霸道了", "fpx"})
        ayigu = next(row for row in rows if row["phrase"] == "阿一古")
        self.assertFalse(ayigu["unexplained"])
        self.assertTrue(next(row for row in rows if row["phrase"] == "正义必胜")["unexplained"])
        self.assertEqual((model, port, made[1]), ("qwen3:14b", 8765, False))
        self.assertRegex(line, r"^\d\d-\d\d \d\d:\d\d 到 \d\d-\d\d \d\d:\d\d：跑了 2 次（每天的 2 次，"
                               r"直播 0 次），找到 4 个说法，3 个是梗，1 个查到了解释；"
                               r"出过的问题：B 站要验证码，那一步跳过了 2 次$")

    # -- the command -----------------------------------------------------------------------

    def run_cli(self, *args):
        """py -m memelearner auto ...: returns what it printed and the log file's text."""
        with mock.patch.dict(os.environ), \
                mock.patch("sys.stdout", io.StringIO()) as screen, \
                mock.patch("sys.stderr", io.StringIO()):
            os.environ.pop("MEMELEARNER_LOG", None)
            try:
                cli.main(["auto", *args])
            finally:
                if hasattr(sys.stdout, "file"):
                    sys.stdout.file.close()
        try:
            with open(auto.log_file(), encoding="utf-8") as f:
                logged = f.read()
        except OSError:
            logged = None
        return screen.getvalue(), logged

    def test_the_command_logs_each_run_but_not_a_look_at_rooms_that_are_not_live(self):
        with mock.patch.object(live, "room_info",
                               lambda room: {"room_id": room, "short_id": 6, "live": False}):
            shown, logged = self.run_cli("live", "--rooms", "6:lol")
        self.assertIsNone(logged)  # no log yet: nothing happened
        self.assertEqual(shown, "")
        shown, logged = self.run_cli("daily")
        self.assertIn("py -m memelearner auto daily", logged)
        self.assertIn("== 总结：第 1 次（每天的）", logged)
        self.assertTrue(logged.endswith(shown))
        with mock.patch.object(auto, "start", return_value=True) as start:
            self.run_cli("start", "--at", "9:30", "--days", "2", "--rooms", "6",
                         "21144080:valorant", "--pull")
        start.assert_called_once_with("9:30", 2, auto.EVERY, ["6", "21144080:valorant"],
                                      list(auto.GAMES), auto.LIVE_HOURS, True)
        with mock.patch.object(auto, "start", return_value=True) as start:
            self.run_cli("start")
        start.assert_called_once_with(auto.AT, auto.DAYS, auto.EVERY, list(auto.ROOMS),
                                      list(auto.GAMES), auto.LIVE_HOURS, False)
        with mock.patch.object(auto, "watch") as watch:
            self.run_cli("live", "--rooms", "21144080:valorant")
        self.assertEqual(watch.call_args[0][2], ["21144080:valorant"])
        with mock.patch.object(auto, "daily") as daily:
            self.run_cli("daily", "--no-pull", "--model", "qwen3:14b")
        daily.assert_called_once_with("qwen3:14b", False)
        with mock.patch.object(auto, "start", return_value=False):
            with self.assertRaises(SystemExit):
                self.run_cli("start")

    def test_the_tasks_run_python_without_a_window(self):
        folder = os.path.join(self.folder, "Python312")
        os.makedirs(folder)
        for name in ("python.exe", "pythonw.exe"):
            open(os.path.join(folder, name), "w").close()
        with mock.patch.object(sys, "executable", os.path.join(folder, "python.exe")):
            self.assertEqual(auto._pythonw(), os.path.join(folder, "pythonw.exe"))
        with mock.patch.object(sys, "executable", os.path.join(folder, "python3.12")):
            self.assertEqual(auto._pythonw(), os.path.join(folder, "python3.12"))
        spaced = os.path.join(self.folder, "Program Files", "pythonw.exe")
        with mock.patch.object(auto, "_pythonw", lambda: spaced):
            self.run_quietly(auto.start)
        self.assertEqual(at(self.task("daily"), "Actions/Exec/Command").text, f'"{spaced}"')
        self.assertEqual(at(self.task("daily"), "Actions/Exec/WorkingDirectory").text,
                         auto.code_folder())  # never quoted


if __name__ == "__main__":
    unittest.main()
