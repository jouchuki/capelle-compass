"""
Concept map: natural language → CBS data sources, by geo level.
"""

CONCEPT_MAP: dict[str, dict] = {
    "unemployment": {
        "aliases": ["werkloosheid", "werkloos", "ww uitkering", "ww", "werkloze beroepsbevolking"],
        "search_terms": ["werkloosheid", "uitkering", "arbeidsdeelname"],
        "by_level": {
            "buurt": {
                "series":  "Personen met een uitkering; soort uitkering, wijken en buurten",
                "latest":  "86003NED",
                "column":  "Werkloosheidsuitkering",
                "years":   "2013–2025",
                "note":    "WW benefit recipients as % of residents 15+. True unemployment rate (%) does not exist at buurt level.",
            },
            "wijk": {
                "series":  "Personen met een uitkering; soort uitkering, wijken en buurten",
                "latest":  "86003NED",
                "column":  "Werkloosheidsuitkering",
                "years":   "2013–2025",
                "note":    "WW benefit recipients as % of residents 15+.",
            },
            "municipality": {
                "series":  "Arbeidsdeelname; gemeenten (regio-indeling 2024)",
                "latest":  "86008NED",
                "column":  "Werkloosheidspercentage",
                "years":   "2014–2023",
                "note":    "True unemployment rate available at municipality level.",
            },
            "national": {
                "series":  "Arbeidsdeelname; kerncijfers",
                "latest":  "85264NED",
                "column":  "Werkloosheidspercentage_21",
                "years":   "2013–2025",
            },
        },
    },
    "employment": {
        "aliases": ["arbeidsdeelname", "werkzaam", "werknemer", "arbeidsparticipatie", "netto arbeidsparticipatie"],
        "search_terms": ["arbeidsdeelname", "werkzame beroepsbevolking"],
        "by_level": {
            "buurt": {
                "series":  "Arbeidsdeelname; wijken en buurten",
                "latest":  "86258NED",
                "column":  "NettoArbeidsparticipatie_3",
                "years":   "2019–2024",
                "note":    "Employment participation rate (%). Breakdowns by gender and age available.",
            },
            "wijk": {
                "series":  "Arbeidsdeelname; wijken en buurten",
                "latest":  "86258NED",
                "column":  "NettoArbeidsparticipatie_3",
                "years":   "2019–2024",
            },
            "municipality": {
                "series":  "Arbeidsdeelname; gemeenten (regio-indeling 2024)",
                "latest":  "86008NED",
                "column":  "NettoArbeidsparticipatie_3",
                "years":   "2014–2023",
            },
        },
    },
    "income": {
        "aliases": ["inkomen", "verdiensten", "loon", "salaris", "gemiddeld inkomen"],
        "search_terms": ["inkomen", "verdiensten"],
        "by_level": {
            "buurt": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "GemiddeldInkomenPerInwoner_78",
                "years":   "1995–2025",
                "note":    "Average income per resident and per recipient. Also: low income %, poverty %.",
            },
            "wijk": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "GemiddeldInkomenPerInwoner_78",
                "years":   "1995–2025",
            },
            "municipality": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "GemiddeldInkomenPerInwoner_78",
                "years":   "1995–2025",
            },
        },
    },
    "poverty": {
        "aliases": ["armoede", "laag inkomen", "minima", "bijstand"],
        "search_terms": ["armoede", "bijstand", "laag inkomen"],
        "by_level": {
            "buurt": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "PersonenInArmoede_81",
                "years":   "2018–2025",
                "note":    "Also: PersonenTot25BovenArmoedegrens. Bijstand (welfare) via uitkering series.",
            },
        },
    },
    "population": {
        "aliases": ["bevolking", "inwoners", "bewoners", "demografie"],
        "search_terms": ["bevolking", "inwoners", "kerncijfers"],
        "by_level": {
            "buurt": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "AantalInwoners_5",
                "years":   "1995–2025",
                "note":    "Full demographics: age groups, gender, origin, household composition.",
            },
        },
    },
    "housing": {
        "aliases": ["woningen", "woning", "vastgoed", "huur", "koop", "woz", "woningmarkt"],
        "search_terms": ["woningen", "woz", "huur", "koopwoning"],
        "by_level": {
            "buurt": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "Woningvoorraad_35",
                "years":   "1995–2025",
                "note":    "WOZ value, owner/rental split, social housing %, housing type, new construction.",
            },
        },
    },
    "crime": {
        "aliases": ["misdrijf", "criminaliteit", "veiligheid", "delicten"],
        "search_terms": ["misdrijven", "criminaliteit"],
        "by_level": {
            "buurt": {
                "series":  "Geregistreerde misdrijven; wijken en buurten",
                "latest":  "84468NED",
                "column":  None,
                "years":   "2016–2018",
                "note":    "Only 3 editions available. CBS stopped publishing crime at buurt level after 2018.",
            },
        },
    },
    "education": {
        "aliases": ["onderwijs", "opleidingsniveau", "opleiding", "scholing"],
        "search_terms": ["onderwijs", "opleidingsniveau"],
        "by_level": {
            "buurt": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "HboWo_69",
                "years":   "2018–2025",
                "note":    "Education level of residents: low (vmbo/mbo1), mid (havo/vwo/mbo2-4), high (hbo/wo).",
            },
            "wijk": {
                "series":  "Bevolking 15 tot 75 jaar; opleidingsniveau, wijken en buurten",
                "latest":  "86232NED",
                "column":  None,
                "years":   "2013–2024",
                "note":    "Detailed education level breakdowns for working-age population.",
            },
        },
    },
    "benefits": {
        "aliases": ["uitkering", "uitkeringen", "bijstand", "ww", "ao uitkering", "sociale zekerheid"],
        "search_terms": ["uitkering", "bijstand"],
        "by_level": {
            "buurt": {
                "series":  "Personen met een uitkering; soort uitkering, wijken en buurten",
                "latest":  "86003NED",
                "column":  None,
                "years":   "2013–2025",
                "note":    "WW, bijstand, AO, AOW — counts and % of residents 15+.",
            },
        },
    },
    "youth care": {
        "aliases": ["jeugdzorg", "jeugdhulp", "jeugd"],
        "search_terms": ["jeugdzorg", "jeugdhulp"],
        "by_level": {
            "buurt": {
                "series":  "Kerncijfers wijken en buurten",
                "latest":  "86165NED",
                "column":  "JongerenMetJeugdzorgInNatura_91",
                "years":   "2018–2025",
            },
        },
    },
    "energy": {
        "aliases": ["energie", "gas", "elektriciteit", "aardgas", "zonnepanelen"],
        "search_terms": ["energie", "aardgas", "elektriciteit"],
        "by_level": {
            "buurt": {
                "series":  "Energieverbruik particuliere woningen; woningtype, wijken en buurten",
                "latest":  "86159NED",
                "column":  None,
                "years":   "2010–2024",
            },
        },
    },
}


