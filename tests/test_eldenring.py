"""Offline tests for the Elden Ring param reader and facts, on a synthetic in-memory table."""
import struct
import unittest

from game_assistant.games.eldenring import params
from game_assistant.games.eldenring.adapter import npc_facts

BASE = 0x7FF000000000


EXE = 0x7FF600000000
REP = 0x7FF100000000


class FakeMemory:
    """Sparse memory: the param file image at BASE, plus the exe's repository pointer chain."""

    def __init__(self, image: bytes, file_addr: int, index: int = 64):
        self.regions = {BASE: image}
        ptrs = {EXE + params.SOLO_PARAM_REPOSITORY_RVA: REP,
                REP + params.HOLDERS + params.HOLDER_SIZE * index: REP + 0x10000,
                REP + 0x10000 + 0x80: REP + 0x20000,
                REP + 0x20000 + 0x80: file_addr}
        for addr, value in ptrs.items():
            self.regions[addr] = struct.pack('<Q', value)

    def read(self, addr, n):
        for base, data in self.regions.items():
            if base <= addr and addr + n <= base + len(data):
                return data[addr - base:addr - base + n]
        if any(base <= addr < base + len(data) for base, data in self.regions.items()):
            raise params.MailboxError('out of range')
        return bytes(n)  # unmapped pointers read as zero here

    def u64(self, addr):
        return struct.unpack('<Q', self.read(addr, 8))[0]

    def modules(self):
        return {'eldenring.exe': (EXE, 0x5000000)}


def make_table(rows: dict[int, dict], pad_before=0x20000) -> tuple[bytes, int]:
    """Build a ParamFile like the game's: header, descriptors, struct name, row data."""
    n = len(rows)
    name = b'NPC_PARAM_ST\0'
    name_off = params.HEADER_SIZE + n * params.DESC_SIZE
    data_off = (name_off + len(name) + 0xF) & ~0xF
    f = bytearray(data_off + n * params.NPC_PARAM_SIZE)
    struct.pack_into('<H', f, 0x0A, n)
    struct.pack_into('<I', f, 0x10, name_off)
    f[0x2D] = 0x84 | 0x01
    for i, (rid, fields) in enumerate(sorted(rows.items())):
        struct.pack_into('<IIQQ', f, params.HEADER_SIZE + i * params.DESC_SIZE, rid, 0,
                         data_off + i * params.NPC_PARAM_SIZE, name_off)
        row = bytearray(params.NPC_PARAM_SIZE)
        for k, v in fields.items():
            off, fmt = params.NPC_FIELDS[k]
            struct.pack_into('<' + fmt, row, off, v)
        f[data_off + i * params.NPC_PARAM_SIZE:data_off + (i + 1) * params.NPC_PARAM_SIZE] = row
    f[name_off:name_off + len(name)] = name
    image = bytes(pad_before) + bytes(f) + bytes(0x100)
    return image, BASE + pad_before


class ParamReaderTests(unittest.TestCase):
    def test_finds_and_reads_rows(self):
        image, addr = make_table({
            36616040: {'hp': 904, 'taken_fire': 1.2, 'taken_holy': 0.6, 'resist_bleed': 999, 'resist_frost': 180},
            10000000: {'hp': 50},
        })
        mem = FakeMemory(image, addr)
        self.assertEqual(params.find_param_file(mem, 'NPC_PARAM_ST'), addr)
        rows = params.read_npc_params(mem)
        self.assertEqual(set(rows), {36616040, 10000000})
        self.assertEqual(rows[36616040]['hp'], 904)
        self.assertAlmostEqual(rows[36616040]['taken_fire'], 1.2, places=5)
        self.assertEqual(rows[36616040]['resist_frost'], 180)

    def test_wrong_row_size_is_refused(self):
        image, addr = make_table({1: {}, 2: {}})
        with self.assertRaises(params.ParamNotFound):
            params.read_param(FakeMemory(image, addr), 'NPC_PARAM_ST', params.NPC_PARAM_SIZE - 8)

    def test_other_holder_index_is_found(self):
        image, addr = make_table({1: {}})
        self.assertEqual(params.find_param_file(FakeMemory(image, addr, index=70), 'NPC_PARAM_ST'), addr)


class FactsTests(unittest.TestCase):
    def test_weakness_resistance_and_immunity(self):
        row = {k: 0 for k in params.NPC_FIELDS}
        row.update(hp=904, runes=120, taken_standard=1.0, taken_slash=1.0, taken_strike=1.0, taken_pierce=1.0,
                   taken_magic=1.0, taken_fire=1.2, taken_lightning=1.0, taken_holy=0.6,
                   resist_poison=200, resist_scarlet_rot=200, resist_bleed=999, resist_frost=180,
                   resist_sleep=999, resist_madness=999, resist_death_blight=999)
        f = npc_facts(row, 'Test Snail')
        self.assertEqual(f['weak_to'], ['fire'])
        self.assertEqual(f['resists'], ['holy'])
        self.assertIn('bleed', f['immune_to'])
        self.assertEqual(f['status_buildup_needed']['frost'], 180)
        self.assertNotIn('name_note', f)
        self.assertIn('name_note', npc_facts(row, None))


