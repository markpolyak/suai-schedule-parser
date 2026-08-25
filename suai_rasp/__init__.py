"""Парсер расписания ГУАП (guap.ru/rasp) и проверка гипотез о переносах пар."""

from .model import Lesson, Ref, Schedule, LESSON_TIMES, DAYS, WEEK_ANY, WEEK_ODD, WEEK_EVEN
from .fetch import Fetcher
from .parse import parse_lessons, parse_directories, parse_meta, dedup

__all__ = [
    "Lesson", "Ref", "Schedule", "Fetcher",
    "parse_lessons", "parse_directories", "parse_meta", "dedup",
    "LESSON_TIMES", "DAYS", "WEEK_ANY", "WEEK_ODD", "WEEK_EVEN",
]
