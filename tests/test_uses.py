"""Offline tests for when and how each learned meme is said (uses.py) and its page.

A fake model answers in the shape a real one does, so these check what the model is shown,
what is kept of its answers, which memes are asked again, and what the page changes. Whether
a real model writes good uses is only shown by a real run.
"""

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import memelearner
from memelearner import cli, judging, store, uses

WEI_NOTE = "这是梗”X出“， 出 读作 chu， 和畜生的畜同音，这个梗是咒骂某选手玩的像个畜生（表现不佳或者人品很差）"
MEMES = {
    "wei出": {"meaning": "「X出」梗：出和畜同音，骂选手打得烂、像个畜生。", "games": ["lol"],
             "added": "2026-11-20", "notes": [WEI_NOTE], "variants": ["kanavi出"],
             "examples": [f"wei出 第{n}句" for n in range(1, 10)]},
    "超威同意了": {"meaning": "同意是认输：调侃 Chovy（超威）关键比赛输掉。", "games": ["lol"],
               "added": "2026-11-05", "variants": ["chovy同意了", "三叔不同意"],
               "examples": ["超威同意了吗", "三叔不同意"]},
    "阿一古": {"meaning": "韩语「아이고」的音译，表示哎呀、无奈。", "games": ["lol", "valorant"],
             "added": "2026-11-10", "examples": ["阿一古", "主播又在阿一古了"]},
}
ANSWERS = {
    "wei出": {"when": ["打得烂", "没有这个场面", "要输了", "输了", "秀了"], "tone": "骂",
             "how": "选手打得很烂、一直送的时候，刷他的名字加“出”骂他。", "template": "「XX出」",
             "who": "选手"},
    "超威同意了": {"when": "输了、要输了", "tone": "开心", "how": "Chovy 那边要输了的时候刷，调侃他同意认输。",
               "template": "XX同意了", "who": "球员"},
    "阿一古": {"when": ["离谱"], "tone": "无奈", "how": "看到离谱的操作时感叹一句。",
             "template": "阿一XX古", "who": ""},
}


