"""시드 로더 테스트.

도메인이 파일을 읽지 않으므로(ADR-007) 여기가 JSON → 도메인 객체 경계다.
데이터가 깨지면 앱이 뜨는 순간이 아니라 **첫 요청에서** 터지므로,
모든 카탈로그가 로드되는지 미리 확인한다.
"""

from app.core.seed import (
    ability_name_map,
    item_catalog,
    learnset,
    move_dex,
    nature_catalog,
    nature_name_map,
    pokedex,
    pokemon_name_map,
    type_chart,
)
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

    def test_pokedex_loads(self):
        dex = pokedex()
        assert dex.get("garchomp").base_stats.speed == 102
        assert len(dex.ids()) > 200

    def test_move_dex_loads(self):
        dex = move_dex()
        earthquake = dex.get("earthquake")
        assert earthquake.power == 100
        assert earthquake.move_type is PokemonType.GROUND
        assert len(dex.ids()) > 500

    def test_pokemon_name_map_loads(self):
        names = pokemon_name_map()
        assert names.ko("garchomp") == "한카리아스"
        assert names.id_of("한카리아스") == "garchomp"

    def test_learnset_loads(self):
        assert learnset().can_learn("garchomp", "earthquake") is True
        assert len(learnset().ids()) > 200

    def test_nature_name_map_loads(self):
        assert nature_name_map().ko("jolly") == "명랑"

    def test_ability_name_map_loads(self):
        assert ability_name_map().ko("roughskin") == "까칠한피부"


class TestCaching:
    """정적 데이터라 프로세스당 한 번만 읽는다."""

    def test_same_instance_is_reused(self):
        assert type_chart() is type_chart()
        assert item_catalog() is item_catalog()
        assert nature_catalog() is nature_catalog()
        assert pokedex() is pokedex()
        assert move_dex() is move_dex()
        assert learnset() is learnset()
        assert pokemon_name_map() is pokemon_name_map()
        assert nature_name_map() is nature_name_map()
        assert ability_name_map() is ability_name_map()
