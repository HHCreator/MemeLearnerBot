"""Record the danmu of a Bilibili live room, the way a viewer who isn't logged in sees them.

    py -m memelearner live https://live.bilibili.com/6          # until you press Ctrl+C
    py -m memelearner live 6 --until 22:55                      # until 10:55 pm, this PC's clock
    py -m memelearner live 6 --hours 3                          # for three hours

It only watches: it never logs in and never sends anything. It joins the room's danmu feed
the way the live page does for a visitor, with the same visitor cookie the rest of the bot
uses (bilibili.py), and writes each danmu to a file in the cache folder as it comes:

    surf/live/ROOM-YYYYMMDD-HHMM.jsonl    one danmu a line, like the replays' files:
        {"id", "kind": "live", "who", "text", "sent"}, plus "auto": 1 for danmu Bilibili
        sends for a viewer (gifts, lotteries), "sticker": 1 for a sticker, "weak": 1 when
        the sender's code was missing and "who" was guessed from what the room shows
    surf/live/ROOM-YYYYMMDD-HHMM.json     the room, start and end, counts, and the first
                                          messages as Bilibili sent them, to check their
                                          shape, with who sent them blanked (scrub())

Who sent a danmu: on the first real run (room 6, 2026-11-07) a visitor got each sender's
account number and name in full. "who" is bilibili.user_hash() of the crc32 of the account
number, the same code a replay's danmu carry, so people are counted the same way in both.
Bilibili has hidden them from visitors before ([0, "张***"]); then the crc32 that each danmu
also carries (info[0][7]) is used. The number and the name are never kept. The first status
lines say how many different senders there were, so a run shows at once whether the codes
came.

How the feed works (what the live page does): ask api.live.bilibili.com for the room's real
id (live.bilibili.com/6 is room 7734200) and for a token and the feed's servers
(getDanmuInfo), open a WebSocket to one of them, send the token, then a heartbeat every 30
seconds. Bilibili sends packets with a 16-byte header (length, header length, version,
operation, sequence); a version 2 packet is more packets zlib-packed. When the connection
drops, it joins again after a few seconds, with a new token when one fails, until the end.

Without any extra package Python has no WebSocket, so this speaks the protocol itself: an
HTTP Upgrade over TLS, then masked binary frames (RFC 6455), a few dozen lines.

For finding phrases, slices() splits a recording into parts of SLICE minutes, and each part
counts as one video, since a phrase must be sent in at least two (surf.MIN_VIDEOS).
"""

import base64
import collections
import hashlib
import json
import os
import re
import select
import socket
import ssl
import struct
import sys
import time
import zlib

from . import bilibili, surf

try:  # optional: with it, the feed is asked for brotli-packed packets, like the live page does
    import brotli
except ImportError:  # pragma: no cover - depends on what is installed
    brotli = None

