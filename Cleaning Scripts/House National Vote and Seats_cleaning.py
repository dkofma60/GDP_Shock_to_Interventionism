"""Clean the national House vote-and-seat series.

The sole transformation is to retain every column to the left of
``ChangePctVotes`` and delete ``ChangePctVotes`` itself and all columns after
it. Source values and row order are otherwise preserved exactly.
"""

from __future__ import annotations

import csv
from pathlib import Path


RAW_DIRECTORY = Path("Raw Data") / "House National Vote and Seats"
OUTPUT_RELATIVE_PATH = (
    Path("Cleaned Data")
    / "House National Vote and Seats"
    / "House National Vote and Seats_cleaned.csv"
)
METADATA_FILENAME = "Access Metadata.csv"
CUTOFF_COLUMN = "ChangePctVotes"


def locate_source(repo_root: Path) -> Path:
    """Locate the one non-metadata CSV in the raw-data directory."""
    raw_directory = repo_root / RAW_DIRECTORY
    if not raw_directory.is_dir():
        raise FileNotFoundError(f"Raw data directory not found: {raw_directory}")

    sources = sorted(
        path
        for path in raw_directory.glob("*.csv")
        if path.is_file() and path.name != METADATA_FILENAME
    )
    if len(sources) != 1:
        found = ", ".join(path.name for path in sources) or "none"
        raise FileNotFoundError(
            f"Expected exactly one non-metadata CSV in {raw_directory}; "
            f"found {len(sources)}: {found}"
        )
    return sources[0]


def clean_rows(source_path: Path) -> tuple[list[str], list[list[str]]]:
    """Read the source and remove the cutoff column and every column after it."""
    with source_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.reader(source_file)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError(f"Source CSV is empty: {source_path}") from error
        rows = list(reader)

    if header.count(CUTOFF_COLUMN) != 1:
        raise ValueError(
            f"Expected exactly one {CUTOFF_COLUMN!r} column; found "
            f"{header.count(CUTOFF_COLUMN)}."
        )
    if not rows:
        raise ValueError("Source CSV contains no data rows.")

    source_width = len(header)
    malformed_rows = [
        row_number
        for row_number, row in enumerate(rows, start=2)
        if len(row) != source_width
    ]
    if malformed_rows:
        raise ValueError(
            "Source rows do not match the header width; first affected rows: "
            f"{malformed_rows[:10]}"
        )

    cutoff_index = header.index(CUTOFF_COLUMN)
    cleaned_header = header[:cutoff_index]
    cleaned_rows = [row[:cutoff_index] for row in rows]
    if not cleaned_header:
        raise ValueError(f"No columns occur before {CUTOFF_COLUMN!r}.")

    return cleaned_header, cleaned_rows


def write_and_verify(
    header: list[str], rows: list[list[str]], output_path: Path
) -> None:
    """Write the cleaned CSV atomically and verify an exact read-back."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.writer(output_file, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)

        with temporary_path.open("r", encoding="utf-8", newline="") as check_file:
            written = list(csv.reader(check_file))
        if written != [header, *rows]:
            raise ValueError("Written CSV did not round-trip exactly.")

        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    """Create the cleaned House national vote-and-seat CSV."""
    repo_root = Path(__file__).resolve().parents[1]
    source_path = locate_source(repo_root)
    output_path = repo_root / OUTPUT_RELATIVE_PATH

    header, rows = clean_rows(source_path)
    write_and_verify(header, rows, output_path)

    print(f"Source: {source_path}")
    print(f"Output: {output_path}")
    print(f"Columns: {', '.join(header)}")
    print(f"Observations: {len(rows)}")
    print(f"Removed {CUTOFF_COLUMN!r} and every column to its right.")
    print("All validations passed.")


if __name__ == "__main__":
    main()
