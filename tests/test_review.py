"""Offline tests for the report in the browser (review.py) and the live-report command.

A fake model judges and merges, as in test_judge.py and test_meanings.py, and the page is
served on this computer and asked the way the browser asks it.
"""

import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from memelearner import cli, explainers, judging, meanings, review, store
from tests import test_judge

AYIGU, CHANT = test_judge.AYIGU, test_judge.CHANT
CHOICES = [  # (key, Mark, Decision, My meaning)
    ("lol:阿一古", "Correct", "Add", ""),
    ("lol:正义必胜", "Correct", "Add", "一般只在 ig 的比赛里刷"),  # a guess, marked Correct
    ("lol:怎么翻", "Wrong", "Add", "嘲笑落后太多、翻不了盘"),
    ("lol:神中神", "I don't know", "Not sure yet", ""),
    ("lol:fpx", "Correct", "Not a meme", "FPX 战队的名字"),
]


class ReviewTest(unittest.TestCase):
    write_run = test_judge.JudgeTest.write_run
    judge = test_judge.JudgeTest.judge

    def setUp(self):
        test_judge.JudgeTest.setUp(self)  # a fake model judges, in a folder of its own
        self.merged = []  # the merging questions the model was asked
        judge = self.model

        def ask(messages, model):
            if messages[0]["content"] != meanings.MERGE:
                return judge(messages, model)
            question = json.loads(messages[1]["content"])
            self.merged.append(question)
            meaning = question["model"] or question["same"][0]["meaning"]
            return json.dumps({"meaning": f"{meaning}；{question['note']}", "english": "merged"},
                              ensure_ascii=False)

        patcher = mock.patch.object(judging, "_ask", ask)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 阿一古 a meme, 正义必胜 a guess, 怎么翻 failed its test, 神中神 unclear, fpx a name
        self.write_run(people={"正义必胜": 8, "神中神": 6})
        self.judge()

    def choose(self, rows, choices=CHOICES):
        for key, mark, decision, note in choices:
            for field, value in (("mark", mark), ("decision", decision), ("note", note)):
                review.choose(rows, key, field, value)

    def test_the_likeliest_memes_come_first_with_the_judges_answers(self):
        rows = review.rows()
        self.assertEqual([row["phrase"] for row in rows], ["阿一古", "正义必胜", "怎么翻", "神中神", "fpx"])
        ayigu, chant, comeback, unclear, team = rows
        self.assertEqual((ayigu["verdict"], ayigu["meaning"], ayigu["guess"], ayigu["test"]),
                         ("meme", AYIGU, False, None))
        self.assertEqual(len(ayigu["examples"]), store.EXAMPLES)
        for line in ayigu["examples"][:review.SHOWN]:  # shown on the page: lines that say more
            self.assertNotEqual(store.normal(line), "阿一古")
        self.assertLessEqual(set(ayigu["sources"]), {"https://www.bilibili.com/video/BV1aaaaaaaaa",
                                                     "https://www.bilibili.com/video/BV1bbbbbbbbb"})
        self.assertTrue(ayigu["sources"])
        self.assertTrue(chant["guess"])
        self.assertEqual((comeback["verdict"], comeback["test"]["fits"]), ("watching", 0))
        self.assertEqual((unclear["kind"], team["kind"], team["why"]),
                         ("unclear", "name", "FPX 是一支战队"))
        self.assertEqual(chant["model"], "deepseek-r1:14b")
        store.reject("神中神", "an everyday phrase")  # learned or turned down: not in it
        self.assertNotIn("神中神", [row["phrase"] for row in review.rows()])

    def test_every_choice_is_saved_and_a_mark_goes_with_the_answer_it_is_on(self):
        rows = review.rows()
        self.choose(rows, [("lol:阿一古", "Correct", "Add", "主播的口头禅"),
                           ("lol:fpx", "", "Not a meme", "")])
        review.choose(rows, "lol:fpx", "decision", "")  # taken back
        self.assertEqual(review.chosen(review.rows())["lol:阿一古"],
                         {"mark": "Correct", "decision": "Add", "note": "主播的口头禅"})
        self.assertEqual(review.chosen(rows)["lol:fpx"], {"mark": "", "decision": "", "note": ""})
        for bad in (("lol:阿一古", "mark", "Great"), ("lol:没有", "mark", "Correct"),
                    ("lol:阿一古", "meaning", "x"), ("lol:阿一古", "note", None)):
            with self.assertRaises(ValueError, msg=bad):
                review.choose(rows, *bad)
        self.judge(again=True)  # the same answer again: its Mark stands
        self.assertEqual(review.chosen(review.rows())["lol:阿一古"]["mark"], "Correct")
        self.model.answers_for["deepseek-r1:14b"] = {"阿一古": dict(
            test_judge.FakeModel.ANSWERS["阿一古"], meaning="另一个意思")}
        self.judge(again=True)  # another answer: the Mark was on the old one
        self.assertEqual(review.chosen(review.rows())["lol:阿一古"],
                         {"mark": "", "decision": "Add", "note": "主播的口头禅"})

    def test_done_learns_the_adds_with_your_notes_merged_and_turns_down_the_rest(self):
        rows = review.rows()
        self.choose(rows)
        result = review.finish(rows)
        ayigu = store.lookup("阿一古")  # no note: the judge's meaning and its lines
        self.assertEqual((ayigu["meaning"], ayigu["english"]), (AYIGU, "a streamer's catchphrase"))
        self.assertEqual((ayigu["examples"], ayigu["sources"]),
                         (rows[0]["examples"], rows[0]["sources"]))
        chant = store.load()["memes"]["正义必胜"]  # a guess you marked Correct, merged
        self.assertEqual((chant["meaning"], chant["english"], chant["notes"]),
                         (f"{CHANT}；一般只在 ig 的比赛里刷", "merged", ["一般只在 ig 的比赛里刷"]))
        # Marked Wrong: the judge's meaning isn't merged, so your note is the meaning
        self.assertEqual((store.lookup("怎么翻")["meaning"], store.lookup("怎么翻")["english"]),
                         ("嘲笑落后太多、翻不了盘", ""))
        self.assertEqual([question["phrase"] for question in self.merged], ["正义必胜"])
        self.assertEqual(store.load()["rejected"]["fpx"]["reason"], "FPX 战队的名字")
        self.assertEqual([row["phrase"] for row in review.rows()], ["神中神"])  # for next time
        self.assertEqual([(a["phrase"], a["from"]) for a in result["added"]],
                         [("阿一古", "judge"), ("正义必胜", "merged"), ("怎么翻", "note")])
        self.assertEqual(result["rejected"], [{"phrase": "fpx", "reason": "FPX 战队的名字"}])
        self.assertEqual(result["left"], [{"phrase": "神中神", "decision": "Not sure yet"}])
        self.assertEqual(result["score"], {"deepseek-r1:14b": {
            "points": 2, "answers": 4, "marks": {"Correct": 3, "Wrong": 1, "I don't know": 1}}})
        self.assertIn("deepseek-r1:14b: 2 points for 4 answers (Correct 3, Wrong 1, "
                      "I don't know 1)", review.summary(result))
        # An answer counts once, and what is left keeps your choices
        again = review.finish(review.rows())
        self.assertEqual((again["added"], again["rejected"], again["score"]), ([], [], {}))
        self.assertEqual(review.chosen(review.rows())["lol:神中神"],
                         {"mark": "I don't know", "decision": "Not sure yet", "note": ""})

    def test_a_guess_you_didnt_mark_right_is_not_learned_and_no_note_gives_the_date(self):
        rows = review.rows()
        self.choose(rows, [("lol:正义必胜", "", "Add", ""), ("lol:fpx", "", "Not a meme", "")])
        result = review.finish(rows)
        self.assertEqual(store.lookup("正义必胜")["meaning"], "")
        self.assertEqual(result["added"][0]["from"], "")
        self.assertTrue(store.load()["rejected"]["fpx"]["reason"].startswith("not a meme (report "))
        self.assertEqual(result["score"], {})

    def test_done_learns_nothing_when_no_model_can_merge_your_notes(self):
        rows = review.rows()
        self.choose(rows, [("lol:阿一古", "", "Add", ""),
                           ("lol:正义必胜", "Correct", "Add", "一般只在 ig 的比赛里刷")])

        def down(messages, model):
            raise judging.ModelUnavailable("Ollama isn't running")

        with mock.patch.object(judging, "_ask", down), \
                self.assertRaises(judging.ModelUnavailable):
            review.finish(rows)
        self.assertEqual(store.load()["memes"], {})
        self.assertEqual(review.chosen(rows)["lol:正义必胜"]["decision"], "Add")

    def test_the_page_holds_the_rows_and_no_line_can_end_its_script(self):
        rows = review.rows()
        line = "</script><script>alert(1)</script>"
        html = review.page([dict(rows[0], examples=[line])], {rows[0]["key"]: {
            "mark": "", "decision": "", "note": ""}}, "token123", "a test run")
        self.assertNotIn(line, html)
        start = html.index('<script type="application/json" id="data">') + 42
        data = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual((data["rows"][0]["examples"], data["token"], data["name"]),
                         ([line], "token123", "deepseek-r1:14b"))
        self.assertEqual(list(data["marks"]), list(review.MARKS))

    def test_the_page_is_only_served_through_its_link_and_done_stops_the_bot(self):
        rows = review.rows()
        report = review.Report(rows, line="a test run")
        base = report.url.split("?")[0].rstrip("/")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy here

        def ask(url, body=None, token=report.token, host=None):
            headers = {"Content-Type": "application/json", "X-Token": token}
            if host:
                headers["Host"] = host
            request = urllib.request.Request(url, None if body is None else json.dumps(
                body, ensure_ascii=False).encode("utf-8"), headers)
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                return error.code, error.read().decode("utf-8")

        ran = {}
        thread = threading.Thread(target=lambda: ran.update(result=report.run(browser=False)))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            thread.start()
            status, html = ask(report.url)
            self.assertEqual(status, 200)
            self.assertIn('"phrase": "阿一古"', html)
            self.assertEqual(ask(base + "/?t=wrong")[0], 403)
            save = {"key": "lol:阿一古", "field": "decision", "value": "Add"}
            self.assertEqual(ask(base + "/save", save, token="wrong")[0], 403)
            self.assertEqual(ask(base + "/save", save, host="evil.example")[0], 403)
            self.assertEqual(ask(base + "/save", dict(save, value="Great"))[0], 400)
            self.assertEqual(ask(base + "/save", save), (200, '{"saved": true}'))
            self.assertEqual(review.chosen(rows)["lol:阿一古"]["decision"], "Add")
            status, answer = ask(base + "/done", {})
            thread.join(10)
        self.assertEqual(status, 200)
        self.assertFalse(thread.is_alive())  # the bot stops once you clicked 完成
        self.assertEqual(json.loads(answer), ran["result"])
        self.assertEqual([a["phrase"] for a in ran["result"]["added"]], ["阿一古"])
        self.assertEqual(store.lookup("阿一古")["meaning"], AYIGU)
        self.assertIn(report.url, printed.getvalue())