class FakeModel:
    """Stands in for judging._ask: answers the question about a meme with the next of
    answers[phrase] (the last one again when they run out), and keeps what it was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []  # (model, instructions, question), in order

    def __call__(self, messages, model):
        assert messages[0]["content"].startswith(uses.USE)
        question = json.loads(messages[1]["content"])
        self.asked.append((model, messages[0]["content"], question))
        given = self.answers[question["phrase"]]
        given = given if isinstance(given, list) else [given]
        n = len(self.phrases(question["phrase"]))
        return json.dumps(given[min(n, len(given)) - 1], ensure_ascii=False)

    def phrases(self, phrase=None):
        return [q["phrase"] for _, _, q in self.asked if phrase in (None, q["phrase"])]


class UsesTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.model = FakeModel(dict(ANSWERS))
        for patcher in (
                mock.patch.object(store, "MEMES_FILE", os.path.join(self.folder, "memes.json")),
                mock.patch.object(judging, "_ask", self.model),
                mock.patch.object(judging, "MODEL", "qwen3:14b"),
                mock.patch.object(judging, "_get_json",
                                  lambda path: {"models": [{"name": "qwen3:14b"}]}),
                mock.patch.object(judging, "_execution_state", lambda flags: None),
                mock.patch.object(judging, "RETRY_WAIT", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        store.save({"memes": json.loads(json.dumps(MEMES)), "rejected": {}})

    def write(self, *args, **options):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = uses.write(*args, **options)
        self.out = out.getvalue()
        return result

    def use(self, phrase):
        return store.load()["memes"][phrase].get("use")

    def test_the_model_gets_the_meme_and_its_use_is_checked_and_kept(self):
        result = self.write()
        self.assertEqual((sorted(result["written"]), result["failed"], result["kept"],
                          result["model"]), (sorted(MEMES), [], 0, "qwen3:14b"))
        model, told, wei = next(asked for asked in self.model.asked
                                if asked[2]["phrase"] == "wei出")
        self.assertEqual(model, "qwen3:14b")
        self.assertIn("- 打得烂 = 选手或战队失误", told)  # the occasions, from occasions.txt
        self.assertIn("骂、嘲讽、反讽", told)
        self.assertEqual((wei["pattern"], wei["notes"], wei["variants"], wei["games"]),
                         ("XX出", [WEI_NOTE], ["kanavi出"], ["英雄联盟（LOL）"]))
        self.assertEqual(wei["names"], [{"name": "wei", "player": ""},
                                        {"name": "kanavi", "player": ""}])
        self.assertEqual(len(wei["lines"]), uses.LINES)
        agreed = self.model.asked[[q["phrase"] for *_, q in self.model.asked].index("超威同意了")][2]
        self.assertEqual(agreed["names"], [{"name": "超威", "player": "Chovy"},
                                           {"name": "chovy", "player": "Chovy"},
                                           {"name": "三叔", "player": "Canyon"}])
        self.assertEqual(agreed["pattern"], "XX同意了")
        self.assertEqual(self.model.phrases("阿一古")[:1], ["阿一古"])
        self.assertEqual(self.model.asked[-1][2]["pattern"], "")  # 阿一古 has no name in it

        wei = self.use("wei出")
        self.assertEqual(wei, {"when": ["打得烂", "要输了", "输了"], "tone": "骂",
                               "how": ANSWERS["wei出"]["how"], "template": "XX出",
                               "who": "选手", "by": "qwen3:14b",
                               "of": uses._of(MEMES["wei出"]), "checked": False})
        agreed = self.use("超威同意了")  # one text of occasions; a tone and who not on the lists
        self.assertEqual((agreed["when"], agreed["tone"], agreed["template"], agreed["who"]),
                         (["输了", "要输了"], "", "XX同意了", "别的"))
        ayigu = self.use("阿一古")  # 阿一XX古 isn't 阿一古 with a name for XX
        self.assertEqual((ayigu["when"], ayigu["template"], ayigu["who"]), (["离谱"], "", ""))
        self.assertIn("[1/3]", self.out)
        self.assertIn("wei出: 打得烂、要输了、输了 · 骂 · XX出（选手） · 选手打得很烂", self.out)
        self.assertEqual(memelearner.lookup("kanavi出")["use"]["template"], "XX出")
        self.assertIn("qwen3:14b wrote the uses of 3 memes.", uses.summary(result))

    def test_a_use_without_an_occasion_or_how_is_asked_again_then_left_for_the_next_run(self):
        self.model.answers["阿一古"] = [{"when": ["随便"], "how": "感叹"},
                                     {"when": ["离谱"], "how": "看到离谱的操作时感叹"}]
        self.model.answers["超威同意了"] = [{"when": ["输了"], "how": "no Chinese here"},
                                       {"when": [], "how": "输了的时候刷"}]
        result = self.write()
        self.assertEqual(len(self.model.phrases("阿一古")), 2)
        self.assertEqual(self.use("阿一古")["how"], "看到离谱的操作时感叹")
        self.assertEqual(result["failed"], ["超威同意了"])
        self.assertEqual(len(self.model.phrases("超威同意了")), uses.TRIES)
        self.assertIsNone(self.use("超威同意了"))
        self.assertIn("asked again next run", self.out)
        self.write()
        self.assertEqual(self.model.phrases()[-1], "超威同意了")  # only it, the next run

    def test_only_the_memes_that_need_a_use_are_asked_and_a_checked_one_never_again(self):
        self.write()
        self.assertEqual(self.write(), {"written": [], "failed": [], "kept": 3, "model": "",
                                        "spent": ""})
        asked = len(self.model.asked)
        data = store.load()  # accept --note gave wei出 a new meaning
        data["memes"]["wei出"]["meaning"] += "一般用在 wei 身上。"
        store.save(data)
        self.assertEqual(self.write()["written"], ["wei出"])
        self.assertEqual(self.model.phrases()[asked:], ["wei出"])

        uses.change("超威同意了", "checked", True)
        asked = len(self.model.asked)
        self.assertEqual(sorted(self.write(again=True)["written"]), ["wei出", "阿一古"])
        self.assertNotIn("超威同意了", self.model.phrases()[asked:])
        result = self.write(["chovy同意了", "阿一古"])  # a variant names its meme
        self.assertEqual((result["written"], result["kept"]), (["阿一古"], 1))
        with self.assertRaisesRegex(ValueError, "isn't a learned meme"):
            self.write(["没学过"])

        table = os.path.join(self.folder, "occasions.txt")  # 离谱 left the table
        with open(table, "w", encoding="utf-8") as f:
            f.write("打得烂 = 打得很差\n输了 = 刚输\n")
        self.model.answers["阿一古"] = {"when": ["输了"], "how": "输了的时候叹一口气"}
        with mock.patch.object(uses, "OCCASIONS_FILE", table):
            self.assertEqual(self.write()["written"], ["阿一古"])
        self.assertEqual(self.use("阿一古")["when"], ["输了"])
        self.assertNotIn("离谱", self.model.asked[-1][1])  # the new table is what it is told
        with open(table, "w", encoding="utf-8") as f:
            f.write("# nothing but a note\n")
        with mock.patch.object(uses, "OCCASIONS_FILE", table), \
                self.assertRaisesRegex(uses.TableError, "has no occasions"):
            self.write()

    def test_an_online_model_that_cant_be_reached_has_the_local_one_write_instead(self):
        with mock.patch.object(judging, "MODEL", "deepseek/deepseek-flash"), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            result = self.write()
            self.assertEqual(result["model"], "qwen3:14b")
            self.assertIn("So qwen3:14b, the model on this computer, writes the uses instead.",
                          self.out)
            self.assertEqual({model for model, *_ in self.model.asked}, {"qwen3:14b"})
            self.assertEqual(self.use("wei出")["by"], "qwen3:14b")
            with mock.patch.object(judging, "_get_json", mock.Mock(side_effect=OSError)), \
                    self.assertRaisesRegex(judging.ModelUnavailable, "can't be asked either"):
                self.write(again=True)

    def test_what_you_change_is_checked_and_kept_and_what_cant_be_is_refused(self):
        self.write()
        use = uses.change("wei出", "when", ["秀了", "加油", "秀了"])
        self.assertEqual((use["when"], use["checked"], use["by"]),
                         (["秀了", "加油"], True, "qwen3:14b"))
        self.assertEqual(self.use("wei出"), use)
        with self.assertRaisesRegex(ValueError, "场面表里没有「乱来」"):
            uses.change("wei出", "when", ["乱来"])
        with self.assertRaisesRegex(ValueError, "语气只能是"):
            uses.change("wei出", "tone", "开心")
        self.assertEqual(uses.change("wei出", "how", "  打得烂\n的时候刷 ")["how"], "打得烂 的时候刷")
        self.assertEqual(uses.change("wei出", "template", "「xx出」")["template"], "XX出")
        with self.assertRaisesRegex(ValueError, "不是这个梗换了名字的写法"):
            uses.change("wei出", "template", "XX进")
        use = uses.change("wei出", "template", "")
        self.assertEqual((use["template"], use["who"]), ("", ""))
        use = uses.change("wei出", "template", "XX出")
        self.assertEqual((use["template"], use["who"]), ("XX出", "别的"))
        self.assertEqual(uses.change("kanavi出", "who", "选手")["who"], "选手")
        with self.assertRaisesRegex(ValueError, "XX 只能是"):
            uses.change("wei出", "who", "教练")
        with self.assertRaisesRegex(ValueError, "isn't a learned meme"):
            uses.change("没学过", "tone", "骂")
        with self.assertRaisesRegex(ValueError, "no such field"):
            uses.change("wei出", "by", "me")
        self.assertFalse(uses.change("wei出", "checked", False)["checked"])
        self.assertTrue(uses.change("wei出", "checked", True)["checked"])

        data = store.load()  # a meme the model wrote no use for: yours
        del data["memes"]["阿一古"]["use"]
        store.save(data)
        use = uses.change("阿一古", "tone", "无奈")
        self.assertEqual((use["tone"], use["when"], use["by"], use["checked"]),
                         ("无奈", [], "", True))

    def test_the_rows_put_what_to_look_at_first_and_the_page_holds_them_safely(self):
        self.write()
        uses.change("阿一古", "checked", True)
        data = store.load()
        del data["memes"]["wei出"]["use"]
        data["memes"]["超威同意了"]["meaning"] = "</script><script>alert(1)</script>"
        store.save(data)
        rows = uses.rows()
        self.assertEqual([row["phrase"] for row in rows], ["超威同意了", "wei出", "阿一古"])
        self.assertEqual(rows[2]["games"], ["英雄联盟", "无畏契约"])
        html = uses.page(rows, uses.occasions(), "token123")
        self.assertNotIn("</script><script>alert(1)", html)
        start = html.index('<script type="application/json" id="data">') + 42
        shown = json.loads(html[start:html.index("</script>", start)])
        self.assertEqual((shown["token"], shown["tones"][0], shown["occasions"][0]["name"]),
                         ("token123", "夸", "秀了"))
        self.assertEqual(shown["rows"][0]["meaning"], "</script><script>alert(1)</script>")

    def test_the_page_is_only_served_through_its_link_and_done_stops_the_bot(self):
        self.write()
        page = uses.UsesPage()
        base = page.url.split("?")[0].rstrip("/")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy here

        def ask(url, body=None, token=page.token):
            request = urllib.request.Request(url, None if body is None else json.dumps(
                body, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json", "X-Token": token})
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                return error.code, error.read().decode("utf-8")

        ran = {}
        thread = threading.Thread(target=lambda: ran.update(result=page.run(browser=False)))
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            thread.start()
            status, html = ask(page.url)
            self.assertEqual(status, 200)
            self.assertIn('"phrase": "wei出"', html)
            self.assertEqual(ask(base + "/?t=wrong")[0], 403)
            change = {"phrase": "wei出", "field": "tone", "value": "嘲讽"}
            self.assertEqual(ask(base + "/save", change, token="wrong")[0], 403)
            status, answer = ask(base + "/save", dict(change, value="开心"))
            self.assertEqual(status, 400)
            self.assertIn("语气只能是", json.loads(answer)["error"])
            status, answer = ask(base + "/save", change)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(answer)["use"]["tone"], "嘲讽")
            self.assertEqual((self.use("wei出")["tone"], self.use("wei出")["checked"]),
                             ("嘲讽", True))
            status, answer = ask(base + "/done", {})
            thread.join(10)
        self.assertEqual(status, 200)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(answer), {"memes": 3, "uses": 3, "checked": 1})
        self.assertEqual(ran["result"], json.loads(answer))
        self.assertIn(page.url, printed.getvalue())
        self.assertIn("py -m memelearner uses --no-model", printed.getvalue())

    def test_the_command_writes_the_uses_then_opens_the_page(self):
        opened = []

        class FakePage:
            def __init__(self, port):
                opened.append(("page", port))

            def run(self, browser):
                opened.append(("open", browser))
                return {"memes": 3, "uses": 3, "checked": 0}

        def run(*args):
            with mock.patch.object(uses, "UsesPage", FakePage), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                try:
                    cli.main(list(args))
                    code = 0
                except SystemExit as stop:
                    code = stop.code
            return code, printed.getvalue()

        code, printed = run("uses")
        self.assertEqual((code, opened), (0, [("page", 0), ("open", True)]))
        self.assertIn("qwen3:14b wrote the uses of 3 memes.", printed)
        self.assertIn("Of 3 memes, 3 have a use, and you checked 0.", printed)
        opened.clear()
        code, printed = run("uses", "--no-model", "--no-browser", "--port", "8765")
        self.assertEqual((code, opened), (0, [("page", 8765), ("open", False)]))
        self.assertEqual(len(self.model.asked), 3)
        opened.clear()
        code, printed = run("uses", "--no-page", "--again", "wei出")
        self.assertEqual((code, opened, self.model.phrases()[-1]), (0, [], "wei出"))
        with mock.patch.object(judging, "_get_json", mock.Mock(side_effect=OSError)):
            code, printed = run("uses", "--no-page", "--again")
            self.assertEqual(code, 1)
            self.assertIn("Couldn't ask a model: Ollama isn't running", printed)
            code, printed = run("uses", "--again")  # the page still opens
            self.assertEqual((code, opened[-1]), (0, ("open", True)))
            self.assertIn("So the page shows the uses written before.", printed)
        code, printed = run("uses", "没学过")
        self.assertEqual(code, "没学过 isn't a learned meme")

    def test_another_app_writes_them_quietly(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = memelearner.write_uses(quiet=True)
        self.assertEqual((out.getvalue(), len(result["written"])), ("", 3))
        self.assertEqual(memelearner.memes()[0]["use"]["by"], "qwen3:14b")


class OccasionsTest(unittest.TestCase):
    def test_the_table_is_read_one_occasion_a_line(self):
        table = uses.parse("# 说明\n\n秀了 = 打得很好，很秀  # 注\nＢＰ ＝ 选英雄（禁英雄）＃ 注\n")
        self.assertEqual(table, {"秀了": "打得很好，很秀", "BP": "选英雄（禁英雄）"})
        with self.assertRaisesRegex(uses.TableError, r"occasions.txt line 2 \(没有等号\)"):
            uses.parse("秀了 = 打得很好\n没有等号\n")
        with self.assertRaisesRegex(uses.TableError, "BP is on line 1 already"):
            uses.parse("bp = 选英雄\nBP = 也是选英雄\n")
        with self.assertRaisesRegex(uses.TableError, "no occasion before ="):
            uses.parse("= 打得很好\n")

    def test_the_file_is_read_as_utf8_with_or_without_a_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "occasions.txt")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("秀了 = 打得很好\n")
            self.assertEqual(uses.occasions(path), {"秀了": "打得很好"})
            with open(path, "w", encoding="gbk") as f:
                f.write("秀了 = 打得很好\n")
            with self.assertRaisesRegex(uses.TableError, "isn't UTF-8"):
                uses.occasions(path)
            self.assertEqual(uses.occasions(os.path.join(folder, "none.txt")), {})

    def test_the_shipped_table_has_the_moments_of_a_match(self):
        table = uses.occasions()
        for occasion in ("秀了", "打得烂", "翻盘", "要输了", "BP", "回看", "叫人"):
            self.assertIn(occasion, table)


if __name__ == "__main__":
    unittest.main()
