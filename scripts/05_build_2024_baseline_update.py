from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path

from gtap_runtime import RUNGTAP_DIR

from gtap_observed_data import (
    BASE_YEAR,
    OBSERVED_DIR,
    PROJECT_DIR,
    RESULT_DIR,
    STANDARDIZED_DIR,
    pct_change,
    read_csv,
    safe_float,
    write_csv,
)


MODEL_NAME = "gtap2015_10x10"
MODEL_DIR = RUNGTAP_DIR / MODEL_NAME
BASELINE_DIR = RESULT_DIR / "05_baseline_update"


GDP_TFP_SWAP_EXOGENOUS_LINES = [
    "          pop",
    "          psaveslack pfactwld",
    "          profitslack incomeslack endwslack",
    "          cgdslack tradslack",
    "          ams atm atf ats atd",
    "          aosec aoreg avasec avareg",
    "          afcom afsec afreg afecom afesec afereg",
    "          aoall afall afeall",
    "          au dppriv dpgov dpsave",
    "          to tp tm tms tx txs",
    "          qo(ENDW_COMM,REG)",
]


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Build a pre-policy GTAP baseline update from the 2014 database to the latest observed year."
    )
    argument_parser.add_argument("--base-year", type=int, default=BASE_YEAR, help="Original GTAP database year. Default 2014.")
    argument_parser.add_argument(
        "--target-year",
        type=int,
        default=None,
        help="Updated baseline year. Defaults to the latest common GDP/pop year, currently 2024 when data are available.",
    )
    argument_parser.add_argument("--model-name", default=MODEL_NAME)
    argument_parser.add_argument("--include-tariffs", action="store_true", help="Write WITS tariff updates into generated CMF files.")
    argument_parser.add_argument(
        "--write-all-cmfs",
        action="store_true",
        help="Write one cumulative baseline-update CMF for every available target year.",
    )
    return argument_parser


def load_country_map() -> list[dict]:
    rows = read_csv(STANDARDIZED_DIR / "gtap_region_country_map.csv")
    return [row for row in rows if row.get("iso3")]


def load_metric(path: Path) -> dict[tuple[str, int], float]:
    data = {}
    for row in read_csv(path):
        value = safe_float(row.get("value"))
        if value is None:
            continue
        data[(row["iso3"], int(row["year"]))] = value
    return data


def aggregate_regions(
    country_map: list[dict],
    gdp: dict[tuple[str, int], float],
    pop: dict[tuple[str, int], float],
    tariffs: dict[tuple[str, int], float],
) -> list[dict]:
    years = sorted({year for _, year in gdp.keys()} & {year for _, year in pop.keys()})
    region_members = defaultdict(list)
    for row in country_map:
        region_members[row["gtap_region"]].append(row["iso3"])

    rows: list[dict] = []
    for region, members in sorted(region_members.items()):
        for year in years:
            gdp_values = [gdp[(iso3, year)] for iso3 in members if (iso3, year) in gdp]
            pop_values = [pop[(iso3, year)] for iso3 in members if (iso3, year) in pop]
            tariff_weighted = []
            tariff_weights = []
            for iso3 in members:
                key = (iso3, year)
                if key in tariffs and key in gdp:
                    tariff_weighted.append(tariffs[key] * gdp[key])
                    tariff_weights.append(gdp[key])
            if not gdp_values or not pop_values:
                continue
            rows.append(
                {
                    "gtap_region": region,
                    "year": year,
                    "gdp_constant": sum(gdp_values),
                    "population": sum(pop_values),
                    "tariff_ahs_weighted_avg": (
                        sum(tariff_weighted) / sum(tariff_weights) if tariff_weights else None
                    ),
                    "countries_with_gdp": len(gdp_values),
                    "countries_with_population": len(pop_values),
                    "countries_with_tariff": len(tariff_weights),
                }
            )
    return rows


