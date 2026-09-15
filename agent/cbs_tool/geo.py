"""
Geo resolution: municipality name → GM code, and level prefix mapping.
"""
import json
from pathlib import Path
import os

MUNICIPALITIES_F      = Path(__file__).parent / "municipalities.json"
_MUNICIPALITIES_DATA  = Path(os.environ.get("CBS_DATA_DIR", str(Path(__file__).resolve().parents[1] / "reference" / "cbs"))) / "municipalities.json"


def _load_municipalities_file() -> dict:
    """Load municipalities.json from data dir or package dir. Returns {} if not found."""
    for path in (_MUNICIPALITIES_DATA, MUNICIPALITIES_F):
        if path.exists():
            with open(path) as f:
                return json.load(f)
    return {}

# All 342 Dutch municipalities (name → GM code)
KNOWN_MUNICIPALITIES: dict[str, str] = {
    # G4
    "amsterdam":                  "GM0363",
    "rotterdam":                  "GM0599",
    "den haag":                   "GM0518",
    "'s-gravenhage":              "GM0518",
    "utrecht":                    "GM0344",
    # G40 + common
    "eindhoven":                  "GM0772",
    "groningen":                  "GM0014",
    "tilburg":                    "GM0855",
    "almere":                     "GM0034",
    "breda":                      "GM0758",
    "nijmegen":                   "GM0268",
    "enschede":                   "GM0153",
    "haarlem":                    "GM0392",
    "arnhem":                     "GM0202",
    "zaanstad":                   "GM0479",
    "amersfoort":                 "GM0307",
    "apeldoorn":                  "GM0200",
    "dordrecht":                  "GM0505",
    "leiden":                     "GM0546",
    "maastricht":                 "GM0935",
    "zwolle":                     "GM0193",
    "westland":                   "GM1783",
    "delft":                      "GM0503",
    "emmen":                      "GM0114",
    "deventer":                   "GM0150",
    "helmond":                    "GM0794",
    "venlo":                      "GM0983",
    "leeuwarden":                 "GM0080",
    "alkmaar":                    "GM0361",
    "'s-hertogenbosch":           "GM0796",
    "s-hertogenbosch":            "GM0796",
    "den bosch":                  "GM0796",
    "zoetermeer":                 "GM0637",
    "capelle aan den ijssel":     "GM0502",
    "capelle":                    "GM0502",
    "haarlemmermeer":             "GM0394",
    "ede":                        "GM0228",
    "westvoorne":                 "GM0614",
    "nissewaard":                 "GM1nobody",
    "súdwest-fryslân":            "GM1900",
    "sudwest-fryslan":            "GM1900",
    "gouda":                      "GM0513",
    "purmerend":                  "GM0439",
    "schiedam":                   "GM0606",
    "hoorn":                      "GM0405",
    "middelburg":                 "GM0687",
    "roermond":                   "GM0957",
    "vlissingen":                 "GM0718",
    "alphen aan den rijn":        "GM0484",
    "hoofddorp":                  "GM0394",
    "lelystad":                   "GM0995",
    "sittard-geleen":             "GM1883",
    "heerlen":                    "GM0917",
    "venray":                     "GM0984",
    "spijkenisse":                "GM1930",
    "zoetermeer":                 "GM0637",
    "nieuwegein":                 "GM0356",
    "rijswijk":                   "GM0603",
    "barendrecht":                "GM0489",
    "lansingerland":              "GM1621",
    "vlaardingen":                "GM0622",
    "hardenberg":                 "GM0160",
    "oss":                        "GM0828",
    "woerden":                    "GM0632",
    "veenendaal":                 "GM0345",
    "hilversum":                  "GM0402",
    "krimpen aan den ijssel":     "GM0542",
    "capelle aan den ijssel":     "GM0502",
    "velsen":                     "GM0453",
    "beverwijk":                  "GM0375",
    "heerhugowaard":              "GM0398",
    "alkmaar":                    "GM0361",
    "den helder":                 "GM0400",
    "hoeksche waard":             "GM1963",
    "nissewaard":                 "GM1930",
    "goeree-overflakkee":         "GM1924",
    "westerkwartier":             "GM1969",
    "stadskanaal":                "GM0037",
    "midden-groningen":           "GM1952",
    "oldambt":                    "GM1895",
    "veendam":                    "GM0047",
    "pekela":                     "GM0765",
    "bellingwedde":               "GM0007",
    "delfzijl":                   "GM0010",
    "appingedam":                 "GM0003",
    "loppersum":                  "GM0024",
    "eemsmond":                   "GM0755",
    "bedum":                      "GM0005",
    "ten boer":                   "GM0009",
    "haren":                      "GM0017",
    "hoogezand-sappemeer":        "GM0018",
    "slochteren":                 "GM0040",
    "menterwolde":                "GM0023",
    "leek":                       "GM0022",
    "grootegast":                 "GM0015",
    "marum":                      "GM0025",
    "winsum":                     "GM0050",
    "de marne":                   "GM0008",
    "assen":                      "GM0106",
    "meppel":                     "GM0119",
    "hoogeveen":                  "GM0118",
    "coevorden":                  "GM0109",
    "borger-odoorn":              "GM1681",
    "aa en hunze":                "GM1680",
    "tynaarlo":                   "GM1730",
    "noordenveld":                "GM1699",
    "de wolden":                  "GM1690",
    "westerveld":                 "GM1701",
    "zwolle":                     "GM0193",
    "deventer":                   "GM0150",
    "almelo":                     "GM0141",
    "hengelo":                    "GM0164",
    "enschede":                   "GM0153",
    "oldenzaal":                  "GM0173",
    "haaksbergen":                "GM0158",
    "hellendoorn":                "GM0163",
    "rijssen-holten":             "GM1742",
    "wierden":                    "GM0189",
    "twenterand":                 "GM1700",
    "losser":                     "GM0168",
    "dinkelland":                 "GM1774",
    "tubbergen":                  "GM0183",
    "steenwijkerland":            "GM1708",
    "zwartewaterland":            "GM1896",
    "kampen":                     "GM0166",
    "hattem":                     "GM0160",
    "oldebroek":                  "GM0269",
    "elburg":                     "GM0230",
    "nunspeet":                   "GM0302",
    "ermelo":                     "GM0233",
    "harderwijk":                 "GM0243",
    "putten":                     "GM0273",
    "nijkerk":                    "GM0267",
    "barneveld":                  "GM0203",
    "scherpenzeel":               "GM0279",
    "wageningen":                 "GM0289",
    "rhenen":                     "GM0274",
    "veenendaal":                 "GM0345",
    "utrechtse heuvelrug":        "GM1581",
    "de bilt":                    "GM0310",
    "zeist":                      "GM0355",
    "bunnik":                     "GM0312",
    "houten":                     "GM0321",
    "ijsselstein":                "GM0353",
    "lopik":                      "GM0331",
    "montfoort":                  "GM0335",
    "oudewater":                  "GM0589",
    "bodegraven-reeuwijk":        "GM1901",
    "gouda":                      "GM0513",
    "waddinxveen":                "GM0627",
    "zuidplas":                   "GM1892",
    "krimpenerwaard":             "GM1931",
    "schoonhoven":                "GM0608",
    "bergambacht":                "GM0491",
    "lekkerkerk":                 "GM0544",
    "vlist":                      "GM0623",
    "stolwijk":                   "GM0610",
    "ouderkerk":                  "GM0587",
    "moordrecht":                 "GM0556",
    "zevenhuizen-moerkapelle":    "GM0638",
}


