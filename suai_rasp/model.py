"""Модель данных расписания ГУАП."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
DAY_NUM = {d: i + 1 for i, d in enumerate(DAYS)}

#: Номер пары -> (начало, конец) в формате HH:MM
LESSON_TIMES = {
    1: ("09:30", "11:00"),
    2: ("11:10", "12:40"),
    3: ("13:00", "14:30"),
    4: ("15:10", "16:40"),
    5: ("17:00", "18:30"),
    6: ("18:40", "20:10"),
    7: ("20:20", "21:50"),
}

WEEK_ANY = 0    #: каждую неделю
WEEK_ODD = 1    #: верхняя (нечётная), маркер ▲
WEEK_EVEN = 2   #: нижняя (чётная), маркер ▼

WEEK_NAMES = {WEEK_ANY: "каждую", WEEK_ODD: "верхняя (нечётная)", WEEK_EVEN: "нижняя (чётная)"}


@dataclass(frozen=True)
class Ref:
    """Ссылка на сущность справочника: числовой id + отображаемое имя."""

    id: int
    name: str

    def __str__(self) -> str:  # pragma: no cover - косметика
        return self.name


@dataclass
class Lesson:
    """Одно занятие в сетке расписания.

    Занятия «вне сетки» имеют day=None и slot=None.
    """

    day: str | None
    slot: int | None
    week: int
    kind: str
    subject: str
    room: Ref | None
    chair: Ref | None
    teachers: list[Ref] = field(default_factory=list)
    groups: list[Ref] = field(default_factory=list)
    uid: int = -1  #: позиция в Schedule.lessons, проставляется при индексации

    @property
    def in_grid(self) -> bool:
        return self.day is not None and self.slot is not None

    @property
    def key(self) -> tuple:
        """Ключ временного слота: (день, пара)."""
        return (self.day, self.slot)

    def overlaps_week(self, other: "Lesson") -> bool:
        """Пересекаются ли занятия по чётности недели."""
        return self.week == WEEK_ANY or other.week == WEEK_ANY or self.week == other.week

    def time_str(self) -> str:
        if not self.in_grid:
            return "вне сетки"
        b, e = LESSON_TIMES[self.slot]
        return f"{self.day} {self.slot} пара ({b}—{e}) [{WEEK_NAMES[self.week]}]"

    def short(self) -> str:
        who = ", ".join(t.name.split(",")[0] for t in self.teachers) or "—"
        gr = ", ".join(g.name for g in self.groups) or "—"
        room = self.room.name if self.room else "—"
        return f"{self.time_str()} | {self.kind} | {self.subject} | ауд. {room} | {who} | гр. {gr}"


class Schedule:
    """Набор занятий с индексами для проверки конфликтов."""

    def __init__(self, lessons: list[Lesson], meta: dict | None = None):
        self.lessons = lessons
        self.meta = meta or {}
        self.reindex()

    def reindex(self) -> None:
        self.by_room: dict[int, list[Lesson]] = {}
        self.by_teacher: dict[int, list[Lesson]] = {}
        self.by_group: dict[int, list[Lesson]] = {}
        self.rooms: dict[int, str] = {}
        self.teachers: dict[int, str] = {}
        self.groups: dict[int, str] = {}
        self.chairs: dict[int, str] = {}
        self._room_profile: dict[int, dict] | None = None
        for i, ls in enumerate(self.lessons):
            ls.uid = i
            if ls.room:
                self.by_room.setdefault(ls.room.id, []).append(ls)
                self.rooms[ls.room.id] = ls.room.name
            if ls.chair:
                self.chairs[ls.chair.id] = ls.chair.name
            for t in ls.teachers:
                self.by_teacher.setdefault(t.id, []).append(ls)
                self.teachers[t.id] = t.name
            for g in ls.groups:
                self.by_group.setdefault(g.id, []).append(ls)
                self.groups[g.id] = g.name

    # ---------- сериализация ----------

    def to_json(self, path: str | Path) -> None:
        data = {"meta": self.meta, "lessons": [asdict(x) for x in self.lessons]}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "Schedule":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        lessons = []
        for d in raw["lessons"]:
            d = dict(d)
            d["room"] = Ref(**d["room"]) if d["room"] else None
            d["chair"] = Ref(**d["chair"]) if d["chair"] else None
            d["teachers"] = [Ref(**x) for x in d["teachers"]]
            d["groups"] = [Ref(**x) for x in d["groups"]]
            lessons.append(Lesson(**d))
        return cls(lessons, raw.get("meta"))

    # ---------- поиск ----------

    def find_group(self, name: str) -> int | None:
        for gid, gname in self.groups.items():
            if gname.lower() == name.lower():
                return gid
        return None

    def find_teacher(self, fragment: str) -> list[int]:
        f = fragment.lower()
        return [tid for tid, n in self.teachers.items() if f in n.lower()]

    def find_room(self, fragment: str) -> list[int]:
        f = fragment.lower()
        return [rid for rid, n in self.rooms.items() if f in n.lower()]

    def __len__(self) -> int:
        return len(self.lessons)
