# Селищни образувания / Territorial formations

---

## Български

### Описание
Този набор данни съдържа **селищните образувания** на Република България
(EKATTE регистър на НСИ) — курортни комплекси, бизнес паркове, индустриални
зони, квартали и други обособени територии, които не са самостоятелни
населени места, но имат собствен ЕКАТТЕ код.

НСИ публикува регистъра само като **атрибутни данни** (без полигони).
Геометриите тук са взаимствани от **OpenStreetMap** чрез Overpass API и са
свързани с НСИ записите по приоритет:

1. `ref:ekatte` точно съвпадение (увереност 1.0)
2. Точно име в границите на родителското землище (увереност 0.9)
3. Размито съвпадение в родителското землище — max от
   `token_set_ratio` и `partial_ratio` ≥ 85 (увереност = резултат / 100)
4. Съвпадение по „ядрено име“ (с премахнати общи префикси като
   „Курортен комплекс“, „Природен парк“) — `ratio ≥ 95` (увереност 0.7)

Записи без намерен полигон остават в CSV, но не влизат в GeoJSON.

### Файлове

#### `territorial_formations.csv`
Целият регистър на НСИ — **всички** записи, включително без открита геометрия.

| колона | описание |
|---|---|
| `ekatte` | 5-символен ЕКАТТЕ код, със запазени водещи нули (string) |
| `kind` | 1 = с национално значение, 2 = с местно значение |
| `name_bg` | име на български, точно както е в регистъра |
| `name_en` | латинска транслитерация |
| `parent_ekatte` | код от `area1` (5 цифри ЕКАТТЕ или NUTS4 като `VAR06`) |
| `parent_name` | име на родителското землище / община |
| `parent_is_nuts4` | `1` ако `parent_ekatte` е NUTS4 код |
| `area2_ekatte` | втори родителски код (за образувания, попадащи в две землища) |
| `area2_name` | име на втория родител |
| `document` | НСИ документ за заповедта, с която е създадено образуванието |

#### `territorial_formations.geojson`
Само записите с открит полигон в OSM. Свойствата на всеки `Feature` включват
всички колони на CSV плюс:

| свойство | описание |
|---|---|
| `osm_type` | `relation` или `way` |
| `osm_id` | OSM идентификатор |
| `match_method` | `ref_ekatte`, `name_exact`, `name_fuzzy` или `name_core` |
| `match_confidence` | 0.0–1.0 (виж стратегията по-горе) |

Координатите са закръглени до 6 знака след десетичната запетая. Записите са
сортирани по `ekatte` за байтова стабилност.

#### `data/match_log.csv`
Одиторска следа за всеки НСИ запис: решение, OSM ID, резултат, причина.
Този файл се проследява в git, за да се вижда историята на покритието.

### Възпроизвеждане
```bash
cd territorial_formations
pip install -r requirements.txt
./scripts/build.sh
```

Подкомандите (отделно):
```bash
python3 scripts/fetch_nsi.py    # → territorial_formations.csv
python3 scripts/fetch_osm.py    # → data/osm_raw.json (~50–200 MB)
python3 scripts/match.py        # → territorial_formations.geojson + match_log.csv
```

Флагът `--cached` пропуска изтеглянето, ако `data/*_raw.json` вече съществуват.

### Лиценз
- НСИ регистъра: публични данни на Националния статистически институт.
- OSM полигоните и **полученият GeoJSON** се разпространяват под лиценза
  на OSM — **Open Database License (ODbL)**. При използване е задължително
  посочване „© OpenStreetMap contributors“ и запазване на ODbL.

### Известни ограничения
- Покритието не е пълно — много образувания все още нямат полигон в OSM.
  Записите с `kind=1` (национално значение), за които не е намерено
  съвпадение, се изброяват изрично от `match.py` в края на отчета — те са с
  най-висок приоритет за допълване ръчно.
- Имена с латински числа (`XI шахта`) и дигитални суфикси (`Бизнес парк
  Бургас 1..5`) се обработват точно — размитият матчинг е изключен за имена,
  завършващи на цифра, за да не се обединяват номерирани серии.

---

## English

### Overview
This dataset publishes the **territorial formations** of Bulgaria from the
NSI EKATTE registry — resort complexes, business and industrial parks, urban
quarters and other named subdivisions that are not standalone settlements
but carry their own EKATTE code.

NSI publishes the registry as **attributes only** (no geometry). Polygons
here are sourced from **OpenStreetMap** via the Overpass API and joined to
NSI records by priority:

1. `ref:ekatte` exact match (confidence 1.0)
2. Exact name within parent settlement bbox (confidence 0.9)
3. Fuzzy within parent bbox — max of `token_set_ratio` and `partial_ratio`
   ≥ 85 (confidence = score / 100)
