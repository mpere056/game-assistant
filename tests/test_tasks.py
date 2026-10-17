"""Doing a crafting plan (core/tasks.py): the clicks that fill a crafting grid; gathering in a stand-in world."""
import unittest

from game_assistant.core.crafting import Recipe, Source
from game_assistant.core.interface import Camera, Player, Screen, Snapshot
from game_assistant.core.tasks import TaskRunner, _fill_clicks

P = ('mc:oak_planks', 'mc:birch_planks')
S = ('mc:stick',)
PICK = Recipe('r:pick', 'mc:wooden_pickaxe', 1, (P, P, P, (), S, (), (), S, ()), 3, 3)


def table_screen(held):
    """A crafting table's slots: result, grid0-8, then the player's (index, x, y, item, count, role)."""
    slots = [[0, 500, 100, '', 0, 'result']]
    slots += [[1 + i, 100 + 40 * (i % 3), 60 + 40 * (i // 3), '', 0, f'grid{i}'] for i in range(9)]
    slots += [[10 + i, 100 + 40 * i, 400, it, n, f'hotbar{i}'] for i, (it, n) in enumerate(held)]
    return {'menu': 'CraftingMenu', 'slots': slots}


class FillTests(unittest.TestCase):
    def test_a_pickaxe_into_a_table(self):
        sc = table_screen([('mc:oak_planks', 5), ('mc:stick', 4)])
        clicks = _fill_clicks(sc, PICK, 1)
        grid = {s[0] - 1: (s[1], s[2]) for s in sc['slots'] if s[5].startswith('grid')}
        rights = [(x, y) for _i, x, y, b in clicks if b == 'right']
        self.assertEqual(rights, [grid[0], grid[1], grid[2], grid[4], grid[7]])
        lefts = [(x, y) for _i, x, y, b in clicks if b == 'left']
        self.assertEqual(lefts, [(100, 400), (100, 400), (140, 400), (140, 400)])   # pick up, put back each

    def test_not_enough(self):
        self.assertIsNone(_fill_clicks(table_screen([('mc:oak_planks', 2), ('mc:stick', 4)]), PICK, 1))


class World:
    """One oak log block ahead; mining it (attack held while aimed) drops a log, picked up at once."""

    def __init__(self):
        self.inv, self.logs, self.walks, self.said = {}, 1, [], []
        self.aimed, self.mined = False, 0.0

    def snap(self):
        cam = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 70, 1.7)
        return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), cam, (), None,
                        screen=Screen(0, 0, 800, 600, True),
                        extra={'inventory': dict(self.inv), 'aim_block': (0, 1, 1) if self.logs else None,
                               'tool_slot': -1, 'slot': 0})

    def apply(self, intent, dt):
        if intent is not None and 'attack' in intent.keys and self.logs:
            self.mined += dt
            if self.mined > 0.5:
                self.logs -= 1
                self.inv['mc:oak_log'] = self.inv.get('mc:oak_log', 0) + 1

    def task_find(self, blocks, exclude):
        return ((0.0, 0.5, 1.5), 'Oak Log', (0, 1, 1)) if self.logs and (0, 1, 1) not in exclude else None

    def task_items(self, items, radius):
        return []

    def task_walk(self, point, name):
        self.walks.append(name)

    def task_stop(self):
        pass

    def task_say(self, line):
        self.said.append(line)


class GatherTests(unittest.TestCase):
    def test_walks_mines_and_is_done(self):
        w = World()
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 1, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 1)
        for _ in range(10):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual(w.walks, ['Oak Log'])
        r.on_event('arrived')
        for _ in range(120):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
            if not r.active:
                break
        self.assertEqual(w.inv.get('mc:oak_log'), 1)
        self.assertFalse(r.active)
        self.assertIn('done', r.events)


class WornOutToolTests(unittest.TestCase):
    def test_a_tool_gone_when_the_next_block_is_looked_for_fails_the_step(self):
        # (the stone pickaxe wore out down a mine: the step went on "mining" iron with a wooden one, nothing
        # dropping, the same ore walked to again and again, 2026-10-16). Failed with NEEDS_TOOL, the goal runner
        # plans again from what's held: a new pickaxe in it.
        from game_assistant.core.skills import NEEDS_TOOL

        class W(World):
            def task_tool(self, need, have):
                return 'mc:stone_pickaxe' if have.get('mc:stone_pickaxe') else None

            def snap(self):
                s = super().snap()
                s.extra['hotbar'] = [('mc:stone_pickaxe', 1)] if self.inv.get('mc:stone_pickaxe') else []
                return s
        w = W()
        w.inv['mc:stone_pickaxe'] = 1
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 3, Source('mc:oak_log', ('mc:oak_log',), 'mc:stone_pickaxe'))],
                'mc:oak_log', 3)
        r.update(w.snap(), 1 / 30, False)
        self.assertEqual(r.phase, 'walk')                # (it had the tool: off to the first)
        del w.inv['mc:stone_pickaxe']                    # (worn out on it)
        r._set('find')                                   # (that one done: the next looked for)
        r.update(w.snap(), 1 / 30, False)
        self.assertFalse(r.active)
        self.assertEqual(r.outcome.reason.kind, NEEDS_TOOL)

    def test_a_tool_gone_on_the_way_fails_the_step_at_once(self):
        # (the walk to the ore dug through stone with the pickaxe till it broke; then the ore was punched by hand,
        # 15 s and nothing dropping, 2026-10-17)
        from game_assistant.core.skills import NEEDS_TOOL

        class W(World):
            stops = 0

            def task_tool(self, need, have):
                return 'mc:stone_pickaxe' if have.get('mc:stone_pickaxe') else None

            def task_stop(self):
                self.stops += 1

            def snap(self):
                s = super().snap()
                s.extra['hotbar'] = [('mc:stone_pickaxe', 1)] if self.inv.get('mc:stone_pickaxe') else []
                return s
        w = W()
        w.inv['mc:stone_pickaxe'] = 1
        r = TaskRunner(w)
        r.start([('gather', 'mc:raw_iron', 3, Source('mc:iron_ore', ('mc:raw_iron',), 'mc:stone_pickaxe'))],
                'mc:raw_iron', 3)
        r.update(w.snap(), 1 / 30, False)
        self.assertEqual(r.phase, 'walk')
        del w.inv['mc:stone_pickaxe']                    # (broke digging the way there)
        r.update(w.snap(), 1 / 30, True)
        self.assertFalse(r.active)
        self.assertEqual(r.outcome.reason.kind, NEEDS_TOOL)
        self.assertGreaterEqual(w.stops, 1)              # (the walk stopped)