class FakeReport:
    """Stands in for review.Report in the command tests: records what it was given."""

    def __init__(self, steps):
        self.steps = steps

    def __call__(self, rows, model, port, line):
        self.steps.append(("report", model, port))
        return self

    def run(self, browser):
        self.steps.append(("open", browser))
        return {"added": [{"phrase": "阿一古", "meaning": AYIGU, "from": "judge", "missing": []}],
                "rejected": [], "left": [], "score": {}}


class CommandTest(unittest.TestCase):
    def run_cli(self, *args):
        steps = []

        def judge(games, model, again):
            steps.append(("judge", model))
            if model == "down":
                raise judging.ModelUnavailable("the model is down")

        with mock.patch.object(cli, "_live_phrases",
                               lambda args: steps.append(("live-phrases", args.recordings))), \
                mock.patch.object(judging, "judge", judge), \
                mock.patch.object(judging, "_execution_state", lambda flags: None), \
                mock.patch.object(explainers, "explain",
                                  lambda *a: steps.append(("explain", a[3], a[5], a[7]))), \
                mock.patch.object(review, "rows", lambda: [{"key": "lol:阿一古"}]), \
                mock.patch.object(review, "about", lambda: ""), \
                mock.patch.object(review, "Report", FakeReport(steps)), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            try:
                cli.main(list(args))
            except SystemExit as stop:
                steps.append(("stopped", str(stop.code)))
        return steps, printed.getvalue()

    def test_live_report_finds_judges_explains_then_opens_the_report(self):
        steps, printed = self.run_cli("live-report", "6-20261107-1649")
        model = judging.MODEL
        self.assertEqual(steps, [("live-phrases", ["6-20261107-1649"]), ("judge", model),
                                 ("explain", model, explainers.TOP, True),
                                 ("report", model, 0), ("open", True)])
        self.assertIn("Learned 1 meme, turned down 0, left 0", printed)
        steps, _ = self.run_cli("live-report", "--model", "qwen3:14b", "--no-explain",
                                "--port", "8765", "--no-browser")
        self.assertEqual(steps, [("live-phrases", []), ("judge", "qwen3:14b"),
                                 ("report", "qwen3:14b", 8765), ("open", False)])
        steps, _ = self.run_cli("live-report", "--explain-top", "3", "--no-audio")
        self.assertEqual(steps[2], ("explain", judging.MODEL, 3, False))

    def test_live_report_stops_before_the_report_when_nothing_could_judge(self):
        steps, printed = self.run_cli("live-report", "--model", "down")
        self.assertEqual(steps[:2], [("live-phrases", []), ("judge", "down")])
        self.assertEqual(steps[2][0], "stopped")
        self.assertIn("py -m memelearner report", steps[2][1])
        self.assertEqual(len(steps), 3)

    def test_report_opens_the_page_or_says_there_is_nothing_to_report(self):
        steps, _ = self.run_cli("report")
        self.assertEqual(steps, [("report", judging.MODEL, 0), ("open", True)])
        with mock.patch.object(review, "rows", lambda: []), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            cli.main(["report"])
        self.assertIn("Nothing to report", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
