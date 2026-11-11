"""Running by itself for days on this computer, with Windows Task Scheduler.

    py -m memelearner auto start      make the scheduled tasks (开始自动跑.bat)
    py -m memelearner auto stop       end and delete them (停止自动跑.bat)
    py -m memelearner auto status     the runs so far, the pool and the log's end (看看进度.bat)
    py -m memelearner auto report     the pool's memes in the browser report (打开报告.bat)
    py -m memelearner auto daily      one daily run: what the daily task runs
    py -m memelearner auto live --rooms 6:lol   look at a live room and record it while it is
                                      live: what its live task runs every EVERY minutes

A daily run reads the newest videos of GAMES (learn); a live run records a room of ROOMS
while it is live (until it goes offline, or for LIVE_HOURS) and finds its phrases
(live-phrases). Both then judge the phrases, look up every meme found (explain: the videos
and web pages that explain it, after which it is judged again) and keep them in the pool,
surf/auto/pool.json, which gathers the runs of the DAYS days for the report at the end
(report() and review.py: one row per meme, its newest verdict). They only read, like the
commands they run.

start writes surf/auto/settings.json and registers the tasks for this user, from XML: the
daily one, and a live one for each room, so two rooms can be recorded at once. They run
pythonw, so no window opens; a run missed while the computer was off or asleep starts when
it is back (StartWhenAvailable); a task doesn't start again while it still runs
(IgnoreNew); they all stop by themselves after DAYS days (EndBoundary). The first daily run
starts at once, the next ones at AT on the days after; a live task looks every EVERY
minutes. With --pull, a daily run first pulls the newest code of the folder's branch (git
pull --ff-only) and runs that, so a fix pushed during the days is used from the next run.

Runs take turns finding, judging and looking up, as they share candidates.json (_turn(), a
lock file); a live run records without waiting for its turn. What a run prints goes to
surf/auto/log.txt (a look at rooms that aren't live only to surf/auto/rooms.json, so the
log doesn't fill up with them), ending with a summary line: what it read and found, the
memes nothing explains, and the problems it ran into (PROBLEMS: Bilibili's rate limit or a
captcha, DeepSeek without balance or key so that qwen3:14b stood in, no model at all, sites
that refused the bot, an error).
"""

import contextlib
import datetime
import os
import re
import subprocess
import sys
import time
import traceback

from . import bilibili, explainers, judging, live, review, surf

DAYS = 3  # days the tasks run
AT = "12:00"  # when the daily run starts, this computer's time
EVERY = 30  # minutes between two looks at the live rooms
LIVE_HOURS = 6  # a recording stops after this, if the room is still live
GAMES = ("lol", "valorant")  # whose videos a daily run reads
ROOMS = ("6:lol",)  # the live rooms watched, as room:game
LIVE_TOP, LIVE_KNOWN_TOP = 40, 10  # phrases kept of a recording, as live-phrases keeps
WAIT_MINUTES = 240  # a run waits this long for another run's turn, then gives up
LOCK_HOURS = 8  # a turn held longer than this was left by a run that died
TAIL = 40  # lines of the log status shows
DAILY_TASK = "MemeLearnerBot daily"  # the scheduled tasks' names
LIVE_TASK = "MemeLearnerBot live {room}"  # one for each room, so two rooms can be recorded at once
VERSION = 1  # pool.json's format
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console for git

# What went wrong in a run, by the lines the bot prints when it happens (once each)
PROBLEMS = (
    ("B 站限速（412）或风控", r"Bilibili keeps refusing requests|\(HTTP 4(12|29)\)"),
    ("B 站要验证码，那一步跳过了", r"Bilibili wants a captcha"),
    ("连不上 B 站", r"Couldn't reach Bilibili|network trouble"),
    ("DeepSeek 没余额", r"the balance has run out"),
    ("DeepSeek 的密钥没设或不对", r"put your API key in the|API key in \S+ was refused"),
    ("在线模型问不了，本地模型顶上", r"the model on this computer, (judges|sums|merges)"),
    ("模型都问不了", r"Couldn't ask the model"),
    ("有网站挡住了机器人", r"^Left alone for the rest of the run"),
    ("出错", r"^Traceback"),
)