FALLBACK_HOST = {"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}
HEARTBEAT = 30  # seconds between heartbeats, like the live page
SILENCE = 75  # seconds without a byte from Bilibili before the connection counts as dead
STATUS_EVERY = 60  # seconds between status lines
STOP_EVERY = 600  # seconds between two asks whether to stop (record()'s stop)
SHOW_FIRST = 5  # danmu printed as they come at the start, to show it works
CHECK_AFTER = 100  # danmu after which it says whether the sender codes are there
SAMPLES = 20  # DANMU_MSG messages kept as Bilibili sent them, to check their shape
OTHER_SAMPLES = 2  # ... and of each other kind of message (at most OTHER_KINDS kinds)
OTHER_KINDS = 40
# What tells who a viewer is, in the messages Bilibili sends: blanked in the samples (scrub())
PRIVATE = {"uid", "mid", "uname", "name", "face", "pb", "user_hash", "reply_mid", "reply_uname",
           "combo_id", "batch_combo_id"}  # a gift combo's id holds the account number
HIDDEN = "hidden"
SLICE = 60  # minutes of a recording that count as one video when finding phrases
LIVE_PEOPLE = 5  # different people who must send a phrase on its own, in a live room's run
LIVE_TOP = 300  # phrases kept from a live room's run (new ones are often less common)

OP_HEARTBEAT, OP_HEARTBEAT_REPLY, OP_MESSAGE, OP_AUTH, OP_AUTH_REPLY = 2, 3, 5, 7, 8
HEADER = struct.Struct(">IHHII")  # packet length, header length, version, operation, sequence
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455's fixed string for the handshake


class NeedBrotli(RuntimeError):
    """Bilibili sent brotli-packed packets, and the brotli package isn't installed."""


class Refused(Exception):
    """Bilibili refused to let this connection join the room (a stale token, say)."""


class Closed(Exception):
    """The connection to the danmu feed ended."""


def live_dir():
    """surf/live/ in the cache folder, where the recordings go."""
    return os.path.join(surf.SURF_DIR, "live")


# -- the room -----------------------------------------------------------------------------

def room_of(link):
    """The room number in a live room's link (https://live.bilibili.com/6?live_from=81001 is
    6), or the number itself; ValueError when there's none."""
    text = str(link).strip()
    found = re.search(r"live\.bilibili\.com/(?:h5/|blanc/)?(\d+)", text) or \
        re.fullmatch(r"(\d+)", text)
    if not found:
        raise ValueError(f"{link} isn't a live room's link or number "
                         "(like https://live.bilibili.com/6)")
    return int(found.group(1))


def room_info(room):
    """{"room_id", "short_id", "live", "title"} for a room's number or short number (6 is
    the short number of room 7734200)."""
    data = bilibili._request("/room/v1/Room/room_init", {"id": room}, base=bilibili.LIVE_API)
    found = {"room_id": data["room_id"], "short_id": data.get("short_id") or 0,
             "live": data.get("live_status") == 1, "title": ""}
    try:
        about = bilibili._request("/room/v1/Room/get_info", {"room_id": data["room_id"]},
                                  base=bilibili.LIVE_API, retries=0) or {}
        found["title"] = about.get("title") or ""
    except (bilibili.Error, bilibili.Blocked, KeyError, TypeError, OSError):
        pass  # only the title: the danmu don't need it
    return found


def danmu_info(room_id):
    """(token, servers) for joining a room's danmu feed; servers are dicts with "host" and
    "wss_port". The token is only good for a while: ask again after a failed join."""
    data = bilibili._request("/xlive/web-room/v1/index/getDanmuInfo",
                             {"id": room_id, "type": 0, "web_location": "444.8"},
                             signed=True, base=bilibili.LIVE_API) or {}
    hosts = [h for h in data.get("host_list") or [] if isinstance(h, dict) and h.get("host")]
    return data.get("token") or "", hosts + [FALLBACK_HOST]


# -- packets ------------------------------------------------------------------------------

def packet(operation, body=b""):
    """One packet for Bilibili's danmu feed."""
    return HEADER.pack(HEADER.size + len(body), HEADER.size, 1, operation, 1) + body


def unpack(data):
    """Yield (operation, body) for each packet in data, unpacking packed ones (version 2
    zlib, version 3 brotli) into the packets inside."""
    while len(data) >= HEADER.size:
        length, header, version, operation, _ = HEADER.unpack_from(data)
        if length < header or length > len(data) or header < HEADER.size:
            return  # a broken packet: skip the rest of this message
        body, data = data[header:length], data[length:]
        if operation == OP_MESSAGE and version == 2:
            yield from unpack(zlib.decompress(body))
        elif operation == OP_MESSAGE and version == 3:
            if brotli is None:
                raise NeedBrotli("Bilibili sent brotli-packed danmu. Install brotli with: "
                                 "py -m pip install brotli, then start again")
            yield from unpack(brotli.decompress(body))
        else:
            yield operation, body


# -- a minimal WebSocket client (RFC 6455) --------------------------------------------------

def ws_frame(payload, opcode=0x2):
    """A client frame: final, masked (a client must mask what it sends)."""
    size = len(payload)
    if size < 126:
        head = struct.pack(">BB", 0x80 | opcode, 0x80 | size)
    elif size < 65536:
        head = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, size)
    else:
        head = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, size)
    mask = os.urandom(4)
    return head + mask + _masked(payload, mask)


