from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


PRICE_FILE = Path(__file__).resolve().parents[2] / "config" / "prices.json"


@dataclass(frozen=True, slots=True)
class PriceEstimate:
    service_code: str
    service_name: str
    price_min: int | None
    price_max: int | None
    duration_hours: int
    matched_keywords: tuple[str, ...]

    @property
    def formatted_price(self) -> str:
        if self.price_min is None or self.price_max is None:
            return "нужна оценка мастера"
        if self.price_min == self.price_max:
            return f"{self.price_min:,} ₽".replace(",", " ")
        low = f"{self.price_min:,}".replace(",", " ")
        high = f"{self.price_max:,}".replace(",", " ")
        return f"{low}–{high} ₽"


class PriceCatalog:
    def __init__(self, price_file: Path = PRICE_FILE) -> None:
        payload = json.loads(price_file.read_text(encoding="utf-8"))
        self.minimum_order = int(payload["minimum_order"])
        self.services = payload["services"]

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^а-яёa-z0-9\s-]", " ", text.lower())

    def estimate(self, description: str) -> PriceEstimate:
        normalized = self._normalize(description)
        recognized: list[tuple[dict, list[str]]] = []

        for service in self.services:
            matches = [
                keyword
                for keyword in service["keywords"]
                if re.search(
                    rf"(?<![а-яёa-z0-9]){re.escape(self._normalize(keyword).strip())}",
                    normalized,
                )
            ]
            if matches:
                recognized.append((service, matches))

        if not recognized:
            return PriceEstimate(
                service_code="custom",
                service_name="Нестандартная работа",
                price_min=None,
                price_max=None,
                duration_hours=3,
                matched_keywords=(),
            )

        # В одной заявке могут быть разные задачи: например, собрать комод и
        # повесить зеркало. Каталог складывает диапазоны, но минимальный заказ
        # применяется один раз ко всей заявке.
        recognized = recognized[:4]
        price_min = max(
            sum(int(service["price_min"]) for service, _ in recognized),
            self.minimum_order,
        )
        price_max = max(
            sum(int(service["price_max"]) for service, _ in recognized),
            price_min,
        )
        return PriceEstimate(
            service_code="+".join(service["code"] for service, _ in recognized),
            service_name=" + ".join(service["name"] for service, _ in recognized),
            price_min=price_min,
            price_max=price_max,
            duration_hours=min(
                sum(int(service.get("duration_hours", 3)) for service, _ in recognized),
                8,
            ),
            matched_keywords=tuple(
                keyword
                for _, matches in recognized
                for keyword in matches
            ),
        )
