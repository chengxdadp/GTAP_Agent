from __future__ import annotations

import shutil
import subprocess
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from gtap_runtime import RUNGTAP_DIR

PROJECT_DIR = Path(__file__).resolve().parents[1]
RESULT_DIR = PROJECT_DIR / "result"
ASSET_DIR = PROJECT_DIR / "asset"
WORK_DIR = RUNGTAP_DIR / "work"
MODEL_NAME = "gtap2015_10x10"
MODEL_DIR = RUNGTAP_DIR / MODEL_NAME
EXTRA_SOLUTION_MAP_ENTRIES = [("AVRG", "avareg")]

SCENARIO_CMF = RESULT_DIR / "05_baseline_update" / "cmf" / "baseline_update_2014_to_2024.cmf"
RUN_RESULT_DIR = RESULT_DIR / "03_run_baseline_2024"


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Run a scripted RunGTAP/GEMPACK scenario and collect outputs.")
    argument_parser.add_argument(
        "--cmf",
        type=Path,
        default=SCENARIO_CMF,
        help="Scenario GTAP.cmf file to run. Defaults to the 2024 baseline-update CMF.",
    )
    argument_parser.add_argument("--model-name", default=MODEL_NAME, help="RunGTAP model directory name.")
    argument_parser.add_argument("--result-dir", type=Path, default=RUN_RESULT_DIR, help="Directory for collected outputs.")
    argument_parser.add_argument(
        "--set-as-default-baseline",
        action="store_true",
        help="After a successful baseline run, set gdata.upd and newrate.har as the project's default 2024 baseline.",
    )
    return argument_parser


def require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Required file not found: {path}")


def require_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Required directory not found: {path}")


def portable_scenario_text(text: str) -> str:
    """Rebind older CMFs to the selected project-local runtime and model."""
    substitutions = [
        (r"(?im)^[ \t]*![ \t]*Model directory:[ \t]*.+$", f"! Model directory: {MODEL_DIR}"),
        (r"(?im)^[ \t]*aux files[ \t]*=[ \t]*.+?[\\/]+GTAP[ \t]*;", f"aux files = {RUNGTAP_DIR}\\GTAP;"),
        (r"(?im)^[ \t]*file[ \t]+gtapSETS[ \t]*=[ \t]*.+?[\\/]+sets\.har[ \t]*;", f"file gtapSETS = {MODEL_DIR}\\sets.har;"),
        (r"(?im)^[ \t]*file[ \t]+gtapPARM[ \t]*=[ \t]*.+?[\\/]+default\.prm[ \t]*;", f"file gtapPARM = {MODEL_DIR}\\default.prm;"),
        (r"(?im)^[ \t]*Solution file[ \t]*=[ \t]*.+?[\\/]+work[\\/]+GTAP[ \t]*;", f"Solution file = {WORK_DIR}\\GTAP;"),
        (
            r"(?im)^[ \t]*file[ \t]+gtapDATA[ \t]*=[ \t]*.+?[\\/]+basedata\.har[ \t]*;",
            f"file gtapDATA = {MODEL_DIR}\\basedata.har;",
        ),
    ]
    updated = text
    for pattern, replacement in substitutions:
        updated = re.sub(pattern, lambda _: replacement, updated)
    return updated