if __name__ == '__main__':
    unittest.main()


class WikiTextTest(unittest.TestCase):
    def test_templates_become_words(self):
        from game_assistant.games.eldenring.wiki import clean
        text = ("'''{{PAGENAME}}''' is an [[Info Item]] in {{ER}}.\n"
                "{{Description\n|EN_line1=\nJump while on horseback near a spiritspring.\n}}\n"
                "{{Quote | speaker = Melina | text = I offer you an accord.}}\n"
                "{{Drops table|row\n| name = [[Bloodhound Claws]]\n| quantity = 1\n| notes = Once\n}}\n"
                "Deals {{dmg|fire}} damage. {{icon|hp|link=HP}}{{Fairuse}}")
        out = clean(text, 'About Spiritspring Jumping')
        self.assertIn('About Spiritspring Jumping is an Info Item in Elden Ring.', out)
        self.assertIn('Jump while on horseback near a spiritspring.', out)
        self.assertIn('"I offer you an accord." (Melina)', out)
        self.assertIn('name: Bloodhound Claws, quantity: 1, notes: Once', out)
        self.assertIn('Deals fire damage.', out)
        self.assertNotIn('{{', out)

    def test_names_ignore_accents(self):
        from game_assistant.games.eldenring.wiki import _fold
        self.assertEqual(_fold(' Kalé '), 'kale')


class AttackWindowTests(unittest.TestCase):
    def test_variants_use_their_base_model(self):
        """c4311 (Godrick Soldier) has no animation files: it plays c4310's (measured live)."""
        from game_assistant.games.eldenring.adapter import EldenRingAdapter
        a = EldenRingAdapter.__new__(EldenRingAdapter)
        a._attacks = {'c4310': {'attacks': {'3000': [[0.8, 0.933], [0.8, 0.933]]}}}
        self.assertEqual(a.attack_windows('c4311', 3000), [(0.8, 0.933)])   # duplicates once
        self.assertEqual(a.attack_windows('c4310', 3000), [(0.8, 0.933)])
        self.assertEqual(a.attack_windows('c4311', 20), [])
        self.assertEqual(a.attack_windows('c9999', 3000), [])


class AnimQueueTests(unittest.TestCase):
    def test_every_playing_animation_from_the_queue(self):
        """A Godrick Soldier's real queue: its 4 s animation and a 0.03 s layer alternate every frame;
        the newest entry alone (what was read first) is only the layer."""
        from game_assistant.games.eldenring.adapter import _playing
        blob = bytearray(0xB8)
        struct.pack_into('<QIIfI', blob, 0, 7, 8, 6, 1.0, 1)        # write 8, read 6 (newest), valid
        for i in range(10):
            entry = (3043000, 0.02, 0.0, 0.03) if i % 2 == 0 else (3000704, 3.1 + i * 0.01, 0.0, 4.0)
            struct.pack_into('<ifff', blob, 0x18 + i * 16, *entry)
        anims, speed = _playing(bytes(blob), 0)
        self.assertEqual([a for a, _t, _l in anims], [3000704, 3043000])   # main (longest) first
        self.assertAlmostEqual(anims[0][1], 3.15, places=4)                 # its newest entry (index 5)


class BlockedGapTests(unittest.TestCase):
    """Gatefront, 2026-10-11 21:56: the navmesh has a gap at a barricade, made-up step links bridged it, and
    banning them one at a time left the next one: the walk got stuck at the barricade."""

    def test_made_up_links_near_a_blocked_one_are_not_used(self):
        from game_assistant.games.eldenring.navgraph import WorldGraph
        g = WorldGraph.__new__(WorldGraph)
        g.link_blocks = [(100.0, 10.0, 200.0)]
        self.assertTrue(g._near_block((101.0, 10.0, 201.0), (102.0, 10.0, 202.0)))    # 2 m away: blocked too
        self.assertFalse(g._near_block((110.0, 10.0, 200.0), (111.0, 10.0, 200.0)))   # 10 m away: fine
        self.assertFalse(g._near_block((101.0, 20.0, 201.0), (102.0, 20.0, 202.0)))   # another level

    def test_neighbours_drop_blocked_made_up_links_only(self):
        from game_assistant.games.eldenring.navgraph import FACE_BITS, WorldGraph
        g = WorldGraph.__new__(WorldGraph)
        g.learned, g.banned, g.walls = {}, set(), []
        g.link_blocks = [(0.0, 0.0, 0.0)]
        blk = type('B', (), {})()
        blk.nbrs = [[(5, (0.5, 0.0, 0.0), (1.0, 0.0, 0.0)),            # a navmesh edge: kept
                     (6, (0.5, 0.0, 0.0), (1.0, 0.0, 0.0), 3),         # a step link by the block: dropped
                     (7, (0.5, 0.0, 0.0), (1.0, 0.0, 0.0), 4)]]        # a ladder (the game's): kept
        g.block = lambda bid: blk
        self.assertEqual([x[0] for x in g.neighbours(0 << FACE_BITS)], [5, 7])
