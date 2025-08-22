import pynput

def move_left(controller: pynput.keyboard.Controller):
    controller.press(pynput.keyboard.Key.left)
    controller.release(pynput.keyboard.Key.left)

def move_right(controller: pynput.keyboard.Controller):
    controller.press(pynput.keyboard.Key.right)
    controller.release(pynput.keyboard.Key.right)

def move_up(controller: pynput.keyboard.Controller):
    controller.press(pynput.keyboard.Key.up)
    controller.release(pynput.keyboard.Key.up)

def move_down(controller: pynput.keyboard.Controller):
    controller.press(pynput.keyboard.Key.down)
    controller.release(pynput.keyboard.Key.down)
