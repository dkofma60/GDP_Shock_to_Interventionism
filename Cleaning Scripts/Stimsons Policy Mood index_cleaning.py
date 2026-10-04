"""Clean Stimson's quarterly Policy Mood series.

Raw input directory:
Raw Data/Stimson's Policy Mood index

The source workbook contains annual, biennial, and quarterly series arranged
side by side. This script retains only the quarterly Year, Quarter, and Mood
records, removes the workbook's Average row, combines Year and Quarter into a
source-style quarterly date identifier, and writes a two-column CSV.

Source URL: https://stimson.web.unc.edu/data/
Recorded access date: 2026-08-18
The provenance values are preserved from the raw directory's
``Access Metadata.csv``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


RAW_DIRECTORY = Path("Raw Data") / "Stimson's Policy Mood index"
OUTPUT_RELATIVE_PATH = (
    Path("Cleaned Data")
    / "Stimsons Policy Mood index"
    / "Stimsons_Policy_Mood_index_cleaned.csv"
)
SOURCE_SHEET = "Data"
QUARTERLY_COLUMNS = "H:J"
FINAL_COLUMNS = ["date", "mood"]

EXPECTED_OBSERVATIONS = 265
EXPECTED_FIRST_DATE = "1958q4"
EXPECTED_LAST_DATE = "2024q4"


def locate_source(repo_root: Path) -> Path:
    """Find the sole non-temporary Excel workbook in the raw directory."""
    raw_directory = repo_root / RAW_DIRECTORY
    if not raw_directory.is_dir():
        raise FileNotFoundError(f"Raw data directory not found: {raw_directory}")

    workbooks = sorted(
        path
        for path in raw_directory.glob("*.xlsx")
        if path.is_file() and not path.name.startswith("~$")
    )
    if len(workbooks) != 1:
        found = ", ".join(path.name for path in workbooks) or "none"
        raise FileNotFoundError(
            "Expected exactly one non-temporary .xlsx workbook in "
            f"{raw_directory}; found {len(workbooks)}: {found}"
        )
    return workbooks[0]


def read_quarterly_block(source_path: Path) -> pd.DataFrame:
    """Read and validate the workbook's quarterly block in columns H through J."""
    workbook = pd.ExcelFile(source_path, engine="openpyxl")
    if SOURCE_SHEET not in workbook.sheet_names:
        raise ValueError(
            f"Required sheet {SOURCE_SHEET!r} not found; "
            f"available sheets: {workbook.sheet_names}"
        )

    block = pd.read_excel(
        workbook,
        sheet_name=SOURCE_SHEET,
        header=None,
        usecols=QUARTERLY_COLUMNS,
    )
    if block.shape[1] != 3 or len(block) < 3:
        raise ValueError(
            "Quarterly block must contain three columns and at least one record."
        )

    section_header = block.iloc[0].tolist()
    column_header = block.iloc[1].tolist()
    if section_header[0] != "Quarterly":
        raise ValueError(
            f"Expected the Quarterly section in column H; found {section_header[0]!r}."
        )
    if column_header != ["Year", "Quarter", "Mood"]:
        raise ValueError(
            "Unexpected quarterly column headers; expected "
            f"['Year', 'Quarter', 'Mood'], found {column_header}."
        )

    body = block.iloc[2:].copy()
    body.columns = ["year", "quarter", "mood"]

    year_numeric = pd.to_numeric(body["year"], errors="coerce")
    record_mask = year_numeric.notna()
    blank_mask = body.isna().all(axis=1)
    average_mask = body["year"].eq("Average")

    if int(average_mask.sum()) != 1:
        raise ValueError(
            "Expected exactly one Average row after the quarterly observations; "
            f"found {int(average_mask.sum())}."
        )
    average_row = body.loc[average_mask].iloc[0]
    if pd.notna(average_row["quarter"]):
        raise ValueError("Average row unexpectedly contains a quarter value.")

    unexpected_rows = body.loc[~record_mask & ~blank_mask & ~average_mask]
    if not unexpected_rows.empty:
        raise ValueError(
            "Unexpected non-quarterly rows within the quarterly block: "
            f"{unexpected_rows.to_dict(orient='records')}"
        )

    quarterly = body.loc[record_mask].copy().reset_index(drop=True)
    quarterly["year"] = pd.to_numeric(quarterly["year"], errors="raise")
    quarterly["quarter"] = pd.to_numeric(
        quarterly["quarter"], errors="raise"
    )
    quarterly["mood"] = pd.to_numeric(quarterly["mood"], errors="raise")

    if quarterly[["year", "quarter", "mood"]].isna().any().any():
        raise ValueError("Quarterly source observations contain missing values.")
    if not (quarterly["year"] % 1 == 0).all():
        raise ValueError("Quarterly source contains a non-integer year.")
    if not (quarterly["quarter"] % 1 == 0).all():
        raise ValueError("Quarterly source contains a non-integer quarter.")

    quarterly["year"] = quarterly["year"].astype("int64")
    quarterly["quarter"] = quarterly["quarter"].astype("int64")
    if not quarterly["quarter"].between(1, 4).all():
        invalid = quarterly.loc[
            ~quarterly["quarter"].between(1, 4), "quarter"
        ].tolist()
        raise ValueError(f"Quarter must be between 1 and 4; found {invalid}.")

    return quarterly