class StationWorld:
    """A crafting table to put down at (0, 0, 2), on the ground's top (the block below); the crosshair (as the
    game's) stops on the first plant along its line (cells (0, 0, 1) then (0, 0, 2)), else on the ground's top.
    A click with the table in hand puts it where the game would: into a plant that's replaceable (grass), else on
    the face clicked; a hit knocks a plant out."""

    def __init__(self, plants):
        self.plants = dict(plants)                    # cell: (id, replaceable)
        self.placed, self.slot, self.attacks = None, 0, 0
        self.hot = [['mc:dirt', 3], ['mc:crafting_table', 1]]

    def aimed(self):
        if self.placed == (0, 0, 2):
            return (0, 0, 2), 'mc:crafting_table', 'south', False, True
        for c in ((0, 0, 1), (0, 0, 2)):
            if c in self.plants:
                return c, self.plants[c][0], 'south', self.plants[c][1], False
        return (0, -1, 2), 'mc:grass_block', 'up', False, True

    def snap(self):
        cam = Camera((0.0, 1.3, 0.0), (0.0, -0.6, 0.8), (1.0, 0.0, 0.0), (0.0, 0.8, 0.6), 70, 1.7)
        aim, aid, face, repl, solid = self.aimed()
        inv = {'mc:dirt': 3} | ({} if self.placed else {'mc:crafting_table': 1})
        return Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), cam, (), None,
                        screen=Screen(0, 0, 800, 600, True),
                        extra={'inventory': inv, 'aim_block': aim, 'aim_id': aid, 'aim_face': face,
                               'aim_replaceable': repl, 'aim_solid': solid, 'hotbar': self.hot, 'slot': self.slot})

    def apply(self, intent, dt):
        for t in intent.taps if intent is not None else ():
            aim, _id, _face, repl, solid = self.aimed()
            if t.startswith('slot'):
                self.slot = int(t[4:]) - 1
            elif t == 'attack' and aim in self.plants:
                del self.plants[aim]
                self.attacks += 1
            elif t == 'interact' and self.hot[self.slot][0] == 'mc:crafting_table' and not self.placed:
                if aim in self.plants:
                    if repl:
                        del self.plants[aim]
                        self.placed = aim
                elif aim == (0, -1, 2):
                    self.placed = (0, 0, 2)

    def task_place_spot(self, exclude=()):
        return ((0.5, 0.0, 2.5), (0, -1, 2), 'up', (0, 0, 2))

    def task_stop(self):
        pass

    def task_say(self, line):
        pass


class StationTests(unittest.TestCase):
    def put_down(self, plants):
        w = StationWorld(plants)
        r = TaskRunner(w)
        r.start([('station', 'mc:crafting_table')], 'mc:crafting_table', 1)
        for _ in range(300):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
            if not r.active:
                break
        return w, r

    def test_a_flower_in_the_spot_is_knocked_out_and_the_table_really_put_down(self):
        # (a flower where the table was to go: the crosshair on it there was taken for the table, 2026-10-13)
        w, r = self.put_down({(0, 0, 2): ('mc:poppy', False)})
        self.assertEqual((w.attacks, w.placed), (1, (0, 0, 2)))
        self.assertIn('done', r.events)

    def test_grass_in_the_way_is_knocked_out(self):
        # (grass between the eye and the spot: the crosshair stopped on it, nothing was clicked, 10 s and "I
        # couldn't place the crafting table", four times on a grassy hill, 2026-10-13)
        w, r = self.put_down({(0, 0, 1): ('mc:short_grass', True), (0, 0, 2): ('mc:short_grass', True)})
        self.assertIn('done', r.events)
        self.assertTrue(w.placed)

    def test_grass_in_the_spot_is_put_over(self):
        w, r = self.put_down({(0, 0, 2): ('mc:short_grass', True)})
        self.assertEqual((w.attacks, w.placed), (0, (0, 0, 2)))       # no need to knock it out
        self.assertIn('done', r.events)


