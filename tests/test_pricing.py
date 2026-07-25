from app.core.pricing import PriceCatalog


def test_recognizes_faucet() -> None:
    result = PriceCatalog().estimate("Нужно заменить смеситель на кухне")
    assert result.service_code == "faucet"
    assert result.price_min == 2000
    assert result.price_max == 3500


def test_unknown_work_requires_master() -> None:
    result = PriceCatalog().estimate("Нужна консультация по сложному объекту")
    assert result.service_code == "custom"
    assert result.price_min is None
    assert result.formatted_price == "нужна оценка мастера"


def test_combines_multiple_jobs() -> None:
    result = PriceCatalog().estimate("Собрать комод и повесить зеркало")
    assert result.service_code == "furniture+wall_mount"
    assert result.price_min == 4000
    assert result.price_max == 10500