def clean_and_validate(quarterly: pd.DataFrame) -> pd.DataFrame:
    """Create the quarterly date identifier and validate the cleaned series."""
    cleaned = pd.DataFrame(
        {
            "date": (
                quarterly["year"].astype(str)
                + "q"
                + quarterly["quarter"].astype(str)
            ),
            "mood": quarterly["mood"],
        }
    )
    cleaned["date"] = cleaned["date"].astype("string")
    cleaned = cleaned[FINAL_COLUMNS]

    if list(cleaned.columns) != FINAL_COLUMNS:
        raise ValueError(f"Final columns are not exactly {FINAL_COLUMNS}.")
    if cleaned.isna().any().any():
        raise ValueError("Cleaned data contain missing values.")
    if not cleaned["date"].str.fullmatch(r"\d{4}q[1-4]").all():
        raise ValueError("Cleaned date values do not conform to YYYYq1–YYYYq4.")
    if cleaned["date"].duplicated().any():
        duplicates = cleaned.loc[
            cleaned["date"].duplicated(keep=False), "date"
        ].tolist()
        raise ValueError(f"Duplicate quarterly dates found: {duplicates}")
    if len(cleaned) != EXPECTED_OBSERVATIONS:
        raise ValueError(
            f"Expected {EXPECTED_OBSERVATIONS} observations; found {len(cleaned)}."
        )
    if cleaned.iloc[0]["date"] != EXPECTED_FIRST_DATE:
        raise ValueError(
            f"Expected first date {EXPECTED_FIRST_DATE}; "
            f"found {cleaned.iloc[0]['date']}."
        )
    if cleaned.iloc[-1]["date"] != EXPECTED_LAST_DATE:
        raise ValueError(
            f"Expected last date {EXPECTED_LAST_DATE}; "
            f"found {cleaned.iloc[-1]['date']}."
        )

    quarter_index = quarterly["year"] * 4 + quarterly["quarter"] - 1
    expected_index = list(range(quarter_index.iloc[0], quarter_index.iloc[-1] + 1))
    if quarter_index.tolist() != expected_index:
        raise ValueError("Quarterly observations are not chronological and contiguous.")

    # The transformation must not alter any quarterly Mood observation.
    if not cleaned["mood"].equals(quarterly["mood"]):
        raise ValueError("Quarterly Mood values changed during cleaning.")

    return cleaned


def write_and_verify(cleaned: pd.DataFrame, output_path: Path) -> None:
    """Write the CSV atomically and verify an exact read-back."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    try:
        cleaned.to_csv(temporary_path, index=False)
        written = pd.read_csv(temporary_path, dtype={"date": "string"})
        pd.testing.assert_frame_equal(written, cleaned, check_exact=True)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    """Create the cleaned Stimson quarterly Policy Mood CSV."""
    repo_root = Path(__file__).resolve().parents[1]
    source_path = locate_source(repo_root)
    output_path = repo_root / OUTPUT_RELATIVE_PATH

    quarterly = read_quarterly_block(source_path)
    cleaned = clean_and_validate(quarterly)
    write_and_verify(cleaned, output_path)

    print(f"Source: {source_path}")
    print(f"Output: {output_path}")
    print(f"Columns: {', '.join(cleaned.columns)}")
    print(f"Observations: {len(cleaned)}")
    print(f"Coverage: {cleaned.iloc[0]['date']} through {cleaned.iloc[-1]['date']}")
    print(f"Missing values: {int(cleaned.isna().sum().sum())}")
    print(f"Duplicate dates: {int(cleaned['date'].duplicated().sum())}")
    print("Annual, biennial, and Average rows excluded.")
    print("All validations passed.")


if __name__ == "__main__":
    main()