def _masked(payload, mask):
    """payload XOR the 4-byte mask, repeated (fast enough for megabytes)."""
    if not payload:
        return b""
    whole = (mask * (len(payload) // 4 + 1))[:len(payload)]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(whole, "big")).to_bytes(
        len(payload), "big")


def take_frame(buffer):
    """(final, opcode, payload, size) for the first whole frame in buffer, or None when it
    hasn't all come yet."""
    if len(buffer) < 2:
        return None
    final, opcode = bool(buffer[0] & 0x80), buffer[0] & 0x0F
    size, start = buffer[1] & 0x7F, 2
    if size == 126:
        if len(buffer) < 4:
            return None
        size, start = struct.unpack_from(">H", buffer, 2)[0], 4
    elif size == 127:
        if len(buffer) < 10:
            return None
        size, start = struct.unpack_from(">Q", buffer, 2)[0], 10
    mask = None
    if buffer[1] & 0x80:
        if len(buffer) < start + 4:
            return None
        mask, start = bytes(buffer[start:start + 4]), start + 4
    if len(buffer) < start + size:
        return None
    payload = bytes(buffer[start:start + size])
    return final, opcode, (_masked(payload, mask) if mask else payload), start + size


def ws_open(host, port=443, cookies=None, timeout=15):
    """Open wss://host/sub the way the live page does; returns (socket, bytes already read
    after the handshake)."""
    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET /sub HTTP/1.1", f"Host: {host}", "Upgrade: websocket",
                 "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
                 "Sec-WebSocket-Version: 13", "Origin: https://live.bilibili.com",
                 f"User-Agent: {bilibili.HEADERS['User-Agent']}",
                 "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8"]
        if cookies:
            lines.append("Cookie: " + "; ".join(f"{k}={v}" for k, v in cookies.items()))
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        answer = b""
        while b"\r\n\r\n" not in answer:
            chunk = sock.recv(4096)
            if not chunk or len(answer) > 65536:
                raise Closed(f"{host} didn't answer the WebSocket handshake")
            answer += chunk
        head, rest = answer.split(b"\r\n\r\n", 1)
        head = head.decode("latin-1")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if " 101 " not in head.split("\r\n")[0] + " " or accept not in head:
            raise Closed(f"{host} refused the WebSocket: {head.splitlines()[0]}")
        return sock, rest
    except BaseException:
        raw.close()
        raise


# -- danmu --------------------------------------------------------------------------------

def _at(items, index, default=None):
    return items[index] if isinstance(items, list) and len(items) > index else default


def scrub(message):
    """A copy of a raw message for the samples in the .json, without who sent it: account
    numbers, names, faces, sender codes and protobuf parts ("pb") become "hidden". What
    Bilibili itself left empty (0, "") stays, so a sample still shows what a visitor gets,
    and so does the rest of its shape."""
    def walk(value, key=None):
        if key in PRIVATE:
            return value if not value else HIDDEN
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if key == "extra" and isinstance(value, str) and value:  # JSON inside the JSON
            try:
                return json.dumps(walk(json.loads(value)), ensure_ascii=False)
            except ValueError:
                return HIDDEN
        return value

    copy = walk(message)
    info = copy.get("info") if isinstance(copy, dict) else None
    for part, index in ((0, 7), (2, 0), (2, 1)):  # DANMU_MSG: the code, account number, name
        row = _at(info, part)
        if _at(row, index):
            row[index] = HIDDEN
    return copy


