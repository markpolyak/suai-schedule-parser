"""CLI: сборка слепка расписания и проверка гипотез о переносах.

    python -m suai_rasp dump
    python -m suai_rasp show --group 1232
    python -m suai_rasp free --day Среда --slot 3 --campus "Б. Морская 67"
    python -m suai_rasp slots --group 1232 --subject "Обработка навигационной"
    python -m suai_rasp move  --group 1232 --subject "Обработка навигационной" --to "Среда 3"
    python -m suai_rasp validate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import conflicts as C
from .fetch import Fetcher
from .model import DAYS, LESSON_TIMES, Lesson, Ref, Schedule, WEEK_ANY, WEEK_EVEN, WEEK_ODD
from .parse import dedup, parse_directories, parse_lessons, parse_meta

DEFAULT_DB = Path(__file__).resolve().parent.parent / "schedule.json"


# ---------------------------------------------------------------- сборка

def cmd_dump(args) -> int:
    f = Fetcher(delay=args.delay, ttl=0 if args.refresh else None)
    index = f.index()
    dirs = parse_directories(index)
    meta = parse_meta(index)
    print(f"справочники: групп {len(dirs['groups'])}, преподавателей {len(dirs['teachers'])}, "
          f"кафедр {len(dirs['chairs'])}, аудиторий {len(dirs['rooms'])}")
    print(f"семестр: {meta.get('semester')} {meta.get('academic_year')}, сборка {meta.get('built')}")

    lessons = []
    chairs = sorted(dirs["chairs"].items())
    for i, (cid, cname) in enumerate(chairs, 1):
        lessons += parse_lessons(f.by_chair(cid))
        print(f"\r  кафедра {cname} [{i}/{len(chairs)}] — {len(lessons)} занятий", end="", flush=True)
    print()

    sch = Schedule(dedup(lessons), meta | {"source": "chairs", "directories": {
        k: len(v) for k, v in dirs.items()}})
    sch.to_json(args.db)
    print(f"сохранено: {args.db} — {len(sch)} занятий, "
          f"{len(sch.groups)} групп, {len(sch.teachers)} преподавателей, {len(sch.rooms)} аудиторий")
    missing = set(dirs["groups"]) - set(sch.groups)
    if missing:
        print(f"внимание: у {len(missing)} групп из справочника нет занятий")
    return 0


def load(args) -> Schedule:
    if not Path(args.db).exists():
        sys.exit(f"нет файла {args.db} — сначала выполните: python -m suai_rasp dump")
    return Schedule.from_json(args.db)


# ---------------------------------------------------------------- выбор занятий

def select(sch: Schedule, args) -> list[Lesson]:
    pool = sch.lessons
    if args.group:
        gid = sch.find_group(args.group)
        if gid is None:
            sys.exit(f"группа «{args.group}» не найдена")
        pool = sch.by_group[gid]
    elif args.teacher:
        ids = sch.find_teacher(args.teacher)
        if len(ids) != 1:
            sys.exit("уточните преподавателя: " + "; ".join(sch.teachers[i] for i in ids[:10])
                     if ids else "преподаватель не найден")
        pool = sch.by_teacher[ids[0]]
    elif args.room:
        ids = sch.find_room(args.room)
        if len(ids) != 1:
            sys.exit("уточните аудиторию: " + "; ".join(sch.rooms[i] for i in ids[:10])
                     if ids else "аудитория не найдена")
        pool = sch.by_room[ids[0]]

    out = list(pool)
    if args.subject:
        out = [l for l in out if args.subject.lower() in l.subject.lower()]
    if getattr(args, "kind", None):
        out = [l for l in out if args.kind.lower() in l.kind.lower()]
    if getattr(args, "day", None):
        out = [l for l in out if l.day == _day(args.day)]
    if getattr(args, "slot", None):
        out = [l for l in out if l.slot == args.slot]
    return out


def _day(text: str) -> str:
    t = text.lower()
    for d in DAYS:
        if d.lower().startswith(t[:3]):
            return d
    sys.exit(f"не понял день недели: {text}")


def _parse_to(text: str) -> tuple[str, int, int | None]:
    """'Среда 3' / 'ср 3 верх' -> (день, пара, чётность|None)."""
    parts = text.replace(",", " ").split()
    if len(parts) < 2:
        sys.exit("формат --to: «День Пара [верх|низ|каждую]», например «Среда 3 верх»")
    day, slot = _day(parts[0]), int(parts[1])
    week = None
    if len(parts) > 2:
        w = parts[2].lower()
        week = WEEK_ODD if w.startswith("вер") else WEEK_EVEN if w.startswith("ниж") else WEEK_ANY
    return day, slot, week


# ---------------------------------------------------------------- команды

def cmd_show(args) -> int:
    sch = load(args)
    ls = select(sch, args)
    grid = [l for l in ls if l.in_grid]
    grid.sort(key=lambda l: (DAYS.index(l.day), l.slot, l.week))
    for l in grid:
        print(" ", l.short())
    for l in [x for x in ls if not x.in_grid]:
        print("  [вне сетки]", l.subject, "|", l.kind)
    print(f"\nвсего: {len(ls)} ({len(grid)} в сетке)")
    if args.grid:
        if args.group:
            print()
            print(C.day_grid(sch, "group", sch.find_group(args.group)))
    return 0


def cmd_validate(args) -> int:
    sch = load(args)
    issues = C.validate(sch)
    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity == "warning"]
    for i in errors:
        print(i)
    if args.verbose:
        for i in warns:
            print(i)
    print(f"\nконфликтов: {len(errors)}, предупреждений: {len(warns)} "
          f"(показать все: --verbose)")
    return 0


def cmd_free(args) -> int:
    sch = load(args)
    week = {"верх": WEEK_ODD, "низ": WEEK_EVEN}.get(args.week or "", WEEK_ANY)
    rooms = C.free_rooms(sch, _day(args.day), args.slot, week,
                         campus=args.campus, min_groups=args.groups,
                         kinds={args.kind} if args.kind else None,
                         chair=args.chair)
    b, e = LESSON_TIMES[args.slot]
    print(f"{_day(args.day)}, {args.slot} пара ({b}—{e}): свободно {len(rooms)} аудиторий")
    for r in rooms[:args.limit]:
        print(f"  {r['name']:32} загрузка {r['lessons']:3} зан./нед., "
              f"максимум групп {r['max_groups_seen']}, типы: {', '.join(r['kinds'])[:60]}")
    return 0


def cmd_slots(args) -> int:
    sch = load(args)
    ls = select(sch, args)
    ls = [l for l in ls if l.in_grid]
    if len(ls) != 1:
        print("уточните занятие (--subject/--kind/--day/--slot). Подходят:")
        for l in ls[:20]:
            print("  ", l.short())
        return 1
    lesson = ls[0]
    print("Занятие:", lesson.short())
    cands = C.candidate_slots(sch, lesson, keep_room=not args.any_room,
                              same_campus=not args.any_campus,
                              weeks=[lesson.week] if not args.any_week else [WEEK_ANY, WEEK_ODD, WEEK_EVEN])
    print(f"\nдопустимых слотов: {len(cands)}")
    for c in cands[:args.limit]:
        rooms = ", ".join(r["name"] for r in c["rooms"][:3])
        tail = f" (+{c['rooms_total'] - 3})" if c["rooms_total"] > 3 else ""
        flag = "та же ауд." if c["keep_room"] else "нужна другая ауд."
        print(f"  {c['day']:12} {c['slot']} пара {c['time']}  [{flag}]  {rooms}{tail}")
        for w in c["warnings"]:
            print(f"      ⚠ {w.entity}: {w.detail}")
    return 0


def cmd_move(args) -> int:
    sch = load(args)
    ls = [l for l in select(sch, args) if l.in_grid]
    if len(ls) != 1:
        print("уточните занятие (--subject/--kind/--day/--slot). Подходят:")
        for l in ls[:20]:
            print("  ", l.short())
        return 1
    day, slot, week = _parse_to(args.to)
    room = None
    if args.room_to:
        ids = sch.find_room(args.room_to)
        if len(ids) != 1:
            sys.exit("уточните аудиторию: " + "; ".join(sch.rooms[i] for i in ids[:10]))
        room = Ref(ids[0], sch.rooms[ids[0]])
    print(C.report(sch, [C.Move(ls[0], day=day, slot=slot, week=week, room=room)]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="suai_rasp", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB), help="файл слепка расписания")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="скачать и сохранить полный слепок расписания")
    d.add_argument("--delay", type=float, default=0.4, help="пауза между запросами, с")
    d.add_argument("--refresh", action="store_true", help="игнорировать HTTP-кэш")
    d.set_defaults(func=cmd_dump)

    def add_selectors(sp, with_time=True):
        sp.add_argument("--group")
        sp.add_argument("--teacher")
        sp.add_argument("--room")
        sp.add_argument("--subject")
        sp.add_argument("--kind")
        if with_time:
            sp.add_argument("--day")
            sp.add_argument("--slot", type=int)

    s = sub.add_parser("show", help="показать расписание группы/преподавателя/аудитории")
    add_selectors(s)
    s.add_argument("--grid", action="store_true", help="дополнительно сетка занятости")
    s.set_defaults(func=cmd_show)

    v = sub.add_parser("validate", help="проверить весь слепок на конфликты")
    v.add_argument("--verbose", action="store_true")
    v.set_defaults(func=cmd_validate)

    fr = sub.add_parser("free", help="свободные аудитории в слоте")
    fr.add_argument("--day", required=True)
    fr.add_argument("--slot", type=int, required=True)
    fr.add_argument("--week", choices=["верх", "низ"])
    fr.add_argument("--campus")
    fr.add_argument("--chair")
    fr.add_argument("--kind")
    fr.add_argument("--groups", type=int, default=0, help="сколько групп должно поместиться")
    fr.add_argument("--limit", type=int, default=30)
    fr.set_defaults(func=cmd_free)

    sl = sub.add_parser("slots", help="куда можно перенести занятие")
    add_selectors(sl)
    sl.add_argument("--any-room", action="store_true", help="разрешить смену аудитории")
    sl.add_argument("--any-campus", action="store_true", help="разрешить другой корпус")
    sl.add_argument("--any-week", action="store_true", help="разрешить смену чётности недели")
    sl.add_argument("--limit", type=int, default=30)
    sl.set_defaults(func=cmd_slots)

    mv = sub.add_parser("move", help="проверить конкретный перенос")
    add_selectors(mv)
    mv.add_argument("--to", required=True, help="«Среда 3» или «Среда 3 верх»")
    mv.add_argument("--room-to")
    mv.set_defaults(func=cmd_move)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