def compute_baseline_targets(region_rows: list[dict], base_year: int) -> tuple[list[dict], list[dict]]:
    by_region_year = {(row["gtap_region"], int(row["year"])): row for row in region_rows}
    annual: list[dict] = []
    cumulative: list[dict] = []

    for row in region_rows:
        region = row["gtap_region"]
        year = int(row["year"])
        previous = by_region_year.get((region, year - 1))
        base = by_region_year.get((region, base_year))

        def update_record(reference: dict | None, update_type: str) -> dict | None:
            if reference is None or year <= int(reference["year"]):
                return None
            pop_shock = pct_change(row["population"], reference["population"])
            gdp_shock = pct_change(row["gdp_constant"], reference["gdp_constant"])
            tariff_ref = reference.get("tariff_ahs_weighted_avg")
            tariff_now = row.get("tariff_ahs_weighted_avg")
            tariff_rate_pct = pct_change(tariff_now, tariff_ref)
            return {
                "update_type": update_type,
                "gtap_region": region,
                "base_year": int(reference["year"]),
                "target_year": year,
                "pop_pct": pop_shock,
                "gdp_pct": gdp_shock,
                "gdp_target_variable": "qgdp",
                "tfp_endogenous_variable": "avareg",
                "tariff_rate_change_pct": tariff_rate_pct,
                "tariff_base": tariff_ref,
                "tariff_target": tariff_now,
            }

        annual_record = update_record(previous, "annual")
        cumulative_record = update_record(base, "cumulative")
        if annual_record:
            annual.append(annual_record)
        if cumulative_record:
            cumulative.append(cumulative_record)
    return annual, cumulative


def format_number(value: float | None) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return ""
    return f"{value:.6f}"


def write_baseline_csvs(region_rows: list[dict], annual: list[dict], cumulative: list[dict]) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(
        BASELINE_DIR / "region_macro_panel.csv",
        region_rows,
        [
            "gtap_region",
            "year",
            "gdp_constant",
            "population",
            "tariff_ahs_weighted_avg",
            "countries_with_gdp",
            "countries_with_population",
            "countries_with_tariff",
        ],
    )
    fieldnames = [
        "update_type",
        "gtap_region",
        "base_year",
        "target_year",
        "pop_pct",
        "gdp_pct",
        "gdp_target_variable",
        "tfp_endogenous_variable",
        "tariff_rate_change_pct",
        "tariff_base",
        "tariff_target",
    ]
    write_csv(BASELINE_DIR / "annual_baseline_targets.csv", annual, fieldnames)
    write_csv(BASELINE_DIR / "cumulative_baseline_targets.csv", cumulative, fieldnames)


