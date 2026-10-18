"""Small string table for the e-paper views (Finnish by default, English optional).

Select the language with ``config.EPAPER_LANGUAGE`` ("fi" or "en"). Unknown
languages fall back to Finnish, unknown keys fall back to the key itself.
"""

import config

_WEEKDAYS = {
    "fi": ["ma", "ti", "ke", "to", "pe", "la", "su"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}

_STRINGS = {
    "fi": {
        "today": "Tänään", "tomorrow": "Huomenna",
        "average": "Keskiarvo", "peak": "Huippu", "cheapest": "Halvin",
        "events_one": "1 tapahtuma", "events_many": "{n} tapahtumaa",
        "nothing": "Ei tapahtumia",
        "red": "Tärkeä", "green": "Oma", "blue": "Työ", "yellow": "Terveys",
        "updated": "Päivitetty", "weather": "Sää", "price": "Sähkön hinta",
        "feels_like": "tuntuu", "no_data": "Ei tietoa",
    },
    "en": {
        "today": "Today", "tomorrow": "Tomorrow",
        "average": "Average", "peak": "Peak", "cheapest": "Cheapest",
        "events_one": "1 event", "events_many": "{n} events",
        "nothing": "Nothing scheduled",
        "red": "Priority", "green": "Personal", "blue": "Work", "yellow": "Health",
        "updated": "Updated", "weather": "Weather", "price": "Electricity",
        "feels_like": "feels", "no_data": "No data",
    },
}


def language() -> str:
    lang = str(getattr(config, "EPAPER_LANGUAGE", "fi")).lower()
    return lang if lang in _STRINGS else "fi"


def t(key: str) -> str:
    return _STRINGS[language()].get(key, key)


def event_count(n: int) -> str:
    return t("events_one") if n == 1 else t("events_many").format(n=n)


def short_date(day) -> str:
    """'su 18.10.' in Finnish, 'Sun 18 Oct' in English."""
    weekday = _WEEKDAYS[language()][day.weekday()]
    if language() == "fi":
        return f"{weekday} {day.day}.{day.month}."
    return f"{weekday} {day.day} {day.strftime('%b')}"
