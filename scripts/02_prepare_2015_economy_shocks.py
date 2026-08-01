from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from gtap_runtime import RUNGTAP_DIR

PROJECT_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = PROJECT_DIR / "result"
MODEL_NAME = "gtap2015_10x10"
MODEL_DIR = RUNGTAP_DIR / MODEL_NAME

SCENARIO_RESULT_DIR = RESULT_DIR / "02_shocks"
SHOCK_CSV = SCENARIO_RESULT_DIR / "scenario_2015_shocks.csv"
SCENARIO_CMF = SCENARIO_RESULT_DIR / "scenario_2015_gtap.cmf"


# Percent changes from the 2014 base to a stylized 2015 economy.
# pop: regional population growth.
# endowment: broad factor-endowment growth proxy, applied to all ENDW_COMM.
# productivity: economy-wide technology/productivity shock, applied to all PROD_COMM.
#
# This is deliberately simple because the requested aggregation is coarse. It
# captures strong Asia growth, slow EU growth, weak Latin America/RoW conditions,
# and high population growth in SSA/MENA in 2015.
SHOCKS = [
    {"region": "Oceania", "pop": 1.45, "endowment": 2.00, "productivity": 0.50},
    {"region": "EastAsia", "pop": 0.45, "endowment": 3.00, "productivity": 1.60},
    {"region": "SEAsia", "pop": 1.25, "endowment": 4.00, "productivity": 1.20},
    {"region": "SouthAsia", "pop": 1.45, "endowment": 5.00, "productivity": 2.20},
    {"region": "NAmerica", "pop": 0.80, "endowment": 2.50, "productivity": 0.80},
    {"region": "LatinAmer", "pop": 1.05, "endowment": 0.50, "productivity": -1.00},
    {"region": "EU_28", "pop": 0.20, "endowment": 1.50, "productivity": 0.50},
    {"region": "MENA", "pop": 2.00, "endowment": 2.00, "productivity": -0.50},
    {"region": "SSA", "pop": 2.70, "endowment": 3.00, "productivity": 0.50},
    {"region": "RestofWorld", "pop": 0.80, "endowment": 0.50, "productivity": -1.50},
]


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required file not found: {path}")


def write_shock_csv() -> None:
    SCENARIO_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    with SHOCK_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["region", "pop", "endowment", "productivity"])
        writer.writeheader()
        writer.writerows(SHOCKS)


def build_cmf() -> str:
    lines = [
        "! Scenario: stylized 2015 economy from GTAP10A GTAP-APT 2014 baseline",
        f"! Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"! Model directory: {MODEL_DIR}",
        "check-on-read all = warn ;",
        f"aux files = {RUNGTAP_DIR}\\GTAP;",
        f"file gtapSETS = {MODEL_DIR}\\sets.har;",
        f"file gtapDATA = {MODEL_DIR}\\basedata.har;",
        "Updated file gtapDATA = gdata.upd;",
        f"Solution file = {RUNGTAP_DIR}\\work\\GTAP;",
        f"file gtapPARM = {MODEL_DIR}\\default.prm;",
        "Verbal Description =",
        "2015 stylized economy: population, endowment, and productivity growth;",
        "Method = Gragg;",
        "Steps = 2 4 6;",
        "automatic accuracy = no;",
        "subintervals = 1;",
        "exogenous",
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
        "          qo(ENDW_COMM,REG) ;",
        "Rest Endogenous ;",
        "",
    ]

    for shock in SHOCKS:
        region = shock["region"]
        lines.append(f'Shock pop("{region}") = {shock["pop"]:.2f};')
        lines.append(f'Shock qo(ENDW_COMM,"{region}") = uniform {shock["endowment"]:.2f};')
        lines.append(f'Shock aoall(PROD_COMM,"{region}") = uniform {shock["productivity"]:.2f};')
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    require_file(MODEL_DIR / "basedata.har")
    require_file(MODEL_DIR / "sets.har")
    require_file(MODEL_DIR / "default.prm")

    write_shock_csv()
    cmf_text = build_cmf()
    SCENARIO_CMF.write_text(cmf_text + "\n", encoding="utf-8")

    summary = [
        "Scenario: stylized 2015 economy from 2014 GTAP baseline",
        "Shock units: percent change",
        f"Shock CSV: {SHOCK_CSV}",
        f"GTAP command file: {SCENARIO_CMF}",
        "Applied shocks: pop(REG), qo(ENDW_COMM,REG), aoall(PROD_COMM,REG)",
    ]
    (SCENARIO_RESULT_DIR / "shock_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"OK: wrote shock table to {SHOCK_CSV}")
    print(f"OK: wrote GTAP CMF to {SCENARIO_CMF}")


if __name__ == "__main__":
    main()
