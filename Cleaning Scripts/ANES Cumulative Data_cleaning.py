"""Build the project's minimal respondent-level ANES analytical dataset.

Inputs: February 5, 2026 CDF and May 19, 2026 ANES 2024 Full Release,
located recursively beneath Raw Data/ANES Cumulative Data. Only interview
dates are merged from the separate release; CDF responses and weights remain
the authority. See the output README for coding, PDF citations, and caveats.

Run with Python 3 and pandas from any working directory. Raw inputs are never
written. The script checks their SHA-256 hashes, validates the saved CSV, and
refreshes only the marked validation section of the companion README, if present.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "Raw Data" / "ANES Cumulative Data"
OUTPUT = ROOT / "Cleaned Data" / "ANES Cumulative Data"
FOCAL = ["VCF0806", "VCF0809", "VCF0830", "VCF0839"]
ORIGINAL = [
    "VCF0004", "VCF0006", "VCF0006a", "VCF0009z", *FOCAL, "VCF0301",
    "VCF9027", "VCF0101", "VCF0104", "VCF0105a", "VCF0140a", "VCF0114",
    "VCF0147", "VCF0112", "VCF0118",
]
DERIVED = [
    "pre_interview_date", "pre_interview_quarter",
    "post_interview_date", "post_interview_quarter",
]
FINAL = ORIGINAL[:4] + DERIVED + ORIGINAL[4:]
OFFSETS = ["VCF1015", "VCF1016"]
TS_DATES = ["V243050", "V243052", "V243054", "V243060", "V243062"]
TIMING_METADATA = ["VCF0012", "VCF0015b"]  # Temporary only; never exported.

# Order: health, jobs, aid, services. VCF0013/0014 (CDF PDF pp.23-24)
# document post-only midterms and each historical year's post-variable boundary.
# Apply those boundaries to focal source lists (pp.346-348,360-361,364-365).
# The explicit 1972 jobs form exception overrides its aggregate source number.
# 2012 aid's source is omitted from the CDF: keep its linkage uncertainty flagged
# even though the annual codebook's corresponding aidblack_self item is pre.
# P=pre, O=post, F=form-dependent, U=unresolved provenance, -=not available.
WAVE_CODES = {
    1970: "O-O-", 1972: "PFO-", 1974: "-OO-", 1976: "PPP-",
    1978: "OOO-", 1980: "-OO-", 1982: "-OOO", 1984: "OPPP",
    1986: "-OOO", 1988: "PPPP", 1990: "-OOO", 1992: "PPPP",
    1994: "OOOO", 1996: "PPPP", 1998: "-OOO", 2000: "PPPP",
    2004: "PPPP", 2008: "PPPP", 2012: "PPUP", 2016: "PPPP",
    2020: "PPPP", 2024: "PPPP",
}

# CDF variable codebook PDF p.462 (printed p.458). The printed 1994 Nov 7
# is corrected to Nov 8, verified by the House Clerk's 1994 election record:
# https://clerk.house.gov/member_info/electionInfo/1994/94Stat.htm
# 1952 is supplied by the federal Election Day calendar rule; 2016 and 2020
# are explicit project requirements. The mapping is checked against that rule.
ELECTION_DAYS = {
    1952: "1952-11-04", 1956: "1956-11-06", 1958: "1958-11-04",
    1960: "1960-11-08", 1962: "1962-11-06", 1964: "1964-11-03",
    1966: "1966-11-08", 1968: "1968-11-05", 1970: "1970-11-03",
    1972: "1972-11-07", 1974: "1974-11-05", 1976: "1976-11-02",
    1978: "1978-11-07", 1980: "1980-11-04", 1982: "1982-11-02",
    1984: "1984-11-06", 1986: "1986-11-04", 1988: "1988-11-08",
    1990: "1990-11-06", 1992: "1992-11-03", 1994: "1994-11-08",
    1996: "1996-11-05", 1998: "1998-11-03", 2000: "2000-11-07",
    2002: "2002-11-05", 2004: "2004-11-02", 2008: "2008-11-04",
    2012: "2012-11-06", 2016: "2016-11-08", 2020: "2020-11-03",
}

# (substantive categories, documented nonresponse/nonscale categories).
# 9027 code 0 remains substantive. Code 3 identifies voters whose party is
# DK/NA/refused, and is missing for this party-of-previous-vote control.
RULES = {
    "VCF0806": (set(range(1, 8)), {-1, 0, 9}),
    "VCF0809": (set(range(1, 8)), {-1, 0, 9}),
    "VCF0830": (set(range(1, 8)), {0, 9}),
    "VCF0839": (set(range(1, 8)), {0, 9}),
    "VCF0301": (set(range(1, 8)), {0}),
    "VCF9027": ({0, 1, 2, 5}, {3, 9}),
    "VCF0101": (set(range(17, 100)), {0}),
    "VCF0104": ({1, 2, 3}, {0}),
    "VCF0105a": (set(range(1, 8)), {9}),
    "VCF0140a": (set(range(1, 8)), {8, 9}),
    "VCF0114": (set(range(1, 6)), {0}),
    "VCF0147": ({1, 2, 3, 4, 5, 7}, {8, 9}),
    "VCF0112": ({1, 2, 3, 4}, {0}),
    "VCF0118": (set(range(1, 6)), {9}),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def raw_hashes() -> dict[str, str]:
    result = {}
    for path in sorted(RAW.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[str(path.relative_to(RAW))] = digest.hexdigest()
    return result


def find_one(pattern: str) -> Path:
    paths = sorted(RAW.rglob(pattern))
    require(len(paths) == 1, f"Expected one {pattern}; found {paths}")
    return paths[0]


def read_columns(path: Path, columns: list[str]) -> tuple[pd.DataFrame, int]:
    header = pd.read_csv(path, nrows=0).columns
    require(set(columns) <= set(header), f"Missing columns in {path.name}: {set(columns)-set(header)}")
    data = pd.read_csv(path, usecols=columns, dtype="string", keep_default_na=False)
    data = data.apply(lambda col: col.str.strip()).replace({"": pd.NA, ".": pd.NA})
    return data, len(header)


def integer_column(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    require(numeric.dropna().map(lambda x: math.isfinite(x) and x % 1 == 0).all(),
            f"Noninteger values in {values.name}")
    return numeric.astype("Int64")


def parse_ts_date(values: pd.Series) -> pd.Series:
    # TS user guide p.18: negative numbers denote missing data. These date
    # entries (pp.611-613) define YYYYMMDD, with no positive missing sentinel.
    # Strip whitespace, blank/dot, and negative integer codes before parsing.
    tokens = values.str.strip().replace({"": pd.NA, ".": pd.NA})
    tokens = tokens.mask(tokens.str.fullmatch(r"-\d+(?:\.0+)?", na=False))
    tokens = tokens.str.replace(r"\.0+$", "", regex=True)
    require(tokens.dropna().str.fullmatch(r"\d{8}").all(),
            f"Unrecognized date token in {values.name}: {tokens[tokens.notna() & ~tokens.str.fullmatch(r'\d{8}', na=False)].tolist()}")
    parsed = pd.to_datetime(tokens, format="%Y%m%d", errors="coerce")
    require(parsed.notna().equals(tokens.notna()), f"Invalid calendar date in {values.name}")
    return parsed


def derive_dates(cdf: pd.DataFrame, ts: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    dates = pd.DataFrame(index=cdf.index)
    for year, text in ELECTION_DAYS.items():
        first = pd.Timestamp(year, 11, 1)
        monday = first + pd.Timedelta(days=(0 - first.weekday()) % 7)
        require(pd.Timestamp(text) == monday + pd.Timedelta(days=1),
                f"Election mapping disagrees with calendar rule: {year}")
    anchors = pd.to_datetime(cdf.VCF0004.map(ELECTION_DAYS), errors="coerce")
    for phase, col, sign in [("pre", "VCF1015", -1), ("post", "VCF1016", 1)]:
        offsets = integer_column(cdf[col])
        require(offsets.dropna().between(0, 99).all(), f"Unexpected offset codes in {col}")
        offsets = offsets.mask(offsets.eq(99))  # Must precede date arithmetic.
        usable = cdf.VCF0004.le(2020) & offsets.notna()
        require(anchors[usable].notna().all(), f"Missing election anchor for {col}")
        dates[f"{phase}_interview_date"] = anchors + sign * pd.to_timedelta(
            offsets.where(usable).astype(float), unit="D"
        )

    ts = ts.copy()
    ts.V240001 = integer_column(ts.V240001)
    require(ts.V240001.notna().all(), "Missing TS merge key")
    c24 = cdf.loc[cdf.VCF0004.eq(2024), ["VCF0006"]].copy()
    c24["_row"] = c24.index
    duplicates = {"CDF": int(c24.VCF0006.duplicated().sum()),
                  "TS": int(ts.V240001.duplicated().sum())}
    require(not any(duplicates.values()), f"Duplicate 2024 merge keys: {duplicates}")
    merged = c24.merge(ts, left_on="VCF0006", right_on="V240001", how="left",
                       validate="one_to_one", indicator=True).set_index("_row")
    unmatched = int(merged._merge.ne("both").sum())
    require(unmatched == 0, f"Unmatched 2024 CDF respondents: {unmatched}")
    parsed = {col: parse_ts_date(merged[col]) for col in TS_DATES}
    chosen = {}
    counts = {}
    for phase, priority in [("pre", ["V243052", "V243054", "V243050"]),
                            ("post", ["V243062", "V243060"])]:
        result = pd.Series(pd.NaT, index=merged.index, dtype="datetime64[ns]")
        for col in priority:
            use = result.isna() & parsed[col].notna()
            counts[col] = int(use.sum())
            result.loc[use] = parsed[col].loc[use]
        chosen[phase] = result
        dates.loc[result.index, f"{phase}_interview_date"] = result
        counts[f"{phase}_missing"] = int(result.isna().sum())
    anomalies = {}
    for phase, start, end in [("pre", "2024-08-03", "2024-11-05"),
                              ("post", "2024-11-07", "2025-02-17")]:
        outside = chosen[phase].notna() & ~chosen[phase].between(start, end)
        anomalies[phase] = [
            f"{int(merged.loc[i, 'VCF0006'])}: {chosen[phase].loc[i]:%Y-%m-%d}"
            for i in chosen[phase].index[outside]
        ]
    for phase in ["pre", "post"]:
        values = dates[f"{phase}_interview_date"]
        dates[f"{phase}_interview_quarter"] = values.dt.to_period("Q").astype("string").where(values.notna())
        dates[f"{phase}_interview_date"] = values.dt.strftime("%Y-%m-%d").astype("string")
    return dates, {
        "matched": len(merged) - unmatched, "unmatched": unmatched,
        "duplicate_keys": duplicates,
        "TS_without_CDF": int((~ts.V240001.isin(c24.VCF0006)).sum()),
        "fallback_counts": counts, "outside_guide_field_dates": anomalies,
    }


def validate_output(data: pd.DataFrame) -> None:
    require(list(data.columns) == FINAL and len(FINAL) == 22, "Unexpected final schema")
    require(not data.duplicated(["VCF0004", "VCF0006"]).any(), "Duplicate year/respondent IDs")
    require(data[ORIGINAL[:4]].notna().all().all(), "Missing identifier or weight")
    require(data[FOCAL].notna().any(axis=1).all(), "Retained respondent has no focal outcome")
    for col, (valid, _) in RULES.items():
        observed = set(pd.to_numeric(data[col].dropna()))
        require(observed <= valid, f"Invalid cleaned categories in {col}: {observed-valid}")
    for phase in ["pre", "post"]:
        values = data[f"{phase}_interview_date"]
        require(values.dropna().str.fullmatch(r"\d{4}-\d{2}-\d{2}").all(), "Invalid ISO format")
        parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="raise")
        quarter = parsed.dt.to_period("Q").astype("string").where(parsed.notna())
        require(quarter.equals(data[f"{phase}_interview_quarter"].astype("string")),
                f"{phase} date/quarter disagreement")


def outcome_timing(data: pd.DataFrame, outcome: str,
                   metadata: pd.DataFrame | None = None, *, strict: bool = True) -> pd.DataFrame:
    """Select an outcome-specific date/quarter for downstream macro matching.

    Returns an IN-MEMORY audit aligned to data.index; does not expand/write the
    22-column CSV. Missing selected-wave dates never fall back to the other wave.
    By default unresolved timing raises. strict=False exposes flags and leaves
    their matching keys missing, so callers can explicitly exclude/review them.
    Only 1972 jobs needs the raw form fields, loaded on demand if not supplied.
    """
    require(outcome in FOCAL, f"Not a focal outcome: {outcome}")
    require(data.index.is_unique, "Outcome timing requires a unique DataFrame index")
    years = integer_column(data.VCF0004)
    observed = pd.to_numeric(data[outcome], errors="raise").isin(range(1, 8))
    position = FOCAL.index(outcome)
    wave = years.map({y: codes[position] for y, codes in WAVE_CODES.items()}).astype("string")
    result = pd.DataFrame(index=data.index)
    result["wave"] = wave.map({"P": "pre", "O": "post"}).astype("string")
    result["status"] = pd.Series("unresolved_wave", index=data.index, dtype="string")
    result.loc[wave.eq("U"), "status"] = "unresolved_source_2012_aid"

    mixed = observed & years.eq(1972) & (outcome == "VCF0809")
    if mixed.any():
        keys = ["VCF0004", "VCF0006"]
        if metadata is None:
            metadata, _ = read_columns(find_one("anes_timeseries_cdf_csv_*.csv"), keys + TIMING_METADATA)
        meta = metadata[keys + TIMING_METADATA].copy()
        for col in keys:
            meta[col] = integer_column(meta[col])
        meta = meta.loc[meta.VCF0004.eq(1972)]
        require(not meta.duplicated(keys).any(), "Duplicate temporary form-metadata keys")
        left = data.loc[mixed, keys].copy()
        for col in keys:
            left[col] = integer_column(left[col])
        left["_index"] = left.index
        joined = left.merge(meta, on=keys, how="left", validate="one_to_one", indicator=True).set_index("_index")
        require(joined._merge.eq("both").all(), "Unmatched temporary form metadata")
        form = integer_column(joined.VCF0012)
        mail = integer_column(joined.VCF0015b)
        require(form.dropna().isin([1, 2, 9]).all(), "Unexpected 1972 form code")
        require(mail.dropna().isin([0, 1, 2]).all(), "Unexpected 1972 abbreviated-post code")
        # Post III (mail=1) originated as pre Form I; IV (mail=2) as Form II.
        conflict = ((form.eq(1) & mail.eq(2)) | (form.eq(2) & mail.eq(1))).fillna(False)
        effective = form.where(form.isin([1, 2])).fillna(mail.where(mail.isin([1, 2])))
        chosen = effective.map({1: "pre", 2: "post"}).astype("string").mask(conflict)
        result.loc[joined.index, "wave"] = chosen
        result.loc[joined.index, "status"] = "unresolved_form"
        result.loc[joined.index[conflict], "status"] = "conflicting_form_metadata"

    resolved = observed & result.wave.notna()
    result.loc[resolved, "status"] = "resolved"
    result.loc[~observed, "status"] = "outcome_missing"
    result.loc[~observed, "wave"] = pd.NA
    for suffix in ["date", "quarter"]:
        result[f"interview_{suffix}"] = pd.Series(pd.NA, index=data.index, dtype="string")
        for phase in ["pre", "post"]:
            use = observed & result.wave.eq(phase)
            result.loc[use, f"interview_{suffix}"] = data.loc[use, f"{phase}_interview_{suffix}"].astype("string")
    result.loc[resolved & result.interview_date.isna(), "status"] = "selected_wave_date_missing"
    parsed = pd.to_datetime(result.interview_date, format="%Y-%m-%d", errors="raise")
    expected = parsed.dt.to_period("Q").astype("string").where(parsed.notna())
    require(expected.equals(result.interview_quarter), f"Outcome-specific date/quarter disagreement: {outcome}")
    unresolved = observed & result.wave.isna()
    require(result.loc[unresolved, ["interview_date", "interview_quarter"]].isna().all().all(),
            "Unresolved timing must never receive a matching key")
    if strict:
        require(not unresolved.any(),
                f"{outcome}: {int(unresolved.sum())} responses have unresolved timing. "
                "Use strict=False to inspect flags; do not assign a global wave.")
    return result


def timing_validation(clean: pd.DataFrame, cdf: pd.DataFrame) -> str:
    rows = []
    for outcome in FOCAL:
        timing = outcome_timing(clean, outcome, metadata=cdf, strict=False)
        usable = clean[outcome].notna()
        for year in sorted(clean.VCF0004.unique()):
            use = usable & clean.VCF0004.eq(year)
            if not use.any():
                continue
            group = timing.loc[use]
            rows.append({"Year": int(year), "Outcome": outcome, "Usable N": len(group),
                         "Pre N": int(group.wave.eq("pre").sum()),
                         "Post N": int(group.wave.eq("post").sum()),
                         "Unresolved N": int(group.wave.isna().sum()),
                         "Correct-wave quarter N": int(group.interview_quarter.notna().sum()),
                         "Known wave, date missing N": int(group.status.eq("selected_wave_date_missing").sum())})
    # Independent form summaries make the mixed-wave exception inspectable.
    meta = cdf.loc[cdf.VCF0004.eq(1972), ["VCF0006", *TIMING_METADATA, "VCF0809"]].copy()
    meta = meta.loc[pd.to_numeric(meta.VCF0809).isin(range(1, 8))]
    forms = meta.groupby(TIMING_METADATA, dropna=False).size().reset_index(name="Usable jobs N")
    return "\n".join([
        "### Outcome-specific timing validation", "",
        "Matching keys are selected separately for each outcome. Missing dates never borrow the other wave. "
        "Unresolved-source/form cases have no outcome matching key, even if both generic dates exist. "
        "The output CSV still has exactly 22 columns; these are validation summaries only.", "",
        markdown_table(pd.DataFrame(rows)), "", "1972 jobs form audit (temporary metadata only):", "",
        markdown_table(forms), "",
        "2012 aid remains flagged as unresolved CDF source provenance; its annual-codebook counterpart is pre-election. "
        "See the timing audit discussion and the strict downstream helper above.",
    ])


def markdown_table(frame: pd.DataFrame) -> str:
    frame = frame.fillna("").astype(str)
    return "\n".join([
        "| " + " | ".join(frame.columns) + " |",
        "| " + " | ".join(["---"] * len(frame.columns)) + " |",
        *["| " + " | ".join(row) + " |" for row in frame.itertuples(index=False, name=None)],
    ])


def validation_report(cdf: pd.DataFrame, clean: pd.DataFrame, dims: dict, merge: dict) -> str:
    lines = ["## Validation from the current run", "",
             *[f"- Input `{name}`: {shape}." for name, shape in dims.items()],
             f"- Output: {len(clean):,} respondents × {len(clean.columns)} columns.",
             f"- Dropped (all four focal scales missing): {len(cdf)-len(clean):,}.",
             "- Exact schema, valid categories, unique year/respondent keys, ISO dates, and matching quarters: PASS.",
             f"- 2024 merge before filtering: {merge['matched']:,} matched; {merge['unmatched']} unmatched CDF; "
             f"{merge['TS_without_CDF']} TS-only; duplicate keys: CDF={merge['duplicate_keys']['CDF']}, TS={merge['duplicate_keys']['TS']}.",
             "- Raw Data SHA-256 comparison before/after cleaning: PASS (all files).",
             "- Saved CSV reopened and compared cell-for-cell: PASS.", "",
             "### 2024 date sources before outcome filtering", "",
             markdown_table(pd.DataFrame(list(merge['fallback_counts'].items()), columns=["Selected source / missing", "N"])),
             "", "Dates outside the user guide's stated field periods are preserved, not clipped:", "",
             *[f"- {phase.title()}: " + "; ".join(cases) + "." for phase, cases in merge['outside_guide_field_dates'].items()],
             "", "### Missingness in the final dataset", ""]
    missing = pd.DataFrame({"Column": FINAL, "Missing N": [int(clean[c].isna().sum()) for c in FINAL]})
    missing["Missing %"] = (missing["Missing N"] / len(clean) * 100).map(lambda x: f"{x:.2f}")
    lines += [markdown_table(missing), "", "### Usable focal N and interview-date coverage by study year", "",
              "Counts are unweighted and refer to retained respondents. Input N also shows study years removed by the outcome filter.", ""]
    rows = []
    for year, original in cdf.groupby("VCF0004", sort=True):
        group = clean.loc[clean.VCF0004.eq(year)]
        row = {"Year": int(year), "Input N": len(original), "Retained N": len(group)}
        row.update({c: int(group[c].notna().sum()) for c in FOCAL})
        for phase in ["pre", "post"]:
            values = group[f"{phase}_interview_date"].dropna()
            row[f"{phase.title()} dated N"] = len(values)
            row[f"{phase.title()} date range"] = f"{values.min()}–{values.max()}" if len(values) else ""
        rows.append(row)
    lines += [markdown_table(pd.DataFrame(rows)), "", "### Focal coverage totals", ""]
    for col in FOCAL:
        years = sorted(clean.loc[clean[col].notna(), "VCF0004"].unique())
        lines.append(f"- {col}: N={int(clean[col].notna().sum()):,}; years: " + ", ".join(map(str, years)) + ".")
    lines += ["", timing_validation(clean, cdf)]
    return "\n".join(lines)


def main() -> None:
    before = raw_hashes()
    cdf_path = find_one("anes_timeseries_cdf_csv_*.csv")
    ts_path = find_one("anes_timeseries_2024_csv_*.csv")
    cdf, cdf_width = read_columns(cdf_path, ORIGINAL + OFFSETS + TIMING_METADATA)
    ts, ts_width = read_columns(ts_path, ["V240001"] + TS_DATES)
    dims = {cdf_path.name: f"{len(cdf)} × {cdf_width}", ts_path.name: f"{len(ts)} × {ts_width}"}
    for col in ORIGINAL[:3]:
        cdf[col] = integer_column(cdf[col])
        require(cdf[col].notna().all(), f"Missing ID in {col}")
    require(not cdf.duplicated(["VCF0004", "VCF0006"]).any(), "Duplicate CDF year/respondent keys")
    require(cdf.VCF0004.le(2024).all(), "Unspecified timing rules for a later CDF release")
    weights = pd.to_numeric(cdf.VCF0009z, errors="raise")
    require(weights.notna().all() and weights.map(lambda x: math.isfinite(x) and x >= 0).all(), "Invalid weight")
    # Preserve weight decimal tokens exactly as supplied; they serialize as CSV numbers.
    derived, merge = derive_dates(cdf, ts)
    clean = cdf[ORIGINAL].copy()
    for col, (valid, missing) in RULES.items():
        values = integer_column(clean[col])
        observed = set(values.dropna())
        require(observed <= valid | missing, f"Undocumented code in {col}: {observed-valid-missing}")
        if -1 in missing:
            require(not (values.eq(-1) & cdf.VCF0004.ne(2024)).any(), f"Unexpected -1 year in {col}")
        clean[col] = values.where(values.isin(valid))
    for col in DERIVED:
        clean[col] = derived[col]
    clean = clean.loc[clean[FOCAL].notna().any(axis=1), FINAL].reset_index(drop=True)
    validate_output(clean)
    report = validation_report(cdf, clean, dims, merge)
    require(before == raw_hashes(), "Raw files changed during cleaning")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / "ANES Cumulative Data_cleaned.csv"
    with tempfile.NamedTemporaryFile(dir=OUTPUT, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        clean.to_csv(temporary, index=False, na_rep="")
        saved = pd.read_csv(temporary, dtype="string", keep_default_na=False).replace({"": pd.NA})
        expected = clean.astype("string")
        pd.testing.assert_frame_equal(saved, expected, check_exact=True)
        validate_output(saved)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    readme = OUTPUT / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        begin, end = "<!-- VALIDATION_START -->", "<!-- VALIDATION_END -->"
        require(text.count(begin) == text.count(end) == 1, "README validation markers missing/duplicated")
        text = text.split(begin)[0] + begin + "\n\n" + report + "\n\n" + end + text.split(end)[1]
        readme.write_text(text, encoding="utf-8")
    require(before == raw_hashes(), "Raw files changed during output creation")
    print(report)
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
