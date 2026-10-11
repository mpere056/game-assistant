"""F10 command resolution (core/walk_commands.py) with a stand-in adapter."""
import unittest

from game_assistant.core.walk_commands import CommandResolver, WalkAction


class FakeAdapter:
    def __init__(self, graces, places=(), entities=()):
        self.graces, self.places, self.entities = graces, places, entities

    def snapshot(self):
        from game_assistant.core.interface import Player, Snapshot
        return Snapshot('test', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, tuple(self.entities), None)

    def knowledge(self, type_id):
        return {'name': {1: 'Godrick Soldier', 2: 'Deer'}.get(type_id)}

    def nearest_places(self, kind='site of grace', limit=5):
        return {'results': [{'name': n, 'distance_m': d, 'position': p} for n, d, p in self.graces[:limit]]}

    def locate(self, query):
        q = query.lower()
        return {'results': [{'name': n, 'position': p} for n, p in self.places if q in n.lower()]}


class CommandTests(unittest.TestCase):
    def test_stop(self):
        r = CommandResolver(FakeAdapter([]))
        self.assertEqual(r.handle('Stop walking!').kind, 'stop')
        self.assertEqual(r.handle('never mind, stop').kind, 'stop')
        self.assertEqual(r.handle('ok wait').kind, 'stop')

    def test_nearest_grace_walks_there(self):
        r = CommandResolver(FakeAdapter([('Gatefront', 120, (1, 0, 2)), ('Agheel Lake North', 300, (5, 0, 5))]))
        a = r.handle('go to the nearest site of grace')
        self.assertEqual((a.kind, a.name, a.point), ('walk', 'Gatefront', (1, 0, 2)))

    def test_two_about_equally_near_makes_navi_ask_and_the_answer_walks(self):
        r = CommandResolver(FakeAdapter([('Gatefront', 120, (1, 0, 2)), ('Stormhill Shack', 130, (9, 0, 9))]))
        a = r.handle('Take me to the closest grace please')
        self.assertEqual(a.kind, 'ask')
        self.assertIn('Gatefront', a.say)
        self.assertIn('Stormhill Shack', a.say)
        b = r.handle('the shack')
        self.assertEqual((b.kind, b.name), ('walk', 'Stormhill Shack'))

    def test_named_place(self):
        r = CommandResolver(FakeAdapter([], [('Church of Elleh', (3, 0, 4))]))
        a = r.handle('walk to the Church of Elleh')
        self.assertEqual((a.kind, a.name), ('walk', 'Church of Elleh'))

    def test_unknown_goes_to_the_model(self):
        seen = []

        def model(text):
            seen.append(text)
            return WalkAction('walk', 'Off we go!', (7, 0, 7), 'Waypoint Ruins')
        r = CommandResolver(FakeAdapter([], []), ask_model=model)
        a = r.handle("let's check out those ruins by the lake")
        self.assertEqual(a.name, 'Waypoint Ruins')
        self.assertEqual(len(seen), 1)

    def test_nothing_understood(self):
        r = CommandResolver(FakeAdapter([], []))
        self.assertEqual(r.handle('banana').kind, 'none')


class CharacterOrderTests(unittest.TestCase):
    def _adapter(self):
        from game_assistant.core.interface import Entity
        deer = Entity(10, 2, 'c1', (5.0, 0.0, 30.0), (5.0, 1.0, 30.0), 0.5, 1.5, False, False)
        soldier = Entity(11, 1, 'c2', (0.0, 0.0, 39.0), (0.0, 1.0, 39.0), 0.5, 2.0, True, False)
        far_soldier = Entity(12, 1, 'c2', (0.0, 0.0, 90.0), (0.0, 1.0, 90.0), 0.5, 2.0, True, False)
        return FakeAdapter([('Groveside Cave', 20, (9, 0, 9))], entities=[deer, soldier, far_soldier])

    def test_nearest_named_character_not_a_place(self):
        r = CommandResolver(self._adapter())
        a = r.handle('Walk to the nearest Godrick soldier.')
        self.assertEqual((a.kind, a.name, a.entity_id), ('walk', 'Godrick Soldier', 11))

    def test_nearest_enemy_means_hostile(self):
        r = CommandResolver(self._adapter())
        a = r.handle('go to the closest enemy')
        self.assertEqual(a.entity_id, 11)  # the deer is nearer but not hostile

    def test_nothing_like_that_around(self):
        r = CommandResolver(self._adapter())
        a = r.handle('walk to the nearest dragon')
        self.assertEqual(a.kind, 'none')


class UserPhrasesTests(unittest.TestCase):
    """The user's exact F10 orders from the in-game test (2026-10-10), all meaning the Godrick Soldier
    34-39 m ahead. The place search answers like the real one did (via the Godrick Soldier Ashes item)."""

    def _resolver(self):
        from game_assistant.core.interface import Entity
        deer = Entity(10, 2, 'c1', (5.0, 0.0, 30.0), (5.0, 1.0, 30.0), 0.5, 1.5, False, False)
        soldier = Entity(11, 1, 'c2', (0.0, 0.0, 39.0), (0.0, 1.0, 39.0), 0.5, 2.0, True, False)
        a = FakeAdapter([('Groveside Cave', 20, (9, 0, 9))], entities=[deer, soldier])
        a.locate = lambda q: {'results': [{'name': "Warmaster's Shack", 'position': (0, 0, 390)},
                                          {'name': 'Volcano Manor Request: Istvan', 'position': (0, 0, 900)}],
                              'found_via_item': 'Godrick Soldier Ashes is at Warmaster\'s Shack'}
        return CommandResolver(a)

    def test_every_phrasing_walks_to_the_soldier(self):
        for text in ['Walk to the nearest Godrick soldier.',
                     'Walk to the nearest Godrick soldier, the enemy.',
                     'Go to the Godrick Soldier.',
                     'I mean, the nearest gondric soldier in front of me, please go to that.',
                     'Go to the nearest Godrick soldier in front of me.',
                     'I want you to walk my character to the nearest Godrick soldier, please.']:
            with self.subTest(text=text):
                a = self._resolver().handle(text)
                self.assertEqual((a.kind, a.entity_id), ('walk', 11), a.say)


class NoGameTests(unittest.TestCase):
    def test_order_without_the_game_says_so(self):
        from game_assistant.core.interface import GameNotRunning

        class Gone(FakeAdapter):
            def snapshot(self):
                raise GameNotRunning('closed')
        a = CommandResolver(Gone([])).handle('go to the nearest Godrick soldier')
        self.assertEqual(a.kind, 'none')
        self.assertIn("can't see the game", a.say)


class AttackOrderTests(unittest.TestCase):
    def _resolver(self):
        return UserPhrasesTests()._resolver()

    def test_attack_orders(self):
        for text in ['Attack the nearest Godrick soldier', 'hit that enemy', 'attack it', 'go fight the soldier']:
            with self.subTest(text=text):
                a = self._resolver().handle(text)
                self.assertEqual((a.kind, a.entity_id, a.attack), ('walk', 11, True), a.say)
