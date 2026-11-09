"""Variants of one meme: phrases that are the same meme, written differently.

    超威同意了, chovy同意了, 大超同意了, 三叔不同意     one meme: 「XX同意了」
    超威今年真不一样了, 真不一样了                     one meme: 「XX今年真不一样了」

Two phrases are the same meme when they are the same once these differences are taken away
(same_meme(); shape() is what is left of a phrase):

    Who            A player's name or nickname (data/nicknames.txt, see nicknames.py: 超威,
                   大超 and chovy are Chovy, 三叔 is Canyon), or any word in letters (ruler,
                   kiin), is just "someone", XX: 超威同意了 and ruler同意了 are both
                   XX同意了. A name in Chinese that isn't in the table (兰子, two or three
                   characters) is someone too where other phrases show that names go
                   there, two different ones at least (兰子同意了, next to 超威同意了 and
                   ruler同意了; but 还在洗 isn't 还在ig: one character is too often a word).
    Left out       One is the other with one or two parts left out: 真不一样了 is
                   超威今年真不一样了 without 超威今年, and 超威同意了 is 超威会同意的
                   without 会. What's left must be at least 2 characters that aren't a name,
                   and what's left out names and at most 2 other characters: 感谢多兰 isn't
                   千言万语感谢多兰. Two characters alone need a name with them to leave
                   other characters out: wei发狂了 is 发狂了, but 退役 isn't 感谢退役.
    Turned around  不 or 没 added: 三叔不同意 is 三叔同意了 turned around.
    Written longer A character written more than once in a row (同意意意了, chooooovy), and
                   mood words at the end (了 呢 吧 啊 ...), as the finder counts them.

Phrases that differ in anything else stay apart, however much they look alike: 我们是冠军
and 我们是路边, or 目前不知道怎么赢 and 目前不知道怎么输, mean different things. groups()
puts the variants among some phrases together, each group named by a pattern in which a
name is XX (「XX同意了」), with the names in it and who they are.
"""

import functools
import re

from . import nicknames, store, surf

SLOT = "X"  # someone, in a shape: store.normal() writes lowercase only, so it's never text
PATTERN_NAME = "XX"  # ... and in a pattern you read: 「XX同意了」
NEGATIONS = "不没"  # added to turn a meme around: 三叔不同意
MAX_LEFT_OUT = 2  # parts of a phrase another one may leave out
MIN_KEPT = 2  # characters, not counting names, that one that leaves parts out must keep
MAX_ADDED = 2  # characters, not counting names, that it may leave out
ALONE_KEPT = 3  # ... and that it must keep for that without a name (退役 isn't 感谢退役)
NAME_CHARS = (2, 3)  # characters of a Chinese name that isn't in the table (兰子): one
#                      alone is too often a word (还在洗, 还在送 aren't names)
SHOWN_NAMES = 2  # different names that must go where XX is before an unknown one counts
NOT_NAMES = {"大家", "今年", "今天", "现在", "终于", "一定", "应该", "已经", "果然", "居然",
             "竟然", "必须", "就是", "还是", "也是", "真的", "确实"}  # words, not names
NOT_NAME_START = "我你他她它咱您这那谁都也还就又再才别快真太很要会能可已"  # 我们, 这个, 又 ...
LETTERS = re.compile(r"[0-9a-z]*[a-z][0-9a-z]*")  # a word in letters (with digits: t1, s7)
RUNS = re.compile(r"(.)\1+")


def names(phrase):
    """The names in a phrase, in the order they come: [(name, player)], player from the
    nickname table, or "" for a word in letters that isn't in it (ruler). As
    store.normal() writes them: chooooovy."""
    text = store.normal(phrase)
    return [(text[start:end], player) for start, end, player in _names(text)]


def _names(text):
    """Where the names are in a text that store.normal() wrote: [(start, end, player)]."""
    found = nicknames.spans(text)
    for word in LETTERS.finditer(text):
        if not any(start < word.end() and word.start() < end for start, end, _ in found):
            found.append((word.start(), word.end(), ""))
    return sorted(found)


def _with(text, found, name):
    """text with each name in it as name, one for names that come one after another."""
    pieces, at = [], 0
    for start, end, _ in found:
        pieces.append(text[at:start])
        if not (pieces[-1] == "" and len(pieces) > 1):  # one XX for 超威chovy
            pieces.append(name)
        at = end
    return "".join(pieces) + text[at:]


def shape(phrase):
    """What is left of a phrase once names are someone (X), each character is written once
    and the mood words at its end are left out: 超威同意了 -> X同意, 三叔不同意 -> X不同意,
    chooooovy同意意意了 -> X同意, 真不一样了 -> 真不一样."""
    text = store.normal(phrase)
    shape = RUNS.sub(r"\1", _with(text, _names(text), SLOT))
    while shape and shape[-1] in surf.PARTICLES:
        shape = shape[:-1]
    return shape


def pattern(phrase):
    """A phrase with each name in it as XX: 超威同意了 -> XX同意了; one without names is as
    it is (真不一样了)."""
    text = store.normal(phrase)
    return _with(text, _names(text), PATTERN_NAME)


def _kept(shape):
    """The characters of a shape that aren't someone."""
    return len(shape) - shape.count(SLOT)


@functools.lru_cache(maxsize=65536)
def _left_out(short, long):
    """The fewest parts of long to leave out so that what's left is short, or None when
    leaving parts out can't make it short."""
    @functools.lru_cache(maxsize=None)
    def fewest(i, j, leaving):
        if i == len(short):
            return 0 if j == len(long) or leaving else 1
        if len(long) - j < len(short) - i:
            return None
        best = []
        if short[i] == long[j]:
            best.append(fewest(i + 1, j + 1, False))
        skip = fewest(i, j + 1, True)
        if skip is not None:
            best.append(skip + (0 if leaving else 1))
        best = [b for b in best if b is not None]
        return min(best) if best else None
    return fewest(0, 0, False)


