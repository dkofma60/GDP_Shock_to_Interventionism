"""Create the respondent-level GSS 1972-2024 analysis extract.

The script reads the cumulative Stata file directly with pandas, converts Stata
extended missing values to blank CSV fields, selects only the requested columns,
and keeps the standard/base national-spending wordings. A respondent is retained
only when at least one target substantive response is nonmissing. Raw ``DATEINTV``
MMDD values are retained, and ``interview_quarter`` is derived from ``YEAR`` and
the interview month. Experimental ``...Y`` wordings are deliberately never read
or merged.

Run with Python 3 and pandas from any working directory. Raw inputs are never
modified. The output directory is created if needed and the CSV is replaced
atomically after in-memory and saved-file validation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "Raw Data" / "GSS Cumulative"
OUTPUT_DIRECTORY = ROOT / "Cleaned Data" / "GSS Cumulative"
OUTPUT_FILE = OUTPUT_DIRECTORY / "GSS Cumulative_cleaned.csv"

REQUESTED_COLUMNS = [
    "YEAR",
    "ID",
    "DATEINTV",
    "WTSSPS",
    "WTSSNRPS",
    "VPSU",
    "VSTRAT",
    "FORM",
    "BALLOT",
    "NATSPAC",
    "NATENVIR",
    "NATHEAL",
    "NATCITY",
    "NATCRIME",
    "NATDRUG",
    "NATEDUC",
    "NATRACE",
    "NATARMS",
    "NATAID",
    "NATFARE",
    "NATROAD",
    "NATSOC",
    "NATMASS",
    "NATPARK",
    "NATCHLD",
    "NATSCI",
    "NATENRGY",
    "EQWLTH",
]

NATIONAL_SPENDING_COLUMNS = [
    "NATSPAC",
    "NATENVIR",
    "NATHEAL",
    "NATCITY",
    "NATCRIME",
    "NATDRUG",
    "NATEDUC",
    "NATRACE",
    "NATARMS",
    "NATAID",
    "NATFARE",
    "NATROAD",
    "NATSOC",
    "NATMASS",
    "NATPARK",
    "NATCHLD",
    "NATSCI",
    "NATENRGY",
]

SUBSTANTIVE_TARGET_COLUMNS = [*NATIONAL_SPENDING_COLUMNS, "EQWLTH"]

Y_VARIANTS = {
    "NATSPACY",
    "NATENVIY",
    "NATHEALY",
    "NATCITYY",
    "NATCRIMY",
    "NATDRUGY",
    "NATEDUCY",
    "NATRACEY",
    "NATARMSY",
    "NATAIDY",
    "NATFAREY",
}

INTEGER_COLUMNS = {
    "YEAR",
    "ID",
    "DATEINTV",
    "VPSU",
    "VSTRAT",
    "FORM",
    "BALLOT",
    *NATIONAL_SPENDING_COLUMNS,
    "EQWLTH",
}

DERIVED_COLUMN = "interview_quarter"


def require(condition: bool, message: str) -> None:
    """Raise a clear validation error when a required condition is false."""
    if not condition:
        raise ValueError(message)


def locate_stata_directory() -> Path:
    """Resolve the documented folder name and the underscore spelling in-repo."""
    if not RAW_ROOT.is_dir():
        raise FileNotFoundError(f"Raw GSS directory not found: {RAW_ROOT}")

    preferred = [RAW_ROOT / "GSS Stata", RAW_ROOT / "GSS_stata"]
    matches = [path for path in preferred if path.is_dir()]
    if not matches:
        matches = [
            path
            for path in RAW_ROOT.iterdir()
            if path.is_dir()
            and re.sub(r"[^a-z0-9]", "", path.name.casefold()) == "gssstata"
        ]
    unique_matches = sorted(set(matches))
    if len(unique_matches) != 1:
        raise FileNotFoundError(
            "Expected exactly one GSS Stata directory under "
            f"{RAW_ROOT}; found {[str(path) for path in unique_matches]}."
        )
    return unique_matches[0]


def locate_cumulative_dta(stata_directory: Path) -> Path:
    """Identify one cumulative GSS .dta and fail rather than guess ambiguously."""
    all_dta = sorted(path for path in stata_directory.glob("*.dta") if path.is_file())
    likely_cumulative = [
        path
        for path in all_dta
        if "cumul" in path.name.casefold()
        or re.search(r"(?:^|\D)7224(?:\D|$)", path.stem) is not None
    ]
    if len(likely_cumulative) == 1:
        return likely_cumulative[0]
    if len(all_dta) == 1:
        return all_dta[0]
    raise FileNotFoundError(
        "Could not identify exactly one cumulative GSS Stata file in "
        f"{stata_directory}. Found {[path.name for path in all_dta]}; "
        f"cumulative candidates were {[path.name for path in likely_cumulative]}."
    )


def source_schema(source_file: Path) -> tuple[dict[str, str], list[str]]:
    """Return a case-insensitive canonical-to-source name map and selected names."""
    with pd.io.stata.StataReader(
        source_file, convert_categoricals=False, convert_missing=False
    ) as reader:
        source_names = list(reader.variable_labels())

    folded: dict[str, str] = {}
    for name in source_names:
        key = name.casefold()
        require(key not in folded, f"Case-insensitive duplicate Stata column: {name}")
        folded[key] = name

    selected = [column for column in REQUESTED_COLUMNS if column.casefold() in folded]
    require("YEAR" in selected and "ID" in selected, "Source must contain YEAR and ID.")
    return folded, selected


def read_and_clean(
    source_file: Path, source_names: dict[str, str], selected: list[str]
) -> pd.DataFrame:
    """Read all rows, normalize names/types, and apply no substantive recodes."""
    usecols = [source_names[column.casefold()] for column in selected]
    data = pd.read_stata(
        source_file,
        columns=usecols,
        convert_categoricals=False,
        convert_missing=False,
        preserve_dtypes=False,
    )
    data = data.rename(columns={source_names[c.casefold()]: c for c in selected})
    require(list(data.columns) == selected, "Column normalization changed column order.")

    # convert_missing=False converts every Stata extended missing (.d, .i, etc.)
    # to NaN. No valid values are changed; nullable integers only prevent `.0`
    # suffixes in the CSV representation.
    for column in selected:
        data[column] = pd.to_numeric(data[column], errors="raise")
        if column in INTEGER_COLUMNS:
            observed = data[column].dropna()
            require(
                observed.mod(1).eq(0).all(),
                f"{column} contains a non-integer substantive/design value.",
            )
            data[column] = data[column].astype("Int64")

    require(data["YEAR"].notna().all(), "YEAR contains missing values.")
    require(data["ID"].notna().all(), "ID contains missing values.")
    return data.sort_values(["YEAR", "ID"], kind="stable").reset_index(drop=True)


def expected_output_columns(selected: list[str]) -> list[str]:
    """Place the derived quarter immediately after its raw DATEINTV source."""
    require("DATEINTV" in selected, "Source must contain DATEINTV.")
    columns = selected.copy()
    columns.insert(columns.index("DATEINTV") + 1, DERIVED_COLUMN)
    return columns


def interview_quarter_values(data: pd.DataFrame) -> pd.Series:
    """Derive YYYYq# from YEAR and the month component of raw MMDD DATEINTV."""
    require("DATEINTV" in data, "Cannot derive interview quarter without DATEINTV.")
    observed = data["DATEINTV"].notna()
    raw_dates = data["DATEINTV"].astype("Int64")
    months = raw_dates // 100
    days = raw_dates % 100
    invalid_encoding = observed & (~months.between(1, 12) | ~days.between(1, 99))
    require(
        not invalid_encoding.any(),
        "DATEINTV contains values that are not recognizable MMDD encodings: "
        f"{sorted(raw_dates[invalid_encoding].astype(int).unique().tolist())}",
    )

    quarters = ((months - 1) // 3 + 1).astype("Int64").astype("string")
    years = data["YEAR"].astype("Int64").astype("string")
    return (years + "q" + quarters).where(observed, pd.NA).astype("string")


def add_interview_quarter(data: pd.DataFrame) -> pd.DataFrame:
    """Add the derived quarter without changing the raw interview-date field."""
    result = data.copy()
    raw_dateintv = result["DATEINTV"].copy()
    result.insert(
        result.columns.get_loc("DATEINTV") + 1,
        DERIVED_COLUMN,
        interview_quarter_values(result),
    )
    require(
        result["DATEINTV"].equals(raw_dateintv),
        "Deriving interview_quarter changed raw DATEINTV values.",
    )
    return result


def parsed_interview_dates(data: pd.DataFrame) -> pd.Series:
    """Parse complete calendar dates for validation ranges, leaving raw data alone."""
    mmdd = data["DATEINTV"].astype("Int64").astype("string").str.zfill(4)
    date_text = data["YEAR"].astype("Int64").astype("string") + "-" + mmdd
    return pd.to_datetime(date_text, format="%Y-%m%d", errors="coerce")


def retain_substantive_respondents(data: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep respondents with at least one of the 19 target responses."""
    missing_targets = [
        column for column in SUBSTANTIVE_TARGET_COLUMNS if column not in data
    ]
    require(
        not missing_targets,
        f"Cannot apply substantive row filter; missing columns: {missing_targets}",
    )
    retain = data[SUBSTANTIVE_TARGET_COLUMNS].notna().any(axis=1)
    dropped_rows = int((~retain).sum())
    filtered = data.loc[retain].reset_index(drop=True)
    require(
        filtered[SUBSTANTIVE_TARGET_COLUMNS].notna().any(axis=1).all(),
        "A retained respondent has no nonmissing substantive target response.",
    )
    return filtered, dropped_rows


def validate_values(data: pd.DataFrame, expected_columns: list[str]) -> None:
    """Validate schema, exclusions, sorting, and substantive response domains."""
    require(list(data.columns) == expected_columns, "Unexpected output schema.")
    present_y = sorted(Y_VARIANTS.intersection(data.columns))
    require(not present_y, f"Experimental Y variants entered the output: {present_y}")
    require(
        data[SUBSTANTIVE_TARGET_COLUMNS].notna().any(axis=1).all(),
        "Output contains a respondent with all 19 substantive targets missing.",
    )
    expected_quarters = interview_quarter_values(data)
    actual_quarters = data[DERIVED_COLUMN].astype("string")
    require(
        actual_quarters.equals(expected_quarters),
        "interview_quarter does not match YEAR and DATEINTV.",
    )
    require(
        actual_quarters.dropna().str.fullmatch(r"\d{4}q[1-4]").all(),
        "interview_quarter contains a value outside YYYYq1-YYYYq4 format.",
    )

    expected_order = data.sort_values(["YEAR", "ID"], kind="stable").reset_index(
        drop=True
    )
    require(data.equals(expected_order), "Rows are not sorted by YEAR and ID.")

    for column in NATIONAL_SPENDING_COLUMNS:
        if column not in data:
            continue
        observed = set(pd.to_numeric(data[column].dropna(), errors="raise"))
        unexpected = observed - {1, 2, 3}
        require(not unexpected, f"{column} has out-of-range values: {sorted(unexpected)}")

    if "EQWLTH" in data:
        observed = set(pd.to_numeric(data["EQWLTH"].dropna(), errors="raise"))
        unexpected = observed - set(range(1, 8))
        require(not unexpected, f"EQWLTH has out-of-range values: {sorted(unexpected)}")


def write_atomically(data: pd.DataFrame, destination: Path) -> None:
    """Write deterministic CSV text and replace the prior output only on success."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{destination.stem}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            data.to_csv(stream, index=False, na_rep="", lineterminator="\n")
        temporary_path.replace(destination)
        destination.chmod(0o644)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def validate_round_trip(source: pd.DataFrame, saved: pd.DataFrame) -> None:
    """Confirm CSV serialization preserves every value and missing position."""
    require(source.shape == saved.shape, "Saved CSV shape differs from source extract.")
    for column in source.columns:
        source_missing = source[column].isna().reset_index(drop=True)
        saved_missing = saved[column].isna().reset_index(drop=True)
        require(
            source_missing.equals(saved_missing),
            f"Saved CSV changed missing-value positions in {column}.",
        )
        observed = ~source_missing
        if column == DERIVED_COLUMN:
            source_values = source.loc[observed, column].astype("string")
            saved_values = saved.loc[observed, column].astype("string")
            require(
                source_values.reset_index(drop=True).equals(
                    saved_values.reset_index(drop=True)
                ),
                f"Saved CSV changed one or more values in {column}.",
            )
        else:
            source_values = pd.to_numeric(
                source.loc[observed, column], errors="raise"
            ).astype(float)
            saved_values = pd.to_numeric(
                saved.loc[observed, column], errors="raise"
            ).astype(float)
            require(
                (source_values.to_numpy() == saved_values.to_numpy()).all(),
                f"Saved CSV changed one or more numeric values in {column}.",
            )


def build_year_validation(
    source: pd.DataFrame, retained: pd.DataFrame
) -> pd.DataFrame:
    """Build annual input, retention, substantive, and interview-date checks."""
    rows: list[dict[str, object]] = []
    for year in sorted(int(value) for value in source["YEAR"].unique()):
        source_year = source[source["YEAR"] == year]
        retained_year = retained[retained["YEAR"] == year]
        parsed_dates = parsed_interview_dates(retained_year).dropna()
        if parsed_dates.empty:
            date_range = "—"
        else:
            date_range = (
                f"{parsed_dates.min():%Y-%m-%d} to "
                f"{parsed_dates.max():%Y-%m-%d}"
            )

        row: dict[str, object] = {
            "Year": year,
            "Input N": len(source_year),
            "Retained N": len(retained_year),
        }
        row.update(
            {
                f"{column} N": int(retained_year[column].notna().sum())
                for column in SUBSTANTIVE_TARGET_COLUMNS
            }
        )
        row["Dated N"] = int(retained_year["DATEINTV"].notna().sum())
        row["Interview date range"] = date_range
        rows.append(row)

    return pd.DataFrame(rows)


def validate_year_validation(
    table: pd.DataFrame, source: pd.DataFrame, retained: pd.DataFrame
) -> None:
    """Reconcile the annual table to source and retained microdata totals."""
    expected_columns = [
        "Year",
        "Input N",
        "Retained N",
        *(f"{column} N" for column in SUBSTANTIVE_TARGET_COLUMNS),
        "Dated N",
        "Interview date range",
    ]
    require(list(table.columns) == expected_columns, "Unexpected annual table schema.")
    require(int(table["Input N"].sum()) == len(source), "Annual input Ns do not sum.")
    require(
        int(table["Retained N"].sum()) == len(retained),
        "Annual retained Ns do not sum.",
    )
    require(
        table["Retained N"].le(table["Input N"]).all(),
        "An annual retained N exceeds its input N.",
    )
    for column in SUBSTANTIVE_TARGET_COLUMNS:
        require(
            int(table[f"{column} N"].sum()) == int(retained[column].notna().sum()),
            f"Annual usable Ns do not reconcile for {column}.",
        )
    require(
        int(table["Dated N"].sum()) == int(retained["DATEINTV"].notna().sum()),
        "Annual dated Ns do not reconcile to nonmissing DATEINTV.",
    )


def markdown_validation_table(table: pd.DataFrame) -> str:
    """Format the annual validation table without requiring tabulate."""
    headers = list(table.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---:" if h != "Interview date range" else "---" for h in headers) + "|",
    ]
    for row in table.itertuples(index=False, name=None):
        values = [
            str(value)
            if header == "Year"
            else f"{value:,}"
            if isinstance(value, int)
            else str(value)
            for header, value in zip(headers, row)
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def sha256(path: Path) -> str:
    """Calculate a provenance hash without loading the full file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def print_validation_report(
    source_file: Path,
    output_file: Path,
    source_rows: int,
    dropped_rows: int,
    data: pd.DataFrame,
    absent_requested: list[str],
    annual_validation: pd.DataFrame,
) -> None:
    """Print the requested row/schema checks and per-variable sanity statistics."""
    print(f"Source: {source_file.relative_to(ROOT)}")
    print(f"Source SHA-256: {sha256(source_file)}")
    print(f"Output: {output_file.relative_to(ROOT)}")
    print(f"Output SHA-256: {sha256(output_file)}")
    print(
        f"Rows: source={source_rows:,}; dropped={dropped_rows:,}; "
        f"output={len(data):,}"
    )
    print("Row filter: every output row has >=1 nonmissing substantive target; valid=yes")
    print(f"Columns ({len(data.columns)}): {', '.join(data.columns)}")
    print(f"Requested columns absent from source: {absent_requested or 'none'}")
    print("Experimental Y variants present: none")
    print("Substantive domains: national spending=1-3; EQWLTH=1-7; valid=yes")
    print("DATEINTV: raw MMDD values retained; interview_quarter=YYYYq#; valid=yes")
    print("Per-variable nonmissing counts and observed year ranges:")
    years = pd.to_numeric(data["YEAR"], errors="raise")
    for column in data.columns:
        available = data[column].notna()
        if available.any():
            first_year = int(years[available].min())
            last_year = int(years[available].max())
            span = f"{first_year}-{last_year}"
        else:
            span = "none"
        print(f"  {column:<10} {int(available.sum()):>6,}  {span}")
    print("Year-by-year validation table (Dated N is nonmissing raw DATEINTV):")
    print(markdown_validation_table(annual_validation))


def main() -> None:
    stata_directory = locate_stata_directory()
    source_file = locate_cumulative_dta(stata_directory)
    source_names, selected = source_schema(source_file)
    absent_requested = [column for column in REQUESTED_COLUMNS if column not in selected]

    source_data = read_and_clean(source_file, source_names, selected)
    source_rows = len(source_data)
    cleaned, dropped_rows = retain_substantive_respondents(source_data)
    cleaned = add_interview_quarter(cleaned)
    output_columns = expected_output_columns(selected)
    require(
        source_rows == len(cleaned) + dropped_rows,
        "Retained and dropped row counts do not reconcile to the source.",
    )
    validate_values(cleaned, output_columns)
    annual_validation = build_year_validation(source_data, cleaned)
    validate_year_validation(annual_validation, source_data, cleaned)
    write_atomically(cleaned, OUTPUT_FILE)

    # Re-read the artifact itself so validation covers serialization as well as
    # the in-memory frame. Empty CSV fields are interpreted as proper missing.
    saved = pd.read_csv(
        OUTPUT_FILE,
        keep_default_na=True,
        float_precision="round_trip",
        dtype={DERIVED_COLUMN: "string"},
    )
    require(len(saved) == len(cleaned), "Saved CSV row count differs from filtered data.")
    require(list(saved.columns) == output_columns, "Saved CSV schema differs from request.")
    for column in saved.columns.difference([DERIVED_COLUMN]):
        saved[column] = pd.to_numeric(saved[column], errors="raise")
    validate_round_trip(cleaned, saved)
    validate_values(saved, output_columns)
    saved_annual_validation = build_year_validation(source_data, saved)
    validate_year_validation(saved_annual_validation, source_data, saved)
    require(
        annual_validation.equals(saved_annual_validation),
        "Saved CSV changed the year-by-year validation table.",
    )

    print_validation_report(
        source_file,
        OUTPUT_FILE,
        source_rows,
        dropped_rows,
        saved,
        absent_requested,
        saved_annual_validation,
    )


if __name__ == "__main__":
    main()