def resolve_code(name_or_code: str) -> str:
    """Resolve a municipality name or GM code to a GM code."""
    s = name_or_code.strip()
    if s.upper().startswith("GM"):
        return s.upper()
    key = s.lower()
    if key in KNOWN_MUNICIPALITIES:
        return KNOWN_MUNICIPALITIES[key]
    # Try municipalities.json (built during catalog build)
    data = _load_municipalities_file()
    if data:
        by_name = data.get("by_name", data) if "by_name" in data else data
        if key in by_name:
            return by_name[key]
    # Live fallback
    return _live_resolve(s)


def get_geo_name(gm_code: str) -> str:
    """Resolve GM code to municipality name (best effort). Prefer longer names."""
    reverse: dict[str, str] = {}
    for name, code in KNOWN_MUNICIPALITIES.items():
        titled = name.title()
        existing = reverse.get(code, "")
        if len(titled) > len(existing):
            reverse[code] = titled
    # Overlay names from built municipalities file
    data = _load_municipalities_file()
    if data:
        by_code = data.get("by_code", {}) if "by_code" in data else {}
        reverse.update(by_code)
    return reverse.get(gm_code.upper(), gm_code)


def _live_resolve(name: str) -> str:
    """Search CBS geo dimension for a matching municipality name."""
    import cbsodata
    try:
        vals = cbsodata.get_meta("84583NED", "RegioS")
        for v in vals:
            if name.lower() in v.get("Title", "").lower() and v["Key"].strip().startswith("GM"):
                return v["Key"].strip()
    except Exception:
        pass
    raise ValueError(
        f"Could not resolve municipality: '{name}'. "
        f"Try using the GM code directly (e.g. GM0502 for Capelle aan den IJssel)."
    )


LEVEL_PREFIX = {
    "municipality": "GM",
    "wijk":         "WK",
    "buurt":        "BU",
}


def build_geo_filter(geo_dimension: str, gm_code: str, level: str = "municipality") -> str:
    """Build OData filter string for geo filtering."""
    gm_digits = gm_code[2:]  # strip "GM"

    if geo_dimension == "WijkenEnBuurten":
        if level == "municipality":
            return f"startswith(WijkenEnBuurten,'GM{gm_digits}')"
        elif level == "wijk":
            return f"startswith(WijkenEnBuurten,'WK{gm_digits}')"
        elif level == "buurt":
            return f"startswith(WijkenEnBuurten,'BU{gm_digits}')"
        return f"startswith(WijkenEnBuurten,'GM{gm_digits}')"
    else:
        # RegioS, Gemeenten, or other key
        # Some tables pad GM codes to 10 chars, others don't — use startswith
        # to match both "GM0502" and "GM0502    "
        prefix = {"municipality": "GM", "wijk": "WK", "buurt": "BU"}.get(level, "GM")
        return f"startswith({geo_dimension},'{prefix}{gm_digits}')"


def parse_multi_geo(geo_str: str) -> list[str]:
    """Parse comma-separated municipality names/codes → list of GM codes."""
    parts = [p.strip() for p in geo_str.split(",")]
    return [resolve_code(p) for p in parts if p]