def find_concept(query: str) -> tuple[str, dict] | None:
    """Match a query string to a concept. Returns (concept_name, concept_dict) or None."""
    q = query.lower()
    for concept_name, concept in CONCEPT_MAP.items():
        if concept_name in q:
            return concept_name, concept
        for alias in concept.get("aliases", []):
            if alias in q:
                return concept_name, concept
    return None


def availability(concept_query: str, level: str | None = None) -> dict:
    """
    Return availability info for a concept, optionally filtered to a geo level.
    """
    result = find_concept(concept_query)
    if not result:
        return {
            "concept": concept_query,
            "found": False,
            "message": f"No concept mapping found for '{concept_query}'. Try: {', '.join(CONCEPT_MAP.keys())}",
            "suggestion": f"Use `cbs search \"{concept_query}\"` to discover relevant tables.",
        }

    concept_name, concept = result
    levels = concept.get("by_level", {})

    if level and level not in levels:
        # Find finest available level
        level_order = ["buurt", "wijk", "municipality", "corop", "province", "national"]
        available_levels = [l for l in level_order if l in levels]
        return {
            "concept":   concept_name,
            "found":     True,
            "requested_level": level,
            "available": False,
            "message":   f"'{concept_name}' is not available at {level} level.",
            "finest_available": available_levels[0] if available_levels else None,
            "alternatives": {l: levels[l] for l in available_levels},
        }

    filtered = {l: v for l, v in levels.items() if not level or l == level}

    return {
        "concept":       concept_name,
        "found":         True,
        "by_level":      filtered,
        "search_terms":  concept.get("search_terms", []),
        "next": [
            f"cbs series {v['latest']}"
            for v in filtered.values() if v.get("latest")
        ] + [
            f"cbs get {v['latest']} --geo '<city>' --level {l}"
            for l, v in filtered.items() if v.get("latest")
        ],
    }
