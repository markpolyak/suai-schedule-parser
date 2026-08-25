"""Проверка гипотез о переносе занятий: конфликты, свободные аудитории, переезды.

Гипотеза не изменяет исходное расписание: перенос применяется к копиям затронутых
занятий, и конфликты пересчитываются только для затронутых аудиторий,
преподавателей и групп.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .model import (DAYS, LESSON_TIMES, Lesson, Ref, Schedule,
                    WEEK_ANY, WEEK_EVEN, WEEK_NAMES, WEEK_ODD)

#: Аудитории-заглушки: реальной занятости не отражают, на конфликты не проверяются.
PSEUDO_ROOM_MARKERS = ("вне сетки", "Дистант")

#: Корпуса в пешей доступности: переход между парами реально практикуется
#: (в расписании 2026/27 таких переходов 531, между другими корпусами — ни одного).
NEARBY_CAMPUSES = {frozenset({"Ленсовета 14", "Гастелло 15"})}

#: Аудитории, где одновременные занятия разных групп — норма.
SHARED_ROOM_PATTERNS = ("спортзал", "бассейн", "манеж")

_CAMPUS_RE = re.compile(r"\(([^()]*)\)\s*$")


def campus_of(room_name: str | None) -> str | None:
    m = _CAMPUS_RE.search(room_name or "")
    return m.group(1) if m else None


def room_number(room_name: str | None) -> str:
    return _CAMPUS_RE.sub("", room_name or "").strip()


def is_pseudo_room(room: Ref | None) -> bool:
    """Аудитория без номера («(Б. Морская 67)»), «(вне сетки)», «(Дистант)»."""
    if room is None:
        return True
    if not room_number(room.name):
        return True
    return any(m in room.name for m in PSEUDO_ROOM_MARKERS)


@dataclass
class Issue:
    severity: str          # "error" | "warning"
    kind: str              # room | teacher | group | campus | capacity
    entity: str
    where: str
    detail: str
    lessons: list[Lesson] = field(default_factory=list)

    def __str__(self) -> str:
        mark = "✗" if self.severity == "error" else "⚠"
        return f"{mark} [{self.kind}] {self.entity} — {self.where}: {self.detail}"

    @property
    def sig(self) -> tuple:
        return (self.kind, self.entity, self.where, self.detail)


@dataclass
class Move:
    """Гипотеза: перенести занятие в другой слот и/или аудиторию."""

    lesson: Lesson
    day: str | None = None
    slot: int | None = None
    week: int | None = None
    room: Ref | None = None

    def applied(self) -> Lesson:
        """Копия занятия с применённым переносом (uid сохраняется)."""
        return replace(
            self.lesson,
            day=self.day if self.day is not None else self.lesson.day,
            slot=self.slot if self.slot is not None else self.lesson.slot,
            week=self.week if self.week is not None else self.lesson.week,
            room=self.room if self.room is not None else self.lesson.room,
        )

    def describe(self) -> str:
        n = self.applied()
        return f"{self.lesson.short()}\n      →  {n.short()}"


# --------------------------------------------------------------------------
# занятость
# --------------------------------------------------------------------------

def _hits(lessons, day, slot, week, exclude_uids=()):
    out = []
    for l in lessons:
        if l.uid in exclude_uids or not l.in_grid:
            continue
        if l.day == day and l.slot == slot and (
                l.week == WEEK_ANY or week == WEEK_ANY or l.week == week):
            out.append(l)
    return out


def group_busy(sch: Schedule, gid: int, day: str, slot: int, week: int = WEEK_ANY) -> list[Lesson]:
    return _hits(sch.by_group.get(gid, []), day, slot, week)


def teacher_busy(sch: Schedule, tid: int, day: str, slot: int, week: int = WEEK_ANY) -> list[Lesson]:
    return _hits(sch.by_teacher.get(tid, []), day, slot, week)


def room_busy(sch: Schedule, rid: int, day: str, slot: int, week: int = WEEK_ANY) -> list[Lesson]:
    return _hits(sch.by_room.get(rid, []), day, slot, week)


# --------------------------------------------------------------------------
# профиль аудиторий
# --------------------------------------------------------------------------

def room_load_profile(sch: Schedule) -> dict[int, dict]:
    """Для каждой аудитории: корпус, загрузка и максимальное наблюдавшееся число групп.

    Вместимость в местах сайт не публикует, поэтому «сколько групп туда
    исторически ставили» — единственная доступная оценка сверху.
    """
    if sch._room_profile is None:
        prof = {}
        for rid, ls in sch.by_room.items():
            name = sch.rooms[rid]
            prof[rid] = {
                "id": rid,
                "name": name,
                "campus": campus_of(name),
                "max_groups_seen": max((len(l.groups) for l in ls), default=0),
                "lessons": sum(1 for l in ls if l.in_grid),
                "kinds": sorted({l.kind for l in ls}),
                "chairs": sorted({l.chair.name for l in ls if l.chair}),
            }
        sch._room_profile = prof
    return sch._room_profile


# --------------------------------------------------------------------------
# ядро: конфликты внутри списка занятий одной сущности
# --------------------------------------------------------------------------

def _clashes(lessons: list[Lesson], kind: str, entity: str) -> list[Issue]:
    byslot = defaultdict(list)
    for l in lessons:
        if l.in_grid:
            byslot[(l.day, l.slot)].append(l)
    shared = kind == "room" and any(p in entity.lower() for p in SHARED_ROOM_PATTERNS)
    sev = "warning" if shared else "error"
    out = []
    for (day, slot), grp in byslot.items():
        for a, b in itertools.combinations(grp, 2):
            if a.overlaps_week(b):
                out.append(Issue(
                    sev, kind, entity,
                    f"{day}, {slot} пара ({LESSON_TIMES[slot][0]}—{LESSON_TIMES[slot][1]})",
                    f"одновременно «{a.subject}» ({a.kind}, {WEEK_NAMES[a.week]}) "
                    f"и «{b.subject}» ({b.kind}, {WEEK_NAMES[b.week]})",
                    [a, b]))
    return out


def _campus_hops(lessons: list[Lesson], entity: str) -> list[Issue]:
    """Переезд между корпусами в соседних парах (перерыв 10—30 минут)."""
    byday = defaultdict(list)
    for l in lessons:
        if l.in_grid and not is_pseudo_room(l.room):
            byday[l.day].append(l)
    out = []
    for day, dls in byday.items():
        dls = sorted(dls, key=lambda l: l.slot)
        for a, b in itertools.combinations(dls, 2):
            if b.slot - a.slot != 1 or not a.overlaps_week(b):
                continue
            ca, cb = campus_of(a.room.name), campus_of(b.room.name)
            if not ca or not cb or ca == cb:
                continue
            near = frozenset({ca, cb}) in NEARBY_CAMPUSES
            out.append(Issue(
                "warning" if near else "error", "campus", entity,
                f"{day}, пары {a.slot}→{b.slot}",
                f"переезд {ca} → {cb} за перерыв "
                f"{LESSON_TIMES[a.slot][1]}—{LESSON_TIMES[b.slot][0]}"
                + ("" if near else " — корпуса в разных концах города"),
                [a, b]))
    return out


def _capacity(lesson: Lesson, sch: Schedule) -> list[Issue]:
    if not lesson.in_grid or is_pseudo_room(lesson.room):
        return []
    cap = room_load_profile(sch).get(lesson.room.id, {}).get("max_groups_seen", 0)
    if len(lesson.groups) > cap:
        return [Issue("warning", "capacity", lesson.room.name,
                      f"{lesson.day}, {lesson.slot} пара",
                      f"{len(lesson.groups)} групп при исторически наблюдавшемся максимуме {cap}",
                      [lesson])]
    return []


# --------------------------------------------------------------------------
# валидация всего расписания
# --------------------------------------------------------------------------

def validate(sch: Schedule, check_campus: bool = True, check_capacity: bool = True) -> list[Issue]:
    """Полная проверка расписания (в исходных данных ГУАП конфликтов почти нет —
    служит sanity-check'ом парсера и базой для сравнения «до/после»)."""
    issues: list[Issue] = []
    for rid, ls in sch.by_room.items():
        if is_pseudo_room(Ref(rid, sch.rooms[rid])):
            continue
        issues += _clashes(ls, "room", sch.rooms[rid])
    for tid, ls in sch.by_teacher.items():
        issues += _clashes(ls, "teacher", sch.teachers[tid])
    for gid, ls in sch.by_group.items():
        issues += _clashes(ls, "group", sch.groups[gid])
    if check_campus:
        for gid, ls in sch.by_group.items():
            issues += _campus_hops(ls, sch.groups[gid])
        for tid, ls in sch.by_teacher.items():
            issues += _campus_hops(ls, sch.teachers[tid])
    if check_capacity:
        for l in sch.lessons:
            issues += _capacity(l, sch)
    return _uniq(issues)


# --------------------------------------------------------------------------
# проверка гипотезы
# --------------------------------------------------------------------------

def check_hypothesis(sch: Schedule, moves: list[Move], check_campus: bool = True,
                     check_capacity: bool = True) -> tuple[list[Issue], list[Issue]]:
    """Возвращает (появившиеся проблемы, исчезнувшие проблемы) для набора переносов."""
    applied = {mv.lesson.uid: mv.applied() for mv in moves}

    rooms, teachers, groups = set(), set(), set()
    for uid, new in applied.items():
        old = sch.lessons[uid]
        for l in (old, new):
            if l.room:
                rooms.add(l.room.id)
            teachers.update(t.id for t in l.teachers)
            groups.update(g.id for g in l.groups)

    def variant(base: list[Lesson], keep: callable) -> list[Lesson]:
        """Список занятий сущности после применения переносов."""
        out = [l for l in base if l.uid not in applied]
        out += [n for n in applied.values() if keep(n)]
        return out

    before: list[Issue] = []
    after: list[Issue] = []

    for rid in rooms:
        if is_pseudo_room(Ref(rid, sch.rooms.get(rid, ""))):
            continue
        base = sch.by_room.get(rid, [])
        name = sch.rooms.get(rid, str(rid))
        before += _clashes(base, "room", name)
        after += _clashes(variant(base, lambda n: n.room and n.room.id == rid), "room", name)

    for tid in teachers:
        base = sch.by_teacher.get(tid, [])
        name = sch.teachers.get(tid, str(tid))
        before += _clashes(base, "teacher", name)
        new = variant(base, lambda n: any(t.id == tid for t in n.teachers))
        after += _clashes(new, "teacher", name)
        if check_campus:
            before += _campus_hops(base, name)
            after += _campus_hops(new, name)

    for gid in groups:
        base = sch.by_group.get(gid, [])
        name = sch.groups.get(gid, str(gid))
        before += _clashes(base, "group", name)
        new = variant(base, lambda n: any(g.id == gid for g in n.groups))
        after += _clashes(new, "group", name)
        if check_campus:
            before += _campus_hops(base, name)
            after += _campus_hops(new, name)

    if check_capacity:
        for uid, new in applied.items():
            before += _capacity(sch.lessons[uid], sch)
            after += _capacity(new, sch)

    before_sigs = {i.sig for i in before}
    after_sigs = {i.sig for i in after}
    added = _uniq([i for i in after if i.sig not in before_sigs])
    removed = _uniq([i for i in before if i.sig not in after_sigs])
    return added, removed


def _uniq(issues: list[Issue]) -> list[Issue]:
    seen, out = set(), []
    for i in issues:
        if i.sig not in seen:
            seen.add(i.sig)
            out.append(i)
    return out


def apply_moves(sch: Schedule, moves: list[Move]) -> Schedule:
    """Копия расписания с применёнными переносами — для сквозной перепроверки
    через `validate`. Для оценки гипотез используйте `check_hypothesis`: она
    быстрее и ничего не копирует."""
    import copy

    lessons = copy.deepcopy(sch.lessons)
    for mv in moves:
        n = mv.applied()
        t = lessons[mv.lesson.uid]
        t.day, t.slot, t.week, t.room = n.day, n.slot, n.week, n.room
    return Schedule(lessons, dict(sch.meta))


def is_feasible(sch: Schedule, moves: list[Move], allow_warnings: bool = True) -> bool:
    added, _ = check_hypothesis(sch, moves)
    if any(i.severity == "error" for i in added):
        return False
    return allow_warnings or not added


# --------------------------------------------------------------------------
# поиск вариантов
# --------------------------------------------------------------------------

def free_rooms(sch: Schedule, day: str, slot: int, week: int = WEEK_ANY,
               campus: str | None = None, min_groups: int = 0,
               kinds: set[str] | None = None, chair: str | None = None,
               exclude_uids: tuple = ()) -> list[dict]:
    """Аудитории, свободные в указанном слоте.

    campus     — фильтр по корпусу ("Б. Морская 67", "Гастелло 15", "Ленсовета 14", ...)
    min_groups — отсечь аудитории, куда столько групп никогда не ставили
    kinds      — оставить те, где такие занятия уже проводились
    chair      — оставить закреплённые за кафедрой (по фактическому использованию)
    """
    out = []
    for rid, p in room_load_profile(sch).items():
        if is_pseudo_room(Ref(rid, p["name"])):
            continue
        if campus and p["campus"] != campus:
            continue
        if p["max_groups_seen"] < min_groups:
            continue
        if kinds and not kinds & set(p["kinds"]):
            continue
        if chair and not any(chair in c for c in p["chairs"]):
            continue
        if _hits(sch.by_room[rid], day, slot, week, exclude_uids):
            continue
        out.append(p)
    return sorted(out, key=lambda p: (-p["max_groups_seen"], p["name"]))


def candidate_slots(sch: Schedule, lesson: Lesson, days: list[str] | None = None,
                    slots: list[int] | None = None, weeks: list[int] | None = None,
                    keep_room: bool = True, same_campus: bool = True,
                    allow_warnings: bool = True, max_rooms: int = 5) -> list[dict]:
    """Слоты, куда занятие можно перенести без конфликтов у групп и преподавателей.

    keep_room=True   — сначала пробуем оставить текущую аудиторию, иначе подбираем свободные.
    same_campus=True — альтернативные аудитории только в том же корпусе.
    Возвращает список словарей со слотом, вариантами аудиторий и предупреждениями.
    """
    days = days or DAYS[:6]
    slots = slots or sorted(LESSON_TIMES)
    weeks = weeks if weeks is not None else [lesson.week]
    campus = campus_of(lesson.room.name) if (lesson.room and same_campus) else None

    results = []
    for day, slot, week in itertools.product(days, slots, weeks):
        if (day, slot, week) == (lesson.day, lesson.slot, lesson.week):
            continue

        # 1. люди: конфликты групп и преподавателей не зависят от выбора аудитории
        probe = Move(lesson, day=day, slot=slot, week=week)
        added, removed = check_hypothesis(sch, [probe])
        people_errors = [i for i in added if i.severity == "error" and i.kind in ("group", "teacher")]
        if people_errors:
            continue

        # 2. аудитории
        room_opts = []
        keeps = keep_room and lesson.room is not None and (
            is_pseudo_room(lesson.room)
            or not _hits(sch.by_room[lesson.room.id], day, slot, week, (lesson.uid,)))
        if keeps:
            room_opts.append({"id": lesson.room.id, "name": lesson.room.name,
                              "campus": campus_of(lesson.room.name), "kept": True})
        alts = free_rooms(sch, day, slot, week, campus=campus,
                          min_groups=len(lesson.groups),
                          kinds={lesson.kind},
                          exclude_uids=(lesson.uid,))
        for a in alts:
            if not any(o["id"] == a["id"] for o in room_opts):
                room_opts.append(a | {"kept": False})
        if not room_opts:
            continue

        warns = [i for i in added if i.severity == "warning"]
        if warns and not allow_warnings:
            continue
        results.append({
            "day": day, "slot": slot, "week": week,
            "time": f"{LESSON_TIMES[slot][0]}—{LESSON_TIMES[slot][1]}",
            "keep_room": bool(keeps),
            "rooms": room_opts[:max_rooms],
            "rooms_total": len(room_opts),
            "warnings": warns,
            "fixes": removed,
            "move": Move(lesson, day=day, slot=slot, week=week,
                         room=None if keeps else Ref(room_opts[0]["id"], room_opts[0]["name"])),
        })

    results.sort(key=lambda r: (len(r["warnings"]), not r["keep_room"],
                                DAYS.index(r["day"]), r["slot"]))
    return results


def swap(a: Lesson, b: Lesson) -> list[Move]:
    """Гипотеза «поменять два занятия местами»."""
    return [Move(a, day=b.day, slot=b.slot, week=b.week),
            Move(b, day=a.day, slot=a.slot, week=a.week)]


# --------------------------------------------------------------------------
# отчёты
# --------------------------------------------------------------------------

def report(sch: Schedule, moves: list[Move], **kw) -> str:
    """Человекочитаемый вердикт по гипотезе."""
    added, removed = check_hypothesis(sch, moves, **kw)
    errors = [i for i in added if i.severity == "error"]
    warns = [i for i in added if i.severity == "warning"]
    lines = ["ГИПОТЕЗА:"]
    lines += [f"  • {mv.describe()}" for mv in moves]
    verdict = "НЕВОЗМОЖНО" if errors else ("ВОЗМОЖНО с оговорками" if warns else "ВОЗМОЖНО")
    lines.append(f"ВЕРДИКТ: {verdict}")
    if errors:
        lines.append("Конфликты:")
        lines += [f"  {i}" for i in errors]
    if warns:
        lines.append("Предупреждения:")
        lines += [f"  {i}" for i in warns]
    if removed:
        lines.append("Исчезают проблемы:")
        lines += [f"  {i}" for i in removed]
    return "\n".join(lines)


def day_grid(sch: Schedule, kind: str, entity_id: int) -> str:
    """Текстовая сетка занятости группы / преподавателя / аудитории."""
    index = {"group": sch.by_group, "teacher": sch.by_teacher, "room": sch.by_room}[kind]
    names = {"group": sch.groups, "teacher": sch.teachers, "room": sch.rooms}[kind]
    ls = [l for l in index.get(entity_id, []) if l.in_grid]
    grid = defaultdict(list)
    for l in ls:
        grid[(l.day, l.slot)].append(l)
    lines = [f"{names.get(entity_id, entity_id)} — занятость ({len(ls)} занятий)"]
    header = "пара | " + " | ".join(f"{d[:2]:^22}" for d in DAYS[:6])
    lines.append(header)
    for slot in sorted(LESSON_TIMES):
        cells = []
        for day in DAYS[:6]:
            items = grid.get((day, slot), [])
            if not items:
                cells.append(" " * 22)
            else:
                mark = {0: " ", 1: "▲", 2: "▼"}
                txt = ",".join(f"{mark[i.week]}{i.subject[:18]}" for i in items)
                cells.append(f"{txt[:22]:22}")
        lines.append(f"  {slot}  | " + " | ".join(cells))
    return "\n".join(lines)
