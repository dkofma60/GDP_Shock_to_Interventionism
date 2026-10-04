"""Clean and combine the repository's presidential approval poll files.

Raw inputs are all American Presidency Project and Gallup CSV files in
``Raw Data/Presidential Approval``. American Presidency Project dates are
standardized to zero-padded MM/DD/YYYY. Gallup date ranges are parsed into
matching Start Date and End Date fields; year-marker and non-data reminder rows
are excluded. Approval values are retained without aggregation.

The output contains Start Date, End Date, Approving, Disapproving, Unsure, and
POTUS, sorted by poll start date and then end date.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

import pandas as pd


RAW_DIRECTORY = Path("Raw Data") / "Presidential Approval"
OUTPUT_RELATIVE_PATH = (
    Path("Cleaned Data")
    / "Presidential Approval"
    / "Presidential Approval_cleaned.csv"
)

APP_PREFIX = "American Presidency Project - Approval Ratings for POTUS - "
GALLUP_PREFIX = "Gallup - Approval Ratings for POTUS - "
METADATA_FILENAME = "Access Metadata.csv"

APP_COLUMNS = [
    "Start Date",
    "End Date",
    "Approving",
    "Disapproving",
    "Unsure/NoData",
]
GALLUP_COLUMNS = ["X.1", "Approve", "Disapprove", "No opinion"]
OUTPUT_COLUMNS = [
    "Start Date",
    "End Date",
    "Approving",
    "Disapproving",
    "Unsure",
    "POTUS",
]

MONTH_NUMBERS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
GALLUP_DATE_PATTERN = re.compile(
    r"^(?P<year>\d{4}) "
    r"(?P<start_month>[A-Z][a-z]{2}) "
    r"(?P<start_day>\d{1,2})-"
    r"(?:(?P<end_month>[A-Z][a-z]{2}) )?"
    r"(?P<end_day>\d{1,2})$"
)
YEAR_MARKER_PATTERN = re.compile(r"^<b>\d{4}</b>$")


def discover_source_files(repo_root: Path) -> tuple[Path, list[Path]]:
    """Find every APP and Gallup approval CSV, excluding provenance metadata."""
    raw_directory = repo_root / RAW_DIRECTORY
    if not raw_directory.is_dir():
        raise FileNotFoundError(f"Raw data directory not found: {raw_directory}")

    csv_files = sorted(path for path in raw_directory.glob("*.csv") if path.is_file())
    approval_files = [
        path
        for path in csv_files
        if path.name.startswith(APP_PREFIX) or path.name.startswith(GALLUP_PREFIX)
    ]
    unrelated = [
        path.name
        for path in csv_files
        if path.name != METADATA_FILENAME and path not in approval_files
    ]
    if unrelated:
        raise ValueError(
            "Unrecognized CSV files in the presidential approval directory: "
            f"{unrelated}"
        )
    if not approval_files:
        raise FileNotFoundError(
            f"No APP or Gallup approval CSV files found in {raw_directory}."
        )
    return raw_directory, approval_files


def filename_subject(path: Path, prefix: str) -> str:
    """Return the president label following the recognized filename prefix."""
    if not path.name.startswith(prefix) or path.suffix.lower() != ".csv":
        raise ValueError(f"Unexpected approval filename: {path.name}")
    subject = path.stem.removeprefix(prefix).strip()
    if not subject:
        raise ValueError(f"Filename has no president name: {path.name}")
    return subject


def build_potus_lookup(files: list[Path]) -> dict[Path, str]:
    """Infer full names, resolving Gallup surname labels against APP filenames."""
    app_names = {
        filename_subject(path, APP_PREFIX)
        for path in files
        if path.name.startswith(APP_PREFIX)
    }
    lookup: dict[Path, str] = {}

    for path in files:
        if path.name.startswith(APP_PREFIX):
            lookup[path] = filename_subject(path, APP_PREFIX)
            continue

        gallup_subject = filename_subject(path, GALLUP_PREFIX)
        base_subject = re.sub(r"\s+Second Term$", "", gallup_subject).strip()
        matches = sorted(
            name
            for name in app_names
            if name == base_subject or name.rsplit(" ", 1)[-1] == base_subject
        )
        if len(matches) == 1:
            lookup[path] = matches[0]
        elif len(base_subject.split()) >= 2 and not matches:
            lookup[path] = base_subject
        else:
            raise ValueError(
                f"Could not infer one full POTUS name for {path.name}; "
                f"candidate APP names: {matches}."
            )

    return lookup


def validate_approval_value(value: str, path: Path, row_number: int) -> str:
    """Validate a percentage while preserving its original text representation."""
    value = value.strip()
    if not value:
        raise ValueError(f"Missing approval value in {path.name}, row {row_number}.")
    try:
        numeric = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(
            f"Nonnumeric approval value {value!r} in {path.name}, row {row_number}."
        ) from error
    if not numeric.is_finite() or not Decimal("0") <= numeric <= Decimal("100"):
        raise ValueError(
            f"Approval value outside 0–100 in {path.name}, row {row_number}: {value}"
        )
    return value


def parse_app_date(value: str, path: Path, row_number: int) -> date:
    """Parse an APP date with either a four- or two-digit year."""
    value = value.strip()
    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid APP date {value!r} in {path.name}, row {row_number}.")


def parse_gallup_date_range(
    value: str, path: Path, row_number: int
) -> tuple[date, date]:
    """Parse Gallup ranges such as '2024 Dec 2-18' or '2022 Nov 9-Dec 2'."""
    match = GALLUP_DATE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(
            f"Invalid Gallup date range {value!r} in {path.name}, row {row_number}."
        )

    year = int(match.group("year"))
    start_month_name = match.group("start_month")
    end_month_name = match.group("end_month") or start_month_name
    if start_month_name not in MONTH_NUMBERS or end_month_name not in MONTH_NUMBERS:
        raise ValueError(
            f"Invalid Gallup month in {value!r}, {path.name}, row {row_number}."
        )

    start_month = MONTH_NUMBERS[start_month_name]
    end_month = MONTH_NUMBERS[end_month_name]
    end_year = year + int(end_month < start_month)
    try:
        start_date = date(year, start_month, int(match.group("start_day")))
        end_date = date(end_year, end_month, int(match.group("end_day")))
    except ValueError as error:
        raise ValueError(
            f"Invalid Gallup calendar date in {value!r}, "
            f"{path.name}, row {row_number}."
        ) from error

    duration = (end_date - start_date).days
    if duration < 0 or duration > 62:
        raise ValueError(
            f"Implausible Gallup date range {value!r} in {path.name}, "
            f"row {row_number}."
        )
    return start_date, end_date


def output_record(
    start_date: date,
    end_date: date,
    approving: str,
    disapproving: str,
    unsure: str,
    potus: str,
    source_name: str,
    source_row: int,
) -> dict[str, object]:
    """Create one standardized record with internal sorting/audit fields."""
    if end_date < start_date:
        raise ValueError(
            f"End date precedes start date in {source_name}, row {source_row}."
        )
    return {
        "Start Date": start_date.strftime("%m/%d/%Y"),
        "End Date": end_date.strftime("%m/%d/%Y"),
        "Approving": approving,
        "Disapproving": disapproving,
        "Unsure": unsure,
        "POTUS": potus,
        "_start_sort": start_date,
        "_end_sort": end_date,
        "_source": source_name,
        "_source_row": source_row,
    }


def clean_app_file(path: Path, potus: str) -> list[dict[str, object]]:
    """Standardize one American Presidency Project CSV without changing values."""
    raw = pd.read_csv(path, dtype="string", keep_default_na=False)
    if list(raw.columns) != APP_COLUMNS:
        raise ValueError(
            f"Unexpected APP columns in {path.name}; expected {APP_COLUMNS}, "
            f"found {list(raw.columns)}."
        )

    records = []
    for index, row in raw.iterrows():
        row_number = index + 2
        start_date = parse_app_date(row["Start Date"], path, row_number)
        end_date = parse_app_date(row["End Date"], path, row_number)
        records.append(
            output_record(
                start_date,
                end_date,
                validate_approval_value(row["Approving"], path, row_number),
                validate_approval_value(row["Disapproving"], path, row_number),
                validate_approval_value(row["Unsure/NoData"], path, row_number),
                potus,
                path.name,
                row_number,
            )
        )
    if not records:
        raise ValueError(f"APP file contains no poll observations: {path.name}")
    return records


def clean_gallup_file(
    path: Path, potus: str
) -> tuple[list[dict[str, object]], int, int]:
    """Standardize one Gallup CSV and exclude recognized non-data rows."""
    raw = pd.read_csv(path, dtype="string", keep_default_na=False, encoding="utf-8-sig")
    if list(raw.columns) != GALLUP_COLUMNS:
        raise ValueError(
            f"Unexpected Gallup columns in {path.name}; expected {GALLUP_COLUMNS}, "
            f"found {list(raw.columns)}."
        )

    records = []
    year_markers_removed = 0
    nondata_rows_removed = 0
    for index, row in raw.iterrows():
        row_number = index + 2
        label = row["X.1"].strip()
        values = [
            row["Approve"].strip(),
            row["Disapprove"].strip(),
            row["No opinion"].strip(),
        ]

        if YEAR_MARKER_PATTERN.fullmatch(label):
            if any(values):
                raise ValueError(
                    f"Year-marker row contains approval values in {path.name}, "
                    f"row {row_number}."
                )
            year_markers_removed += 1
            continue

        match = GALLUP_DATE_PATTERN.fullmatch(label)
        if match is None and all(value in {"", "%"} for value in values):
            nondata_rows_removed += 1
            continue
        if match is None:
            raise ValueError(
                f"Unrecognized Gallup row in {path.name}, row {row_number}: "
                f"{row.to_dict()}"
            )

        start_date, end_date = parse_gallup_date_range(label, path, row_number)
        records.append(
            output_record(
                start_date,
                end_date,
                validate_approval_value(values[0], path, row_number),
                validate_approval_value(values[1], path, row_number),
                validate_approval_value(values[2], path, row_number),
                potus,
                path.name,
                row_number,
            )
        )

    if not records:
        raise ValueError(f"Gallup file contains no poll observations: {path.name}")
    return records, year_markers_removed, nondata_rows_removed


def combine_and_validate(
    files: list[Path], potus_lookup: dict[Path, str]
) -> tuple[pd.DataFrame, int, int]:
    """Clean all source files, combine observations, sort, and validate output."""
    records: list[dict[str, object]] = []
    year_markers_removed = 0
    nondata_rows_removed = 0

    for path in files:
        potus = potus_lookup[path]
        if path.name.startswith(APP_PREFIX):
            records.extend(clean_app_file(path, potus))
        else:
            gallup_records, year_rows, nondata_rows = clean_gallup_file(path, potus)
            records.extend(gallup_records)
            year_markers_removed += year_rows
            nondata_rows_removed += nondata_rows

    combined = pd.DataFrame.from_records(records)
    combined = combined.sort_values(
        ["_start_sort", "_end_sort"], kind="stable"
    ).reset_index(drop=True)
    cleaned = combined[OUTPUT_COLUMNS].copy()

    if list(cleaned.columns) != OUTPUT_COLUMNS:
        raise ValueError(f"Output columns are not exactly {OUTPUT_COLUMNS}.")
    if cleaned.isna().any().any() or cleaned.eq("").any().any():
        raise ValueError("Cleaned output contains missing or empty values.")
    if cleaned.astype(str).apply(
        lambda column: column.str.fullmatch(r"<b>\d{4}</b>")
    ).any().any():
        raise ValueError("A Gallup year-marker row remains in the cleaned output.")

    parsed_start = cleaned["Start Date"].map(
        lambda value: datetime.strptime(value, "%m/%d/%Y").date()
    )
    parsed_end = cleaned["End Date"].map(
        lambda value: datetime.strptime(value, "%m/%d/%Y").date()
    )
    if (parsed_end < parsed_start).any():
        raise ValueError("At least one cleaned End Date precedes its Start Date.")
    chronological_keys = list(zip(parsed_start, parsed_end))
    if chronological_keys != sorted(chronological_keys):
        raise ValueError("Cleaned observations are not in chronological order.")
    if len(cleaned) != len(records):
        raise ValueError("Observation count changed while combining source files.")

    return cleaned, year_markers_removed, nondata_rows_removed


def write_and_verify(cleaned: pd.DataFrame, output_path: Path) -> None:
    """Write the combined CSV atomically and verify an exact textual read-back."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    try:
        cleaned.to_csv(temporary_path, index=False)
        written = pd.read_csv(temporary_path, dtype="string", keep_default_na=False)
        expected_text = {
            column: cleaned[column].astype(str).tolist() for column in OUTPUT_COLUMNS
        }
        actual_text = {
            column: written[column].tolist() for column in OUTPUT_COLUMNS
        }
        if list(written.columns) != OUTPUT_COLUMNS or actual_text != expected_text:
            raise ValueError("Written CSV did not round-trip exactly.")
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    """Create the combined presidential approval dataset."""
    repo_root = Path(__file__).resolve().parents[1]
    raw_directory, files = discover_source_files(repo_root)
    potus_lookup = build_potus_lookup(files)
    cleaned, year_markers_removed, nondata_rows_removed = combine_and_validate(
        files, potus_lookup
    )
    output_path = repo_root / OUTPUT_RELATIVE_PATH
    write_and_verify(cleaned, output_path)

    print(f"Raw directory: {raw_directory}")
    print(f"Source files combined: {len(files)}")
    print(f"Output: {output_path}")
    print(f"Columns: {', '.join(cleaned.columns)}")
    print(f"Observations: {len(cleaned)}")
    print(
        f"Coverage: {cleaned.iloc[0]['Start Date']} through "
        f"{cleaned.iloc[-1]['End Date']}"
    )
    print(f"Gallup year-marker rows removed: {year_markers_removed}")
    print(f"Gallup non-data rows removed: {nondata_rows_removed}")
    print("Date parsing and chronological ordering: PASS")
    print("All validations passed.")


if __name__ == "__main__":
    main()
