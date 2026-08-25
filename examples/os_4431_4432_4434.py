"""Сбор занятий по «Операционным системам» (гр. 4431, 4432, 4434) в один-два дня.

Запуск:  python examples/os_4431_4432_4434.py [1|2|3]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from suai_rasp import conflicts as C, plan as P
from suai_rasp.model import DAYS, LESSON_TIMES, Schedule

DB = Path(__file__).resolve().parent.parent / "schedule.json"
sch = Schedule.from_json(DB)

CAMPUS = "Б. Морская 67"          # номера аудиторий дублируются между корпусами
WORK_DAYS = ["Понедельник", "Вторник", "Четверг", "Пятница"]   # без среды
SLOTS = [2, 3, 4, 5, 6, 7]        # без первой пары
SLOTS_BEFORE_17 = [s for s in SLOTS if s <= 4]                 # закончить до 17:00
SLOTS_BEFORE_2010 = [s for s in SLOTS if s <= 6]               # закончить до 20:10


def room(number: str) -> int:
    ids = [i for i, n in sch.rooms.items() if n == f"{number} ({CAMPUS})"]
    assert len(ids) == 1, (number, ids)
    return ids[0]


LAB_ROOMS = [room(n) for n in ("23-10", "23-09", "23-08")]
LEC_ROOMS = [room(n) for n in ("32-03", "52-35", "53-07", "52-18")]

# «Операционные системы» читают три преподавателя — фильтруем и по нему
OS = [l for l in sch.lessons
      if l.subject == "Операционные системы" and any("Поляк" in t.name for t in l.teachers)]
LECTURE = [l for l in OS if l.kind == "Лекция"]                  # две записи: ▲ и ▼
LABS = {g: next(l for l in OS if l.kind.startswith("Лаб") and l.groups[0].name == g)
        for g in ("4431", "4432", "4434")}
LEC_NAME = "Лекция ▲+▼"


def units(days=None, lab_slots=None, lec_days=None, lec_slots=None):
    """Лекция — одна единица из двух записей (обе недели в один слот)."""
    u = [P.Unit(LEC_NAME, LECTURE, LEC_ROOMS,
                lec_days or days or [], lec_slots or SLOTS)]
    u += [P.Unit(f"ЛР {g}", [LABS[g]], LAB_ROOMS, days or [], lab_slots or SLOTS)
          for g in LABS]
    return u


def quality(p: P.Placement):
    """Сортировка: раньше закончить, меньше окон, лекция перед лабораторными."""
    byday = {}
    for _, (d, s) in p.slots.items():
        byday.setdefault(d, []).append(s)
    gaps = sum(max(v) - min(v) + 1 - len(v) for v in byday.values())
    last = max(s for _, s in p.slots.values())
    ld, ls = p.slots[LEC_NAME]
    after = sum(1 for n, (d, s) in p.slots.items()
                if n != LEC_NAME and (DAYS.index(d), s) > (DAYS.index(ld), ls))
    return (last, gaps, -after, len({r.name for rs in p.rooms.values() for r in rs}))


def show(results, title, limit=4):
    ranked = sorted(results, key=quality)
    print(f"\n=== {title} — вариантов: {len(results)} ===")
    for i, p in enumerate(ranked[:limit], 1):
        last, gaps, after, rooms = quality(p)
        print(f"  вариант {i}: конец {LESSON_TIMES[last][1]}, окон {gaps}, "
              f"ЛР после лекции {-after}/3")
        print(p.describe())
    if ranked:
        after = C.apply_moves(sch, ranked[0].moves)
        errs = [i for i in C.validate(after) if i.severity == "error"]
        print(f"  сквозная проверка лучшего варианта: ошибок в расписании — {len(errs)}")
    return ranked


# ------------------------------------------------------------------ задачи

def task1():
    """Всё в один будний день, кроме среды."""
    return show(P.search(sch, units(days=WORK_DAYS), accept=P.same_day, max_results=100000),
                "ЗАДАЧА 1: один день")


def task2():
    """Два дня; один — понедельник с началом после 14:00 (пары 4—7),
    во второй день закончить до 17:00 (пары 1—4)."""
    def accept(assign):
        days = {d for d, _ in assign.values()}
        if len(days) != 2 or "Понедельник" not in days:
            return False
        return all((s >= 4) if d == "Понедельник" else (s <= 4) for d, s in assign.values())

    return show(P.search(sch, units(days=WORK_DAYS), accept=accept, max_results=100000),
                "ЗАДАЧА 2: понедельник + ещё один день")


def task3():
    """Лекция остаётся в субботу на 2 паре; три ЛР — подряд в один будний день
    (кроме среды) с окончанием до 20:10."""
    def accept(assign):
        labs = {n: v for n, v in assign.items() if n.startswith("ЛР")}
        if len({d for d, _ in labs.values()}) != 1:
            return False
        slots = sorted(s for _, s in labs.values())
        return slots[-1] - slots[0] <= 4        # подряд, допускается окно

    u = units(days=WORK_DAYS, lab_slots=SLOTS_BEFORE_2010,
              lec_days=["Суббота"], lec_slots=[2])
    return show(P.search(sch, u, accept=accept, max_results=100000),
                "ЗАДАЧА 3: лекция в субботу + три ЛР подряд", limit=4)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "123"
    for n in which:
        {"1": task1, "2": task2, "3": task3}[n]()
