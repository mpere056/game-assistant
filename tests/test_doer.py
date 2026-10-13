"""Thinking orders through (core/doer.py) with a stand-in model and world."""
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
        self.assertIn('not done', client.sent[2]['content'][0]['content'])      # the failure came back to it
        self.assertIn('Done: get me a wooden pickaxe', client.sent[3]['content'][0]['content'])
        self.assertIn('holding: 1 things', client.sent[3]['content'][0]['content'])   # and the state after

    def test_says_what_it_is_doing_as_each_step_starts(self):
        script = [('act', {'order': 'get me a wooden pickaxe', 'say': 'No pickaxe: making one first!'}),
                  ('finish', {'line': 'Done!'})]
        client, world = Client(script), World()
        Doer(world, 'Minecraft', '', guard=Guard(), client=client).run('dig down to the cave')
        lines = [line for _t, line in world.said]
        self.assertLess(lines.index('No pickaxe: making one first!'), lines.index('Done: get me a wooden pickaxe'))
