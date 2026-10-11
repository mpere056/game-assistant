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
        for text, mode in [('Attack the nearest Godrick soldier', 'fight'), ('hit that enemy', 'fight'),
                           ('Attack that nearest soldier.', 'fight'), ('attack it', 'fight'),
                           ('hit it once', 'hit'), ('land one hit on the soldier', 'hit'),
                           ('just hit that enemy once', 'hit'), ('go fight the soldier', 'fight'),
                           ('kill the nearest Godrick soldier', 'fight'), ('take down that enemy', 'fight')]:
            with self.subTest(text=text):
                a = self._resolver().handle(text)
                self.assertEqual((a.kind, a.entity_id, a.attack), ('walk', 11, mode), a.say)

    def test_orders_from_the_deer_session(self):
        # runtime/chat-20261011.jsonl 14:39-14:41: these got "I don't see any ... to attack".
        for text, who in [('Can you kill this Godrick soldier from me?', 11),
                          ('Can you kill one of the deer for me?', 10),
                          ('Kill one of those deers as well', 10)]:
            with self.subTest(text=text):
                a = self._resolver().handle(text)
                self.assertEqual((a.kind, a.entity_id, a.attack), ('walk', who, 'fight'), a.say)

    def test_the_next_one_is_the_same_kind(self):
        r = self._resolver()
        self.assertEqual(r.handle('kill the deer').entity_id, 10)
        a = r.handle('Nice, kill the next one too.')
        self.assertEqual((a.kind, a.entity_id), ('walk', 10), a.say)


class FieldAdapter(FakeAdapter):
    """A field with a camera looking north: a Springhare close behind-right, a soldier ahead, another soldier
    to the west, a boar far east."""
    NAMES = {1: 'Godrick Soldier', 3: 'Springhare', 4: 'Boar'}

    def __init__(self):
        from game_assistant.core.interface import Entity
        super().__init__([], entities=[
            Entity(21, 3, 'c6', (4.0, 0.0, -3.0), (4.0, 0.5, -3.0), 0.3, 0.5, True, False),     # springhare 5 m
            Entity(22, 1, 'c2', (0.0, 0.0, 15.0), (0.0, 1.0, 15.0), 0.5, 2.0, True, False),     # soldier ahead
            Entity(23, 1, 'c2', (-12.0, 0.0, 0.0), (-12.0, 1.0, 0.0), 0.5, 2.0, True, False),   # soldier west
            Entity(24, 4, 'c5', (38.0, 0.0, 0.0), (38.0, 1.0, 0.0), 0.8, 1.5, True, False)])    # boar east

    def snapshot(self):
        from game_assistant.core.interface import Camera, Player, Snapshot
        cam = Camera((0.0, 2.0, -3.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60, 16 / 9)
        return Snapshot('test', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), cam, tuple(self.entities), None)

    def knowledge(self, type_id):
        return {'name': self.NAMES.get(type_id)}

    def raycast(self, rays, timeout=2.0):
        from game_assistant.core.interface import RayHit
        return [RayHit(a, b, False) for a, b in rays]


class SessionOrderTests(unittest.TestCase):
    """Orders from runtime/chat-20261011.jsonl 14:37-15:11 that went wrong."""

    def test_this_enemy_in_front_of_me_is_the_one_ahead(self):
        # 15:10: went for a Springhare (nearest hostile, behind-right) instead of the soldier in front.
        a = CommandResolver(FieldAdapter()).handle('Okay, can you kill this enemy in front of me?')
        self.assertEqual((a.kind, a.entity_id, a.attack), ('walk', 22, 'fight'), a.say)

    def test_a_kind_named_earlier_in_the_order(self):
        # 15:09: "I think there was a boar around here. Can you kill that too?" went for the nearest enemy.
        a = CommandResolver(FieldAdapter()).handle('I think there was a boar around here. Can you kill that too?')
        self.assertEqual((a.kind, a.entity_id), ('walk', 24), a.say)

    def test_the_next_one_without_a_verb(self):
        # 14:37: "Alright, now the next one please." after a soldier: nothing happened.
        r = CommandResolver(FieldAdapter())
        r.handle('kill the nearest soldier')
        a = r.handle('Alright, now the next one please.')
        self.assertEqual((a.kind, a.attack, a.name), ('walk', 'fight', 'Godrick Soldier'), a.say)

    def test_kill_all_of_them(self):
        # 15:11: "can you kill all of the soldiers around here for me?" (twice): nothing.
        for text in ['I mean, can you kill all of the soldiers around here for me?',
                     "Alright, there's some more soldiers around here. Can you kill all of them for me?"]:
            with self.subTest(text=text):
                a = CommandResolver(FieldAdapter()).handle(text)
                self.assertEqual((a.kind, a.attack, a.every), ('walk', 'fight', 'Godrick Soldier'), a.say)
                self.assertIn(a.entity_id, (22, 23))

    def test_mentions_are_whole_words(self):
        # 'here' must not match 'Deer' (difflib would call them alike)
        r = CommandResolver(FieldAdapter())
        self.assertIsNone(r._mentioned('is there anything here'))


