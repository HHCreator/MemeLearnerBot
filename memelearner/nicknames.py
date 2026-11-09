"""Players' nicknames: data/nicknames.txt, a table you edit yourself.

Danmu call players by nicknames the model often doesn't know: Chovy is 超威, 大超 or
chooovy, and Canyon is 三叔. So 超威同意了, chovy同意了 and 大超同意了 look like three
memes when they are one meme about one player, and 三叔不同意 is the same one turned
around. The table says who each nickname is:

    Chovy = 超威, 大超, chooovy
    Canyon = 三叔, 三姆斯

One player a line: the player on the left of =, the nicknames on the right, apart by
commas (, ， 、 or spaces); # starts a note. The player's own ID counts as a nickname, and a
player can have more than one line. Case and width don't matter (ＣＨＯＶＹ is chovy), nor
how many times a letter is written: chooooovy, chooovy and chovy are one nickname (key()).
A team works the same way (IG = 埃及).

player_of() says who a nickname is, find() finds the nicknames in a phrase or a line, and
is_name() says whether a phrase is only a nickname: the finder doesn't list those (surf.py),
since they are names, not memes.
"""

import os
import re
import unicodedata

from . import store

NICKNAMES_FILE = os.path.join(store.HERE, "data", "nicknames.txt")
SEPARATORS = re.compile(r"[,，、;；/\s]+")
RUN = re.compile(r"([a-z])\1+")  # a letter written more than once in a row: chooooovy
LATIN = re.compile(r"[0-9a-z]")

_cache = {}  # the last table read: {"stamp": (path, time, size), "players", "keys"}


class TableError(ValueError):
    """data/nicknames.txt has a line the bot can't read: the message names it."""


def key(word):
    """The form a nickname is looked up by: written as store.normal() writes it, with each
    letter written once where it's written more than once in a row (ＣＨＯＯＯＯＶＹ -> chovy,
    kiin -> kin, so kiiin is kiin). Chinese characters stay as they are: 飞飞 isn't 飞."""
    return RUN.sub(r"\1", store.normal(word))


def parse(text, name="nicknames.txt"):
    """The table in a text: {player: [nickname, ...]}, each player's own ID first, then the
    nicknames as the text writes them, in its order, each once. Raises TableError naming the
    line when a line has no = or no player, or when a nickname is another player's already."""
    players, owner = {}, {}  # owner: {nickname key: (player, line number)}
    spelled = {}  # each player's key: the player as the first line with them writes it
    for number, raw in enumerate(text.splitlines(), 1):
        line = unicodedata.normalize("NFKC", raw).split("#", 1)[0].strip()
        if not line:
            continue
        where = f"{name} line {number} ({raw.strip()})"
        if "=" not in line:
            raise TableError(f"{where}: write the player, =, then the nicknames "
                             "(Chovy = 超威, 大超)")
        player, _, rest = line.partition("=")
        player = spelled.setdefault(key(player), player.strip())
        if not key(player):
            raise TableError(f"{where}: there's no player before =")
        names = players.setdefault(player, [])
        for nickname in [player] + SEPARATORS.split(rest):
            nickname = nickname.strip()
            k = key(nickname)
            if not k:
                continue
            other, line_of = owner.setdefault(k, (player, number))
            if other != player:
                raise TableError(f"{where}: {nickname} is {other}'s already "
                                 f"(line {line_of}); one nickname can only be one player's")
            if all(store.normal(known) != store.normal(nickname) for known in names):
                names.append(nickname)  # chooovy stays too, as you wrote it
    return players


def load(path=None):
    """The table in data/nicknames.txt (or in path): {player: [nickname, ...]}, as parse()
    gives it; empty when there's no file. Read again only when the file changed."""
    return dict(_read(path)["players"])


def _read(path=None):
    path = path or NICKNAMES_FILE
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return {"players": {}, "keys": {}}
    stamp = (path, stat.st_mtime_ns, stat.st_size)
    if _cache.get("stamp") != stamp:
        try:
            with open(path, encoding="utf-8-sig") as f:  # -sig: an editor may add a BOM
                players = parse(f.read(), os.path.basename(path))
        except UnicodeDecodeError:
            raise TableError(f"{path} isn't UTF-8: save it as UTF-8 (Notepad: Save as, "
                             "Encoding UTF-8)") from None
        _cache.update(stamp=stamp, players=players,
                      keys={key(nickname): player for player, nicknames in players.items()
                            for nickname in nicknames})
    return _cache


def player_of(word, path=None):
    """Who a nickname is: 超威 -> "Chovy", CHOOOOVY -> "Chovy", 三叔 -> "Canyon"; None when
    it's nobody's in the table."""
    return _read(path)["keys"].get(key(word))


def find(text, path=None):
    """The nicknames in a text (a phrase or a line), in the order they come: [(nickname,
    player)], each nickname as the text writes it, the way store.normal() writes it
    (chooooovy). Where two could start, the longer one wins (大超 rather than 超, were both
    in the table), and one of letters or digits only counts as a whole word: chovy is in
    "chovy同意了", not in "chovyx"."""
    text = store.normal(text)
    return [(text[start:end], player) for start, end, player in spans(text, path)]


def spans(text, path=None):
    """Where the nicknames are in a text that store.normal() wrote, as find() finds them:
    [(start, end, player)]."""
    keys = _read(path)["keys"]
    if not keys:
        return []
    # The text with each letter written once, and where each of its characters came from
    squeezed, starts, ends = [], [], []
    for i, char in enumerate(text):
        if squeezed and LATIN.match(char) and char.isalpha() and text[i - 1] == char:
            ends[-1] = i + 1
            continue
        squeezed.append(char)
        starts.append(i)
        ends.append(i + 1)
    squeezed = "".join(squeezed)
    longest = max(map(len, keys))
    found, at = [], 0
    while at < len(squeezed):
        for length in range(min(longest, len(squeezed) - at), 0, -1):
            piece = squeezed[at:at + length]
            if piece in keys and _whole(squeezed, at, at + length):
                found.append((starts[at], ends[at + length - 1], keys[piece]))
                at += length
                break
        else:
            at += 1
    return found


def _whole(text, start, end):
    """Whether text[start:end] isn't part of a longer run of letters and digits."""
    if LATIN.match(text[start]) and start > 0 and LATIN.match(text[start - 1]):
        return False
    return not (LATIN.match(text[end - 1]) and end < len(text) and LATIN.match(text[end]))


def is_name(phrase, path=None):
    """Whether a whole phrase is only a nickname in the table (chooovy, 超威！): a name, not
    a meme."""
    return bool(store.normal(phrase)) and player_of(phrase, path) is not None
