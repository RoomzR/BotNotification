"""Тесты разбора объёма бутылок и совместимости с каталогом бара."""

from src.vision.menu_matcher import (
    CLUB_MAX_LITERS,
    LARGE_MIN_LITERS,
    parse_volume_liters,
    volumes_compatible,
)


def test_parse_bar_volumes():
    assert parse_volume_liters("0.33 л") == 0.33
    assert parse_volume_liters("0,5л") == 0.5
    assert parse_volume_liters("GORILLA 0.45") == 0.45
    assert parse_volume_liters("500 ml") == 0.5
    assert parse_volume_liters("500мл") == 0.5
    assert parse_volume_liters("330ml") == 0.33


def test_parse_large_pet():
    assert parse_volume_liters("1 л") == 1.0
    assert parse_volume_liters("1.5L") == 1.5
    assert parse_volume_liters("2л Coca-Cola") == 2.0
    assert parse_volume_liters("coca cola 2 l") == 2.0


def test_large_vs_club_incompatible():
    assert volumes_compatible(2.0, 0.33) is False
    assert volumes_compatible(1.0, 0.5) is False
    assert volumes_compatible(0.5, 0.5) is True
    assert volumes_compatible(0.33, 0.33) is True
    assert volumes_compatible(0.45, 0.5) is True  # допуск 0.2
    assert volumes_compatible(1.5, None) is False
    assert volumes_compatible(0.5, None) is True


def test_thresholds():
    assert CLUB_MAX_LITERS < LARGE_MIN_LITERS
    assert CLUB_MAX_LITERS >= 0.5
    assert LARGE_MIN_LITERS <= 1.0
