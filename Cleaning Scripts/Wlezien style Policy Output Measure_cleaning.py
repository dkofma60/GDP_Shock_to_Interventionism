"""Full-join the four quarterly Wlezien-style policy-output series.

The three source CSVs are joined on ``observation_date``. Dates are converted
from quarter-start calendar dates to ``YYYYq#`` identifiers. The four FRED
series-code column names are retained unchanged.
"""

from __future__ import annotations

from functools import reduce
from pathlib import Path

import pandas as pd


RAW_DIRECTORY_CANDIDATES = (
    Path("Raw Data") / "Wlezien-style Policy Output Measure",
    Path("Raw Data") / "Wlezien style Policy Output Measure",
)
OUTPUT_RELATIVE_PATH = (
    Path("Cleaned Data")
    / "Wlezien style Policy Output Measure"
    / "Wlezien style Policy Output Measure_cleaned.csv"
)
SOURCE_SCHEMAS = {
    "FGEXPND and GDP.csv": ["observation_date", "FGEXPND", "GDP"],
    "A091RC1Q027SBEA.csv": ["observation_date", "A091RC1Q027SBEA"],
    "W068RCQ027SBEA.csv": ["observation_date", "W068RCQ027SBEA"],
}
FINAL_COLUMNS = [
    "observation_date",
    "FGEXPND",
    "GDP",
    "A091RC1Q027SBEA",
    "W068RCQ027SBEA",
]
W068_FIRST_QUARTER = "1960q1"


def locate_raw_directory(repo_root: Path) -> Path:
    """Locate the raw directory, accepting the repository's hyphenated name."""
    matches = [
        repo_root / relative_path
        for relative_path in RAW_DIRECTORY_CANDIDATES
        if (repo_root / relative_path).is_dir()
    ]
    if len(matches) != 1:
        found = ", ".join(str(path) for path in matches) or "none"
        raise FileNotFoundError(
            "Expected exactly one Wlezien policy-output raw directory; "
            f"found {len(matches)}: {found}"
        )
    return matches[0]


def quarter_index(quarter_dates: pd.Series) -> pd.Series:
    """Convert validated YYYYq# strings to sortable integer quarter indexes."""
    parts = quarter_dates.str.extract(r"^(?P<year>\d{4})q(?P<quarter>[1-4])$")
    if parts.isna().any().any():
        invalid = quarter_dates.loc[parts.isna().any(axis=1)].tolist()
        raise ValueError(f"Invalid quarterly dates: {invalid[:10]}")
    return parts["year"].astype("int64") * 4 + parts["quarter"].astype("int64") - 1


def read_source(source_path: Path, expected_columns: list[str]) -> pd.DataFrame:
    """Read one source, validate its schema, and normalize its date key."""
    source = pd.read_csv(source_path, dtype="string", keep_default_na=False)
    if list(source.columns) != expected_columns:
        raise ValueError(
            f"Unexpected columns in {source_path.name}: {list(source.columns)}; "
            f"expected {expected_columns}."
        )
    if source.empty:
        raise ValueError(f"Source contains no observations: {source_path}")
    if source["observation_date"].eq("").any():
        raise ValueError(f"Blank observation dates found in {source_path.name}.")
    if source["observation_date"].duplicated().any():
        duplicates = source.loc[
            source["observation_date"].duplicated(keep=False), "observation_date"
        ].tolist()
        raise ValueError(
            f"Duplicate observation dates in {source_path.name}: {duplicates[:10]}"
        )

    parsed_dates = pd.to_datetime(
        source["observation_date"], format="%Y-%m-%d", errors="raise"
    )
    quarter_start = parsed_dates.dt.day.eq(1) & parsed_dates.dt.month.isin([1, 4, 7, 10])
    if not quarter_start.all():
        invalid = source.loc[~quarter_start, "observation_date"].tolist()
        raise ValueError(
            f"Dates must be quarter-start dates in {source_path.name}: {invalid[:10]}"
        )
    source["observation_date"] = (
        parsed_dates.dt.year.astype(str)
        + "q"
        + (((parsed_dates.dt.month - 1) // 3) + 1).astype(str)
    ).astype("string")

    for column in expected_columns[1:]:
        if source[column].eq("").any():
            raise ValueError(f"Blank source values found in {source_path.name}:{column}.")
        pd.to_numeric(source[column], errors="raise")

    return source


def clean_and_validate(raw_directory: Path) -> pd.DataFrame:
    """Full-join all sources and validate quarterly coverage and missingness."""
    sources = [
        read_source(raw_directory / filename, expected_columns)
        for filename, expected_columns in SOURCE_SCHEMAS.items()
    ]
    cleaned = reduce(
        lambda left, right: left.merge(
            right, on="observation_date", how="outer", validate="one_to_one"
        ),
        sources,
    )
    cleaned = cleaned[FINAL_COLUMNS]
    cleaned["_quarter_index"] = quarter_index(cleaned["observation_date"])
    cleaned = cleaned.sort_values("_quarter_index", kind="stable").reset_index(drop=True)

    indexes = cleaned["_quarter_index"].tolist()
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError("Full-joined quarterly date coverage contains a gap.")
    if cleaned["observation_date"].duplicated().any():
        raise ValueError("Full join produced duplicate quarterly dates.")

    first_w068_index = int(quarter_index(pd.Series([W068_FIRST_QUARTER])).iloc[0])
    before_w068 = cleaned["_quarter_index"] < first_w068_index
    if not cleaned.loc[before_w068, "W068RCQ027SBEA"].isna().all():
        raise ValueError("W068RCQ027SBEA must be null before 1960q1.")
    if cleaned.loc[~before_w068, "W068RCQ027SBEA"].isna().any():
        raise ValueError("W068RCQ027SBEA contains a missing value from 1960q1 onward.")
    if cleaned[["FGEXPND", "GDP", "A091RC1Q027SBEA"]].isna().any().any():
        raise ValueError("A non-W068 series contains a missing joined value.")

    return cleaned.drop(columns="_quarter_index")


def write_and_verify(cleaned: pd.DataFrame, output_path: Path) -> None:
    """Write the CSV atomically and verify values, blanks, and column order."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    expected = cleaned.astype("string").fillna("")
    try:
        cleaned.to_csv(temporary_path, index=False, na_rep="", lineterminator="\n")
        written = pd.read_csv(temporary_path, dtype="string", keep_default_na=False)
        if list(written.columns) != FINAL_COLUMNS or not written.equals(expected):
            raise ValueError("Written CSV did not round-trip exactly.")
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    """Create the full-joined quarterly policy-output CSV."""
    repo_root = Path(__file__).resolve().parents[1]
    raw_directory = locate_raw_directory(repo_root)
    output_path = repo_root / OUTPUT_RELATIVE_PATH

    cleaned = clean_and_validate(raw_directory)
    write_and_verify(cleaned, output_path)

    print(f"Source directory: {raw_directory}")
    print(f"Output: {output_path}")
    print(f"Columns: {', '.join(cleaned.columns)}")
    print(f"Observations: {len(cleaned)}")
    print(
        f"Coverage: {cleaned.iloc[0]['observation_date']} through "
        f"{cleaned.iloc[-1]['observation_date']}"
    )
    print(f"W068 pre-1960 blanks: {int(cleaned['W068RCQ027SBEA'].isna().sum())}")
    print("All validations passed.")


if __name__ == "__main__":
    main()
