"""Live check of the agent's tool loop against a pretend game scene (real Claude Haiku 5.5 calls,
well under a cent). Not part of the offline tests. Run: .venv\\Scripts\\python -m tests.agent_live_check"""
from game_assistant.core.agent import Agent
from game_assistant.core.interface import (CAP_CAMERA, CAP_ENTITIES, CAP_KNOWLEDGE, CAP_RAYCAST, CAP_SEARCH, Camera, Entity,
                                          Player, RayHit, Snapshot)
from game_assistant.games.eldenring.knowledge import EldenRingKnowledge

CAM = Camera((0.0, 1.6, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), 60.0, 16 / 9)
SNAIL = Entity(1, 36616040, 'c3661', (0.0, 0.0, 10.0), (0.0, 0.9, 10.0), 0.6, 1.8, True, False, 904, 904)
WOLF = Entity(2, 10000000, 'c4180', (-8.0, 0.0, 6.0), (-8.0, 0.6, 6.0), 0.6, 1.2, True, False, 120, 120)
FACTS = {
    36616040: {'name': None, 'base_hp': 904, 'damage_taken_percent': {'standard': 100, 'fire': 120, 'holy': 60},
               'weak_to': ['fire'], 'resists': ['holy'], 'status_buildup_needed': {'frost': 180},
               'immune_to': ['bleed', 'sleep'], 'name_note': 'no name in the names list for this type; say you do not know its name'},
    10000000: {'name': 'Test Wolf', 'base_hp': 120, 'damage_taken_percent': {'standard': 100}, 'weak_to': [],
               'resists': [], 'status_buildup_needed': {'bleed': 100}, 'immune_to': []},
}


class PretendGame:
    game = 'Elden Ring (pretend scene for testing)'
    capabilities = frozenset({CAP_CAMERA, CAP_ENTITIES, CAP_RAYCAST, CAP_KNOWLEDGE, CAP_SEARCH})
    web_sources = ('eldenring.wiki.fextralife.com', 'eldenring.fandom.com')
    kb = EldenRingKnowledge()  # the real name lists (Get-GameData.bat); no game needed

    def snapshot(self):
        return Snapshot(self.game, 1, True, Player((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 'alive'), CAM, (SNAIL, WOLF), None)

    def raycast(self, rays, timeout=2.0):
        return [RayHit(a, b, False) for a, b in rays]

    def knowledge(self, type_id):
        return FACTS.get(type_id)

    def search(self, kind, query):
        return self.kb.search(kind, query)


def main():
    agent = Agent(PretendGame())
    import sys
    qs = ['What is that and what is it weak to?', 'Is anything else nearby?', 'Can it be bled?',
          'Where can I find Moonveil?', 'Where do I get Golden Seeds?', 'Which region is Stormveil Cliffside in?',
          "What are Malenia's attacks and how do I dodge Waterfowl Dance?"]
    for q in qs[int(sys.argv[1]) if len(sys.argv) > 1 else 0:]:
        a = agent.ask(q)
        print(f'Q: {q}\nA: {a.text.strip()}\n   tools {a.tools_used}, first word {a.first_word_s and round(a.first_word_s, 2)} s, '
              f'total {a.total_s:.2f} s, ${a.usd:.5f}{"  NOTE " + a.notice if a.notice else ""}\n')


if __name__ == '__main__':
    main()