def parse_danmu(message, now=None):
    """The record for a DANMU_MSG message, or None when it holds no text.

    info[0] is [.., mode, size, color, time sent (ms), random id, .., sender's code (crc32
    of the account number, hex), .., auto (1 or 2: sent for a gift or a lottery), .., kind
    (0 text, 1 sticker, 2 voice), .., {"extra": JSON with "id_str", "user_hash" ...}];
    info[1] is the text; info[2] is [account number, name, ...], which a visitor may get as
    [0, "张***", ...] (on 2026-11-07 a visitor got them in full); info[3] the fan medal;
    info[4] the user level. Only a hash of the code is kept, never the name or number.
    """
    info = message.get("info")
    if not isinstance(info, list):
        return None
    meta = info[0] if isinstance(_at(info, 0), list) else []
    text = _at(info, 1)
    if not isinstance(text, str) or not text.strip():
        return None
    user = _at(info, 2) if isinstance(_at(info, 2), list) else []
    extra = {}
    mode = _at(meta, 15)
    if isinstance(mode, dict):
        try:
            extra = json.loads(mode.get("extra") or "{}")
        except (TypeError, ValueError):
            extra = {}
        extra = extra if isinstance(extra, dict) else {}
    sent = _at(meta, 4)
    sent = sent / 1000 if isinstance(sent, (int, float)) and sent > 0 else (now or time.time())

    uid = _at(user, 0)
    if isinstance(uid, int) and uid > 0:  # Bilibili doesn't hide it (it didn't on 2026-11-07)
        code = bilibili.mid_hash(uid)
    else:  # the crc32 in hex, sometimes with leading zeros (0b6fe440), or in "extra" in decimal
        code = str(_at(meta, 7) or "").lower().lstrip("0")
        if not code and str(extra.get("user_hash") or "").isdigit():
            code = format(int(extra["user_hash"]), "x").lstrip("0")
    record = {"id": "", "kind": "live", "who": "", "text": text, "sent": round(sent, 3)}
    if code:
        record["who"] = bilibili.user_hash(code)
    else:
        # No code: tell people apart, roughly, by what the room shows of them
        medal = _at(info, 3) if isinstance(_at(info, 3), list) else []
        level = _at(info, 4) if isinstance(_at(info, 4), list) else []
        guess = [_at(user, 1), _at(user, 7), _at(medal, 0), _at(medal, 1), _at(level, 0)]
        record["who"] = bilibili.user_hash("guess:" + json.dumps(guess, ensure_ascii=False))
        record["weak"] = 1
    record["id"] = "l" + str(extra.get("id_str") or f"{_at(meta, 4)}-{_at(meta, 5)}-{code}")
    if _at(meta, 9) not in (None, 0):
        record["auto"] = 1
    if _at(meta, 12) not in (None, 0):
        record["sticker"] = 1
    return record


def parse_combo(message, now=None):
    """Records for a DM_INTERACTION combo ("他们都在说 正义必胜 x120"), which Bilibili shows when
    many send the same thing: {"kind": "combo", "text", "count", "sent"}."""
    data = message.get("data")
    if not isinstance(data, dict) or data.get("type") != 102:
        return []
    try:
        inner = json.loads(data.get("data") or "{}")
    except (TypeError, ValueError):
        return []
    found = []
    for combo in (inner.get("combo") or []) if isinstance(inner, dict) else []:
        if isinstance(combo, dict) and combo.get("content"):
            found.append({"id": f"c{combo.get('id', '')}", "kind": "combo",
                          "text": combo["content"], "count": combo.get("cnt", 0),
                          "sent": round(now or time.time(), 3)})
    return found


# -- recording ----------------------------------------------------------------------------

