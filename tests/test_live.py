"""Offline tests for the live danmu recorder (live.py). They never visit Bilibili: the feed is
a made-up socket that sends packets in the shapes Bilibili's danmu feed sends. Whether the
feed still answers a visitor that way is only shown by a real run of
py -m memelearner live."""

import io
import json
import os
import tempfile
import time
import unittest
import zlib
from unittest import mock

from memelearner import bilibili, live, surf

from .test_surf import VISITOR, FakeBilibili

CRC = "7c2c6a45"


def danmu_msg(text, sent_ms, code=CRC, user_hash="2083285573", id_str="abc1", auto=0,
              sticker=0, uid=0):
    """A DANMU_MSG the way a visitor gets it: name hidden, account number 0."""
    extra = {"send_from_me": False, "content": text, "user_hash": user_hash,
             "id_str": id_str, "dm_type": sticker, "hit_combo": 0}
    meta = [0, 1, 25, 16777215, sent_ms, 1730679990, 0, code, 0, auto, 0, "", sticker, "{}",
            "{}", {"extra": json.dumps(extra), "mode": 0, "show_player_type": 0,
                   "user": {"uid": 0, "base": {"name": "张***"}}},
            {"activity_identity": ""}, 0]
    return {"cmd": "DANMU_MSG", "dm_v2": "",
            "info": [meta, text, [uid, "张***", 0, 0, 0, 10000, 1, ""], [], [0, 0, 9868950],
                     ["", ""], 0, 0, None, {"ts": sent_ms // 1000, "ct": "AB"}, 0, 0]}


def combo(text, count):
    inner = {"combo": [{"id": 7, "status": 4, "content": text, "cnt": count,
                        "guide": "他们都在说:"}], "merge_interval": 1000}
    return {"cmd": "DM_INTERACTION", "data": {"data": json.dumps(inner), "type": 102}}


def message_packet(message):
    return live.packet(live.OP_MESSAGE, json.dumps(message, ensure_ascii=False).encode())


def packed(*packets):
    """Packets zlib-packed into one (version 2), as the feed sends them."""
    body = zlib.compress(b"".join(packets))
    return live.HEADER.pack(live.HEADER.size + len(body), live.HEADER.size, 2,
                            live.OP_MESSAGE, 0) + body


def server_frame(payload, opcode=0x2):
    """A frame the way a server sends it: not masked."""
    if len(payload) < 126:
        head = bytes([0x80 | opcode, len(payload)])
    else:
        head = bytes([0x80 | opcode, 126]) + len(payload).to_bytes(2, "big")
    return head + payload


class FakeSocket:
    """Stands in for the WebSocket's socket: hands out the bytes it was given, in small
    pieces (so frames arrive split), then says the connection closed."""

    def __init__(self, data, piece=50):
        self.data, self.piece, self.sent, self.closed = bytearray(data), piece, [], False

    def sendall(self, data):
        self.sent.append(bytes(data))

    def recv(self, size):
        chunk = bytes(self.data[:min(size, self.piece)])
        del self.data[:len(chunk)]
        return chunk

    def close(self):
        self.closed = True


class PacketTest(unittest.TestCase):
    def test_packets_round_trip_and_packed_ones_are_unpacked(self):
        auth = live.packet(live.OP_AUTH_REPLY, b'{"code":0}')
        beat = live.packet(live.OP_HEARTBEAT_REPLY, (5).to_bytes(4, "big"))
        inner = [message_packet(danmu_msg("正义必胜", 1)), message_packet(combo("ig牛逼", 9))]
        found = list(live.unpack(auth + packed(*inner) + beat))
        self.assertEqual([op for op, _ in found], [8, 5, 5, 3])
        self.assertEqual(json.loads(found[1][1])["info"][1], "正义必胜")

    def test_a_broken_packet_stops_the_message_without_an_error(self):
        broken = live.HEADER.pack(9999, 16, 1, 5, 1) + b"{}"
        self.assertEqual(list(live.unpack(live.packet(3, b"x") + broken)), [(3, b"x")])

    def test_websocket_frames_round_trip_at_every_size(self):
        for size in (0, 5, 125, 126, 300, 70000):
            payload = os.urandom(size)
            frame = live.ws_frame(payload)
            self.assertEqual(frame[1] & 0x80, 0x80)  # a client masks what it sends
            final, opcode, got, used = live.take_frame(bytearray(frame + b"next"))
            self.assertEqual((final, opcode, got, used), (True, 2, payload, len(frame)))
            self.assertIsNone(live.take_frame(bytearray(frame[:-1])))  # not all here yet


class ParseTest(unittest.TestCase):
    def test_a_visitors_danmu_is_counted_by_the_senders_code(self):
        record = live.parse_danmu(danmu_msg("正义必胜", 1730680000123))
        self.assertEqual(record, {"id": "labc1", "kind": "live",
                                  "who": bilibili.user_hash(CRC), "text": "正义必胜",
                                  "sent": 1730680000.123})

    def test_the_same_person_gets_the_same_code_logged_in_or_not(self):
        uid = 12345
        logged_in = live.parse_danmu(danmu_msg("a", 1, code="", uid=uid))
        visitor = live.parse_danmu(danmu_msg("a", 1, code=bilibili.mid_hash(uid)))
        self.assertEqual(logged_in["who"], visitor["who"])

    def test_a_zero_padded_code_is_the_same_person(self):
        # From the first real recording: uid 3493112492066879 came with code 0b6fe440
        by_uid = live.parse_danmu(danmu_msg("a", 1, code="0b6fe440", uid=3493112492066879))
        by_code = live.parse_danmu(danmu_msg("a", 1, code="0b6fe440"))
        by_extra = live.parse_danmu(danmu_msg("a", 1, code="", user_hash="191882304"))
        self.assertEqual(by_uid["who"], by_code["who"])
        self.assertEqual(by_uid["who"], by_extra["who"])

    def test_without_codes_senders_are_guessed_and_marked(self):
        record = live.parse_danmu(danmu_msg("x", 1, code="", user_hash=""))
        self.assertEqual(record["weak"], 1)
        other = live.parse_danmu(danmu_msg("x", 1, code="0", user_hash="5"))
        self.assertNotIn("weak", other)  # the code in "extra" is used when info[0][7] is blank

    def test_gift_danmu_and_stickers_are_marked(self):
        self.assertEqual(live.parse_danmu(danmu_msg("x", 1, auto=2))["auto"], 1)
        self.assertEqual(live.parse_danmu(danmu_msg("x", 1, sticker=1))["sticker"], 1)

    def test_odd_messages_give_nothing(self):
        for message in ({}, {"info": "x"}, {"info": [[], ""]}, {"info": [[], None]}):
            self.assertIsNone(live.parse_danmu(message))
        self.assertEqual(live.parse_combo({"data": {"type": 1}}), [])

    def test_combos_are_kept_with_their_counts(self):
        self.assertEqual(live.parse_combo(combo("ig牛逼", 120), now=5),
                         [{"id": "c7", "kind": "combo", "text": "ig牛逼", "count": 120,
                           "sent": 5}])

    def test_room_links(self):
        self.assertEqual(live.room_of("https://live.bilibili.com/6?live_from=81001"), 6)
        self.assertEqual(live.room_of("live.bilibili.com/h5/7734200"), 7734200)
        self.assertEqual(live.room_of(" 6 "), 6)
        with self.assertRaises(ValueError):
            live.room_of("https://www.bilibili.com/video/BV1xx411c7mD")

    def test_end_times(self):
        now = time.mktime((2026, 11, 3, 19, 10, 0, 0, 0, -1))
        self.assertEqual(live.end_time("22:55", now=now) - now, 3 * 3600 + 45 * 60)
        self.assertEqual(live.end_time("19:00", now=now) - now, 86400 - 600)  # tomorrow
        self.assertEqual(live.end_time(hours=1.5, now=now) - now, 5400)
        self.assertIsNone(live.end_time(now=now))
        with self.assertRaises(ValueError):
            live.end_time("25:00", now=now)


class RecordTest(unittest.TestCase):
    def setUp(self):
        bilibili._cookies = None
        bilibili._wbi = None
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        for target, name, value in ((bilibili, "REQUEST_DELAY", 0),
                                    (bilibili, "REQUEST_JITTER", 0),
                                    (surf, "SURF_DIR", self.folder),
                                    (live, "_readable", lambda sock, wait: True),
                                    (live, "_stay_awake", lambda on: None),
                                    (live.time, "sleep", lambda seconds: None)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fetch = FakeBilibili({**VISITOR,
            "/room/v1/Room/room_init": {"code": 0, "data": {"room_id": 7734200, "short_id": 6,
                                                             "live_status": 1}},
            "/room/v1/Room/get_info": {"code": 0, "data": {"title": "TES vs IG"}},
            "/xlive/web-room/v1/index/getDanmuInfo": {"code": 0, "data": {
                "token": "TOKEN", "host_list": [{"host": "a.chat.bilibili.com", "port": 2243,
                                                 "wss_port": 443, "ws_port": 2244}]}}})
        patcher = mock.patch.object(bilibili, "_fetch", self.fetch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_it_joins_records_joins_again_after_a_drop_and_stops_on_ctrl_c(self):
        first = (server_frame(live.packet(live.OP_AUTH_REPLY, b'{"code":0}'))
                 + server_frame(packed(message_packet(danmu_msg("正义必胜", 1000, id_str="1")),
                                       message_packet(danmu_msg("正义必胜", 1000, id_str="1")),
                                       message_packet(combo("ig牛逼", 50)),
                                       message_packet({"cmd": "ONLINE_RANK_COUNT"})))
                 + server_frame(b"hi", opcode=0x9))
        second = (server_frame(live.packet(live.OP_AUTH_REPLY, b'{"code":0}'))
                  + server_frame(message_packet(danmu_msg("阿一古", 2000, code="beef",
                                                          id_str="2", auto=1))))
        sockets = [FakeSocket(first), FakeSocket(second)]
        opened = []

        def ws_open(host, port, cookies):
            opened.append((host, port, cookies))
            if not sockets:
                raise KeyboardInterrupt
            return sockets.pop(0), b""

        out = io.StringIO()
        with mock.patch.object(live, "ws_open", ws_open):
            path = live.record("https://live.bilibili.com/6?live_from=81001", out=out)

        self.assertEqual(os.path.basename(path)[:2], "6-")
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([(m["kind"], m["text"]) for m in lines],
                         [("live", "正义必胜"), ("combo", "ig牛逼"), ("live", "阿一古")])
        self.assertEqual(lines[2]["auto"], 1)
        self.assertEqual(opened[0], ("a.chat.bilibili.com", 443, {"buvid3": "BUVID3"}))
        with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as f:
            info = json.load(f)
        self.assertEqual((info["danmu"], info["senders"], info["auto"], info["combos"],
                          info["joins"]), (2, 2, 1, 1, 2))
        self.assertEqual(info["room"]["room_id"], 7734200)
        self.assertEqual(len(info["samples"]["DANMU_MSG"]), 3)
        self.assertIn("ONLINE_RANK_COUNT", info["kinds"])
        self.assertIn("Stopped.", out.getvalue())
        self.assertIn("TES vs IG", out.getvalue())
        self.assertIn("2 danmu from 2 different senders", out.getvalue())
        signed = [query for path, query, _ in self.fetch.calls if path.endswith("getDanmuInfo")]
        self.assertEqual(signed[0]["id"], "7734200")
        self.assertIn("w_rid", signed[0])

    def test_what_it_sends_is_the_join_then_heartbeats(self):
        sock = FakeSocket(server_frame(live.packet(live.OP_AUTH_REPLY, b'{"code":0}'))
                          + server_frame(b"ping!", opcode=0x9))
        recording = live.Recording(os.path.join(self.folder, "x.jsonl"), {}, io.StringIO())
        with self.assertRaises(live.Closed):
            live.listen(sock, b"", 7734200, "TOKEN", "BUVID3", recording)
        recording.close()
        frames = [live.take_frame(bytearray(data)) for data in sock.sent]
        operations = [live.HEADER.unpack_from(payload)[3] if opcode == 2 else opcode
                      for _, opcode, payload, _ in frames]
        self.assertEqual(operations, [live.OP_AUTH, live.OP_HEARTBEAT, 0xA])
        join = json.loads(frames[0][2][live.HEADER.size:])
        self.assertEqual((join["uid"], join["roomid"], join["key"], join["buvid"]),
                         (0, 7734200, "TOKEN", "BUVID3"))
        self.assertEqual(frames[2][2], b"ping!")
        self.assertEqual(recording.joins, 1)

    def test_a_refused_join_raises(self):
        sock = FakeSocket(server_frame(live.packet(live.OP_AUTH_REPLY, b'{"code":-101}')))
        recording = live.Recording(os.path.join(self.folder, "x.jsonl"), {}, io.StringIO())
        with self.assertRaises(live.Refused):
            live.listen(sock, b"", 1, "T", "B", recording)
        recording.close()


class SliceTest(unittest.TestCase):
    def test_each_hour_is_a_video_and_gift_danmu_are_left_out(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "6-20261103-1905.jsonl")
            start = 1_800_000_000
            rows = [{"id": "1", "kind": "live", "who": "a", "text": "x", "sent": start},
                    {"id": "2", "kind": "live", "who": "b", "text": "x", "sent": start + 3599},
                    {"id": "3", "kind": "live", "who": "c", "text": "x", "sent": start + 3600},
                    {"id": "4", "kind": "live", "who": "d", "text": "x", "sent": start + 9000},
                    {"id": "5", "kind": "live", "who": "e", "text": "x", "sent": start + 10,
                     "auto": 1},
                    {"id": "6", "kind": "combo", "text": "x", "count": 9, "sent": start + 20}]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(row) for row in rows) + '\n{"cut off')
            found = list(live.slices([path]))
        self.assertEqual([(game, video, m["id"]) for game, video, m in found],
                         [("lol", "live:6-20261103-1905#1", "1"),
                          ("lol", "live:6-20261103-1905#1", "2"),
                          ("lol", "live:6-20261103-1905#2", "3"),
                          ("lol", "live:6-20261103-1905#3", "4")])


if __name__ == "__main__":
    unittest.main()


class FindTest(unittest.TestCase):
    """live.find(): a recording's phrases next to the replays read before it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        for name, value in (("SURF_DIR", self.folder),
                            ("VIDEOS_FILE", os.path.join(self.folder, "videos.json")),
                            ("CANDIDATES_FILE", os.path.join(self.folder, "candidates.json"))):
            patcher = mock.patch.object(surf, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.start = 1_800_000_000
        # Two replays, read before: 正义必胜 is old news, 汽车人 is from Valorant's
        replay = [{"id": f"d{i}", "kind": "danmu", "who": f"r{i}", "text": "正义必胜",
                   "sent": self.start - 86400 - i} for i in range(40)]
        replay += [{"id": f"x{i}", "kind": "danmu", "who": f"x{i}",
                    "text": f"第{i}句很长的讨论弹幕说的是别的事情", "sent": self.start - 86400}
                   for i in range(3000)]
        surf._save_json(surf.VIDEOS_FILE, {"BV1": {"game": "lol"}, "BV2": {"game": "lol"},
                                           "BV3": {"game": "valorant"}})
        os.makedirs(os.path.join(self.folder, "bilibili"))
        for bvid, rows in (("BV1", replay[:1520]), ("BV2", replay[1520:]),
                           ("BV3", [{"id": "v", "kind": "danmu", "who": "v", "text": "汽车人",
                                     "sent": self.start - 86400}])):
            with open(surf.messages_path(bvid), "w", encoding="utf-8") as f:
                f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        # The recording: 肉鸽之神 is new and comes back in the second hour; 一时兴起 only
        # in the first; 正义必胜 as before
        rows = []
        for i in range(30):
            rows.append({"id": f"a{i}", "kind": "live", "who": f"p{i}", "text": "肉鸽之神",
                         "sent": self.start + (0 if i < 15 else 3700) + i})
            rows.append({"id": f"b{i}", "kind": "live", "who": f"q{i}", "text": "正义必胜！",
                         "sent": self.start + 60 * i})
            rows.append({"id": f"c{i}", "kind": "live", "who": f"s{i}", "text": "一时兴起",
                         "sent": self.start + 10 + i})
            rows.append({"id": f"g{i}", "kind": "live", "who": f"g{i}", "text": "肉鸽之神",
                         "sent": self.start + 50 + i, "auto": 1})  # sent for a gift
        os.makedirs(live.live_dir())
        self.path = os.path.join(live.live_dir(), "6-20261103-1905.jsonl")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        surf._save_json(os.path.splitext(self.path)[0] + ".json", {"room": {
            "title": "TES vs IG", "link": "https://live.bilibili.com/6", "game": "lol"}})

    def test_new_phrases_are_the_ones_nobody_sent_before(self):
        found, summary, compared = live.find(known=set())
        status = {c["phrase"]: (c["status"], c["people"], c["videos"]) for c in found}
        self.assertEqual(status["肉鸽之神"], ("new", 30, 2))  # gift danmu aren't counted
        self.assertIn(status["正义必胜"][0], ("steady", "rising"))  # sent before: not new
        self.assertNotIn("一时兴起", status)  # one hour only
        self.assertNotIn("汽车人", status)  # another game's
        self.assertEqual((compared["parts"], compared["danmu"], compared["senders"],
                          compared["before"]), (2, 90, 90, 3040))

    def test_the_recording_is_part_of_what_the_judge_reads(self):
        videos = {video for game, video, _ in surf.load_messages() if video.startswith("live")}
        self.assertEqual(videos, {"live:6-20261103-1905#1", "live:6-20261103-1905#2"})
        self.assertEqual(live.video_entry("live:6-20261103-1905#2"),
                         {"game": "lol", "author": "", "url": "https://live.bilibili.com/6",
                          "title": "TES vs IG (live 6-20261103-1905, hour 2)"})

    def test_saving_keeps_the_rooms_link_for_the_examples(self):
        found, summary, compared = live.find(known=set())
        surf.save(found, summary, 0, live=compared)
        saved = surf._load_json(surf.CANDIDATES_FILE, {})
        self.assertEqual(saved["live"]["recordings"], ["6-20261103-1905.jsonl"])
        self.assertEqual(saved["videos"]["live:6-20261103-1905#1"]["url"],
                         "https://live.bilibili.com/6")

    def test_by_default_it_takes_the_last_days_recordings_together(self):
        for name in ("6-20261102-1900.jsonl", "6-20261103-2010.jsonl"):
            open(os.path.join(live.live_dir(), name), "w").close()
        self.assertEqual([os.path.basename(p) for p in live.last_day()],
                         ["6-20261103-1905.jsonl", "6-20261103-2010.jsonl"])

    def test_without_recordings_it_says_so(self):
        os.remove(self.path)
        with self.assertRaises(ValueError):
            live.find()
