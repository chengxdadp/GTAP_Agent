from __future__ import annotations

import json
from pathlib import Path

from gtap_runtime import GEMPACK_DIR, GTAPAGG_DIR, RUNGTAP_DIR


REQUIRED = {
    "RunGTAP programs": [
        RUNGTAP_DIR / name
        for name in [
            "GTAP.exe",
            "SHOCKS.exe",
            "ALTPAR.exe",
            "PEELAS.exe",
            "sltohta.exe",
            "seenva.exe",
            "GTPVEW.exe",
            "DECOMP.exe",
            "GTPVOL.exe",
            "GTAP.map",
            "DECOMP.map",
        ]
    ],
    "Aggregated model": [
        RUNGTAP_DIR / "gtap2015_10x10" / name
        for name in ["basedata.har", "baserate.har", "sets.har", "default.prm", "default.pel", "altertax.prm"]
    ],
    "Default model templates": [
        RUNGTAP_DIR / "model_templates" / "gtap2015_10x10" / name
        for name in ["default.pel", "altertax.prm"]
    ],
    "GEMPACK conversion tools": [GEMPACK_DIR / "har2csv.exe", GEMPACK_DIR / "har2txt.exe"],
    "GTAPAgg": [
        GTAPAGG_DIR / "gtapagg2.exe",
        GTAPAGG_DIR / "gtapagg.lic",
        GTAPAGG_DIR / "GTAP10A" / "GTAP" / "runagg.bat",
        GTAPAGG_DIR / "GTAP10A" / "GTAP" / "default.txt",
        GTAPAGG_DIR / "GTAP10A" / "GTAP" / "2014" / "basedata.har",
    ],
}


def main() -> None:
    groups = []
    missing: list[str] = []
    for name, paths in REQUIRED.items():
        absent = [str(path) for path in paths if not path.is_file()]
        missing.extend(absent)
        groups.append({"name": name, "ok": not absent, "missing": absent})

    payload = {
        "ok": not missing,
        "runtime": {
            "rungtap": str(RUNGTAP_DIR),
            "gtapagg": str(GTAPAGG_DIR),
            "gempack": str(GEMPACK_DIR),
        },
        "groups": groups,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
