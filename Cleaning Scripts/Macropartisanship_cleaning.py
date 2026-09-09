"""Clean the quarterly Erikson–MacKuen–Stimson macropartisanship series.

Series: Erikson–MacKuen–Stimson macropartisanship, as supplied in Green,
Hamel, and Miller, *Macropartisanship Revisited*.

Raw input:
Raw Data/Macropartisanship/Green Hamel Miller Macropartisanship/macro_data.csv

Source replication archive: Green, Hamel, and Miller replication materials.
Dataverse DOI: 10.7910/DVN/2OL2CM.
Recorded source/download URL: https://doi.org/10.7910/DVN/2OL2CM
Recorded access date: 2026-08-18
These provenance values are preserved from
``Raw Data/Macropartisanship/Access Metadata.csv``.

Source coverage is 1953Q1–2021Q1. Although this project's eventual analysis
window begins in 1967, cleaning intentionally preserves the full source series.
``macropartisanship`` is the raw quarterly series. ``macropid_adj`` is the
survey-mode-adjusted version constructed with the coefficients embedded in the
authors' ``Xtb syntax.do``. The underlying poll-level field dates and sample
sizes are not contained in this quarterly replication dataset, so this script
does not fabricate them or reconstruct the underlying Gallup/CBS-NYT polls.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd


SOURCE_FILENAME = "macro_data.csv"
ARCHIVE_DIRECTORY = "Green Hamel Miller Macropartisanship"
REQUIRED_SOURCE_COLUMNS = [
    "year",
    "date",
    "macropartisanship",
    "macro_landline_only",
    "macro_two_modes",
]
FINAL_COLUMNS = ["date", "year", "macropartisanship", "macropid_adj"]

# Published regression coefficients copied exactly from Xtb syntax.do.
LANDLINE_ONLY_COEFFICIENT = Decimal("-0.0388495")
TWO_MODES_COEFFICIENT = Decimal("-0.0144603")

EXPECTED_FIRST_QUARTER = "1953q1"
EXPECTED_LAST_QUARTER = "2021q1"
EXPECTED_OBSERVATIONS = 273


def locate_source(repo_root: Path) -> Path:
    """Locate the inspected source file at its repository-relative path."""
    raw_root = repo_root / "Raw Data"
    if not raw_root.is_dir():
        raise FileNotFoundError(f"Raw-data directory not found: {raw_root}")

    archive_directory = raw_root / "Macropartisanship" / ARCHIVE_DIRECTORY
    if not archive_directory.is_dir():
        raise FileNotFoundError(
            "Macropartisanship replication archive directory not found: "
            f"{archive_directory}"
        )

    exact_matches = [
        path
        for path in archive_directory.iterdir()
        if path.is_file() and path.name == SOURCE_FILENAME
    ]
    if len(exact_matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one file named {SOURCE_FILENAME!r} in "
            f"{archive_directory}; found {len(exact_matches)}."
        )
    return exact_matches[0]


def prepare_source(source_path: Path) -> pd.DataFrame:
    """Read source data and validate fields needed for the transformation."""
    source = pd.read_csv(
        source_path,
        dtype={"date": "string", "macropartisanship": "string"},
    )

    missing_columns = [
        column for column in REQUIRED_SOURCE_COLUMNS if column not in source.columns
    ]
    if missing_columns:
        raise ValueError(f"Source is missing required columns: {missing_columns}")

    source = source[REQUIRED_SOURCE_COLUMNS].copy()
    if source.isna().any().any():
        missing_counts = source.isna().sum()
        raise ValueError(
            "Required source columns contain missing values: "
            f"{missing_counts[missing_counts > 0].to_dict()}"
        )

    for column in ["year", "macro_landline_only", "macro_two_modes"]:
        source[column] = pd.to_numeric(source[column], errors="raise")

    try:
        source["macropartisanship"] = source["macropartisanship"].map(Decimal)
    except InvalidOperation as error:
        raise ValueError("macropartisanship contains a nonnumeric value.") from error
    if not source["macropartisanship"].map(lambda value: value.is_finite()).all():
        raise ValueError("macropartisanship contains a non-finite value.")

    if not (source["year"] % 1 == 0).all():
        raise ValueError("Source year contains non-integer values.")
    source["year"] = source["year"].astype("int64")

    for column in ["macro_landline_only", "macro_two_modes"]:
        unexpected = set(source[column].unique()) - {0, 1}
        if unexpected:
            raise ValueError(
                f"{column} must be binary; unexpected values: {sorted(unexpected)}"
            )
    if (source["macro_landline_only"] + source["macro_two_modes"] > 1).any():
        raise ValueError("Mode indicators overlap in at least one observation.")

    date_parts = source["date"].str.extract(
        r"^(?P<date_year>\d{4})q(?P<quarter>[1-4])$"
    )
    if date_parts.isna().any().any():
        invalid_dates = source.loc[date_parts.isna().any(axis=1), "date"].tolist()
        raise ValueError(
            "Date must conform to YYYYq1–YYYYq4; invalid values: "
            f"{invalid_dates[:10]}"
        )

    date_year = date_parts["date_year"].astype("int64")
    if not date_year.equals(source["year"]):
        mismatches = source.loc[date_year != source["year"], ["date", "year"]]
        raise ValueError(
            "Year encoded in date does not equal year column: "
            f"{mismatches.head(10).to_dict(orient='records')}"
        )

    source["_quarter"] = date_parts["quarter"].astype("int64")
    source = source.sort_values(["year", "_quarter"], kind="stable").reset_index(
        drop=True
    )
    return source


def clean_and_validate(source: pd.DataFrame) -> tuple[pd.DataFrame, int, Decimal]:
    """Apply the published adjustment and validate the cleaned quarterly panel."""
    # Literal translation of the authors' Stata command:
    # gen macropid_adj = macropartisanship
    #     - macro_landline_only * -.0388495
    #     - macro_two_modes * -.0144603
    calculated_adjustment = (
        source["macropartisanship"]
        - source["macro_landline_only"] * LANDLINE_ONLY_COEFFICIENT
        - source["macro_two_modes"] * TWO_MODES_COEFFICIENT
    ).rename("macropid_adj")

    cleaned = source[["date", "year", "macropartisanship"]].copy()
    cleaned["macropid_adj"] = calculated_adjustment
    cleaned = cleaned[FINAL_COLUMNS]

    # Recompute independently from the retained source fields and demand exact
    # equality, ensuring the adjusted series was derived from the published
    # coefficients rather than copied from another field.
    recomputed = (
        source["macropartisanship"]
        - source["macro_landline_only"] * LANDLINE_ONLY_COEFFICIENT
        - source["macro_two_modes"] * TWO_MODES_COEFFICIENT
    ).rename("macropid_adj")
    if not cleaned["macropid_adj"].equals(recomputed):
        raise ValueError(
            "macropid_adj does not exactly match the published mode adjustment."
        )

    if list(cleaned.columns) != FINAL_COLUMNS:
        raise ValueError(f"Final columns are not exactly {FINAL_COLUMNS}.")
    if cleaned.isna().any().any():
        raise ValueError("Missing values exist in the final cleaned columns.")
    if cleaned["date"].duplicated().any():
        duplicates = cleaned.loc[cleaned["date"].duplicated(False), "date"].tolist()
        raise ValueError(f"Duplicate quarterly dates found: {duplicates}")
    if len(cleaned) != EXPECTED_OBSERVATIONS:
        raise ValueError(
            f"Expected {EXPECTED_OBSERVATIONS} observations; found {len(cleaned)}."
        )
    if cleaned.iloc[0]["date"] != EXPECTED_FIRST_QUARTER:
        raise ValueError(
            f"Expected first quarter {EXPECTED_FIRST_QUARTER}; "
            f"found {cleaned.iloc[0]['date']}."
        )
    if cleaned.iloc[-1]["date"] != EXPECTED_LAST_QUARTER:
        raise ValueError(
            f"Expected last quarter {EXPECTED_LAST_QUARTER}; "
            f"found {cleaned.iloc[-1]['date']}."
        )

    quarter_index = source["year"] * 4 + source["_quarter"] - 1
    if not quarter_index.is_monotonic_increasing or quarter_index.duplicated().any():
        raise ValueError("Quarters are not strictly monotonically ordered.")
    expected_index = list(range(quarter_index.iloc[0], quarter_index.iloc[-1] + 1))
    if quarter_index.tolist() != expected_index:
        raise ValueError("Quarterly coverage has one or more gaps.")

    absolute_adjustment = (
        cleaned["macropid_adj"] - cleaned["macropartisanship"]
    ).abs()
    changed_observations = int((absolute_adjustment > 0).sum())
    maximum_absolute_difference = max(absolute_adjustment)
    if changed_observations == 0:
        raise ValueError("Mode adjustment changes no observations; check source flags.")

    return cleaned, changed_observations, maximum_absolute_difference


def decimal_to_csv_text(value: Decimal) -> str:
    """Render an exact decimal without binary artifacts or insignificant zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def write_and_verify(cleaned: pd.DataFrame, output_path: Path) -> None:
    """Write atomically and verify that the CSV round-trips without alteration."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    serialized = cleaned.copy()
    for column in ["macropartisanship", "macropid_adj"]:
        serialized[column] = serialized[column].map(decimal_to_csv_text)
    try:
        serialized.to_csv(temporary_path, index=False)
        written = pd.read_csv(temporary_path, dtype="string")
        expected_text = {
            column: [str(value) for value in serialized[column]]
            for column in FINAL_COLUMNS
        }
        actual_text = {
            column: written[column].tolist() for column in FINAL_COLUMNS
        }
        if list(written.columns) != FINAL_COLUMNS or actual_text != expected_text:
            raise ValueError("Written CSV did not round-trip exactly.")
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    """Create the validated, full-coverage cleaned dataset."""
    repo_root = Path(__file__).resolve().parents[1]
    source_path = locate_source(repo_root)
    output_path = (
        repo_root
        / "Cleaned Data"
        / "Macropartisanship"
        / "Macropartisanship_cleaned.csv"
    )

    source = prepare_source(source_path)
    cleaned, changed_observations, maximum_absolute_difference = clean_and_validate(
        source
    )
    write_and_verify(cleaned, output_path)

    print(f"Source: {source_path}")
    print(f"Output: {output_path}")
    print(f"Columns: {', '.join(cleaned.columns)}")
    print(f"Observations: {len(cleaned)}")
    print(f"Coverage: {cleaned.iloc[0]['date']} through {cleaned.iloc[-1]['date']}")
    print(f"Missing final values: {int(cleaned.isna().sum().sum())}")
    print(f"Duplicate quarters: {int(cleaned['date'].duplicated().sum())}")
    print(f"Adjusted observations: {changed_observations}")
    print(
        "Maximum absolute adjustment: "
        f"{maximum_absolute_difference}"
    )
    print("All validations passed.")


if __name__ == "__main__":
    main()
