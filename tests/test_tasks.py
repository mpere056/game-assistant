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
