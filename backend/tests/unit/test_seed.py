"""시드 로더 테스트.

도메인이 파일을 읽지 않으므로(ADR-007) 여기가 JSON → 도메인 객체 경계다.
데이터가 깨지면 앱이 뜨는 순간이 아니라 **첫 요청에서** 터지므로,
세 카탈로그가 모두 로드되는지 미리 확인한다.
"""

from app.core.seed import item_catalog, nature_catalog, type_chart
from app.domain.items import ItemEffectKind
from app.domain.nature import StatKey
from app.domain.typechart import PokemonType, effectiveness


class TestLoaders:
    def test_type_chart_loads(self):
        chart = type_chart()
        assert effectiveness(chart, PokemonType.WATER, (PokemonType.FIRE,)) == 2.0

    def test_item_catalog_loads(self):
        catalog = item_catalog()
        life_orb = catalog.get("lifeorb")
        assert life_orb is not None
        assert life_orb.kind is ItemEffectKind.DAMAGE_FLAT
        assert life_orb.multiplier == 1.3

    def test_nature_catalog_loads(self):
        catalog = nature_catalog()
        assert catalog.get("jolly").raised is StatKey.SPEED
        assert len(catalog.names()) == 25


class TestCaching:
    """정적 데이터라 프로세스당 한 번만 읽는다."""

    def test_same_instance_is_reused(self):
        assert type_chart() is type_chart()
        assert item_catalog() is item_catalog()
        assert nature_catalog() is nature_catalog()