def _leaves_out(a, b):
    """Whether one shape is the other with up to MAX_LEFT_OUT parts left out: it keeps at
    least MIN_KEPT characters that aren't names, and leaves out names and up to MAX_ADDED
    other characters; to leave out any of those, it must keep ALONE_KEPT characters or a
    name (see the top of this file)."""
    short, long = sorted((a, b), key=len)
    kept, added = _kept(short), _kept(long) - _kept(short)
    if kept < MIN_KEPT or added > MAX_ADDED or added and kept < ALONE_KEPT and SLOT not in short:
        return False
    parts = _left_out(short, long)
    return parts is not None and parts <= MAX_LEFT_OUT


def _turned(shape):
    """A shape without the 不 or 没 that turn it around."""
    return "".join(char for char in shape if char not in NEGATIONS)


def _same_shapes(a, b):
    """same_meme() for two shapes, without names in Chinese that aren't in the table."""
    if a == b:
        return _kept(a) >= 1  # not two names on their own (chooovy and ruler)
    if _turned(a) == _turned(b):
        return _kept(_turned(a)) >= 1
    return _leaves_out(a, b) or _leaves_out(_turned(a), _turned(b))


def same_meme(a, b):
    """Whether two phrases are the same meme (see the top of this file). An unknown name in
    Chinese only counts in groups(), which sees where other names go."""
    return _same_shapes(shape(a), shape(b))


def _fill(slotted, other):
    """The Chinese name that other has where slotted has someone (X同意 and 兰子同意: 兰子),
    or None. Without 不 and 没, so 兰子不同意 fits X同意 with 兰子 too."""
    slotted, other = _turned(slotted), _turned(other)
    if slotted.count(SLOT) != 1 or SLOT in other or _kept(slotted) < MIN_KEPT:
        return None
    before, after = slotted.split(SLOT)
    low, high = NAME_CHARS
    found = re.fullmatch(re.escape(before) + f"([一-鿿]{{{low},{high}}})" + re.escape(after),
                         other)
    if not found:
        return None
    name = found.group(1)
    return None if name in NOT_NAMES or name[0] in NOT_NAME_START else name


def groups(phrases):
    """The phrases that are the same meme, together: [{"pattern": "XX同意了", "phrases":
    [超威同意了, chovy同意了, ...], "names": [{"name": "超威", "player": "Chovy"}, ...]}].

    phrases: {phrase: people} (how many people sent it), or the phrases in a list, the most
    sent first. In a group, the most sent phrase comes first. The pattern is the most sent
    phrase that has a name, with the name as XX, when at least half as many people send it
    as the first one (「XX今年真不一样了」: 107 people, next to 153 for 真不一样了), else the
    first one (让二追三, not XX加油让二追三). The group with the most sent phrase comes
    first. names: the names in its phrases, each once, player "" for one that isn't in the
    table (ruler, and 兰子 where others show a name goes there)."""
    people = dict(phrases) if isinstance(phrases, dict) else {p: 0 for p in phrases}
    order = sorted(people, key=lambda p: -people[p])  # stable: a list keeps its order
    rank = {phrase: i for i, phrase in enumerate(order)}
    shapes = {phrase: shape(phrase) for phrase in order}
    found = {phrase: _names(store.normal(phrase)) for phrase in order}
    leader = {phrase: phrase for phrase in order}

    def lead(phrase):
        while leader[phrase] != phrase:
            leader[phrase] = leader[leader[phrase]]
            phrase = leader[phrase]
        return phrase

    def join(a, b):
        a, b = lead(a), lead(b)
        if a != b:  # the one sent more leads
            first, second = sorted((a, b), key=rank.get)
            leader[second] = first

    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if _same_shapes(shapes[a], shapes[b]):
                join(a, b)

    # Names in Chinese that aren't in the table: where two different names go already
    who = {}  # {shape with someone: the names that go there}
    for phrase in order:
        if shapes[phrase].count(SLOT) == 1:
            who.setdefault(_turned(shapes[phrase]), set()).update(
                player or store.normal(phrase)[start:end]
                for start, end, player in found[phrase])
    unknown = {}  # {phrase: the name in it that isn't in the table}
    for slotted, names_there in who.items():
        if len(names_there) < SHOWN_NAMES:
            continue
        for phrase in order:
            name = _fill(slotted, shapes[phrase])
            if name:
                unknown[phrase] = name
                join(phrase, next(p for p in order if _turned(shapes[p]) == slotted))

    together = {}
    for phrase in order:
        together.setdefault(lead(phrase), []).append(phrase)
    for phrase, name in unknown.items():  # where it is, as names() gives them
        text = store.normal(phrase)
        if name in text:
            found[phrase] = sorted(found[phrase] + [(text.index(name),
                                                     text.index(name) + len(name), "")])

    result = []
    for members in together.values():
        named = [p for p in members if found[p] and 2 * people[p] >= people[members[0]]]
        if named:
            head = store.normal(named[0])
            name = _with(head, found[named[0]], PATTERN_NAME)
        else:
            name = members[0]
        listed, seen = [], set()
        for phrase in members:
            text = store.normal(phrase)
            for start, end, player in found[phrase]:
                if nicknames.key(text[start:end]) not in seen:
                    seen.add(nicknames.key(text[start:end]))
                    listed.append({"name": text[start:end], "player": player})
        result.append({"pattern": name, "phrases": members, "names": listed})
    return result
