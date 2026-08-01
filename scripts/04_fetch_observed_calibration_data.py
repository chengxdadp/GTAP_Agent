from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from gtap_observed_data import (
    BASE_YEAR,
    OBSERVED_DIR,
    STANDARDIZED_DIR,
    WITS_TARIFF,
    FetchConfig,
    env_un_token,
    ensure_dirs,
    latest_non_null_year,
    parse_gtapagg_mapping,
    read_csv,
    resolve_gtapagg_mapping,
    un_wpp_population_from_api,
    un_wpp_population_from_file,
    wits_tariff_series,
    world_bank_indicator,
    write_csv,
    write_manifest,
    WORLD_BANK_GDP,
    WORLD_BANK_POP,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Incrementally fetch GDP, population, and WITS tariff calibration data for GTAP shocks."
    )
    argument_parser.add_argument("--start-year", type=int, default=BASE_YEAR)
    argument_parser.add_argument("--end-year", type=int, default=None, help="Defaults to the latest available GDP year.")
    argument_parser.add_argument("--refresh", action="store_true", help="Ignore cached raw API responses.")
    argument_parser.add_argument("--skip-wits", action="store_true", help="Skip WITS tariff calls.")
    argument_parser.add_argument("--max-wits-calls", type=int, default=None, help="Limit WITS calls for a smoke test.")
    argument_parser.add_argument("--wits-sleep", type=float, default=0.05, help="Seconds to sleep between WITS calls.")
    argument_parser.add_argument("--un-token", default=env_un_token(), help="UN Data Portal bearer token.")
    argument_parser.add_argument("--wpp-file", type=Path, default=None, help="Local WPP CSV/XLSX file to use instead of API.")
    argument_parser.add_argument(
        "--mapping-file",
        type=Path,
        default=None,
        help="GTAPAgg mapping used to build region/sector lookup tables. Defaults to the project default mapping.",
    )
    return argument_parser


def main() -> None:
    args = parser().parse_args()
    ensure_dirs()

    mapping_file = (args.mapping_file or resolve_gtapagg_mapping()).resolve()
    if not mapping_file.is_file():
        raise FileNotFoundError(f"Aggregation mapping not found: {mapping_file}")
    sectors, regions = parse_gtapagg_mapping(mapping_file)
    country_rows = [row for row in regions if row["iso3"]]
    countries = sorted({row["iso3"] for row in country_rows})

    first_end = args.end_year or datetime.now().year
    gdp_rows = world_bank_indicator(countries, WORLD_BANK_GDP, args.start_year, first_end, args.refresh)
    inferred_end = latest_non_null_year(gdp_rows) or first_end
    end_year = args.end_year or inferred_end
    if args.end_year is None and end_year != first_end:
        gdp_rows = [row for row in gdp_rows if row["year"] <= end_year]

    pop_rows = []
    population_source = "world_bank_fallback"
    if args.wpp_file:
        pop_rows = un_wpp_population_from_file(args.wpp_file, countries, args.start_year, end_year)
        population_source = "un_wpp_file"
    elif args.un_token:
        pop_rows = un_wpp_population_from_api(countries, args.start_year, end_year, args.un_token, args.refresh)
        population_source = "un_wpp_api"

    if not pop_rows:
        pop_rows = world_bank_indicator(countries, WORLD_BANK_POP, args.start_year, end_year, args.refresh)
        population_source = "world_bank_population_fallback"

    tariff_rows = []
    if not args.skip_wits:
        existing_tariffs = [] if args.refresh else read_csv(STANDARDIZED_DIR / "wits_tariff_all_products.csv")
        existing_keys = {
            (row["iso3"], int(row["year"]))
            for row in existing_tariffs
            if row.get("iso3") and row.get("year")
        }
        tariff_rows = wits_tariff_series(
            countries,
            args.start_year,
            end_year,
            refresh=args.refresh,
            sleep_seconds=args.wits_sleep,
            max_calls=args.max_wits_calls,
            existing_keys=existing_keys,
        )
        tariff_rows = existing_tariffs + tariff_rows

    write_csv(
        STANDARDIZED_DIR / "gtap_region_country_map.csv",
        regions,
        ["gtap_code", "iso3", "country_name", "gtap_region"],
    )
    write_csv(
        STANDARDIZED_DIR / "gtap_sector_map.csv",
        sectors,
        ["gtap_sector", "gtap_sector_name", "gtap_sector_agg"],
    )
    write_csv(
        STANDARDIZED_DIR / "world_bank_gdp_constant.csv",
        gdp_rows,
        ["iso3", "country_name", "year", "indicator", "value", "source"],
    )
    write_csv(
        STANDARDIZED_DIR / "population.csv",
        pop_rows,
        ["iso3", "country_name", "year", "indicator", "value", "source"],
    )
    write_csv(
        STANDARDIZED_DIR / "wits_tariff_all_products.csv",
        tariff_rows,
        ["iso3", "year", "indicator", "value", "source"],
    )

    write_manifest(
        OBSERVED_DIR / "fetch_manifest.json",
        [
            {
                "name": "GTAP aggregation mapping",
                "source": str(mapping_file),
                "indicator": "region and sector aggregation",
                "rows": len(regions) + len(sectors),
            },
            {
                "name": "GDP constant",
                "source": "World Bank DataBank / API",
                "indicator": WORLD_BANK_GDP,
                "rows": len(gdp_rows),
                "start_year": args.start_year,
                "end_year": end_year,
            },
            {
                "name": "Population",
                "source": population_source,
                "indicator": "WPP_TOTAL_POPULATION or SP.POP.TOTL",
                "rows": len(pop_rows),
                "start_year": args.start_year,
                "end_year": end_year,
            },
            {
                "name": "Tariff weighted average",
                "source": "WITS SDMX API",
                "indicator": WITS_TARIFF,
                "rows": len(tariff_rows),
                "start_year": args.start_year,
                "end_year": end_year,
                "skipped": args.skip_wits,
            },
        ],
    )

    print(f"OK: standardized data written to {STANDARDIZED_DIR}")
    print(f"Years: {args.start_year}-{end_year}")
    print(f"GDP rows: {len(gdp_rows)}")
    print(f"Population rows: {len(pop_rows)} ({population_source})")
    print(f"WITS tariff rows: {len(tariff_rows)}")
    print(f"Aggregation mapping: {mapping_file}")


if __name__ == "__main__":
    main()