class Recording:
    """Writes what the feed sends to the .jsonl file, and keeps count."""

    def __init__(self, path, room, out=None):
        self.path, self.room, self.out = path, room, out
        self.file = open(path, "a", encoding="utf-8")
        self.started = time.time()
        self.lines = self.auto = self.stickers = self.weak = self.combos = 0
        self.people = set()
        self.kinds = collections.Counter()
        self.samples = collections.defaultdict(list)
        self.seen = collections.OrderedDict()  # recent ids, so a danmu sent twice counts once
        self.last_text = ""
        self.last_status = self.last_flush = time.time()
        self.minute = collections.deque()  # times of the danmu in the last minute
        self.checked = False
        self.joins = 0
        self.stopped = False  # record()'s stop() said to stop
        self.save_info()

    def message(self, body):
        """Take one message (op 5) from the feed."""
        try:
            message = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = str(message.get("cmd") or "").split(":")[0]
        self.kinds[kind] += 1
        keep = self.samples[kind]
        if kind == "DANMU_MSG":
            if len(keep) < SAMPLES:
                keep.append(scrub(message))
            record = parse_danmu(message)
            if record:
                self.danmu(record)
        else:
            if len(keep) < OTHER_SAMPLES and len(self.samples) <= OTHER_KINDS:
                keep.append(scrub(message))
            if kind == "DM_INTERACTION":
                for record in parse_combo(message):
                    self.combos += 1
                    self.write(record)

    def danmu(self, record):
        if record["id"] in self.seen:
            return
        self.seen[record["id"]] = True
        if len(self.seen) > 5000:
            self.seen.popitem(last=False)
        self.write(record)
        self.lines += 1
        self.people.add(record["who"])
        self.auto += record.get("auto", 0)
        self.stickers += record.get("sticker", 0)
        self.weak += record.get("weak", 0)
        self.last_text = record["text"]
        self.minute.append(time.time())
        if self.lines <= SHOW_FIRST:
            self.say(f"  {time.strftime('%H:%M:%S', time.localtime(record['sent']))}  "
                     f"{record['text']}")
        if self.lines == CHECK_AFTER and not self.checked:
            self.check()

    def check(self):
        """Say whether the danmu come with their senders' codes, which the finder needs."""
        self.checked = True
        if self.weak > self.lines // 2:
            self.say(f"Check: {self.weak} of the first {self.lines} danmu came without the "
                     "sender's code, so senders are guessed from the hidden name, medal and "
                     "level. The finder's people counts will be rough. Recording goes on.")
        else:
            self.say(f"Check: the first {self.lines} danmu came from {len(self.people)} "
                     "different senders (by their codes), so the finder can count people.")
        self.save_info()

    def write(self, record):
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        if time.time() - self.last_flush >= 5:
            self.file.flush()
            self.last_flush = time.time()

    def say(self, text):
        print(text, file=self.out, flush=True)

    def status(self, force=False):
        """A status line every STATUS_EVERY seconds."""
        now = time.time()
        while self.minute and self.minute[0] < now - 60:
            self.minute.popleft()
        if not force and now - self.last_status < STATUS_EVERY:
            return
        self.last_status = now
        extra = "".join(part for part in (
            f", {self.auto} sent for gifts" if self.auto else "",
            f", {self.stickers} stickers" if self.stickers else "") if part)
        self.say(f"{time.strftime('%H:%M')}  {self.lines} danmu from {len(self.people)} "
                 f"different senders{extra}; {len(self.minute)} in the last minute. "
                 f"Latest: {self.last_text[:30]}")
        if not self.lines and now - self.started > 120:
            self.say("  No danmu yet: is the room live? It keeps listening.")
        self.save_info()

    def save_info(self):
        """Write ROOM-....json beside the danmu: the room, times, counts and samples."""
        self.file.flush()
        surf._save_json(os.path.splitext(self.path)[0] + ".json", {
            "room": self.room, "file": os.path.basename(self.path),
            "started": int(self.started), "updated": int(time.time()),
            "danmu": self.lines, "senders": len(self.people), "auto": self.auto,
            "stickers": self.stickers, "without_code": self.weak, "combos": self.combos,
            "joins": self.joins, "kinds": dict(self.kinds.most_common()),
            "samples": self.samples})

    def close(self):
        self.save_info()
        self.file.close()


def _readable(sock, wait):
    """Whether sock has data to read now, waiting up to wait seconds. Tests replace this."""
    if getattr(sock, "pending", None) and sock.pending():
        return True
    return bool(select.select([sock], [], [], wait)[0])


def listen(sock, rest, room_id, token, buvid, recording, until=None, stop=None):
    """Join the room on an open WebSocket and record until the end time or the connection
    ends (Closed), or Bilibili refuses to let it join (Refused), or stop() says to stop
    (asked every STOP_EVERY seconds; recording.stopped is then True)."""
    auth = {"uid": 0, "roomid": room_id, "protover": 3 if brotli else 2, "buvid": buvid,
            "platform": "web", "type": 2, "key": token}
    sock.sendall(ws_frame(packet(OP_AUTH, json.dumps(auth, separators=(",", ":")).encode())))
    buffer, parts = bytearray(rest), []
    last_beat, last_byte, last_ask = 0.0, time.time(), time.time()
    while until is None or time.time() < until:
        now = time.time()
        if stop is not None and now - last_ask >= STOP_EVERY:
            last_ask = now
            if stop():
                recording.stopped = True
                return
        if now - last_beat >= HEARTBEAT:
            sock.sendall(ws_frame(packet(OP_HEARTBEAT, b"[object Object]")))
            last_beat = now
        if now - last_byte > SILENCE:
            raise Closed(f"no word from Bilibili for {SILENCE} seconds")
        if _readable(sock, 1):
            chunk = sock.recv(65536)
            if not chunk:
                raise Closed("Bilibili closed the connection")
            buffer += chunk
            last_byte = time.time()
        while True:
            frame = take_frame(buffer)
            if frame is None:
                break
            final, opcode, payload, size = frame
            del buffer[:size]
            if opcode == 0x8:
                raise Closed("Bilibili closed the connection")
            if opcode == 0x9:  # ping: answer with the same payload
                sock.sendall(ws_frame(payload, 0xA))
                continue
            if opcode not in (0x0, 0x1, 0x2):
                continue
            parts.append(payload)
            if not final:
                continue
            data, parts = b"".join(parts), []
            for operation, body in unpack(data):
                if operation == OP_AUTH_REPLY:
                    try:
                        code = json.loads(body.decode("utf-8", "replace")).get("code", 0)
                    except (ValueError, AttributeError):
                        code = 0
                    if code != 0:
                        raise Refused(f"Bilibili refused to let it join (code {code})")
                    recording.joins += 1
                elif operation == OP_MESSAGE:
                    recording.message(body)
        recording.status()