def cmf_text(records: list[dict], base_year: int, target_year: int, model_name: str, include_tariffs: bool) -> str:
    model_dir = RUNGTAP_DIR / model_name
    exogenous_lines = GDP_TFP_SWAP_EXOGENOUS_LINES.copy()
    exogenous_lines[-1] = f"{exogenous_lines[-1]} ;"
    lines = [
        f"! GTAP baseline update: {base_year} database to {target_year} baseline",
        "! This is a pre-policy baseline update. User policy shocks should be applied after this baseline.",
        "! Sources: World Bank GDP; UN WPP population when configured, otherwise World Bank population fallback; WITS tariffs when fetched.",
        "! Closure: standard multiregion GE closure plus GEMPACK swap statements for GDP targeting.",
        "! Swap: avareg(REG) is swapped with qgdp(REG); qgdp is then shocked to observed real GDP growth and avareg solves as implied regional TFP.",
        "! Standard endowment quantities qo(ENDW_COMM,REG) remain exogenous and unshocked unless a separate factor-endowment scenario is supplied.",
        f"! Model directory: {model_dir}",
        "check-on-read all = warn ;",
        f"aux files = {RUNGTAP_DIR}\\GTAP;",
        f"file gtapSETS = {model_dir}\\sets.har;",
        f"file gtapDATA = {model_dir}\\basedata.har;",
        "Updated file gtapDATA = gdata.upd;",
        f"Solution file = {RUNGTAP_DIR}\\work\\GTAP;",
        f"file gtapPARM = {model_dir}\\default.prm;",
        "Verbal Description =",
        f"Baseline update {base_year}-{target_year} using observed GDP and population;",
        "Method = Gragg;",
        "Steps = 4 8 12;",
        "automatic accuracy = no;",
        "subintervals = 10;",
        "exogenous",
        *exogenous_lines,
        "Rest Endogenous ;",
        "",
    ]
    for record in sorted(records, key=lambda item: item["gtap_region"]):
        if record.get("gdp_pct") is not None:
            region = record["gtap_region"]
            lines.append(f'Swap avareg("{region}") = qgdp("{region}");')
    lines.append("")
    for record in sorted(records, key=lambda item: item["gtap_region"]):
        region = record["gtap_region"]
        if record.get("pop_pct") is not None:
            lines.append(f'Shock pop("{region}") = {record["pop_pct"]:.6f};')
        if record.get("gdp_pct") is not None:
            lines.append(f'Shock qgdp("{region}") = {record["gdp_pct"]:.6f};')
        if include_tariffs and record.get("tariff_rate_change_pct") is not None:
            lines.append(
                f'Shock tms(PROD_COMM,REG,"{region}") = rate% {record["tariff_rate_change_pct"]:.6f} from file tms.shk;'
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parser().parse_args()
    model_name = args.model_name

    country_map = load_country_map()
    gdp = load_metric(STANDARDIZED_DIR / "world_bank_gdp_constant.csv")
    pop = load_metric(STANDARDIZED_DIR / "population.csv")
    tariffs = load_metric(STANDARDIZED_DIR / "wits_tariff_all_products.csv")

    if not country_map or not gdp or not pop:
        raise RuntimeError(f"Missing standardized data. Run {PROJECT_DIR / 'scripts' / '04_fetch_observed_calibration_data.py'} first.")

    region_rows = aggregate_regions(country_map, gdp, pop, tariffs)
    annual, cumulative = compute_baseline_targets(region_rows, args.base_year)
    write_baseline_csvs(region_rows, annual, cumulative)

    available_targets = sorted({int(row["target_year"]) for row in cumulative})
    if not available_targets:
        raise RuntimeError("No cumulative baseline updates could be built.")

    target_year = args.target_year or max(available_targets)
    target_records = [row for row in cumulative if int(row["target_year"]) == target_year]
    if not target_records:
        raise RuntimeError(f"No cumulative baseline records for target year {target_year}. Available: {available_targets}")

    cmf_dir = BASELINE_DIR / "cmf"
    cmf_dir.mkdir(parents=True, exist_ok=True)
    if args.write_all_cmfs:
        for year in available_targets:
            records = [row for row in cumulative if int(row["target_year"]) == year]
            (cmf_dir / f"baseline_update_{args.base_year}_to_{year}.cmf").write_text(
                cmf_text(records, args.base_year, year, model_name, args.include_tariffs),
                encoding="utf-8",
            )

    target_cmf = cmf_dir / f"baseline_update_{args.base_year}_to_{target_year}.cmf"
    target_cmf.write_text(
        cmf_text(target_records, args.base_year, target_year, model_name, args.include_tariffs),
        encoding="utf-8",
    )

    (BASELINE_DIR / "baseline_update_summary.txt").write_text(
        "\n".join(
            [
                f"Base year: {args.base_year}",
                f"Target year: {target_year}",
                f"Available target years: {', '.join(map(str, available_targets))}",
                "Role: pre-policy baseline update",
                "Closure: standard multiregion GE plus GEMPACK swap statements",
                "GDP target: qgdp(REG) exogenous",
                "Implied TFP adjustment: avareg(REG) endogenous",
                "Endowment quantities: qo(ENDW_COMM,REG) left at the standard exogenous closure with no GDP-proxy shock",
                f"Include tariffs in CMF: {args.include_tariffs}",
                f"Target CMF: {target_cmf}",
                f"Annual baseline targets: {BASELINE_DIR / 'annual_baseline_targets.csv'}",
                f"Cumulative baseline targets: {BASELINE_DIR / 'cumulative_baseline_targets.csv'}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"OK: baseline update panel written to {BASELINE_DIR}")
    print(f"OK: target baseline CMF written to {target_cmf}")


if __name__ == "__main__":
    main()