class ScreenshotSessionTests(unittest.TestCase):
    """15:21-15:22: "How many soldiers ... around here?" (Navi: three Godrick Soldiers), then "Can you kill
    all of them from me?" fought "that enemy" and went on to an Eagle 35 m up a cliff; "Oh no, not the
    Eagle, all the soldiers." was a question back."""

    def _resolver(self):
        from game_assistant.core.interface import Entity
        a = FieldAdapter()
        a.NAMES = {**FieldAdapter.NAMES, 5: 'Eagle'}
        a.entities = list(a.entities) + [Entity(25, 5, 'c7', (3.0, 35.0, 4.0), (3.0, 35.5, 4.0), 0.5, 1.0, True,
                                                False)]
        return CommandResolver(a)

    def test_them_means_what_was_just_talked_about(self):
        r = self._resolver()
        r.heard('How many soldiers do you see or do you know of that are around here?\n'
                'Three Godrick Soldiers nearby. One is about 17 metres ahead, and the other two are about 40 '
                'and 70 metres away.')
        a = r.handle('Can you kill all of them from me?')
        self.assertEqual((a.kind, a.attack, a.every), ('walk', 'fight', 'Godrick Soldier'), a.say)

    def test_any_enemy_is_never_one_far_above(self):
        r = self._resolver()
        a = r.handle('kill all of them')            # nothing said before: any enemy, but not the Eagle up there
        self.assertNotEqual(a.entity_id, 25)
        self.assertIsNone(r.character('enemy', max_range=4.9))   # (only the Eagle is that near on the map)

    def test_a_correction_without_a_verb(self):
        r = self._resolver()
        r.handle('kill the eagle')
        a = r.handle('Oh no, not the Eagle, all the soldiers.')
        self.assertEqual((a.kind, a.attack, a.every), ('walk', 'fight', 'Godrick Soldier'), a.say)
        a = r.handle('kill the eagle')
        b = r.handle('not the eagle all the soldiers')   # speech without the comma
        self.assertEqual((b.kind, b.every), ('walk', 'Godrick Soldier'), b.say)

    def test_that_is_still_what_the_player_looks_at(self):
        r = self._resolver()
        r.heard('Is there a boar around?\nA Boar about 38 metres to your left.')
        a = r.handle('kill that')                    # singular: not the boar from the conversation
        self.assertNotEqual(a.entity_id, 24)


class SequenceOrderTests(unittest.TestCase):
    """15:4x: "Alright, please kill the boar and then kill any remaining soldiers." -> "I don't see any boar
    and then kill soldiers to attack." (the whole rest was taken as the name)."""

    def test_splitting(self):
        from game_assistant.core.walk_commands import split_orders
        for text, want in [
                ('Alright, please kill the boar and then kill any remaining soldiers',
                 ['Alright, please kill the boar', 'kill any remaining soldiers']),
                ("Please kill the boar and then after you've killed the boar, kill any soldiers around here, please",
                 ['Please kill the boar', 'kill any soldiers around here, please']),
                ('kill the boar then go to the nearest grace', ['kill the boar', 'go to the nearest grace']),
                ('kill the boar and the soldier', ['kill the boar', 'kill the soldier']),
                ('walk to the church and stand there', ['walk to the church and stand there']),
                ('Kill all the soldiers around here, please', ['Kill all the soldiers around here, please'])]:
            with self.subTest(text=text):
                self.assertEqual(split_orders(text), want)

    def test_the_boar_first_then_all_the_soldiers(self):
        r = CommandResolver(FieldAdapter())
        a = r.handle('Alright, please kill the boar and then kill any remaining soldiers.')
        self.assertEqual((a.kind, a.entity_id, a.attack), ('walk', 24, 'fight'), a.say)
        self.assertEqual(a.then, ['kill any remaining soldiers'])
        b = r.handle(a.then[0])
        self.assertEqual((b.kind, b.attack, b.every), ('walk', 'fight', 'Godrick Soldier'), b.say)