def end_time(until=None, hours=None, now=None):
    """The unix time to stop at: --hours from now, or the next time the clock shows --until
    (HH:MM, this computer's time; tomorrow if that's already past); None for no end."""
    now = time.time() if now is None else now
    if hours is not None:
        return now + hours * 3600
    if not until:
        return None
    found = re.fullmatch(r"(\d{1,2})[:：](\d{2})", until.strip())
    if not found or int(found.group(1)) > 23 or int(found.group(2)) > 59:
        raise ValueError(f"--until wants a time like 22:55, not {until}")
    day = time.localtime(now)
    end = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, int(found.group(1)),
                       int(found.group(2)), 0, 0, 0, -1))
    return end if end > now else end + 86400


def _stay_awake(on):
    """Ask Windows not to sleep while recording (nothing elsewhere)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:  # pragma: no cover - only matters on Windows
        pass


def record(link, until=None, path=None, game="lol", out=None, stop=None):
    """Record a live room's danmu until the unix time until (None: until Ctrl+C), or until
    stop() says to stop (asked every STOP_EVERY seconds: the room went offline, say);
    returns the .jsonl file's path. Joins again by itself when the connection drops. game is
    what the room is about, for finding phrases later; out is where it says what it does
    (None: sys.stdout, as it is then)."""
    room = room_info(room_of(link))
    started = time.strftime("%Y%m%d-%H%M")
    name = room["short_id"] or room["room_id"]
    path = path or os.path.join(live_dir(), f"{name}-{started}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    link = f"https://live.bilibili.com/{name}"
    recording = Recording(path, dict(room, link=link, game=game), out)
    say = recording.say
    say(f"Room {name} (room {room['room_id']}): {room['title'] or '(no title)'}"
        + ("" if room["live"] else "  - not live right now, listening anyway"))
    say(f"Recording to {path}")
    say("Until " + (time.strftime("%a %H:%M", time.localtime(until)) if until else
                    "you press Ctrl+C") + ". It only watches: no login, nothing is sent.")
    _stay_awake(True)
    token, hosts, turn, fails = None, [], 0, 0
    try:
        while until is None or time.time() < until:
            joins = recording.joins
            try:
                if token is None:
                    try:
                        token, hosts = danmu_info(room["room_id"])
                    except (bilibili.Blocked, bilibili.Error) as error:
                        say(f"Bilibili didn't give a token ({error}); trying without one")
                        token, hosts = "", [FALLBACK_HOST]
                host = hosts[turn % len(hosts)]
                buvid = bilibili._visitor_cookies()["buvid3"]
                sock, rest = ws_open(host["host"], host.get("wss_port") or 443,
                                     {"buvid3": buvid})
                try:
                    say(f"Connected to the danmu feed at {host['host']}" if not joins
                        else f"Connected again at {host['host']}")
                    listen(sock, rest, room["room_id"], token, buvid, recording, until, stop)
                    fails = 0
                finally:
                    sock.close()
                if recording.stopped:
                    say("Stopping: " + (getattr(stop, "why", "") or "asked to stop"))
                    break
            except (Refused, Closed, OSError, ValueError, KeyError, TypeError,
                    bilibili.Error, zlib.error) as error:
                if recording.joins > joins:
                    fails = 0  # it was in: only this connection ended
                say(f"The connection dropped ({error}); joining again ...")
                fails += 1
                turn += 1
                if fails >= 2:
                    token = None  # a fresh token, in case this one went stale
            if until is not None and time.time() >= until:
                break
            time.sleep(min(120, 5 * 2 ** min(fails, 5)) if fails else 2)
    except KeyboardInterrupt:
        say("Stopped.")
    finally:
        _stay_awake(False)
        recording.status(force=True)
        recording.close()
    say(f"Saved {recording.lines} danmu to {path}")
    return path


# -- reading recordings back --------------------------------------------------------------

def recordings():
    """The .jsonl recordings in surf/live/, oldest first."""
    folder = live_dir()
    try:
        names = sorted(name for name in os.listdir(folder) if name.endswith(".jsonl"))
    except OSError:
        return []
    return [os.path.join(folder, name) for name in names]


def about(path):
    """What ROOM-....json beside a recording says: {"room": {...}, "started", ...}."""
    return surf._load_json(os.path.splitext(path)[0] + ".json", {})


def video_entry(video):
    """A video's entry, like videos.json's, for a part of a recording
    ("live:6-20261103-1905#2"): the room's title and link."""
    name, _, part = video[len("live:"):].partition("#")
    room = about(os.path.join(live_dir(), name + ".jsonl")).get("room") or {}
    return {"game": room.get("game", "lol"), "author": "",
            "title": f"{room.get('title') or 'live room'} (live {name}, hour {part or 1})",
            "url": room.get("link") or ""}