class Screen2x2:
    """The inventory's 2x2 crafting: clicks as the game does them. A click on the result with Shift held moves
    all it makes into the inventory; without, it picks one craft up onto the cursor (what went wrong live)."""

    def __init__(self, planks=10):
        self.open, self.grid, self.carried, self.shift = False, [None] * 4, None, False
        self.inv = {'mc:oak_planks': planks}
        self.src_xy = (100, 400)
        self.ptr = (0, 0)
        self.off = (0, 0)            # where the pointer really lands, off from where it was sent

    def result(self):
        if self.grid[0] and self.grid[2] and self.grid[0][1] > 0 and self.grid[2][1] > 0:
            return 'mc:stick'
        return ''

    def screen(self):
        slots = [[0, 500, 100, self.result(), 4 if self.result() else 0, 'result']]
        slots += [[1 + i, 100 + 40 * (i % 2), 60 + 40 * (i // 2), (self.grid[i] or ('', 0))[0],
                   (self.grid[i] or ('', 0))[1], f'grid{i}'] for i in range(4)]
        n = self.inv.get('mc:oak_planks', 0)
        slots.append([36, 100, 400, 'mc:oak_planks' if n else '', n, 'hotbar0'])
        slots.append([37, 140, 400, 'mc:stick' if self.inv.get('mc:stick') else '', self.inv.get('mc:stick', 0),
                      'hotbar1'])
        slots.append([38, 180, 400, '', 0, 'hotbar2'])
        c = self.carried or ('', 0)
        hov = next((sl[0] for sl in slots if (sl[1], sl[2]) == self.ptr), -1)
        return {'menu': 'InventoryMenu', 'slots': slots, 'carried': c[0], 'carried_count': c[1], 'hovered': hov,
                'mouse': [self.ptr[0], self.ptr[1], 0]}

    def point(self, x, y):
        self.ptr = (round(x + self.off[0]), round(y + self.off[1]))

    def click(self, x, y, button, shift):
        if (x, y) == (500, 100) and self.result():
            if self.shift:
                while self.result():
                    for i in (0, 2):
                        self.grid[i] = (self.grid[i][0], self.grid[i][1] - 1)
                    self.inv['mc:stick'] = self.inv.get('mc:stick', 0) + 4
            else:
                for i in (0, 2):
                    self.grid[i] = (self.grid[i][0], self.grid[i][1] - 1)
                self.carried = ('mc:stick', (self.carried or ('', 0))[1] + 4)
            return
        if (x, y) == self.src_xy:
            if self.carried is None:
                self.carried, self.inv['mc:oak_planks'] = ('mc:oak_planks', self.inv['mc:oak_planks']), 0
            else:
                self.inv['mc:oak_planks'] += self.carried[1]
                self.carried = None
            return
        for i in range(4):
            if (x, y) == (100 + 40 * (i % 2), 60 + 40 * (i // 2)) and button == 'right' and self.carried:
                g = self.grid[i] or (self.carried[0], 0)
                self.grid[i] = (g[0], g[1] + 1)
                self.carried = (self.carried[0], self.carried[1] - 1) if self.carried[1] > 1 else None
                return
        if (x, y) in ((140, 400), (180, 400)) and self.carried:  # a hotbar slot to put it in
            self.inv[self.carried[0]] = self.inv.get(self.carried[0], 0) + self.carried[1]
            self.carried = None


class CraftScreenTests(unittest.TestCase):
    def run_craft(self, shift_works, off=(0, 0)):
        sc = Screen2x2()
        stick = Recipe('r:stick', 'mc:stick', 4, (P, P), 1, 2)

        sc.off = off

        class W(World):
            def task_point(self, x, y):
                sc.point(x, y)

            def task_press(self, button):
                sc.click(sc.ptr[0], sc.ptr[1], button, False)

        w = W()
        r = TaskRunner(w)
        r.start([('craft', stick, 2)], 'mc:stick', 8)
        for _ in range(30 * 8):
            inv = dict(sc.inv)
            snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, (), None,
                            screen=Screen(0, 0, 800, 600, True),
                            extra={'inventory': inv, 'container': sc.screen() if sc.open else None})
            intent = r.update(snap, 1 / 30, False)
            if intent is not None:
                if 'inventory' in intent.taps:
                    sc.open = True
                if 'escape' in intent.taps:
                    if sc.carried:                      # closing puts the cursor's stack back
                        sc.inv[sc.carried[0]] = sc.inv.get(sc.carried[0], 0) + sc.carried[1]
                        sc.carried = None
                    sc.open = False
                sc.shift = shift_works and 'sneak' in intent.keys
            if not r.active:
                break
        return r, sc

    def test_sticks_with_shift_held_before_the_click(self):
        r, sc = self.run_craft(True)
        self.assertEqual(r.result, 'done')
        self.assertEqual(sc.inv.get('mc:stick'), 8)
        self.assertEqual(sc.inv.get('mc:oak_planks'), 6)     # 10, less two for each of two crafts

    def test_a_result_picked_up_onto_the_cursor_is_put_down_and_counted(self):
        r, sc = self.run_craft(False)
        self.assertEqual(r.result, 'done')
        self.assertEqual(sc.inv.get('mc:stick'), 8)

    def test_a_pointer_that_lands_off_is_corrected_before_any_click(self):
        r, sc = self.run_craft(True, off=(-7, 12))      # (the window's origin off by 7 and 12 pixels)
        self.assertEqual(r.result, 'done')
        self.assertEqual(sc.inv.get('mc:stick'), 8)
        self.assertEqual(r.calib, [7.0, -12.0])


class Furnace:
    """A furnace screen, as the game does it: input, fuel, result, and the hotbar. Left on a held stack picks it
    all up, right on a slot puts one in, left back puts the rest back; Shift and a left on the result moves it
    all to the hotbar. It cooks one each `cook` s while something burns (a log: `burn` s)."""

    def __init__(self, inv, cook=0.3, burn=0.45):
        self.inv, self.open, self.carried, self.shift = dict(inv), False, None, False
        self.slot = {'input': None, 'fuel': None, 'result': None}
        self.cook, self.burn, self.t, self.left, self.fire = cook, burn, 0.0, 0.0, 0.0
        self.ptr = (0, 0)
        self.hot = sorted(self.inv)                                   # one hotbar slot each, in order

    def xy(self, role):
        return {'input': (300, 100), 'fuel': (300, 180), 'result': (420, 140)}[role]

    def screen(self):
        slots = [[i, *self.xy(r), (self.slot[r] or ('', 0))[0], (self.slot[r] or ('', 0))[1], r]
                 for i, r in enumerate(('input', 'fuel', 'result'))]
        for j, it in enumerate(self.hot):
            slots.append([30 + j, 100 + 40 * j, 400, it if self.inv.get(it) else '', self.inv.get(it, 0), f'hotbar{j}'])
        hov = next((sl[0] for sl in slots if (sl[1], sl[2]) == self.ptr), -1)
        c = self.carried or ('', 0)
        return {'menu': 'FurnaceMenu', 'slots': slots, 'carried': c[0], 'carried_count': c[1], 'hovered': hov,
                'mouse': [*self.ptr, 0], 'lit': self.fire > 0}

    def press(self, button):
        x, y = self.ptr
        for j, it in enumerate(self.hot):
            if (x, y) == (100 + 40 * j, 400):
                if self.carried is None and button == 'left' and self.inv.get(it):
                    self.carried, self.inv[it] = (it, self.inv[it]), 0
                elif self.carried is not None and button == 'left':
                    self.inv[self.carried[0]] = self.inv.get(self.carried[0], 0) + self.carried[1]
                    self.carried = None
                return
        for r in ('input', 'fuel'):
            if (x, y) == self.xy(r) and button == 'right' and self.carried:
                cur = self.slot[r] or (self.carried[0], 0)
                self.slot[r] = (cur[0], cur[1] + 1)
                self.carried = (self.carried[0], self.carried[1] - 1) if self.carried[1] > 1 else None
                return
        if (x, y) == self.xy('result') and self.slot['result'] and self.shift:
            it, n = self.slot['result']
            self.inv[it] = self.inv.get(it, 0) + n
            self.slot['result'] = None

    def tick(self, dt):
        if self.fire <= 0 and self.slot['input'] and self.slot['fuel']:
            f, n = self.slot['fuel']
            self.slot['fuel'] = (f, n - 1) if n > 1 else None
            self.fire = self.burn
        if self.fire > 0 and self.slot['input']:
            self.fire -= dt
            self.left += dt
            if self.left >= self.cook:
                self.left = 0.0
                it, n = self.slot['input']
                self.slot['input'] = (it, n - 1) if n > 1 else None
                r = self.slot['result'] or ('mc:iron_ingot', 0)
                self.slot['result'] = (r[0], r[1] + 1)
        elif self.fire > 0:
            self.fire -= dt


class SmeltTests(unittest.TestCase):
    def test_raw_iron_smelted_into_ingots(self):
        # ("get me an iron pickaxe": the ingots are cooked in a furnace, 2026-10-13)
        fz = Furnace({'mc:raw_iron': 4, 'mc:oak_log': 3})
        ingot = Recipe('r:ingot', 'mc:iron_ingot', 1, (('mc:raw_iron',),), shapeless=True, station='mc:furnace',
                       time=6)

        class W(World):
            def task_find(self, blocks, exclude):
                return ((0.0, 0.5, 2.5), 'Furnace', (0, 0, 2)) if blocks == ('mc:furnace',) else None

            def task_aim(self, key, eye):
                return ((0.0, 0.5, 2.0), None)

            def task_point(self, x, y):
                fz.ptr = (round(x), round(y))

            def task_press(self, button):
                fz.press(button)
        w = W()
        r = TaskRunner(w)
        r.start([('smelt', ingot, 3, 'mc:oak_log', 2)], 'mc:iron_ingot', 3)
        cam = Camera((0.0, 1.6, 0.0), (0.0, -0.4, 0.9), (1.0, 0.0, 0.0), (0.0, 0.9, 0.4), 70, 1.7)
        for _ in range(30 * 20):
            snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), cam, (), None,
                            screen=Screen(0, 0, 800, 600, True),
                            extra={'inventory': dict(fz.inv), 'container': fz.screen() if fz.open else None,
                                   'aim_block': (0, 0, 2)})
            intent = r.update(snap, 1 / 30, False)
            if intent is not None:
                if 'interact' in intent.taps:
                    fz.open = True
                if 'escape' in intent.taps:
                    fz.open = False
                fz.shift = 'sneak' in intent.keys
            fz.tick(1 / 30)
            if not r.active:
                break
        self.assertIn('done', r.events, r.trace[-4:])
        self.assertEqual(fz.inv.get('mc:iron_ingot'), 3)
        self.assertEqual(fz.inv.get('mc:raw_iron'), 1)                  # exactly 3 went in
        self.assertEqual(fz.inv.get('mc:oak_log'), 1)                   # and 2 logs