def ensure_inside(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise RuntimeError(f"Refusing to operate outside {parent_resolved}: {child_resolved}")


def clean_previous_work_outputs() -> None:
    patterns = [
        "*.flg",
        "GTAP.*",
        "gdata.upd",
        "newview.har",
        "newrate.har",
        "decomp.*",
        "GTAPVol.*",
        "Shocks.log",
        "AlterPar.log",
        "PEELAST.log",
        "GTAPVu2.log",
        "SEENVSHK.log",
        "SEENVSHK.flg",
        "SLTOHTSH.log",
        "SLTOHTSH.flg",
        "sltohta.log",
        "sltohta.flg",
        "sldecomp.log",
        "sldecomp.flg",
        "rpt.log",
        "GTAP_agent.map",
    ]
    ensure_inside(WORK_DIR, RUNGTAP_DIR)
    for pattern in patterns:
        for path in WORK_DIR.glob(pattern):
            if path.is_file():
                path.unlink()


def write_solution_map() -> Path:
    base_map = RUNGTAP_DIR / "GTAP.map"
    require_file(base_map)
    lines = base_map.read_text(encoding="utf-8", errors="replace").splitlines()
    existing_variables = {
        parts[1].lower()
        for parts in (line.split() for line in lines)
        if len(parts) >= 2 and not parts[0].startswith("!")
    }
    for header, variable in EXTRA_SOLUTION_MAP_ENTRIES:
        if variable.lower() not in existing_variables:
            lines.append(f"{header} {variable}")
    map_path = WORK_DIR / "GTAP_agent.map"
    map_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return map_path


def write_stored_inputs() -> None:
    solution_map = write_solution_map()
    (WORK_DIR / "GTAPrpt.sti").write_text("bat\nrpt\nGTAP.RPT\n\n", encoding="utf-8")
    (WORK_DIR / "sltohta.inp").write_text(
        "\n".join(
            [
                "bat",
                "wsi",
                "SHL",
                "",
                "GTAP",
                "t",
                "y",
                str(solution_map),
                "GTAP.sli",
                "GTAP.sol",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (WORK_DIR / "seenvshk.inp").write_text(
        "\n".join(
            [
                "bat",
                "",
                "s   ! Solution file",
                f"{WORK_DIR}\\GTAP",
                "s   ! Output SS map file",
                f"{WORK_DIR}\\ssashk.map",
                "x   ! exog vars in map",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (WORK_DIR / "sltohtsh.inp").write_text(
        "\n".join(
            [
                "bat",
                "shk",
                "nlv",
                "",
                f"{WORK_DIR}\\GTAP",
                "c",
                "y  ! SS map file to use",
                f"{WORK_DIR}\\ssashk.map",
                f"{WORK_DIR}\\ssashk.lis",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (WORK_DIR / "sldecomp.inp").write_text(
        "\n".join(
            [
                "bat",
                "nlv",
                "",
                "GTAP",
                "c",
                "y",
                f"{RUNGTAP_DIR}\\DECOMP.map",
                "decomp.sol",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_auxiliary_cmfs() -> None:
    (WORK_DIR / "Shocks.cmf").write_text(
        "\n".join(
            [
                "! Generated for scripted RunGTAP scenario",
                "check-on-read all = warn ;",
                f"aux files = {RUNGTAP_DIR}\\SHOCKS;",
                "display file = SHOCKS.DIS;",
                f"file gtapsets = {MODEL_DIR}\\sets.har;",
                f"file gtapdata = {MODEL_DIR}\\basedata.har;",
                f"file toHAT = {MODEL_DIR}\\to.shk;",
                f"file txsHAT = {MODEL_DIR}\\txs.shk;",
                f"file tmsHAT = {MODEL_DIR}\\tms.shk;",
                f"file tfHAT = {MODEL_DIR}\\tf.shk;",
                f"file tgiHAT = {MODEL_DIR}\\tgm.shk;",
                f"file tgdHAT = {MODEL_DIR}\\tgd.shk;",
                f"file tfiHAT = {MODEL_DIR}\\tfm.shk;",
                f"file tfdHAT = {MODEL_DIR}\\tfd.shk;",
                f"file tpiHAT = {MODEL_DIR}\\tpm.shk;",
                f"file tpdHAT = {MODEL_DIR}\\tpd.shk;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (WORK_DIR / "alterpar.cmf").write_text(
        "\n".join(
            [
                "! Generated for scripted RunGTAP scenario",
                "check-on-read all = warn ;",
                f"aux files = {RUNGTAP_DIR}\\ALTPAR;",
                f"file gtapsets = {MODEL_DIR}\\sets.har;",
                f"file alterpar = {MODEL_DIR}\\altertax.prm;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (WORK_DIR / "PeeLast.cmf").write_text(
        "\n".join(
            [
                "! Generated for scripted RunGTAP scenario",
                f"aux files = {RUNGTAP_DIR}\\PEELAS;",
                f"file gtapsets = {MODEL_DIR}\\sets.har;",
                f"file gtapdata = {MODEL_DIR}\\basedata.har;",
                f"file gtapparm = {MODEL_DIR}\\default.prm;",
                f"file ELASTICITIES = {MODEL_DIR}\\default.pel;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (WORK_DIR / "GTAPVu2.cmf").write_text(
        "\n".join(
            [
                "! Generated for scripted RunGTAP scenario",
                f"aux files = {RUNGTAP_DIR}\\GTPVEW;",
                f"file gtapsets = {MODEL_DIR}\\sets.har;",
                f"file gtapparm = {MODEL_DIR}\\default.prm;",
                "file gtapDATA = gdata.upd;",
                "file gtapVIEW = newview.har;",
                "file TaxRates = newrate.har;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (WORK_DIR / "decomp.cmf").write_text(
        "\n".join(
            [
                "! Generated for scripted RunGTAP scenario",
                "check-on-read all = warn ;",
                f"aux files = {RUNGTAP_DIR}\\DECOMP;",
                f"file gtapsets = {MODEL_DIR}\\sets.har;",
                "file gtapsol = decomp.sol;",
                f"file gtapbase = {MODEL_DIR}\\basedata.har;",
                "file gtapupdate = gdata.upd;",
                "file welview = decomp.har;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (WORK_DIR / "GTAPVol.cmf").write_text(
        "\n".join(
            [
                "! Generated for scripted RunGTAP scenario",
                "check-on-read all = warn ;",
                f"aux files = {RUNGTAP_DIR}\\GTPVOL;",
                f"file gtapsets = {MODEL_DIR}\\sets.har;",
                f"file gtapdata = {MODEL_DIR}\\basedata.har;",
                "file gsltohar  = decomp.sol;",
                "file outfile = GTAPVol.har;",
                "display file = gtapvol.dis;",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_batch_file() -> Path:
    save_sims = MODEL_DIR / "SaveSims"
    save_sims.mkdir(exist_ok=True)
    alterpar_lines = []
    if (MODEL_DIR / "altertax.prm").is_file():
        alterpar_lines = [
            f'"{RUNGTAP_DIR}\\ALTPAR.exe" -cmf AlterPar.cmf -los AlterPar.log',
            "if errorlevel 1 goto error4",
            "if exist cancel.flg goto endbat",
        ]
    peelast_lines = []
    if (MODEL_DIR / "default.pel").is_file():
        peelast_lines = [
            f'"{RUNGTAP_DIR}\\PEELAS.exe" -cmf PEELAST.cmf -los PEELAST.log',
            "if errorlevel 1 goto error6",
            "if exist cancel.flg goto endbat",
        ]
    batch_path = WORK_DIR / "RunGTAP_scenario.bat"
    batch_path.write_text(
        "\n".join(
            [
                "@echo off",
                "chcp 936",
                f'cd /d "{WORK_DIR}"',
                "del gtap.rpt 2>nul",
                f'"{RUNGTAP_DIR}\\GTAP.EXE" -sti GTAPrpt.sti >rpt.log',
                f'del "{save_sims}\\GTAP.*" 2>nul',
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\SHOCKS.exe" -cmf Shocks.cmf -los Shocks.log',
                "if errorlevel 1 goto error3",
                "if exist cancel.flg goto endbat",
                *alterpar_lines,
                *peelast_lines,
                f'"{RUNGTAP_DIR}\\GTAP.exe" -cmf GTAP.cmf -los GTAP.log',
                "if errorlevel 1 goto error7",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\sltohta.exe" -sti sltohta.inp >sltohta.log',
                "if errorlevel 1 goto error8",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\seenva.exe" -sti SEENVSHK.inp >SEENVSHK.log',
                "if errorlevel 1 goto error9",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\sltohta.exe" -sti SLTOHTSH.inp >SLTOHTSH.log',
                "if errorlevel 1 goto error10",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\GTPVEW.exe" -cmf GTAPVu2.cmf -los GTAPVu2.log',
                "if errorlevel 1 goto error11",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\sltohta.exe" -sti sldecomp.inp >sldecomp.log',
                "if errorlevel 1 goto error12",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\DECOMP.exe" -cmf DECOMP.cmf -los DECOMP.log',
                "if errorlevel 1 goto error13",
                "if exist cancel.flg goto endbat",
                f'"{RUNGTAP_DIR}\\GTPVOL.exe" -cmf GTAPVol.cmf -los GTAPVol.log',
                "if errorlevel 1 goto error14",
                f'copy GTAP.cmf "{save_sims}"',
                f'copy GTAP.sl4 "{save_sims}"',
                f'copy GTAP.slc "{save_sims}"',
                f'copy GTAP.avc "{save_sims}"',
                f'copy GTAP.udc "{save_sims}"',
                f'copy GTAP.sol "{save_sims}"',
                f'copy GTAP.exp "{save_sims}"',
                f'copy GTAP.log "{save_sims}"',
                f'copy gdata.upd "{save_sims}\\GTAP.upd"',
                "goto endbat",
                ":error3",
                "echo error while Creating standard shock files >Shocks.flg",
                "goto endbat",
                ":error4",
                "echo error while Creating special ALTERTAX parameter file >AlterPar.flg",
                "goto endbat",
                ":error6",
                "echo error while Running PEELAS >PEELAST.flg",
                "goto endbat",
                ":error7",
                "echo error while Running main GTAP model >GTAP.flg",
                "goto endbat",
                ":error8",
                "echo error while Turning SL4 to HAR >sltohta.flg",
                "goto endbat",
                ":error9",
                "echo error while Running SEEENVA for SSA >SEENVSHK.flg",
                "goto endbat",
                ":error10",
                "echo error while Running SLTOHTA for SSA >SLTOHTSH.flg",
                "goto endbat",
                ":error11",
                "echo error while Running Update GTPVEW >GTAPVu2.flg",
                "goto endbat",
                ":error12",
                "echo error while Running SLTOHTA for DECOMP or GTPVOL >sldecomp.flg",
                "goto endbat",
                ":error13",
                "echo error while Running DECOMP >DECOMP.flg",
                "goto endbat",
                ":error14",
                "echo error while Running GTPVOL >GTAPVol.flg",
                "goto endbat",
                ":endbat",
                "exit /b 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return batch_path


def collect_results() -> None:
    RUN_RESULT_DIR.mkdir(parents=True, exist_ok=True)

    patterns = [
        "*.cmf",
        "*.log",
        "*.rpt",
        "*.sl4",
        "*.slc",
        "*.sol",
        "*.avc",
        "*.udc",
        "*.upd",
        "*.har",
        "*.dis",
        "*.flg",
        "*.bat",
        "*.exp",
        "*.map",
    ]
    for pattern in patterns:
        for path in WORK_DIR.glob(pattern):
            if path.is_file():
                shutil.copy2(path, RUN_RESULT_DIR / path.name)

    save_sims = MODEL_DIR / "SaveSims"
    if save_sims.exists():
        dest = RUN_RESULT_DIR / "SaveSims"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(save_sims, dest)


def main() -> None:
    global MODEL_NAME, MODEL_DIR, SCENARIO_CMF, RUN_RESULT_DIR
    args = parser().parse_args()
    MODEL_NAME = args.model_name
    MODEL_DIR = RUNGTAP_DIR / MODEL_NAME
    SCENARIO_CMF = args.cmf
    RUN_RESULT_DIR = args.result_dir

    require_dir(RUNGTAP_DIR)
    require_dir(WORK_DIR)
    require_dir(MODEL_DIR)
    require_file(MODEL_DIR / "basedata.har")
    require_file(MODEL_DIR / "sets.har")
    require_file(MODEL_DIR / "default.prm")
    require_file(SCENARIO_CMF)

    ensure_inside(RUN_RESULT_DIR, RESULT_DIR)
    if RUN_RESULT_DIR.exists():
        shutil.rmtree(RUN_RESULT_DIR)
    RUN_RESULT_DIR.mkdir(parents=True, exist_ok=True)

    clean_previous_work_outputs()
    scenario_text = SCENARIO_CMF.read_text(encoding="utf-8", errors="replace")
    (WORK_DIR / "GTAP.cmf").write_text(portable_scenario_text(scenario_text), encoding="utf-8")
    write_stored_inputs()
    write_auxiliary_cmfs()
    batch_path = write_batch_file()

    started = datetime.now()
    completed = subprocess.run(
        ["cmd.exe", "/c", str(batch_path)],
        cwd=WORK_DIR,
        text=True,
        capture_output=True,
        check=False,
    )

    (RUN_RESULT_DIR / "rungtap_stdout.log").write_text(completed.stdout, encoding="utf-8", errors="replace")
    (RUN_RESULT_DIR / "rungtap_stderr.log").write_text(completed.stderr, encoding="utf-8", errors="replace")
    collect_results()

    flags = sorted(path.name for path in WORK_DIR.glob("*.flg"))
    required_outputs = ["GTAP.sl4", "GTAP.sol", "gdata.upd", "newview.har", "decomp.har", "GTAPVol.har"]
    missing_outputs = [name for name in required_outputs if not (WORK_DIR / name).is_file()]

    summary = [
        f"Run started: {started.isoformat(timespec='seconds')}",
        f"Run ended: {datetime.now().isoformat(timespec='seconds')}",
        f"Return code: {completed.returncode}",
        f"Model directory: {MODEL_DIR}",
        f"Scenario CMF: {SCENARIO_CMF}",
        f"Batch file: {batch_path}",
        f"Flags: {', '.join(flags) if flags else 'none'}",
        f"Missing required outputs: {', '.join(missing_outputs) if missing_outputs else 'none'}",
        f"Collected results: {RUN_RESULT_DIR}",
    ]
    (RUN_RESULT_DIR / "run_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")

    if flags:
        raise RuntimeError(f"RunGTAP reported errors: {flags}. See {RUN_RESULT_DIR}")
    if missing_outputs:
        raise RuntimeError(f"RunGTAP finished but required outputs are missing: {missing_outputs}")

    if args.set_as_default_baseline:
        set_as_default_baseline()

    print(f"OK: RunGTAP scenario completed. Results: {RUN_RESULT_DIR}")


def set_as_default_baseline() -> None:
    scenario_text = SCENARIO_CMF.read_text(encoding="utf-8", errors="replace")
    baseline_match = re.search(r"(?im)^!\s*GTAP baseline update:\s*\d{4} database to (\d{4}) baseline\s*$", scenario_text)
    if not baseline_match or int(baseline_match.group(1)) != 2024:
        raise ValueError(
            "Only a generated baseline-update CMF targeting 2024 can be set as the project's default baseline."
        )

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    baseline_files = [
        (WORK_DIR / "gdata.upd", ASSET_DIR / "basedata_2024.har"),
        (WORK_DIR / "newrate.har", ASSET_DIR / "baserate_2024.har"),
    ]
    for source, dest in baseline_files:
        require_file(source)
        shutil.copy2(source, dest)
    model_mapping = MODEL_DIR / "aggregation_mapping.txt"
    metadata = {
        "baseline_year": 2024,
        "model_name": MODEL_NAME,
        "model_dir": str(MODEL_DIR),
        "source_cmf": str(SCENARIO_CMF.resolve()),
        "basedata": str(baseline_files[0][1]),
        "baserate": str(baseline_files[1][1]),
        "aggregation_mapping": str(model_mapping) if model_mapping.is_file() else None,
        "set_at": datetime.now().isoformat(timespec="seconds"),
    }
    (ASSET_DIR / "default_baseline.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary_path = RUN_RESULT_DIR / "run_summary.txt"
    note = f"Default 2024 baseline: {baseline_files[0][1]}, {baseline_files[1][1]}\n"
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write(note)
    print(f"OK: set default 2024 baseline to {baseline_files[0][1]} and {baseline_files[1][1]}")


if __name__ == "__main__":
    main()
