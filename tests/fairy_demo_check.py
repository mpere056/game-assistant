"""Visual check of the fairy on the demo scene: runs the real app and saves three small crops around
the fairy (following, showing the pretend wolf, coming back) into runtime/fairy_demo.png. Only a
small square around the fairy is captured, not the whole desktop. Needs Pillow.
Run: .venv\\Scripts\\python -m tests.fairy_demo_check"""
import threading
import time

from PIL import Image, ImageGrab

from game_assistant import app as A


def main():
    a = A.App('demo')
    shots = []

    def grab():
        v = a.last_view
        x, y = (int(v.screen_x), int(v.screen_y)) if v and v.visible else (a.adapter.w // 2, a.adapter.h // 2)
        shots.append((ImageGrab.grab(bbox=(x - 90, y - 90, x + 90, y + 90)), f'{x},{y} {v.mode if v else "-"}'))

    def script():
        time.sleep(4)
        grab()
        a.companion.show(entity_id=1, seconds=5)  # what the fairy tool does for "show me the wolf"
        time.sleep(2.5)
        grab()
        a.root.after(0, lambda: a.ask('come back', 'typed'))  # an instant local command
        time.sleep(2.5)
        grab()
        print('status line:', a.status.cget('text'))
        print('fairy:', [round(v, 2) for v in a.companion.pos], a.companion.mode)
        a.root.after(0, a.close)

    threading.Thread(target=script, daemon=True).start()
    a.run()
    print('fairy at:', [label for _, label in shots])
    sheet = Image.new('RGB', (3 * 360 + 20, 360), (0, 0, 0))
    for i, (im, _) in enumerate(shots):
        sheet.paste(im.resize((360, 360)), (i * 370, 0))
    sheet.save('runtime/fairy_demo.png')
    print('saved runtime/fairy_demo.png')


if __name__ == '__main__':
    main()
