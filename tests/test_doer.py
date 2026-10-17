"""Thinking orders through (core/doer.py) with a stand-in model and world."""
import json
import unittest
from types import SimpleNamespace as NS

from game_assistant.core.doer import Doer


class Guard:
    def check(self):
        pass

    def record(self, *a):
        pass


class Client:
    """Answers in turn with the scripted tool calls; keeps what it was sent."""

    def __init__(self, script):
        self.script, self.sent = list(script), []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw['messages'][-1])
        name, inp = self.script.pop(0)
        return NS(content=[NS(type='tool_use', name=name, input=inp, id=f'id{len(self.sent)}')], usage=None)


class World:
    def __init__(self):
        self.orders, self.said, self.busy_for = [], [], 0

    def doer_state(self):
        return f'holding: {len(self.orders)} things'

    def doer_act(self, order):
        if 'fly' in order:
            return "didn't understand that order"
        self.orders.append(order)
        self.busy_for = 3
        self.said.append((0, f'Done: {order}'))
        return None

    def doer_busy(self):
        self.busy_for -= 1
        return self.busy_for > 0

    def doer_said_since(self, t):
        return [line for _t, line in self.said[-1:]]

    def doer_where(self, name):
        return 'Stone: 12 blocks north'

    def doer_say(self, line):
        self.said.append((0, line))

    def doer_stop(self):
        pass


class DoerTests(unittest.TestCase):
    def test_acts_step_by_step_sees_outcomes_and_finishes(self):
        script = [('where_is', {'name': 'stone'}), ('act', {'order': 'fly to the moon'}),
                  ('act', {'order': 'get me a wooden pickaxe'}), ('act', {'order': 'mine 3 cobblestone'}),
                  ('finish', {'line': 'Here is your stone!'})]
        client, world = Client(script), World()
        d = Doer(world, 'Minecraft', "'get me 3 sticks'", guard=Guard(), client=client)
        said = d.run('get me some stone')
        self.assertEqual(said, 'Here is your stone!')
        self.assertEqual(world.orders, ['get me a wooden pickaxe', 'mine 3 cobblestone'])
        self.assertIn('not_started', client.sent[2]['content'][0]['content'])   # the failure came back to it
        self.assertIn('Done: get me a wooden pickaxe', client.sent[3]['content'][0]['content'])
        self.assertIn('holding: 1 things', client.sent[3]['content'][0]['content'])   # and the state after

    def test_an_answer_in_words_without_finish_is_said_too(self):
        client, world = Client([]), World()
        client.create = lambda **kw: NS(content=[NS(type='text', text='Yes, with one running jump.')], usage=None)
        said = Doer(world, 'Minecraft', '', guard=Guard(), client=client).run('can you jump onto that table?')
        self.assertEqual(said, 'Yes, with one running jump.')
        self.assertIn((0, 'Yes, with one running jump.'), world.said)

    def test_the_way_checked_is_given_to_the_next_order(self):
        # ("okay, do the jump" after check_way: walked to beside the table, not onto it, 2026-10-16)
        way = {'x': 332, 'y': 81, 'z': -423, 'place_blocks': False, 'dig': True}
        world = World()
        world.doer_check_way = lambda args: json.dumps({'can': True, 'go_to': way})
        client = Client([('check_way', {'target': 'that'}), ('finish', {'line': 'Yes!'}),
                         ('finish', {'line': 'Up!'})])
        d = Doer(world, 'Minecraft', '', guard=Guard(), client=client)
        d.run('can you jump onto that table?')
        d.run('okay, do the jump')
        self.assertIn('"x": 332', client.sent[-1]['content'])
        self.assertIn('way_checked_last', client.sent[-1]['content'])

    def test_says_what_it_is_doing_as_each_step_starts(self):
        script = [('act', {'order': 'get me a wooden pickaxe', 'say': 'No pickaxe: making one first!'}),
                  ('finish', {'line': 'Done!'})]
        client, world = Client(script), World()
        Doer(world, 'Minecraft', '', guard=Guard(), client=client).run('dig down to the cave')
        lines = [line for _t, line in world.said]
        self.assertLess(lines.index('No pickaxe: making one first!'), lines.index('Done: get me a wooden pickaxe'))


class TypedWorld(World):
    """A world with the typed tools: a get gives the item; how it ended comes back as data."""

    def __init__(self):
        super().__init__()
        self.held, self.done = {'minecraft:oak_log': 3}, []

    def doer_tools(self):
        return {'get', 'how_to_get', 'go_to', 'situation'}

    def doer_do(self, tool, args):
        if tool == 'go_to' and 'x' not in args and 'place' not in args:
            return 'go_to needs x, y and z, or a place'
        self.done.append((tool, args))
        if tool == 'get':
            self.held['minecraft:oak_log'] -= 1
            self.held[f"minecraft:{args['item']}"] = args.get('count', 1)
        self.busy_for = 2
        return None

    def doer_inventory(self):
        return dict(self.held)

    def doer_outcome(self, since):
        tool = self.done[-1][0]
        return {'ok': tool == 'get', 'why': None if tool == 'get' else {'kind': 'unreachable', 'what': 'there'}}

    def doer_how_to_get(self, item, count):
        return json.dumps({'steps': [f'craft {count} {item}'], 'seconds': 3})

    def doer_situation(self):
        return json.dumps({'health': '20/20'})


