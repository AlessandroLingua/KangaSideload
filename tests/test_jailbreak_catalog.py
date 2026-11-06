"""Test del catalogo dispositivi e della matrice compatibilità jailbreak."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanga.core.devices_catalog import ios_in_range, lookup
from kanga.core.jailbreak_catalog import compatible_tools, find_tool


def test_lookup():
    m = lookup("iPhone9,3")
    assert m and m.name == "iPhone 7" and m.chip == "A10" and m.arch == "arm64" and m.checkm8
    m2 = lookup("iPhone11,2")
    assert m2.arch == "arm64e" and not m2.checkm8
    m3 = lookup("iPod9,1")
    assert m3.name.startswith("iPod touch 7") or "7" in m3.name
    assert lookup("iPhone99,9") is None
    print("[OK] lookup modelli (iPhone/iPod/iPad)")


def test_ios_range():
    assert ios_in_range("15.8.4", "15.0", "16.6.1")
    assert not ios_in_range("16.7", "15.0", "16.6.1")
    assert ios_in_range("14.3", "11.0", "14.8")
    print("[OK] confronto versioni iOS")


def test_matrix_iphone7_1584():
    m = lookup("iPhone9,3")
    res = {c.tool.id: c for c in compatible_tools(m, "15.8.4")}
    assert res["dopamine"].compatible
    assert res["trollinstallerx"].compatible
    assert not res["unc0ver"].compatible
    assert not res["taurine"].compatible
    # palera1n: iPhone 7 è checkm8, iOS in range, ma metodo DFU -> non sideload
    p = res["palera1n"]
    assert p.method == "dfu" and not p.compatible
    print("[OK] matrice iPhone 7 / iOS 15.8.4 (Dopamine+TrollInstallerX, palera1n solo DFU)")


def test_matrix_arm64e():
    m = lookup("iPhone11,2")  # XS A12
    res = {c.tool.id: c for c in compatible_tools(m, "16.0")}
    assert res["dopamine"].compatible is False  # arm64e max 15.4.1
    # TrollInstallerX arm64e range 15.0-16.5.1 -> 16.0 È nel range: compatibile
    assert res["trollinstallerx"].compatible
    m2 = lookup("iPhone13,2")  # 12 A14, iOS 15.4.1
    res2 = {c.tool.id: c for c in compatible_tools(m2, "15.4.1")}
    assert res2["dopamine"].compatible  # arm64e 15.0-15.4.1
    print("[OK] matrice arm64e (A12-A14: range ridotti)")


def test_find_tool():
    t = find_tool("dopamine")
    assert t.repo == "opa334/Dopamine"
    print("[OK] find_tool")


if __name__ == "__main__":
    test_lookup()
    test_ios_range()
    test_matrix_iphone7_1584()
    test_matrix_arm64e()
    test_find_tool()
    print("\nTutti i test jailbreak catalog: PASSATI")