class Busy(Exception):
    """Another run kept its turn too long (WAIT_MINUTES)."""


# -- where things are ---------------------------------------------------------------------

def auto_dir():
    return os.path.join(surf.SURF_DIR, "auto")


def _file(name):
    return os.path.join(auto_dir(), name)


def log_file():
    return _file("log.txt")


def code_folder():
    """The folder the bot's code is in (the one with memelearner/ in it)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settings():
    """surf/auto/settings.json: {"days", "at", "every", "hours", "games", "rooms", "pull",
    "folder", "started", "until"}, what start() set (the defaults before)."""
    found = surf._load_json(_file("settings.json"), {})
    return dict({"days": DAYS, "at": AT, "every": EVERY, "hours": LIVE_HOURS,
                 "games": list(GAMES), "rooms": list(ROOMS), "pull": False,
                 "folder": code_folder(), "started": "", "until": ""}, **found)


def _room(spec):
    """(room number, game) of a room as start() takes it: ROOM[:GAME], the room as a number
    or a link (https://live.bilibili.com/6:lol), the game lol when none is given. ValueError
    when it isn't a room."""
    room, game = re.fullmatch(r"(.+?)(?::([A-Za-z]\w*))?", str(spec).strip()).groups()
    return live.room_of(room), (game or "lol").lower()


def load_pool():
    """surf/auto/pool.json: {"version", "runs": [run, ...], "candidates": {"game:phrase":
    candidate}, "videos": {id: video}}: the runs so far (see _running()), and every phrase
    they found as candidates.json has it (with its verdict), the newest run's, with "runs"
    (which runs found it), "first" (when) and the most "people" that sent it in one run."""
    pool = surf._load_json(_file("pool.json"), {})
    if pool.get("version") != VERSION:
        pool = {}
    return {"version": VERSION, "runs": list(pool.get("runs") or []),
            "candidates": dict(pool.get("candidates") or {}),
            "videos": dict(pool.get("videos") or {})}


def _save_pool(pool):
    surf._save_json(_file("pool.json"), pool)


def keep(run):
    """Put the phrases of the last run (candidates.json: judged and looked up) in the pool."""
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    pool = load_pool()
    for candidate in last.get("candidates") or []:
        key = f"{candidate['game']}:{candidate['phrase']}"
        old = pool["candidates"].get(key) or {}
        new = dict(candidate, runs=sorted(set(old.get("runs") or []) | {run["n"]}),
                   first=old.get("first") or run["started"])
        new["people"] = max(candidate.get("people") or 0, old.get("people") or 0)
        if not new.get("judge") and old.get("judge"):
            new["judge"] = old["judge"]  # not judged this time (no model): the last verdict
        pool["candidates"][key] = new
    pool["videos"].update(last.get("videos") or {})
    _save_pool(pool)


# -- taking turns ---------------------------------------------------------------------------

@contextlib.contextmanager
def _turn(wait=None):
    """Wait for this run's turn to find, judge and look up (another run may be at it: they
    share candidates.json), and hold it. Raises Busy after waiting wait minutes
    (WAIT_MINUTES)."""
    wait = WAIT_MINUTES if wait is None else wait
    path = _file("lock")
    os.makedirs(auto_dir(), exist_ok=True)
    give_up, said = time.time() + wait * 60, False
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                held = time.time() - os.path.getmtime(path)
            except OSError:
                continue  # it was just given back
            if held > LOCK_HOURS * 3600:
                print(f"A turn taken {held / 3600:.0f} hours ago was never given back (that run "
                      "must have stopped halfway): taking it.")
                with contextlib.suppress(OSError):
                    os.remove(path)
                continue
            if time.time() >= give_up:
                raise Busy(f"another run kept its turn for {wait} minutes") from None
            if not said:
                print("Another run is finding, judging or looking up: waiting for it ...")
                said = True
            time.sleep(60)
            continue
        with os.fdopen(handle, "w") as f:
            f.write(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M')}\n")
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


# -- a run --------------------------------------------------------------------------------

class _Seen:
    """What a run prints: to where it went before, and kept to find its problems."""

    def __init__(self, stream, kept):
        self.stream, self.kept = stream, kept

    def write(self, text):
        self.kept.append(text)
        return self.stream.write(text) if self.stream is not None else len(text)

    def flush(self):
        if self.stream is not None:
            self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextlib.contextmanager
def _running(kind):
    """A run of the pool: {"n", "kind", "started", "ended", "read", "found", "memes",
    "explained", "unexplained": [phrase, ...], "problems": {problem: times}, "error",
    "summary"}. Whatever goes wrong inside is logged with its traceback, and the run is
    kept as far as it got; its summary line ends the log."""
    run = {"n": _number(), "kind": kind, "started": time.strftime("%Y-%m-%d %H:%M"),
           "ended": "", "read": "", "found": 0, "memes": 0, "explained": 0, "unexplained": [],
           "problems": {}, "error": ""}
    kept, out, err = [], sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Seen(out, kept), _Seen(err, kept)
    print(f"== Run {run['n']} ({'live' if kind == 'live' else 'daily'}), "
          f"{run['started']}")
    try:
        yield run
    except Busy as error:
        print(f"Skipped: {error}")
        run["error"] = str(error)
    except Exception as error:  # noqa: BLE001 - anything: logged, and the next run goes on
        traceback.print_exc()
        run["error"] = f"{type(error).__name__}: {error}"
    finally:
        sys.stdout, sys.stderr = out, err
        _end(run, "".join(kept))


def _number():
    """The next run's number: one more than the runs started so far (surf/auto/started.txt,
    as a live run that records for hours isn't in the pool yet when a daily one starts)."""
    os.makedirs(auto_dir(), exist_ok=True)
    with open(_file("started.txt"), "a+", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + "\n")
        f.seek(0)
        return sum(1 for line in f if line.strip())


def _counted(run):
    """What the run found, by candidates.json and explainers.json now."""
    last = surf._load_json(surf.CANDIDATES_FILE, {})
    entries = explainers.load()["phrases"]
    found = last.get("candidates") or []
    memes = [c for c in found if (c.get("judge") or {}).get("kind") == "meme"]
    entry = lambda c: entries.get(f"{c['game']}:{c['phrase']}") or {}
    run.update(found=len(found), memes=len(memes),
               explained=sum(1 for c in memes if explainers.told(entry(c))),
               unexplained=[c["phrase"] for c in memes if entry(c).get("looked")
                            and not explainers.told(entry(c))])


def _end(run, printed):
    """Count the run's problems from what it printed, write its summary line and keep it."""
    for problem, pattern in PROBLEMS:
        times = len(re.findall(pattern, printed, re.M))
        if times:
            run["problems"][problem] = times
    run["ended"] = time.strftime("%Y-%m-%d %H:%M")
    run["summary"] = summary(run)
    print(run["summary"])
    pool = load_pool()
    pool["runs"].append(run)
    _save_pool(pool)


def summary(run):
    """A run's summary line, for the log."""
    when = f"{run['started'][5:]}–{run['ended'][11:]}"
    parts = [f"== 总结：第 {run['n']} 次（{'直播' if run['kind'] == 'live' else '每天的'}），{when}"]
    if run["read"]:
        parts.append(f"读了 {run['read']}")
    if run["found"]:
        parts.append(f"找到 {run['found']} 个说法，{run['memes']} 个是梗")
        unexplained = run["unexplained"]
        parts.append(f"查到解释 {run['explained']} 个" + (
            f"，没查到 {len(unexplained)} 个：{'、'.join(unexplained[:20])}"
            + ("……" if len(unexplained) > 20 else "") if unexplained else ""))
    problems = "；".join(f"{p} {n} 次" for p, n in run["problems"].items())
    parts.append(f"问题：{problems or '没有'}")
    if run["error"]:
        parts.append(f"停在：{run['error'][:200]}")
    return "；".join(parts)


def _process(run, model=None):
    """Judge what the run found, look up every meme (explain, which judges them again) and
    keep them in the pool; without a model, the phrases are kept unjudged."""
    try:
        with judging.awake():
            judging.judge(None, model)
            print()
            explainers.explain(None, None, None, model, top=None)
    except judging.ModelUnavailable as error:
        print(f"Couldn't ask the model: {error}. The phrases are kept as they are.")
    finally:
        _counted(run)
        keep(run)


def _git(folder, *args):
    """Run git in folder; returns subprocess.run()'s result. Tests replace this."""
    return subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True,
                          errors="replace", timeout=300, creationflags=NO_WINDOW)


def _pull(folder):
    """git pull --ff-only in folder; True when that brought newer code."""
    try:
        before = _git(folder, "rev-parse", "HEAD").stdout.strip()
        pulled = _git(folder, "pull", "--ff-only", "-q")
        after = _git(folder, "rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Couldn't pull the newest code: {error}")
        return False
    if pulled.returncode:
        print(f"Couldn't pull the newest code: {(pulled.stderr or pulled.stdout).strip()[:300]}")
        return False
    if after != before:
        print(f"Pulled the newest code ({before[:7]} -> {after[:7]}).")
        return True
    return False


def daily(model=None, pull=None):
    """One daily run: read the newest videos of the games in settings(), then _process()."""
    found = settings()
    if (found["pull"] if pull is None else pull) and _pull(found["folder"]):
        # Run the code just pulled, in a fresh Python: this one has the old code loaded
        _again([sys.executable, "-m", "memelearner", "auto", "daily", "--no-pull"]
               + (["--model", model] if model else []), found["folder"])
        return
    with _running("daily") as run, _turn():
        with judging.awake():
            candidates, counts = surf.learn(found["games"])
        run["read"] = (f"{sum((c or {}).get('videos', 0) for c in counts.values())} 个视频里"
                       f"最近的 {sum((c or {}).get('recent', 0) for c in counts.values())} 条"
                       "弹幕和评论")
        print()
        _process(run, model)


def _again(command, folder):
    """Run command in folder, logging to the same log. Tests replace this."""
    subprocess.run(command, cwd=folder, env=dict(os.environ, MEMELEARNER_LOG=log_file()),
                   creationflags=NO_WINDOW)


class _Offline:
    """record()'s stop: whether a live room isn't live any more (asked now and then)."""

    why = "the room isn't live any more"

    def __init__(self, room):
        self.room = room

    def __call__(self):
        try:
            return not live.room_info(self.room)["live"]
        except (bilibili.Error, bilibili.Blocked, OSError, KeyError, TypeError):
            return False  # can't tell: go on


def watch(model=None, log=None, rooms=None):
    """One look at some live rooms (rooms: ROOM[:GAME]s, by default the ones in settings()),
    what a live task runs: record the first one that is live, until it goes offline (or for
    "hours"), then find its phrases and _process() them. Returns the recording's path, or
    None when no room is live. Each room's last look is kept in surf/auto/rooms.json; log()
    is called first when a room is live (cli.py starts the log then, so the log has the
    recordings but not every look at a room that isn't live)."""
    found, looked = settings(), {}
    for spec in found["rooms"] if rooms is None else rooms:
        try:
            room, game = _room(spec)
            info = live.room_info(room)
        except (ValueError, bilibili.Error, bilibili.Blocked, OSError, KeyError,
                TypeError) as error:
            looked[str(spec).partition(":")[0]] = f"看不了：{error}"
            continue
        looked[str(room)] = "开播了" if info["live"] else "没开播"
        if info["live"]:
            break
    when = time.strftime("%Y-%m-%d %H:%M")
    seen = surf._load_json(_file("rooms.json"), {})
    seen.update({room: {"looked": when, "how": how} for room, how in looked.items()})
    surf._save_json(_file("rooms.json"), seen)
    if "开播了" not in looked.values():
        return None
    if log:
        log()
    path = None
    with _running("live") as run:
        path = live.record(room, until=time.time() + found["hours"] * 3600, game=game,
                           stop=_Offline(room))
        with _turn():
            try:
                phrases, counts, compared = live.find([path])
            except ValueError as error:  # no danmu in it: the room went offline at once
                print(f"{error}: nothing to look for.")
                return path
            new = [c for status in ("new", "rising", "first look")
                   for c in [c for c in phrases if c["status"] == status][:LIVE_TOP]]
            others = [c for c in phrases if c["status"] in ("steady", "older")][:LIVE_KNOWN_TOP]
            surf.save(new + others, counts, 0, live=compared)
            run["read"] = (f"{compared['danmu']} 条直播弹幕（{compared['senders']} 个人，"
                           f"{', '.join(compared['recordings'])}）")
            print(f"{len(new + others)} phrases of {compared['danmu']} danmu, as live-phrases "
                  "keeps them.\n")
            _process(run, model)
    return path


# -- the scheduled tasks ------------------------------------------------------------------

def _schtasks(*args):
    """Run schtasks.exe; returns (exit code, what it printed). Tests replace this."""
    try:
        done = subprocess.run(["schtasks", *args], capture_output=True, text=True,
                              errors="replace", timeout=60, creationflags=NO_WINDOW)
    except OSError as error:
        return 1, str(error)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _pythonw():
    """pythonw.exe beside this Python, which runs without a window (or this Python)."""
    folder, name = os.path.split(sys.executable)
    windowless = os.path.join(folder, re.sub(r"^python(?=[\d.]*(\.exe)?$)", "pythonw", name,
                                             flags=re.I))
    return windowless if os.path.exists(windowless) else sys.executable


def _xml(description, trigger, action, folder, hours):
    """A task's XML for schtasks /Create /XML: run as this user while logged on."""
    escape = lambda text: (str(text).replace("&", "&amp;").replace("<", "&lt;")
                           .replace(">", "&gt;").replace('"', "&quot;"))
    user = "\\".join(part for part in (os.environ.get("USERDOMAIN"), os.environ.get("USERNAME"))
                     if part)
    command = _pythonw()
    if " " in command:  # C:\Program Files\...: quoted (but not the folder, which mustn't be)
        command = f'"{command}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
  </RegistrationInfo>
  <Triggers>
{trigger}
  </Triggers>
  <Principals>
    <Principal id="Author">
{f"      <UserId>{escape(user)}</UserId>{chr(10)}" if user else ""}      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT{hours}H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(action)}</Arguments>
      <WorkingDirectory>{escape(folder)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _at(day, at):
    """day (a date) at the clock time at (HH:MM), as Task Scheduler writes times."""
    hour, minute = (int(part) for part in re.fullmatch(r"(\d{1,2})[:：](\d{2})", at).groups())
    return datetime.datetime.combine(day, datetime.time(hour, minute)).strftime("%Y-%m-%dT%H:%M:%S")


def task_names(rooms):
    """The names of the scheduled tasks for some rooms (ROOM:GAME): the daily one first."""
    return [DAILY_TASK] + [LIVE_TASK.format(room=str(spec).partition(":")[0]) for spec in rooms]


def start(at=AT, days=DAYS, every=EVERY, rooms=ROOMS, games=GAMES, hours=LIVE_HOURS,
          pull=False):
    """Register the daily task and a live task for each room (see the top of this file),
    start the first daily run now, and write settings.json. Returns True when all the tasks
    were made. ValueError for a time, a room or a game it doesn't know."""
    clock = re.fullmatch(r"(\d{1,2})[:：](\d{2})", at)
    if not clock or int(clock.group(1)) > 23 or int(clock.group(2)) > 59:
        raise ValueError(f"--at wants a time like 12:00, not {at}")
    rooms = [f"{room}:{game}" for room, game in map(_room, rooms)]  # ValueError: no room
    known = surf.sources()
    unknown = [game for game in list(games) + [spec.partition(":")[2] for spec in rooms]
               if game not in known]
    if unknown:
        raise ValueError(f"Unknown game {', '.join(unknown)}; data/surf_sources.json has: "
                         f"{', '.join(known)}")
    now = datetime.datetime.now().replace(second=0, microsecond=0)
    until = now + datetime.timedelta(days=days)
    end = until.strftime("%Y-%m-%dT%H:%M:%S")
    folder = code_folder()
    os.makedirs(auto_dir(), exist_ok=True)
    for name in set(task_names(settings()["rooms"])) - set(task_names(rooms)):
        _schtasks("/Delete", "/TN", name, "/F")  # a room the last start watched, not this one
    daily, *recording = task_names(rooms)
    tasks = [(daily, "daily", f"MemeLearnerBot: reads the newest {', '.join(games)} videos' "
              f"danmu once a day, judges the memes and looks them up, until {end}.",
              f"""    <CalendarTrigger>
      <StartBoundary>{_at(now.date() + datetime.timedelta(days=1), at)}</StartBoundary>
      <EndBoundary>{end}</EndBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>""", "-m memelearner auto daily", 8)]
    for n, (name, spec) in enumerate(zip(recording, rooms)):
        first = now + datetime.timedelta(minutes=2 + n)  # one after the other
        tasks.append((name, f"live-{spec.partition(':')[0]}",
                      f"MemeLearnerBot: every {every} minutes, records live room {spec} while it "
                      f"is live, until {end}.",
                      f"""    <TimeTrigger>
      <Repetition>
        <Interval>PT{every}M</Interval>
        <Duration>P{days}D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{first.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>
      <EndBoundary>{end}</EndBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>""", f"-m memelearner auto live --rooms {spec}", hours + 3))
    made = True
    for name, file, description, trigger, action, limit in tasks:
        path = _file(f"{file}.xml")
        with open(path, "w", encoding="utf-16") as f:
            f.write(_xml(description, trigger, action, folder, limit))
        code, said = _schtasks("/Create", "/TN", name, "/XML", path, "/F")
        if code:
            print(f"Couldn't make the task {name}: {said.strip()}")
            made = False
    if not made:
        return False
    surf._save_json(_file("settings.json"), {
        "days": days, "at": at, "every": every, "hours": hours, "games": list(games),
        "rooms": rooms, "pull": pull, "folder": folder,
        "started": now.strftime("%Y-%m-%d %H:%M"), "until": until.strftime("%Y-%m-%d %H:%M")})
    code, said = _schtasks("/Run", "/TN", daily)
    print(f"开始了：{len(tasks)} 个计划任务（任务计划程序里叫"
          + "、".join(f"「{name}」" for name, *_ in tasks)
          + f"），跑到 {until.strftime('%m-%d %H:%M')}，然后自己停。")
    print(f"- 每天 {at} 读 {'、'.join(games)} 的新视频弹幕"
          + ("（第一次现在就开始）" if not code else f"（第一次没能现在开始：{said.strip()}）")
          + "，找梗、judge、查解释。" + ("每次先拉一下这个分支的新代码。" if pull else ""))
    if rooms:
        print(f"- 每 {every} 分钟看一次直播间 {'、'.join(rooms)}，开播就录（下播或录满 {hours} "
              "小时停），录完同样找梗、judge、查解释。")
    print("- 在后台跑，不弹窗口；电脑关着或睡着时错过的那次，开机后补跑。")
    print(f"- 日志：{log_file()}（看看进度.bat 显示最后几十行）")
    print(f"- 停：停止自动跑.bat。{days} 天后：打开报告.bat。")
    return True


def stop():
    """End the tasks if they are running, and delete them."""
    for name in task_names(settings()["rooms"]):
        _schtasks("/End", "/TN", name)
        code, said = _schtasks("/Delete", "/TN", name, "/F")
        print(f"删掉了「{name}」" if not code else f"「{name}」没删掉（可能本来就没有）：{said.strip()}")
    with contextlib.suppress(OSError):
        os.remove(_file("lock"))  # a run ended above can't let go of its turn
    print("自动跑停了。已经找到的梗都还在：打开报告.bat 可以看。")


def _tail(path, lines):
    """The last lines of a text file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200_000))
            return f.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        return []


def status(lines=TAIL):
    """Print how it's going: the tasks, each run's summary, the pool, and the log's end."""
    found, pool = settings(), load_pool()
    if found["started"]:
        print(f"从 {found['started']} 跑到 {found['until']}（每天 {found['at']}；直播间 "
              f"{', '.join(found['rooms'])}，每 {found['every']} 分钟看一次）")
    for name in task_names(found["rooms"]):
        code, said = _schtasks("/Query", "/TN", name, "/FO", "CSV", "/NH")
        fields = re.findall(r'"([^"]*)"', said)
        print(f"「{name}」：" + (f"下次 {fields[1]}，现在 {fields[2]}" if not code and len(fields) >= 3
                                else "没有这个任务"))
    for room, seen in surf._load_json(_file("rooms.json"), {}).items():
        print(f"直播间 {room}：{seen.get('looked', '')} 看的时候{seen.get('how', '')}")
    print()
    for run in pool["runs"]:
        print(run.get("summary") or run)
    memes = [c for c in pool["candidates"].values() if (c.get("judge") or {}).get("kind") == "meme"]
    print(f"\n待审清单：{len(pool['candidates'])} 个说法，{len(memes)} 个是梗。")
    tail = _tail(log_file(), lines)
    if tail:
        print(f"\n日志最后 {len(tail)} 行（{log_file()}）：")
        print("\n".join(tail))


def overview(pool=None):
    """The report's first line: what the runs did and found."""
    pool = load_pool() if pool is None else pool
    runs = pool["runs"]
    if not runs:
        return "还没有跑过"
    entries = explainers.load()["phrases"]
    memes = [c for c in pool["candidates"].values() if (c.get("judge") or {}).get("kind") == "meme"]
    told = sum(1 for c in memes if explainers.told(entries.get(f"{c['game']}:{c['phrase']}") or {}))
    problems = {}
    for run in runs:
        for problem, times in (run.get("problems") or {}).items():
            problems[problem] = problems.get(problem, 0) + times
    daily_runs = sum(1 for run in runs if run["kind"] == "daily")
    return (f"{runs[0]['started'][5:]} 到 {runs[-1]['ended'][5:] or runs[-1]['started'][5:]}："
            f"跑了 {len(runs)} 次（每天的 {daily_runs} 次，直播 {len(runs) - daily_runs} 次），"
            f"找到 {len(pool['candidates'])} 个说法，{len(memes)} 个是梗，{told} 个查到了解释；"
            "出过的问题：" + ("；".join(f"{p} {n} 次" for p, n in problems.items()) or "没有"))


def report(model=None, port=0, browser=True):
    """The pool's phrases in the browser report (review.py), its first line overview()."""
    pool = load_pool()
    rows = review.rows({"candidates": list(pool["candidates"].values()),
                        "videos": pool["videos"]})
    if not rows:
        print("Nothing to report: no run found anything yet, or every phrase found is learned "
              "or turned down.")
        return None
    page = review.Report(rows, model or judging.MODEL, port, overview(pool))
    result = page.run(browser)
    if result:
        print("\n".join(review.summary(result)))
    return result