class TypedToolTests(unittest.TestCase):
    def test_typed_tools_offered_as_the_game_has_them(self):
        d = Doer(TypedWorld(), 'Minecraft', '', guard=Guard(), client=Client([]))
        names = [t['name'] for t in d.tools]
        self.assertIn('get', names)
        self.assertIn('situation', names)
        self.assertNotIn('fight', names)                 # (not this game's)
        self.assertIn('act', names)                      # words, for the rest
        self.assertIn('get, how_to_get, go_to, situation, act', d.system)
        plain = Doer(World(), 'Minecraft', '', guard=Guard(), client=Client([]))
        self.assertNotIn('get', [t['name'] for t in plain.tools])

    def test_a_typed_order_comes_back_as_data(self):
        script = [('how_to_get', {'item': 'oak_planks', 'count': 4}),
                  ('get', {'item': 'oak_planks', 'count': 4, 'say': 'Planks first!'}),
                  ('go_to', {'x': 1, 'y': 2, 'z': 3}), ('go_to', {}), ('situation', {}),
                  ('finish', {'line': 'Done!'})]
        client, world = Client(script), TypedWorld()
        Doer(world, 'Minecraft', '', guard=Guard(), client=client).run('get me planks')
        self.assertEqual(world.done[0], ('get', {'item': 'oak_planks', 'count': 4}))   # (no 'say' in it)
        self.assertIn('craft 4 oak_planks', client.sent[1]['content'][0]['content'])
        got = json.loads(client.sent[2]['content'][0]['content'].split('\n<state>')[0])
        self.assertTrue(got['ok'])
        self.assertEqual(got['changed'], {'oak_log': '-1', 'oak_planks': '+4'})
        went = json.loads(client.sent[3]['content'][0]['content'].split('\n<state>')[0])
        self.assertEqual((went['ok'], went['why']['kind']), (False, 'unreachable'))
        self.assertEqual(went['changed'], {})                                     # nothing changed
        self.assertIn('not_started', client.sent[4]['content'][0]['content'])
        self.assertIn('20/20', client.sent[5]['content'][0]['content'])
        self.assertIn('Planks first!', [line for _t, line in world.said])

    def test_an_order_planned_again_mid_way_is_waited_for(self):
        # (a step fails, the goal plans on from there: for a moment nothing runs; that's not the end)
        class Replanned(TypedWorld):
            seq = [True, True, False, True, True, False]

            def doer_busy(self):
                return self.seq.pop(0) if self.seq else False

            def doer_outcome(self, since):
                return {'ok': not self.seq, 'why': None if not self.seq else {'kind': 'unreachable'}}
        client, world = Client([('get', {'item': 'oak_planks'}), ('finish', {'line': 'Done!'})]), Replanned()
        Doer(world, 'Minecraft', '', guard=Guard(), client=client).run('get me planks')
        self.assertTrue(json.loads(client.sent[1]['content'][0]['content'].split('\n<state>')[0])['ok'])


class TwoOrdersTests(unittest.TestCase):
    def test_a_newer_order_ends_the_older_each_with_its_own_log(self):
        # ("make me a bed" and "hello, can you hear me?" thought through at once shared one log: IndexError, and the
        # bed order died without a word, 2026-10-16)
        import threading
        import time

        class Slow(Client):
            def create(self, **kw):
                time.sleep(0.3)
                return super().create(**kw)
        first, second = Slow([('where_is', {'name': 'sheep'}), ('finish', {'line': 'bed'})]), \
            Client([('finish', {'line': 'Yes, I hear you!'})])
        world = World()
        d = Doer(world, 'Minecraft', '', guard=Guard(), client=first)
        log1, log2, out = [], [], {}
        t = threading.Thread(target=lambda: out.setdefault('bed', d.run('make me a bed', log=log1)))
        t.start()
        time.sleep(0.1)
        d.client = second
        out['hello'] = d.run('hello, can you hear me?', log=log2)
        t.join(timeout=3)
        self.assertEqual(out['hello'], 'Yes, I hear you!')
        self.assertEqual(out['bed'], '')                               # (ended: a newer order came)
        self.assertEqual([x['tool'] for x in log2], ['finish'])


class OpenOrdersTests(unittest.TestCase):
    """Orders cut short by a newer one are kept, shown to the doer, and carried on unless replaced ("while you're
    doing this, can you also make me full iron armor?" dropped the shield under way, 2026-10-16)."""

    def app(self):
        import os
        from game_assistant.app import App
        app = App.__new__(App)
        app.lines = []
        app._navi_line = lambda line, urgent=False: app.lines.append(line)
        app.resolver = type('R', (), {'recent': lambda self: '', 'heard': lambda self, t: None})()
        app.log = type('L', (), {'open': lambda self, *a, **k: open(os.devnull, 'w')})()
        app._error = lambda where, e: None
        return app

    def test_cut_short_stays_open_and_the_next_says_what_became_of_it(self):
        app = self.app()

        class D:
            earlier = None

            def __init__(self, plan):
                self.plan = plan                              # order -> (cut short?, earlier)

            def run(self, order, recent, log=None):
                cut, self.earlier = self.plan[order]
                if cut:
                    app._doer_turn += 1                       # (a newer order came while it ran)
                return 'ok'
        app.doer = D({'make me a shield': (True, None), 'also make me iron armor': (False, 'done')})
        app._doer_order('make me a shield')
        self.assertEqual(app._open_orders, ['make me a shield'])         # (cut short: still to do)
        app._doer_order('also make me iron armor')
        self.assertEqual(app._open_orders, [])                            # (both done)
        app.doer = D({'make me a shield': (True, None), 'the pickaxe broke, make one': (False, 'open')})
        app._doer_order('make me a shield')
        app._doer_order('the pickaxe broke, make one')
        self.assertEqual(app._open_orders, ['make me a shield'])         # (fixed; the shield still to do)