class ReserveTests(unittest.TestCase):
    def test_what_the_steps_to_come_use_is_reserved(self):
        # (8 cobblestone dug for a furnace went into a pillar out of the hole: "I don't have what the furnace
        # takes", 2026-10-15)
        C = ('mc:cobblestone',)
        furnace = Recipe('r:furnace', 'mc:furnace', 1, (C, C, C, C, (), C, C, C, C), 3, 3)
        ingot = Recipe('r:ingot', 'mc:iron_ingot', 1, (('mc:raw_iron',),), shapeless=True, station='mc:furnace',
                       time=200)
        r = TaskRunner(World())
        r.start([('gather', 'mc:cobblestone', 8, Source('mc:stone', ('mc:cobblestone',))), ('craft', furnace, 1),
                 ('station', 'mc:furnace'), ('smelt', ingot, 3, 'mc:oak_log', 2)], 'mc:iron_ingot', 3)
        self.assertEqual(r.reserved(), {'mc:cobblestone': 8, 'mc:furnace': 1, 'mc:raw_iron': 3, 'mc:oak_log': 2})
        r.stop()
        self.assertEqual(r.reserved(), {})


class MineBesideTests(unittest.TestCase):
    def test_a_block_not_reached_from_where_the_walk_ended_is_walked_up_to_first(self):
        # (two ores beside the one just mined were left as "could not mine it", 2026-10-15)
        class W(World):
            near = False

            def task_aim(self, key, eye):
                return ((0.0, 1.5, 1.0), None) if self.near else (None, None)

            def task_walk(self, point, name, beside=None):
                self.walks.append((name, beside))
                self.near = beside is not None
        w = W()
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 1, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 1)
        for _ in range(10):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        r.on_event('arrived')
        for _ in range(5):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual(w.walks[-1], ('Oak Log', (0, 1, 1)))           # right up beside it
        r.on_event('arrived')
        for _ in range(120):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
            if not r.active:
                break
        self.assertIn('done', r.events)


