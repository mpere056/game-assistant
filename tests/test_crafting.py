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

    def test_what_is_built_is_not_taken_apart(self):
        # (a crafting table the player put down was mined to "get a crafting table", 2026-10-13)
        b = Book(near=('mc:oak_log', 'mc:crafting_table', 'mc:oak_planks'))
        b.src['mc:crafting_table'] = [Source('mc:crafting_table', ('mc:crafting_table',))]
        b.src['mc:oak_planks'] = [Source('mc:oak_planks', ('mc:oak_planks',))]
        k = kinds(plan(b, 'mc:crafting_table', 1, {}))
        self.assertNotIn(('gather', 'mc:crafting_table'), k)
        self.assertNotIn(('gather', 'mc:oak_planks'), k)
        self.assertEqual(k[0], ('gather', 'mc:oak_log'))

    def smelting_book(self, near=('mc:oak_log', 'mc:stone', 'mc:iron_ore')):
        b = Book(near=near, table_near=True)
        S, _ = ('mc:stick',), ()
        I, C = ('mc:iron_ingot',), ('mc:cobblestone',)
        b.book['mc:iron_ingot'] = [Recipe('r:ingot', 'mc:iron_ingot', 1, (('mc:raw_iron',),), shapeless=True,
                                          station='mc:furnace', time=200)]
        b.book['mc:iron_pickaxe'] = [shaped('mc:iron_pickaxe', 1, [[I, I, I], [_, S, _], [_, S, _]])]
        b.book['mc:furnace'] = [shaped('mc:furnace', 1, [[C, C, C], [C, _, C], [C, C, C]])]
        b.src['mc:raw_iron'] = [Source('mc:iron_ore', ('mc:raw_iron',), 'mc:stone_pickaxe')]
        b.fuels = lambda: {'mc:oak_log': 300, 'mc:oak_planks': 300, 'mc:stick': 100, 'mc:coal': 1600}
        b.station_near = lambda item=None: item != 'mc:furnace'
        return b

    def test_an_iron_pickaxe_smelts_its_ingots(self):
        # ("get me an iron pickaxe": "I can't find a way", the ingots being smelted, 2026-10-13)
        steps = plan(self.smelting_book(), 'mc:iron_pickaxe', 1, {'mc:stone_pickaxe': 1, 'mc:stick': 2})
        k = [(s[0], s[1].result if s[0] in ('craft', 'smelt') else s[1]) for s in steps]
        smelt = next(s for s in steps if s[0] == 'smelt')
        self.assertEqual((smelt[1].result, smelt[2]), ('mc:iron_ingot', 3))
        self.assertEqual(smelt[4], 2)                                   # 600 ticks: two logs or planks
        self.assertLess(k.index(('station', 'mc:furnace')), k.index(('smelt', 'mc:iron_ingot')))
        self.assertLess(k.index(('gather', 'mc:raw_iron')), k.index(('smelt', 'mc:iron_ingot')))
        self.assertEqual(k[-1], ('craft', 'mc:iron_pickaxe'))

    def test_fuel_held_is_burnt_first(self):
        steps = plan(self.smelting_book(), 'mc:iron_pickaxe', 1,
                     {'mc:stone_pickaxe': 1, 'mc:stick': 2, 'mc:coal': 1, 'mc:raw_iron': 3, 'mc:furnace': 1})
        smelt = next(s for s in steps if s[0] == 'smelt')
        self.assertEqual((smelt[3], smelt[4]), ('mc:coal', 1))
        self.assertNotIn('gather', [s[0] for s in steps])

    def test_no_way(self):
        with self.assertRaises(CantMake):
            plan(Book(near=()), 'mc:wooden_pickaxe', 1, {})


class CreatureAndTimeTests(unittest.TestCase):
    def test_a_bed_from_the_wool_a_sheep_gives(self):
        # ("make me a bed": wool comes only from sheep, which no plan knew, 2026-10-16)
        b = Book()
        W = ('mc:white_wool',)
        b.book['mc:white_bed'] = [shaped('mc:white_bed', 1, [[W, W, W], [PLANKS, PLANKS, PLANKS]])]
        sheep = Source('mc:sheep', W, None, on='creature', how='kill', per=1.0, variant=(('color', 'white'),))
        b.src['mc:white_wool'] = [sheep]
        b.nearby = lambda blocks: 10.0 if set(blocks) & (b.near | {'mc:sheep'}) else None
        steps = plan(b, 'mc:white_bed', 1, {})
        hunt = [s for s in steps if s[0] == 'gather' and s[1] == 'mc:white_wool']
        self.assertEqual(len(hunt), 1)
        self.assertEqual((hunt[0][2], hunt[0][3].creature), (3, True))
        self.assertEqual(kinds(steps)[-1], ('craft', 'mc:white_bed'))

    def test_working_a_plan_out_has_a_time_limit(self):
        # (a bed's every wool and dye, each looked for in the world: the order hung, 2026-10-16)
        import time
        from game_assistant.core.crafting import plan_cost
        b = Book()
        slow = b.nearby
        b.nearby = lambda blocks: (time.sleep(0.05), slow(blocks))[1]
        t = time.monotonic()
        with self.assertRaises(CantMake) as got:
            plan_cost(b, 'mc:stone_pickaxe', 1, {}, deadline=time.monotonic() + 0.03)
        self.assertLess(time.monotonic() - t, 1.0)
        self.assertIn('took too long', str(got.exception))
