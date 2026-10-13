"""Getting items (core/crafting.py): what to gather and craft, in order, from a small Minecraft-like book."""
import unittest

from game_assistant.core.crafting import CantMake, Recipe, Source, plan

PLANKS = ('mc:birch_planks', 'mc:oak_planks', 'mc:spruce_planks')
STONES = ('mc:cobblestone', 'mc:blackstone')
PICKAXES = ['mc:wooden_pickaxe', 'mc:stone_pickaxe', 'mc:iron_pickaxe']


def shaped(result, count, rows):
    w, h = len(rows[0]), len(rows)
    return Recipe(f'r:{result}', result, count, tuple(c for row in rows for c in row), w, h)


class Book:
    hand_grid = 2
    station = 'mc:crafting_table'

    def __init__(self, near=('mc:oak_log', 'mc:stone'), table_near=False):
        self.near, self.table_near = set(near), table_near
        P, S, _ = PLANKS, ('mc:stick',), ()
        self.book = {
            'mc:oak_planks': [Recipe('r:oak_planks', 'mc:oak_planks', 4, (('mc:oak_log',),), 1, 1)],
            'mc:birch_planks': [Recipe('r:birch_planks', 'mc:birch_planks', 4, (('mc:birch_log',),), 1, 1)],
            'mc:stick': [shaped('mc:stick', 4, [[P], [P]])],
            'mc:crafting_table': [shaped('mc:crafting_table', 1, [[P, P], [P, P]])],
            'mc:wooden_pickaxe': [shaped('mc:wooden_pickaxe', 1, [[P, P, P], [_, S, _], [_, S, _]])],
            'mc:stone_pickaxe': [shaped('mc:stone_pickaxe', 1, [[STONES, STONES, STONES], [_, S, _], [_, S, _]])],
        }
        self.src = {'mc:oak_log': [Source('mc:oak_log', ('mc:oak_log',))],
                    'mc:birch_log': [Source('mc:birch_log', ('mc:birch_log',))],
                    'mc:cobblestone': [Source('mc:stone', ('mc:cobblestone',), 'mc:wooden_pickaxe')]}

    def recipes(self, item):
        return self.book.get(item, [])

    def sources(self, item):
        return self.src.get(item, [])

    def tool_ok(self, need, have):
        return any(have.get(t, 0) > 0 for t in PICKAXES[PICKAXES.index(need):]) if need in PICKAXES else \
            have.get(need, 0) > 0

    def nearby(self, blocks):
        return 10.0 if set(blocks) & self.near else None

    def station_near(self):
        return self.table_near


def kinds(steps):
    return [(s[0], s[1].result if s[0] == 'craft' else s[1]) for s in steps]


class CraftingPlanTests(unittest.TestCase):
    def test_a_stone_pickaxe_from_nothing(self):
        steps = plan(Book(), 'mc:stone_pickaxe', 1, {})
        k = kinds(steps)
        self.assertEqual(k[0], ('gather', 'mc:oak_log'))
        self.assertIn(('station', 'mc:crafting_table'), k)
        self.assertLess(k.index(('craft', 'mc:wooden_pickaxe')), k.index(('gather', 'mc:cobblestone')))
        self.assertEqual(k[-1], ('craft', 'mc:stone_pickaxe'))
        self.assertEqual(k.count(('craft', 'mc:wooden_pickaxe')), 1)     # one tool, kept for the stone

    def test_what_is_held_is_used(self):
        steps = plan(Book(table_near=True), 'mc:stone_pickaxe', 1,
                     {'mc:wooden_pickaxe': 1, 'mc:stick': 2, 'mc:cobblestone': 1})
        self.assertEqual(kinds(steps), [('gather', 'mc:cobblestone'), ('craft', 'mc:stone_pickaxe')])
        self.assertEqual(steps[0][2], 2)                                 # two more cobblestone

    def test_planks_of_a_wood_that_grows_near(self):
        steps = plan(Book(near=('mc:birch_log',)), 'mc:stick', 4, {})
        self.assertEqual(kinds(steps)[0], ('gather', 'mc:birch_log'))

    def test_no_way(self):
        with self.assertRaises(CantMake):
            plan(Book(near=()), 'mc:wooden_pickaxe', 1, {})