class RecoverTests(unittest.TestCase):
    def test_a_stuck_step_gets_what_it_lacks_first_then_goes_on(self):
        # (no way up out of the hole to the crafting table, 13 blocks wanted, 7 spare: the order was over,
        # 2026-10-15)
        dirt = ('gather', 'mc:dirt', 9, Source('mc:dirt', ('mc:dirt',)))

        class W(World):
            def task_recover(self):
                return [dirt], 'getting dirt first'
        w = W()
        r = TaskRunner(w)
        stick = Recipe('r:stick', 'mc:stick', 4, (P, P), 1, 2)
        r.start([('craft', stick, 1)], 'mc:stick', 4)
        r.phase = 'to_station'
        r.on_event('gave_up')
        self.assertTrue(r.active)
        self.assertEqual(r.steps[0], dirt)                              # first the blocks...
        self.assertEqual(r.steps[1][0], 'craft')                        # ...then the step again
        self.assertIn('getting dirt first', w.said)
        r.phase = 'walk'
        r.recoveries = 2
        r.on_event('gave_up')                                           # (twice at most: then as before)
        self.assertEqual(r.steps[0], dirt)


class StuckHereTests(unittest.TestCase):
    def test_walks_given_up_from_the_same_spot_stop_trying_others(self):
        # (from the bottom of a hole, walks to one log after another, each given up, over and over, 2026-10-15)
        w = World()
        w.logs = 5
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 3, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 3)
        r.pos = (0.0, 53.0, 0.0)
        r.phase = 'walk'
        r.on_event('gave_up')                                           # one: another may do
        self.assertTrue(r.active)
        r.phase = 'walk'
        r.on_event('gave_up')                                           # again from the same spot: stuck here
        self.assertFalse(r.active)
        self.assertIn('failed', r.events)
        self.assertEqual(r.outcome.reason.kind, 'stuck')

    def test_ways_that_need_blocks_are_not_stuck(self):
        # (logs up the trunk the player stood under, 2 blocks to pillar, none spare: "every way out needs
        # something", and the order went back to the doer, 2026-10-16): unreachable, with the route's note
        from game_assistant.core.skills import Reason
        w = World()
        w.logs = 5
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 3, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 3)
        r.pos = (0.0, 80.0, 0.0)
        note = {'found': 'no way', 'blocks_it_would_take': 2, 'blocks_to_build_with': 0}
        for _ in range(2):
            r.phase = 'walk'
            r.on_event('gave_up', Reason('unreachable', {'what': 'Oak Log', 'at': None, 'route': note}))
        self.assertFalse(r.active)
        self.assertEqual(r.outcome.reason.kind, 'unreachable')
        self.assertEqual(r.outcome.reason.get('route'), note)


class ClearFirstTests(unittest.TestCase):
    def test_a_leaf_in_front_of_the_log_is_mined_first(self):
        # (the route planner counts one block in the way of what's mined as none (it's mined first); the mining
        # said "hidden" and walked right up beside it, round the tree, and the step was given up, 2026-10-16)
        class W(World):
            leaf = True

            def snap(self):
                s = super().snap()
                s.extra['aim_block'] = (0, 1, 1) if self.leaf else ((0, 1, 2) if self.logs else None)
                return s

            def apply(self, intent, dt):
                if intent is not None and 'attack' in intent.keys and self.leaf:
                    self.leaf = False                    # (a leaf goes at once)
                elif not self.leaf:
                    super().apply(intent, dt)

            def task_find(self, blocks, exclude):
                return ((0.0, 0.5, 2.5), 'Oak Log', (0, 1, 2)) if self.logs and (0, 1, 2) not in exclude else None

            def task_aim(self, key, eye):
                if tuple(key) == (0, 1, 1):
                    return ((0.0, 1.5, 1.0), None)
                return ((0.0, 1.5, 2.0), None) if not self.leaf else (None, (0, 1, 1))

            def task_walk(self, point, name, beside=None):
                self.walks.append((name, beside))
        w = W()
        r = TaskRunner(w)
        r.start([('gather', 'mc:oak_log', 1, Source('mc:oak_log', ('mc:oak_log',)))], 'mc:oak_log', 1)
        for _ in range(5):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        r.on_event('arrived')
        for _ in range(200):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
            if not r.active:
                break
        self.assertFalse(w.leaf)
        self.assertEqual(w.inv.get('mc:oak_log'), 1)
        self.assertEqual([x for x in w.walks if x[1] is not None], [])     # no walk round to it
        self.assertIn('done', r.events)


