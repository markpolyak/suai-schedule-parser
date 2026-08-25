"""Поиск совместного размещения нескольких занятий («собрать предмет в один день»).

В отличие от `conflicts.candidate_slots`, который двигает одно занятие, здесь
несколько занятий переставляются одновременно и проверяются друг против друга.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import conflicts as C
from .model import DAYS, LESSON_TIMES, Lesson, Ref, Schedule

WORKDAYS = DAYS[:5]


@dataclass
class Unit:
    """Занятия, которые переносятся синхронно в один и тот же слот.

    Например лекция, разбитая на верхнюю и нижнюю неделю, — это одна единица
    из двух записей: расписание у групп должно выглядеть как одна пара.
    """

    name: str
    lessons: list[Lesson]
    rooms: list[int] = field(default_factory=list)   # допустимые id аудиторий; пусто — любые
    days: list[str] = field(default_factory=list)    # пусто — все рабочие дни
    slots: list[int] = field(default_factory=list)   # пусто — все пары

    @property
    def uids(self) -> tuple:
        return tuple(l.uid for l in self.lessons)


@dataclass
class Placement:
    """Найденный вариант: куда встала каждая единица."""

    slots: dict[str, tuple[str, int]]            # имя единицы -> (день, пара)
    rooms: dict[str, list[Ref]]                  # имя единицы -> аудитории её записей
    moves: list[C.Move]
    warnings: list[C.Issue]

    @property
    def days(self) -> set[str]:
        return {d for d, _ in self.slots.values()}

    def describe(self) -> str:
        lines = []
        for name, (day, slot) in sorted(self.slots.items(),
                                        key=lambda kv: (DAYS.index(kv[1][0]), kv[1][1])):
            b, e = LESSON_TIMES[slot]
            rooms = ", ".join(dict.fromkeys(r.name for r in self.rooms[name]))
            lines.append(f"    {day:12} {slot} пара {b}—{e}  {name:22} {rooms}")
        for w in self.warnings:
            lines.append(f"    ⚠ {w.entity}: {w.detail}")
        return "\n".join(lines)


def _free_room(sch: Schedule, lesson: Lesson, day: int, slot: int, rid: int,
               busy_uids: tuple) -> bool:
    return not C._hits(sch.by_room.get(rid, []), day, slot, lesson.week, busy_uids)


def _room_options(sch: Schedule, unit: Unit, day: str, slot: int,
                  busy_uids: tuple, prefer_single: bool = True) -> list[list[Ref]]:
    """Наборы аудиторий для записей единицы: сначала одна на всех, потом раздельные."""
    allowed = unit.rooms or list(sch.rooms)
    per_lesson = []
    for l in unit.lessons:
        ok = [rid for rid in allowed if _free_room(sch, l, day, slot, rid, busy_uids)]
        if not ok:
            return []
        per_lesson.append(ok)

    out = []
    if prefer_single:
        for rid in per_lesson[0]:
            if all(rid in opts for opts in per_lesson):
                out.append([Ref(rid, sch.rooms[rid])] * len(unit.lessons))
    if not out or not prefer_single:
        for combo in itertools.product(*per_lesson):
            if len(set(combo)) > 1 or not prefer_single:
                out.append([Ref(r, sch.rooms[r]) for r in combo])
    return out[:8]


def search(sch: Schedule, units: list[Unit], accept=None, max_results: int = 20,
           allow_warnings: bool = True, prefer_single_room: bool = True) -> list[Placement]:
    """Все размещения единиц, не создающие конфликтов.

    accept(slots) — дополнительный предикат на набор слотов
    {имя единицы: (день, пара)}: «все в один день», «подряд» и т. п.
    """
    all_uids = tuple(u for unit in units for u in unit.uids)

    # допустимые слоты для каждой единицы по отдельности (люди, без аудиторий)
    per_unit: dict[str, list[tuple[str, int]]] = {}
    for unit in units:
        days = unit.days or WORKDAYS
        slots = unit.slots or sorted(LESSON_TIMES)
        ok = []
        for day, slot in itertools.product(days, slots):
            moves = [C.Move(l, day=day, slot=slot) for l in unit.lessons]
            added, _ = C.check_hypothesis(sch, moves)
            if any(i.severity == "error" and i.kind in ("group", "teacher") for i in added):
                continue
            ok.append((day, slot))
        per_unit[unit.name] = ok

    results: list[Placement] = []
    names = [u.name for u in units]
    by_name = {u.name: u for u in units}

    for combo in itertools.product(*(per_unit[n] for n in names)):
        assign = dict(zip(names, combo))
        # один преподаватель — единицы не могут делить слот
        if len(set(combo)) != len(combo):
            continue
        if accept and not accept(assign):
            continue

        room_choices = []
        for name in names:
            day, slot = assign[name]
            opts = _room_options(sch, by_name[name], day, slot, all_uids, prefer_single_room)
            if not opts:
                room_choices = None
                break
            room_choices.append(opts)
        if room_choices is None:
            continue

        for rooms_combo in itertools.product(*room_choices):
            moves, rooms_map, clash = [], {}, False
            used: dict[tuple, int] = {}
            for name, rooms in zip(names, rooms_combo):
                unit = by_name[name]
                day, slot = assign[name]
                rooms_map[name] = list(rooms)
                for l, room in zip(unit.lessons, rooms):
                    # две наши записи не должны сами занять одну аудиторию в одном слоте
                    k = (day, slot, room.id, l.week)
                    if used.get(k):
                        clash = True
                    used[k] = used.get(k, 0) + 1
                    moves.append(C.Move(l, day=day, slot=slot, room=room))
            if clash:
                continue
            added, _ = C.check_hypothesis(sch, moves)
            errors = [i for i in added if i.severity == "error"]
            warns = [i for i in added if i.severity == "warning"]
            if errors or (warns and not allow_warnings):
                continue
            results.append(Placement(assign, rooms_map, moves, warns))
            break  # для набора слотов достаточно одного варианта аудиторий

        if len(results) >= max_results:
            break
    return results


# ---------------------------------------------------------------- предикаты

def same_day(assign) -> bool:
    return len({d for d, _ in assign.values()}) == 1


def days_within(allowed: set[str]):
    return lambda assign: {d for d, _ in assign.values()} <= allowed


def n_days(n: int):
    return lambda assign: len({d for d, _ in assign.values()}) == n


def all_of(*predicates):
    return lambda assign: all(p(assign) for p in predicates)


def consecutive(names: list[str], max_gap: int = 1):
    """Указанные единицы идут друг за другом в один день (окно ≤ max_gap пар)."""
    def check(assign):
        picked = [assign[n] for n in names if n in assign]
        if len({d for d, _ in picked}) != 1:
            return False
        slots = sorted(s for _, s in picked)
        return all(b - a <= max_gap + 1 for a, b in zip(slots, slots[1:]))
    return check
