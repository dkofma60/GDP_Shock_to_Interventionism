"""Clean the first (NGDP) sheet of the SPF forecast workbook.

``YEAR`` and ``QUARTER`` are combined into a ``date`` identifier formatted as
``YYYYq#``. The NGDP forecast columns retain their workbook names, and every
literal ``#N/A`` entry is written as an empty CSV field.
"""

from __future__ import annotations

import math
from numbers import Real
from pathlib import Path
import warnings

import pandas as pd


RAW_DIRECTORY = Path("Raw Data") / "SPF NGDP forecasts"
OUTPUT_RELATIVE_PATH = (
    Path("Cleaned Data")
    / "SPF NGDP forecasts"
    / "SPF NGDP forecasts_cleaned.csv"
)
SOURCE_SHEET = "NGDP"
DATE_COLUMN = "date"


def locate_source(repo_root: Path) -> Path:
    """Locate the sole non-temporary XLSX workbook in the raw directory."""
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
            f"Expected exactly one non-temporary XLSX in {raw_directory}; "
            f"found {len(workbooks)}: {found}"
        )
    return workbooks[0]


def read_first_sheet(source_path: Path) -> pd.DataFrame:
    """Read only the workbook's first sheet and verify that it is NGDP."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cannot parse header or footer so it will be ignored",
            category=UserWarning,
            module=r"openpyxl\.worksheet\.header_footer",
        )
        workbook = pd.ExcelFile(source_path, engine="openpyxl")
        if not workbook.sheet_names or workbook.sheet_names[0] != SOURCE_SHEET:
            first_sheet = workbook.sheet_names[0] if workbook.sheet_names else "none"
            raise ValueError(
                f"Expected first sheet {SOURCE_SHEET!r}; found {first_sheet!r}."
            )
        source = pd.read_excel(
            workbook,
            sheet_name=0,
            dtype=object,
            keep_default_na=False,
        )
    if source.empty:
        raise ValueError("The NGDP sheet contains no observations.")
    return source


def clean_and_validate(source: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Build the quarterly date key and replace literal #N/A values with blanks."""
    columns = list(source.columns)
    if len(columns) < 3 or columns[:2] != ["YEAR", "QUARTER"]:
        raise ValueError(
            "Expected the NGDP sheet to begin with YEAR and QUARTER; "
            f"found {columns[:2]}."
        )
    forecast_columns = columns[2:]
    if any(not isinstance(column, str) or not column for column in forecast_columns):
        raise ValueError("Every NGDP forecast column must have a nonblank name.")

    year = pd.to_numeric(source["YEAR"], errors="raise")
    quarter = pd.to_numeric(source["QUARTER"], errors="raise")
    if year.isna().any() or quarter.isna().any():
        raise ValueError("YEAR and QUARTER may not contain missing values.")
    if not (year % 1 == 0).all() or not (quarter % 1 == 0).all():
        raise ValueError("YEAR and QUARTER must contain integers.")
    year = year.astype("int64")
    quarter = quarter.astype("int64")
    if not quarter.between(1, 4).all():
        invalid = quarter.loc[~quarter.between(1, 4)].tolist()
        raise ValueError(f"QUARTER must be between 1 and 4; found {invalid[:10]}.")

    forecasts = source[forecast_columns].copy()
    na_count = int(forecasts.eq("#N/A").sum().sum())
    unexpected_markers = sorted(
        {
            value
            for value in forecasts.to_numpy().ravel().tolist()
            if isinstance(value, str) and value.startswith("#") and value != "#N/A"
        }
    )
    if unexpected_markers:
        raise ValueError(f"Unexpected spreadsheet error markers: {unexpected_markers}")
    forecasts = forecasts.mask(forecasts.eq("#N/A"), pd.NA)

    invalid_values: list[tuple[str, object]] = []
    for column in forecast_columns:
        for value in forecasts[column].dropna():
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                invalid_values.append((column, value))
                if len(invalid_values) == 10:
                    break
        if len(invalid_values) == 10:
            break
    if invalid_values:
        raise ValueError(f"Nonnumeric NGDP forecast values found: {invalid_values}")

    cleaned = pd.DataFrame(
        {DATE_COLUMN: year.astype(str) + "q" + quarter.astype(str)}
    )
    cleaned = pd.concat([cleaned, forecasts.reset_index(drop=True)], axis=1)
    final_columns = [DATE_COLUMN, *forecast_columns]
    cleaned = cleaned[final_columns]

    if not cleaned[DATE_COLUMN].str.fullmatch(r"\d{4}q[1-4]").all():
        raise ValueError("Cleaned dates do not conform to YYYYq1-YYYYq4.")
    if cleaned[DATE_COLUMN].duplicated().any():
        duplicates = cleaned.loc[
            cleaned[DATE_COLUMN].duplicated(keep=False), DATE_COLUMN
        ].tolist()
        raise ValueError(f"Duplicate quarterly dates found: {duplicates[:10]}")
    indexes = year * 4 + quarter - 1
    if indexes.tolist() != list(range(indexes.iloc[0], indexes.iloc[-1] + 1)):
        raise ValueError("NGDP quarterly coverage is not chronological and contiguous.")
    if int(cleaned[forecast_columns].isna().sum().sum()) != na_count:
        raise ValueError("Cleaned missing values do not match the source #N/A cells.")

    return cleaned, na_count


def write_and_verify(cleaned: pd.DataFrame, output_path: Path) -> None:
    """Write the CSV atomically and confirm that missing values are empty fields."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    expected = cleaned.astype("string").fillna("")
    try:
        cleaned.to_csv(temporary_path, index=False, na_rep="", lineterminator="\n")
        written = pd.read_csv(temporary_path, dtype="string", keep_default_na=False)
        if list(written.columns) != list(cleaned.columns) or not written.equals(expected):
            raise ValueError("Written CSV did not round-trip exactly.")
        if written.eq("#N/A").any().any():
            raise ValueError("Written CSV still contains a #N/A marker.")
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    """Create the cleaned SPF NGDP forecast CSV."""
    repo_root = Path(__file__).resolve().parents[1]
    source_path = locate_source(repo_root)
    output_path = repo_root / OUTPUT_RELATIVE_PATH

    source = read_first_sheet(source_path)
    cleaned, na_count = clean_and_validate(source)
    write_and_verify(cleaned, output_path)

    print(f"Source: {source_path}")
    print(f"Source sheet: {SOURCE_SHEET}")
    print(f"Output: {output_path}")
    print(f"Columns: {', '.join(cleaned.columns)}")
    print(f"Observations: {len(cleaned)}")
    print(f"Coverage: {cleaned.iloc[0][DATE_COLUMN]} through {cleaned.iloc[-1][DATE_COLUMN]}")
    print(f"#N/A cells converted to empty fields: {na_count}")
    print("All validations passed.")


if __name__ == "__main__":
    main()
