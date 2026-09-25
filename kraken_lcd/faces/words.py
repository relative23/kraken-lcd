"""The few words faces print, in German and English."""

WORDS = {
    "en": {
        "liquid": "LIQUID", "pump": "PUMP", "fan": "FAN", "rpm": "RPM",
        "cores": "CORES", "ram_gb": "RAM GB", "vram_gb": "VRAM GB", "power": "POWER", "clock": "CLOCK",
        "min": "MIN", "mean": "MEAN", "max": "MAX", "now": "NOW", "last_24h": "24 H",
        "hot": "HOT", "limit": "limit", "since": "for", "video": "VIDEO",
        "example_title": "Midnight Circuit", "example_artist": "Example artist",
        "weekdays": ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"),
        "months": ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
                   "OCT", "NOV", "DEC"),
    },
    "de": {
        "liquid": "LIQUID", "pump": "PUMPE", "fan": "LÜFTER", "rpm": "U/MIN",
        "cores": "KERNE", "ram_gb": "RAM GB", "vram_gb": "VRAM GB", "power": "LEISTUNG", "clock": "TAKT",
        "min": "MIN", "mean": "MITTEL", "max": "MAX", "now": "JETZT", "last_24h": "24 H",
        "hot": "HEISS", "limit": "Grenze", "since": "seit", "video": "VIDEO",
        "example_title": "Midnight Circuit", "example_artist": "Beispielkünstler",
        "weekdays": ("MO", "DI", "MI", "DO", "FR", "SA", "SO"),
        "months": ("JAN", "FEB", "MÄR", "APR", "MAI", "JUN", "JUL", "AUG", "SEP",
                   "OKT", "NOV", "DEZ"),
    },
}


def words(language: str) -> dict:
    return WORDS.get(language, WORDS["en"])