class HuntTests(unittest.TestCase):
    def test_wool_from_a_sheep_hunted_then_picked_up(self):
        # (a bed's wool comes only from sheep, 2026-10-16)
        sheep = Source('mc:sheep', ('mc:white_wool',), None, on='creature', how='kill', per=1.0,
                       variant=(('color', 'white'),))

        class W(World):
            hunts, lying = [], []

            def task_creature(self, kind, variant, exclude):
                self.asked = (kind, dict(variant))
                return ((3.0, 0.0, 4.0), 'Sheep', 7) if 7 not in exclude else None

            def task_hunt(self, point, name, ident):
                self.hunts.append(ident)

            def task_items(self, items, radius):
                return list(self.lying)
        w = W()
        r = TaskRunner(w)
        r.start([('gather', 'mc:white_wool', 1, sheep)], 'mc:white_wool', 1)
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual(w.hunts, [7])
        self.assertEqual(w.asked, ('mc:sheep', {'color': 'white'}))
        self.assertEqual(r.phase, 'hunt')
        w.lying = [(3.0, 0.0, 4.0)]                     # the fight's over (the app): its wool on the ground
        r.resume()
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual(r.phase, 'pickup')
        w.inv['mc:white_wool'], w.lying = 1, []
        r.on_event('arrived')
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertIn('done', r.events)


class UseTests(unittest.TestCase):
    def test_a_sheep_shorn_with_the_shears_held(self):
        # (the game's data has shearing: the same use skill as every interaction, 2026-10-16)
        from game_assistant.core.interface import Entity
        sheep = Source('mc:sheep', ('mc:white_wool',), 'mc:shears', on='creature', how='use', per=2.0,
                       variant=(('color', 'white'), ('sheared', False)))

        class W(World):
            approached, lying, clicks = [], [], 0

            def snap(self):
                s = super().snap()
                e = Entity(7, 3, None, (0.0, 0.0, 2.0), (0.0, 1.6, 2.0), 0.45, 1.3, False, False, name='Sheep')
                s.extra.update({'hotbar': [('mc:stick', 1), ('mc:shears', 1)], 'slot': self.slot})
                return s.__class__(**{**s.__dict__, 'entities': (e,), 'target_id': 7 if self.slot == 1 else None})
            slot = 0

            def task_creature(self, kind, variant, exclude):
                return ((0.0, 0.0, 2.0), 'Sheep', 7) if 7 not in exclude else None

            def task_approach(self, point, name, ident):
                self.approached.append(ident)

            def task_tool(self, need, have):
                return 'mc:shears' if have.get('mc:shears') else None

            def task_items(self, items, radius):
                return list(self.lying)
        w = W()
        w.inv['mc:shears'] = 1
        r = TaskRunner(w)
        r.start([('gather', 'mc:white_wool', 2, sheep)], 'mc:white_wool', 2)
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual((w.approached, r.phase), ([7], 'approach'))
        r.on_event('arrived')
        for _ in range(60):
            intent = r.update(w.snap(), 1 / 30, False)
            if intent is not None:
                if 'slot2' in intent.taps:
                    w.slot = 1                          # the shears in hand
                if 'interact' in intent.taps:
                    self.assertEqual(w.slot, 1)
                    w.clicks += 1
                    w.lying = [(0.0, 0.0, 2.5)]         # the wool falls
            if r.phase == 'pickup':
                break
        self.assertEqual(w.clicks, 1)
        w.inv['mc:white_wool'], w.lying = 2, []
        r.on_event('arrived')
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertIn('done', r.events)


class SeekTests(unittest.TestCase):
    def test_a_creature_out_of_sight_is_walked_toward_then_hunted(self):
        # (sheep found by the game's search, beyond sight: hunted by id, each was "gone" at once, 3 s apiece,
        # 16 of them twice, 2026-10-17)
        sheep = Source('mc:sheep', ('mc:white_wool',), on='creature', how='kill')

        class W(World):
            approached, seen, stops = [], False, 0

            def task_creature(self, kind, variant, exclude):
                return ((0.0, 0.0, 80.0), 'Sheep', 7)

            def task_seen(self, ident):
                return self.seen

            def task_hunt(self, point, name, ident):
                self.approached.append(ident)

            def task_stop(self):
                self.stops += 1
        w = W()
        r = TaskRunner(w)
        r.start([('gather', 'mc:white_wool', 2, sheep)], 'mc:white_wool', 2)
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual((r.phase, w.walks, w.approached), ('seek', ['Sheep'], []))
        for _ in range(30):                              # (on the way: no "the hunt ended")
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertEqual(r.phase, 'seek')
        w.seen = True                                    # (in sight now)
        for _ in range(3):
            w.apply(r.update(w.snap(), 1 / 30, False), 1 / 30)
        self.assertGreaterEqual(w.stops, 1)
        self.assertEqual((r.phase, w.approached), ('hunt', [7]))


class FuelTests(unittest.TestCase):
    def test_tools_are_never_burnt(self):
        # (the player's own wooden sword was burnt for shears' iron, and another made to burn, 2026-10-17)
        from game_assistant.games.minecraft.adapter import _Book
        b = _Book.__new__(_Book)
        b._fuels = {'minecraft:wooden_sword': 200, 'minecraft:wooden_pickaxe': 200, 'minecraft:bow': 300,
                    'minecraft:oak_planks': 300, 'minecraft:coal': 1600, 'minecraft:bowl': 200}
        self.assertEqual(sorted(b.fuels()), ['minecraft:bowl', 'minecraft:coal', 'minecraft:oak_planks'])


