"""Catalogo modelli Apple: ProductType (es. 'iPhone9,3') -> nome commerciale,
chip e architettura CPU. Serve alla matrice di compatibilità jailbreak.

Arch: 'armv7' (A4-A6, 32-bit), 'arm64' (A7-A11), 'arm64e' (A12+).
checkm8: vulnerabilità BootROM dei chip A5-A11 (jailbreak DFU tipo palera1n;
per i 32-bit vecchi non ci sono jailbreak DFU moderni, restano gli app-jailbreak).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceModel:
    product_type: str
    name: str
    chip: str
    arch: str
    checkm8: bool  # vulnerabile checkm8 (A5-A11)


def _m(product_type: str, name: str, chip: str, arch: str) -> DeviceModel:
    return DeviceModel(product_type, name, chip, arch, arch in ("arm64", "armv7") and chip != "A4")


# ------------------------------- iPhone -------------------------------
_IPHONE = [
    ("iPhone4,1", "iPhone 4s", "A5"),
    ("iPhone5,1", "iPhone 5", "A6"), ("iPhone5,2", "iPhone 5", "A6"),
    ("iPhone5,3", "iPhone 5c", "A6"), ("iPhone5,4", "iPhone 5c", "A6"),
    ("iPhone6,1", "iPhone 5s", "A7"), ("iPhone6,2", "iPhone 5s", "A7"),
    ("iPhone7,1", "iPhone 6 Plus", "A8"), ("iPhone7,2", "iPhone 6", "A8"),
    ("iPhone8,1", "iPhone 6s", "A9"), ("iPhone8,2", "iPhone 6s Plus", "A9"),
    ("iPhone8,4", "iPhone SE (1ª gen)", "A9"),
    ("iPhone9,1", "iPhone 7", "A10"), ("iPhone9,3", "iPhone 7", "A10"),
    ("iPhone9,2", "iPhone 7 Plus", "A10"), ("iPhone9,4", "iPhone 7 Plus", "A10"),
    ("iPhone10,1", "iPhone 8", "A11"), ("iPhone10,4", "iPhone 8", "A11"),
    ("iPhone10,2", "iPhone 8 Plus", "A11"), ("iPhone10,5", "iPhone 8 Plus", "A11"),
    ("iPhone10,3", "iPhone X", "A11"), ("iPhone10,6", "iPhone X", "A11"),
    ("iPhone11,2", "iPhone XS", "A12"),
    ("iPhone11,4", "iPhone XS Max", "A12"), ("iPhone11,6", "iPhone XS Max", "A12"),
    ("iPhone11,8", "iPhone XR", "A12"),
    ("iPhone12,1", "iPhone 11", "A13"),
    ("iPhone12,3", "iPhone 11 Pro", "A13"), ("iPhone12,5", "iPhone 11 Pro Max", "A13"),
    ("iPhone12,8", "iPhone SE (2ª gen)", "A13"),
    ("iPhone13,1", "iPhone 12 mini", "A14"), ("iPhone13,2", "iPhone 12", "A14"),
    ("iPhone13,3", "iPhone 12 Pro", "A14"), ("iPhone13,4", "iPhone 12 Pro Max", "A14"),
    ("iPhone14,4", "iPhone 13 mini", "A15"), ("iPhone14,5", "iPhone 13", "A15"),
    ("iPhone14,2", "iPhone 13 Pro", "A15"), ("iPhone14,3", "iPhone 13 Pro Max", "A15"),
    ("iPhone14,6", "iPhone SE (3ª gen)", "A15"),
    ("iPhone14,7", "iPhone 14", "A15"), ("iPhone14,8", "iPhone 14 Plus", "A15"),
    ("iPhone15,2", "iPhone 14 Pro", "A16"), ("iPhone15,3", "iPhone 14 Pro Max", "A16"),
    ("iPhone15,4", "iPhone 15", "A16"), ("iPhone15,5", "iPhone 15 Plus", "A16"),
    ("iPhone16,1", "iPhone 15 Pro", "A17 Pro"), ("iPhone16,2", "iPhone 15 Pro Max", "A17 Pro"),
    ("iPhone17,3", "iPhone 16", "A18"), ("iPhone17,4", "iPhone 16 Plus", "A18"),
    ("iPhone17,1", "iPhone 16 Pro", "A18 Pro"), ("iPhone17,2", "iPhone 16 Pro Max", "A18 Pro"),
]

# ------------------------------- iPod -------------------------------
_IPOD = [
    ("iPod5,1", "iPod touch (5ª gen)", "A5"),
    ("iPod7,1", "iPod touch (6ª gen)", "A8"),
    ("iPod9,1", "iPod touch (7ª gen)", "A10"),
]

# ------------------------------- iPad -------------------------------
_IPAD = [
    ("iPad2,1", "iPad 2", "A5"), ("iPad2,2", "iPad 2", "A5"),
    ("iPad2,3", "iPad 2", "A5"), ("iPad2,4", "iPad 2", "A5"),
    ("iPad2,5", "iPad mini (1ª gen)", "A5"), ("iPad2,6", "iPad mini (1ª gen)", "A5"),
    ("iPad2,7", "iPad mini (1ª gen)", "A5"),
    ("iPad3,1", "iPad (3ª gen)", "A5X"), ("iPad3,2", "iPad (3ª gen)", "A5X"),
    ("iPad3,3", "iPad (3ª gen)", "A5X"),
    ("iPad3,4", "iPad (4ª gen)", "A6X"), ("iPad3,5", "iPad (4ª gen)", "A6X"),
    ("iPad3,6", "iPad (4ª gen)", "A6X"),
    ("iPad4,1", "iPad Air", "A7"), ("iPad4,2", "iPad Air", "A7"),
    ("iPad4,4", "iPad mini 2", "A7"), ("iPad4,5", "iPad mini 2", "A7"),
    ("iPad4,7", "iPad mini 3", "A7"), ("iPad4,8", "iPad mini 3", "A7"),
    ("iPad5,1", "iPad mini 4", "A8"), ("iPad5,2", "iPad mini 4", "A8"),
    ("iPad5,3", "iPad Air 2", "A8X"), ("iPad5,4", "iPad Air 2", "A8X"),
    ("iPad6,11", "iPad (5ª gen)", "A9"), ("iPad6,12", "iPad (5ª gen)", "A9"),
    ("iPad6,3", "iPad Pro 9,7\"", "A9X"), ("iPad6,4", "iPad Pro 9,7\"", "A9X"),
    ("iPad6,7", "iPad Pro 12,9\" (1ª gen)", "A9X"), ("iPad6,8", "iPad Pro 12,9\" (1ª gen)", "A9X"),
    ("iPad7,1", "iPad Pro 12,9\" (2ª gen)", "A10X"), ("iPad7,2", "iPad Pro 12,9\" (2ª gen)", "A10X"),
    ("iPad7,3", "iPad Pro 10,5\"", "A10X"), ("iPad7,4", "iPad Pro 10,5\"", "A10X"),
    ("iPad7,5", "iPad (6ª gen)", "A10"), ("iPad7,6", "iPad (6ª gen)", "A10"),
    ("iPad7,11", "iPad (7ª gen)", "A10"), ("iPad7,12", "iPad (7ª gen)", "A10"),
    ("iPad8,1", "iPad Pro 11\" (1ª gen)", "A12X"), ("iPad8,2", "iPad Pro 11\" (1ª gen)", "A12X"),
    ("iPad8,3", "iPad Pro 11\" (1ª gen)", "A12X"), ("iPad8,4", "iPad Pro 11\" (1ª gen)", "A12X"),
    ("iPad8,5", "iPad Pro 12,9\" (3ª gen)", "A12X"), ("iPad8,6", "iPad Pro 12,9\" (3ª gen)", "A12X"),
    ("iPad8,7", "iPad Pro 12,9\" (3ª gen)", "A12X"), ("iPad8,8", "iPad Pro 12,9\" (3ª gen)", "A12X"),
    ("iPad8,9", "iPad Pro 11\" (2ª gen)", "A12Z"), ("iPad8,10", "iPad Pro 11\" (2ª gen)", "A12Z"),
    ("iPad8,11", "iPad Pro 12,9\" (4ª gen)", "A12Z"), ("iPad8,12", "iPad Pro 12,9\" (4ª gen)", "A12Z"),
    ("iPad11,1", "iPad mini (5ª gen)", "A12"), ("iPad11,2", "iPad mini (5ª gen)", "A12"),
    ("iPad11,3", "iPad Air (3ª gen)", "A12"), ("iPad11,4", "iPad Air (3ª gen)", "A12"),
    ("iPad11,6", "iPad (8ª gen)", "A12"), ("iPad11,7", "iPad (8ª gen)", "A12"),
    ("iPad12,1", "iPad (9ª gen)", "A13"), ("iPad12,2", "iPad (9ª gen)", "A13"),
    ("iPad13,1", "iPad Air (4ª gen)", "A14"), ("iPad13,2", "iPad Air (4ª gen)", "A14"),
    ("iPad13,4", "iPad Pro 11\" (3ª gen)", "M1"), ("iPad13,5", "iPad Pro 11\" (3ª gen)", "M1"),
    ("iPad13,6", "iPad Pro 11\" (3ª gen)", "M1"), ("iPad13,7", "iPad Pro 11\" (3ª gen)", "M1"),
    ("iPad13,8", "iPad Pro 12,9\" (5ª gen)", "M1"), ("iPad13,9", "iPad Pro 12,9\" (5ª gen)", "M1"),
    ("iPad13,10", "iPad Pro 12,9\" (5ª gen)", "M1"), ("iPad13,11", "iPad Pro 12,9\" (5ª gen)", "M1"),
    ("iPad13,16", "iPad Air (5ª gen)", "M1"), ("iPad13,17", "iPad Air (5ª gen)", "M1"),
    ("iPad13,18", "iPad (10ª gen)", "A14"), ("iPad13,19", "iPad (10ª gen)", "A14"),
]

_CATALOG: dict[str, DeviceModel] = {}


def _build():
    def arch_for(chip: str) -> str:
        if chip in ("A4", "A5", "A5X", "A6", "A6X"):
            return "armv7"
        if chip in ("A7", "A8", "A8X", "A9", "A9X", "A10", "A10X", "A10Z", "A11"):
            return "arm64"
        if chip in ("A12", "A12X", "A12Z", "A13", "A14", "A15", "A16", "A17", "A17 Pro",
                    "A18", "A18 Pro", "M1", "M2"):
            return "arm64e"
        return "arm64"

    for pt, name, chip in _IPHONE + _IPOD + _IPAD:
        _CATALOG[pt] = DeviceModel(pt, name, chip, arch_for(chip), arch_for(chip) == "arm64")


_build()


def lookup(product_type: str) -> DeviceModel | None:
    return _CATALOG.get(product_type)


def ios_in_range(version: str, minimum: str, maximum: str) -> bool:
    def key(v: str) -> tuple[int, ...]:
        parts = []
        for p in v.split("."):
            n = "".join(ch for ch in p if ch.isdigit())
            parts.append(int(n) if n else 0)
        return tuple(parts)

    try:
        return key(minimum) <= key(version) <= key(maximum)
    except Exception:
        return False