def slices(paths, game=None, minutes=SLICE, people_only=True):
    """Yield (game, video, message) for the danmu in some recordings, like
    surf.load_messages() does for replays; game is what each recording says it is about
    (lol unless it says else) unless it's given. Each recording is cut into parts of
    `minutes` from its first danmu, and each part counts as a video
    ("live:6-20261103-1905#2"), so a phrase has to come back in a later part to count as
    sent in two videos. With people_only, danmu Bilibili sent for gifts or lotteries and
    stickers are left out."""
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        its_game = game or (about(path).get("room") or {}).get("game") or "lol"
        first = None
        try:
            f = open(path, encoding="utf-8")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # a line cut off when the computer went off
                if message.get("kind") != "live" or not message.get("sent"):
                    continue
                if people_only and (message.get("auto") or message.get("sticker")):
                    continue
                first = message["sent"] if first is None else first
                part = int((message["sent"] - first) // (minutes * 60)) + 1
                yield its_game, f"live:{name}#{part}", message


def last_day():
    """The recordings of the room and day of the newest one (6-20261103-1905.jsonl and
    6-20261103-2010.jsonl, when the recorder was started again), oldest first."""
    found = recordings()
    if not found:
        return []
    day = os.path.basename(found[-1]).rsplit("-", 1)[0]  # 6-20261103
    return [path for path in found if os.path.basename(path).rsplit("-", 1)[0] == day]


def find(paths=None, minutes=SLICE, people=LIVE_PEOPLE, known=None, top=LIVE_TOP):
    """The phrases people send on their own in some recordings (by default the last day's,
    see last_day()), and how new they are next to everything read before the first of them:
    the replays' danmu and comments, and earlier recordings. Returns (candidates, summary,
    compared), like surf.find_candidates(), where compared is {"recordings": [names],
    "start": unix time, "parts": parts of `minutes`, "danmu": lines, "senders", "before":
    earlier lines}. A phrase's "new" means nobody sent it in what was read before."""
    paths = list(paths or last_day())
    if not paths:
        raise ValueError("No recordings yet: record one with py -m memelearner live LINK")
    live = list(slices(paths, minutes=minutes))
    if not live:
        raise ValueError(f"No danmu in {', '.join(paths)}")
    start = min(message["sent"] for _, _, message in live)
    games = {game for game, _, _ in live}
    chosen = set(paths)
    before = [(game, video, message) for game, video, message in surf.load_messages(live=False)
              if game in games and 0 < message.get("sent", 0) < start]
    before += [(game, video, message)
               for game, video, message in slices([p for p in recordings() if p not in chosen],
                                                  minutes=minutes)
               if game in games and message["sent"] < start]
    candidates, summary = surf.find_candidates(
        live + before, days=0, now=start, min_people=people,
        known=surf.known_phrases() if known is None else known, top=top)
    compared = {"recordings": [os.path.basename(p) for p in paths], "start": int(start),
                "parts": len({video for _, video, _ in live}), "danmu": len(live),
                "senders": len({message["who"] for _, _, message in live}),
                "before": len(before), "minutes": minutes}
    return candidates, summary, compared
