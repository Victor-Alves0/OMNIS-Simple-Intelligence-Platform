import sys
import types
import unittest

neo4j_stub = types.ModuleType('neo4j')
neo4j_stub.GraphDatabase = object
sys.modules.setdefault('neo4j', neo4j_stub)

class _DummyCountry:
    name = 'Testland'
    region = 'EU'
    subregion = 'Western Europe'
    alpha_3 = 'TST'

class _DummyCountries:
    def get(self, **kwargs):
        return _DummyCountry()

pycountry_stub = types.ModuleType('pycountry')
pycountry_stub.countries = _DummyCountries()
sys.modules.setdefault('pycountry', pycountry_stub)

from app.entity_resolution.resolver import EntityResolver, EntityType


class ResolverHeuristicsTest(unittest.TestCase):
    def setUp(self):
        self.resolver = object.__new__(EntityResolver)

    def test_short_actor_requires_review(self):
        mention = EntityResolver._build_actor_mention(self.resolver, 'sig-1', 'RU')
        self.assertIsNotNone(mention)
        self.assertTrue(mention.needs_review)
        self.assertEqual(mention.entity_type, EntityType.ACTOR)

    def test_normal_actor_high_confidence(self):
        mention = EntityResolver._build_actor_mention(self.resolver, 'sig-1', 'Russian Federation')
        self.assertIsNotNone(mention)
        self.assertGreater(mention.confidence, 0.7)
        self.assertFalse(mention.needs_review)

    def test_location_iso2_high_confidence(self):
        mention = EntityResolver._build_location_mention(self.resolver, 'sig-2', 'US')
        self.assertIsNotNone(mention)
        self.assertFalse(mention.needs_review)

if __name__ == '__main__':
    unittest.main()