4. "Core name" match — strip generic prefixes ("Курортен комплекс",
   "Природен парк", etc.) on both sides and require `ratio ≥ 95`
   (confidence 0.7). Catches the case where NSI says "Курортен комплекс
   X" but OSM tags it as "Природен парк X".

Records without a polygon remain in the CSV but are excluded from the
GeoJSON.

### Files

#### `territorial_formations.csv`
The full NSI registry — **every** record, including those without geometry.

| column | description |
|---|---|
| `ekatte` | 5-character EKATTE code, leading zeros preserved (string) |
| `kind` | 1 = national significance, 2 = local significance |
| `name_bg` | Bulgarian name, verbatim from the registry |
| `name_en` | Latin transliteration |
| `parent_ekatte` | code from `area1` (5-digit EKATTE or NUTS4 like `VAR06`) |
| `parent_name` | parent settlement / municipality name |
| `parent_is_nuts4` | `1` if `parent_ekatte` is a NUTS4 code |
| `area2_ekatte` | secondary parent code (for cross-boundary formations) |
| `area2_name` | secondary parent name |
| `document` | NSI document that established the formation |

#### `territorial_formations.geojson`
Only the records with a polygon found in OSM. Each `Feature` carries every
CSV column plus:

| property | description |
|---|---|
| `osm_type` | `relation` or `way` |
| `osm_id` | OSM identifier |
| `match_method` | `ref_ekatte`, `name_exact`, `name_fuzzy`, or `name_core` |
| `match_confidence` | 0.0–1.0 (see strategy above) |

Coordinates are rounded to 6 decimal places. Features are sorted by
`ekatte` for byte-stable output.

#### `data/match_log.csv`
Per-record audit trail: decision, OSM id, score, reason. Tracked in git so
coverage history is visible across re-runs.

### Reproduction
```bash
cd territorial_formations
pip install -r requirements.txt
./scripts/build.sh
```

Or step by step:
```bash
python3 scripts/fetch_nsi.py    # → territorial_formations.csv
python3 scripts/fetch_osm.py    # → data/osm_raw.json (~50–200 MB)
python3 scripts/match.py        # → territorial_formations.geojson + match_log.csv
```

Pass `--cached` to skip downloads when `data/*_raw.json` already exist.

The pipeline is idempotent — with unchanged upstream data, the GeoJSON and
CSV are byte-identical run to run (sorted by `ekatte`, fixed coordinate
precision, `ensure_ascii=False`).

### Coverage stats
The numbers below are populated from `match.py`'s report. Re-run
`./scripts/build.sh` and paste the latest figures here:

```
total NSI records:    <pending>
matched:              <pending> (<pending>%)
  ref_ekatte          <pending>
  name_exact          <pending>
  name_fuzzy          <pending>
  none                <pending>
```

> **Note**: this branch ships the **pipeline** but not yet the materialized
> outputs. The build environment used to generate the scripts had no
> outbound access to `nsi.bg` and `overpass-api.de`, so a maintainer needs
> to run `./scripts/build.sh` once on a machine with internet access to
> populate `territorial_formations.csv`, `territorial_formations.geojson`
> and `data/match_log.csv`. After the first run, the coverage numbers and
> the `kind=1` (national-significance) gap list should be pasted into this
> README.

### Spot-check targets
After the first build, verify these well-known formations are present and
visually correct:

- Албена (Albena)
- Златни пясъци (Zlatni Pyasatsi / Golden Sands)
- Слънчев бряг (Slanchev Bryag / Sunny Beach)
- Св. св. Константин и Елена (Sveti Konstantin i Elena)
- Боровец (Borovets)

### License
- NSI registry: public data from the National Statistical Institute.
- OSM polygons and the **derived GeoJSON** are distributed under the
  **Open Database License (ODbL)**. Attribution to "© OpenStreetMap
  contributors" and preservation of ODbL are required for downstream use.

### Known limitations
- Coverage is partial — many formations still lack a polygon in OSM. The
  `kind=1` (national significance) gaps are enumerated explicitly at the end
  of `match.py`'s report; they are the highest priority for manual mapping.
- Names with Latin numerals (`XI шахта`) and digit suffixes (`Бизнес парк
  Бургас 1..5`) are handled carefully: fuzzy matching is disabled for any
  name ending in a digit so numbered series are never collapsed.
- Some `parent_ekatte` values are NUTS4 codes (e.g. `VAR06`) rather than
  5-digit settlement EKATTE codes; the parent-bbox lookup falls back to
  `municipalities.geojson` in that case.