class OffhandTests(unittest.TestCase):
    def test_a_shield_into_the_off_hand(self):
        # (the user: "it should get a shield before trying to kill a skeleton"; it's held in the off hand)
        class W(World):
            slot, offhand = 0, ''

            def snap(self):
                s = super().snap()
                s.extra.update({'hotbar': [('mc:stone_sword', 1), ('mc:shield', 1)], 'slot': self.slot,
                                'offhand': self.offhand})
                return s
        w = W()
        w.inv['mc:shield'] = 1
        r = TaskRunner(w)
        r.start([('offhand', 'mc:shield')], 'mc:shield', 1)
        for _ in range(60):
            intent = r.update(w.snap(), 1 / 30, False)
            if intent is not None and 'slot2' in intent.taps:
                w.slot = 1
            if intent is not None and 'swap_hands' in intent.taps:
                w.offhand = 'mc:shield'
            if not r.active:
                break
        self.assertIn('done', r.events)
        self.assertEqual(w.offhand, 'mc:shield')


class EquipTests(unittest.TestCase):
    """("Equip my armour": "my tools can only sort the hotbar", 2026-10-17.) The game puts a piece on when it's used
    from the hand, swapping it for what's worn there."""

    def world(self, worn):
        class W(World):
            slot, armor, worn_ = 0, 0, None

            def snap(self):
                s = super().snap()
                hot = [('mc:stone_sword', 1)] + [(k, 1) for k in ('mc:iron_chestplate', 'mc:leather_chestplate')
                                                 if self.inv.get(k)]
                s.extra.update({'hotbar': hot, 'slot': self.slot, 'armor': self.armor})
                return s
        w = W()
        w.worn_ = worn
        w.armor = {'mc:iron_chestplate': 6, 'mc:leather_chestplate': 3}.get(worn, 0)
        return w

    def run_equip(self, w, item):
        r = TaskRunner(w)
        r.start([('equip', item)], item, 1)
        ups = 0
        for _ in range(120):
            intent = r.update(w.snap(), 1 / 30, False)
            if intent is not None:
                if intent.pitch < 0:
                    ups += 1
                hot = w.snap().extra['hotbar']
                for i in range(len(hot)):
                    if f'slot{i + 1}' in intent.taps:
                        w.slot = i
                if 'interact' in intent.taps:               # (used from the hand: worn, swapped)
                    held = w.snap().extra['hotbar'][w.slot][0]
                    w.inv[held] -= 1
                    if w.worn_:
                        w.inv[w.worn_] = w.inv.get(w.worn_, 0) + 1
                    w.worn_ = held
                    w.armor = {'mc:iron_chestplate': 6, 'mc:leather_chestplate': 3}[held]
            if not r.active:
                break
        return r, ups

    def test_put_on_looking_up(self):
        w = self.world(None)
        w.inv['mc:iron_chestplate'] = 1
        r, ups = self.run_equip(w, 'mc:iron_chestplate')
        self.assertIn('done', r.events)
        self.assertEqual((w.worn_, w.armor), ('mc:iron_chestplate', 6))
        self.assertGreater(ups, 0)                      # (looked up first: not used on a block in front)

    def test_worse_than_worn_swapped_back(self):
        w = self.world('mc:iron_chestplate')
        w.inv['mc:leather_chestplate'] = 1
        r, _ups = self.run_equip(w, 'mc:leather_chestplate')
        self.assertIn('done', r.events)
        self.assertEqual((w.worn_, w.armor), ('mc:iron_chestplate', 6))


class UseOnTests(unittest.TestCase):
    """("Sleep in the bed": "I can't lie down in it myself", 2026-10-17.) A block or a creature right-clicked, as a
    player does, walked to first."""

    def test_a_bed_walked_to_beside_then_right_clicked_not_crouched(self):
        class W(World):
            aim, beside = None, None

            def snap(self):
                s = super().snap()
                s.extra.update({'hotbar': [('mc:cobblestone', 64), None], 'slot': self.slot, 'aim_block': self.aim})
                return s
            slot = 0

            def task_walk(self, point, name, beside=None):
                self.walks.append(name)
                self.beside = beside

            def task_aim(self, key, eye):
                return (key[0] + 0.5, key[1] + 0.3, key[2] + 0.5), None
        w = W()
        r = TaskRunner(w)
        r.start([('use', 'block', (12, 64, 3), (-12.5, 64.0, 3.5), None, 'white bed')], 'use', 1)
        clicks = []
        for i in range(90):
            intent = r.update(w.snap(), 1 / 30, False)
            if intent is not None:
                if 'slot2' in intent.taps:
                    w.slot = 1                           # (an empty hand)
                if 'interact' in intent.taps:
                    clicks.append(w.slot)
                    self.assertNotIn('sneak', intent.keys)
            if r.phase == 'go' and i > 5:
                w.aim = (12, 64, 3)                      # (there, looking at it)
                r.on_event('arrived')
            if not r.active:
                break
        self.assertEqual((w.walks, w.beside), (['white bed'], (12, 64, 3)))
        self.assertEqual(clicks, [1])                    # (once, with the empty hand)
        self.assertIn('done', r.events)

    def test_an_item_used_on_a_creature(self):
        from game_assistant.core.interface import Entity

        class W(World):
            approached = []

            def snap(self):
                s = super().snap()
                e = Entity(5, 1, 'mc:horse', (0.0, 0.0, 2.0), (0.0, 1.0, 2.0), 0.7, 1.6, False, False, name='Horse')
                s.extra.update({'hotbar': [('mc:stick', 1), ('mc:saddle', 1)], 'slot': self.slot})
                return s.__class__(**{**s.__dict__, 'entities': (e,), 'target_id': 5})
            slot = 0
        w = W()
        w.inv['mc:saddle'] = 1
        r = TaskRunner(w)
        r.start([('use', 'creature', 5, (0.0, 0.0, 2.0), 'mc:saddle', 'Horse')], 'use', 1)
        clicks = []
        for _ in range(60):
            intent = r.update(w.snap(), 1 / 30, False)
            if intent is not None:
                if 'slot2' in intent.taps:
                    w.slot = 1
                if 'interact' in intent.taps:
                    clicks.append(w.slot)
            if not r.active:
                break
        self.assertEqual(clicks, [1])                    # (in reach already: the saddle used on it, no walk)
        self.assertIn('done', r.events)


