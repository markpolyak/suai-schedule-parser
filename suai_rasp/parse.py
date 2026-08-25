"""Разбор HTML-страниц https://guap.ru/rasp в объекты Lesson."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .model import DAYS, Lesson, Ref, WEEK_ANY, WEEK_EVEN, WEEK_ODD

OUT_OF_GRID = "Вне сетки расписания"
_SLOT_RE = re.compile(r"^(\d+)\s+пара")
_ID_RE = re.compile(r"\?(gr|pr|ch|ad)=(\d+)")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _ref(a) -> Ref:
    m = _ID_RE.search(a.get("href", ""))
    return Ref(int(m.group(2)), _clean(a.get_text()))


def parse_directories(html: str) -> dict[str, dict[int, str]]:
    """Справочники групп/преподавателей/кафедр/аудиторий со стартовой страницы."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict[int, str]] = {}
    for sel_id, key in [("selGroup", "groups"), ("selPrep", "teachers"),
                        ("selChair", "chairs"), ("selRoom", "rooms")]:
        sel = soup.find("select", id=sel_id)
        d: dict[int, str] = {}
        if sel:
            for opt in sel.find_all("option"):
                v = opt.get("value") or ""
                if v.isdigit():
                    d[int(v)] = _clean(opt.get_text())
        out[key] = d
    return out


def parse_meta(html: str) -> dict:
    """Семестр, дата сборки, сегодняшняя дата и номер учебной недели."""
    soup = BeautifulSoup(html, "lxml")
    text = _clean(soup.get_text())
    meta: dict = {}
    m = re.search(r"(Осенний|Весенний) семестр (\d{4}/\d{2}) учебного года", text)
    if m:
        meta["semester"] = m.group(1)
        meta["academic_year"] = m.group(2)
    m = re.search(r"\(сборка: (\d{4}-\d{2}-\d{2})\)", text)
    if m:
        meta["built"] = m.group(1)
    m = re.search(r"Сегодня (.+?) г\.", text)
    if m:
        meta["today"] = m.group(1)
    m = re.search(r"([▲▼])\s*(\d+)\s+(верхняя|нижняя)", text)
    if m:
        meta["week_number"] = int(m.group(2))
        meta["week_parity"] = WEEK_ODD if m.group(3) == "верхняя" else WEEK_EVEN
    return meta


def _week_of(block) -> int:
    marker = block.find("div", recursive=False)
    if marker is None:
        return WEEK_ANY
    cls = marker.get("class") or []
    if "week1" in cls:
        return WEEK_ODD
    if "week2" in cls:
        return WEEK_EVEN
    return WEEK_ANY


def parse_lessons(html: str) -> list[Lesson]:
    """Разбирает страницу расширенного вида (/rasp) в список занятий."""
    soup = BeautifulSoup(html, "lxml")
    content = soup.find("div", class_="content")
    if content is None:
        return []

    lessons: list[Lesson] = []
    day: str | None = None
    slot: int | None = None

    for node in content.find_all(["h4", "div"], recursive=True):
        txt = _clean(node.get_text())
        if node.name == "h4":
            if txt == OUT_OF_GRID:
                day, slot = None, None
            elif txt in DAYS:
                day, slot = txt, None
            continue

        cls = node.get("class") or []
        if "text-danger" in cls and "mt-3" in cls:
            m = _SLOT_RE.match(txt)
            if m:
                slot = int(m.group(1))
            continue

        # карточка занятия: <div class="mb-3 py-2 d-flex gap-2">
        if "mb-3" in cls and "d-flex" in cls and "py-2" in cls:
            lesson = _parse_card(node, day, slot)
            if lesson:
                lessons.append(lesson)

    return lessons


def _parse_card(block, day, slot) -> Lesson | None:
    kind_el = block.find("div", class_="fs-6")
    subj_el = block.find("div", class_="lead")
    if kind_el is None or subj_el is None:
        return None

    body = subj_el.parent
    room = chair = None
    teachers: list[Ref] = []
    groups: list[Ref] = []

    for a in body.find_all("a", href=True):
        m = _ID_RE.search(a["href"])
        if not m:
            continue
        kind = m.group(1)
        if kind == "ad":
            room = _ref(a)
        elif kind == "ch":
            chair = _ref(a)
        elif kind == "pr":
            teachers.append(_ref(a))
        elif kind == "gr":
            groups.append(_ref(a))

    return Lesson(
        day=day if slot is not None else None,
        slot=slot,
        week=_week_of(block),
        kind=_clean(kind_el.get_text()),
        subject=_clean(subj_el.get_text()),
        room=room,
        chair=chair,
        teachers=teachers,
        groups=groups,
    )


def dedup(lessons: list[Lesson]) -> list[Lesson]:
    """Убирает дубли, возникающие при склейке выборок по разным фильтрам."""
    seen: dict[tuple, Lesson] = {}
    for ls in lessons:
        key = (
            ls.day, ls.slot, ls.week, ls.kind, ls.subject,
            ls.room.id if ls.room else None,
            ls.chair.id if ls.chair else None,
            tuple(sorted(t.id for t in ls.teachers)),
            tuple(sorted(g.id for g in ls.groups)),
        )
        seen.setdefault(key, ls)
    return list(seen.values())
