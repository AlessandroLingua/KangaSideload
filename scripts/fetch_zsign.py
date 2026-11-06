"""Scarica zsign dalle release GitHub ufficiali (zhlynn/zsign, MIT).

Uso: python scripts/fetch_zsign.py
Estrae l'eseguibile (e le dipendenze) in kanga/tools/zsign/.
La stessa operazione è disponibile nella GUI («Scarica zsign»).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanga.core.zsign_fetch import ZsignFetchError, fetch_zsign  # noqa: E402


def main() -> int:
    try:
        fetch_zsign(log=lambda m: print(f"[*] {m}"))
    except (ZsignFetchError, OSError) as e:
        print(f"[!] {e}")
        return 1
    except Exception as e:  # rete, zip corrotto, ...
        print(f"[!] Download fallito: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