class InvScreen:
    """The player's inventory screen, as the game clicks it: 9 hotbar slots (indices 36-44) and 27 above them
    (9-35). A left click with nothing on the cursor picks the stack up; with a stack, on an empty slot puts it
    down, on the same item adds to it, on another swaps the two."""

    def __init__(self, hotbar, main):
        self.slot = {36 + i: tuple(s) if s else None for i, s in enumerate(hotbar)}
        self.slot.update({9 + i: tuple(s) if s else None for i, s in enumerate(main)})
        for i in range(9, 45):
            self.slot.setdefault(i, None)
        self.carried, self.open, self.ptr = None, False, (0, 0)

    def xy(self, i):
        return (100 + 40 * ((i - 9) % 9), 300 + 40 * ((i - 9) // 9))

    def screen(self):
        slots = []
        for i in range(9, 45):
            item, n = self.slot[i] or ('', 0)
            role = f'hotbar{i - 36}' if i >= 36 else f'main{i}'
            slots.append([i, *self.xy(i), item, n, role])
        c = self.carried or ('', 0)
        hov = next((s[0] for s in slots if (s[1], s[2]) == self.ptr), -1)
        return {'menu': 'InventoryMenu', 'slots': slots, 'carried': c[0], 'carried_count': c[1], 'hovered': hov,
                'mouse': [self.ptr[0], self.ptr[1], 0]}

    def click(self):
        i = next((k for k in self.slot if self.xy(k) == self.ptr), None)
        if i is None:
            return
        here = self.slot[i]
        if self.carried is None:
            self.carried, self.slot[i] = here, None
        elif here is None:
            self.slot[i], self.carried = self.carried, None
        elif here[0] == self.carried[0]:
            self.slot[i], self.carried = (here[0], here[1] + self.carried[1]), None
        else:
            self.slot[i], self.carried = self.carried, here

    def hotbar(self):
        return [self.slot[36 + i] for i in range(9)]

    def inventory(self):
        out = {}
        for s in list(self.slot.values()) + [self.carried]:
            if s:
                out[s[0]] = out.get(s[0], 0) + s[1]
        return out


class ArrangeTests(unittest.TestCase):
    """The inventory skill ("move the iron into my hotbar and sort things" was "my orders don't reach the
    inventory slots", while crafting used the same screen, 2026-10-16)."""

    def run_arrange(self, sc, layout):
        class W(World):
            def task_point(self, x, y):
                sc.ptr = (round(x), round(y))

            def task_press(self, button):
                sc.click()
        r = TaskRunner(W())
        r.start([('arrange', layout)], 'hotbar', 1)
        for _ in range(30 * 20):
            snap = Snapshot('t', 1, True, Player((0.0, 0.0, 0.0), None, 'alive'), None, (), None,
                            screen=Screen(0, 0, 800, 600, True),
                            extra={'inventory': sc.inventory(), 'container': sc.screen() if sc.open else None})
            intent = r.update(snap, 1 / 30, False)
            if intent is not None:
                if 'inventory' in intent.taps:
                    sc.open = True
                if 'escape' in intent.taps:
                    sc.open = False
            if not r.active:
                break
        return r

    def test_an_item_from_the_inventory_into_the_hotbar_and_a_sort(self):
        sc = InvScreen([('mc:dirt', 9), ('mc:apple', 1), None, ('mc:stone_pickaxe', 1), None, None, None, None,
                        None], [('mc:raw_iron', 6), ('mc:stone_sword', 1), ('mc:cobblestone', 64)])
        layout = ('mc:stone_sword', 'mc:stone_pickaxe', None, None, 'mc:apple', 'mc:cobblestone', 'mc:raw_iron',
                  None, None)
        before = sc.inventory()
        r = self.run_arrange(sc, layout)
        self.assertEqual(r.result, 'done')
        got = [s[0] if s else None for s in sc.hotbar()]
        for i, want in enumerate(layout):
            if want:
                self.assertEqual(got[i], want, (i, got))
        self.assertEqual(sc.inventory(), before)        # (nothing lost or dropped)
        self.assertIsNone(sc.carried)
        self.assertFalse(sc.open)


class HotbarLayoutTests(unittest.TestCase):
    def test_the_usual_layout_with_what_was_asked_for(self):
        from game_assistant.games.minecraft.adapter import MinecraftAdapter
        a = MinecraftAdapter()
        inv = {'minecraft:dirt': 9, 'minecraft:apple': 1, 'minecraft:stone_pickaxe': 1,
               'minecraft:wooden_pickaxe': 1, 'minecraft:raw_iron': 6, 'minecraft:wooden_sword': 1,
               'minecraft:cobblestone': 132, 'minecraft:shield': 1}
        hot = [('minecraft:dirt', 9), ('minecraft:apple', 1), ('minecraft:stone_pickaxe', 1)]
        lay = a.hotbar_layout(inv, hot, ['minecraft:raw_iron'], sort=True)
        self.assertEqual(lay[:3], ('minecraft:wooden_sword', 'minecraft:stone_pickaxe', 'minecraft:apple'))
        self.assertIn('minecraft:raw_iron', lay)
        self.assertIn('minecraft:cobblestone', lay)
        lay = a.hotbar_layout(inv, hot, ['minecraft:raw_iron'])   # (not sorted: into a free slot)
        self.assertEqual(lay[:4], ('minecraft:dirt', 'minecraft:apple', 'minecraft:stone_pickaxe',
                                   'minecraft:raw_iron'))
