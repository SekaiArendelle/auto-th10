import os
import time
import pynput
import auto_th10


class Snapshot:
    def __init__(self, process) -> None:
        self.enemies = auto_th10.get_enemies(process)
        self.enemy_bullets = auto_th10.get_enemy_bullets(process)
        self.enemy_lasers = auto_th10.get_enemy_lasers(process)
        self.hp = auto_th10.get_hp(process)
        self.player = auto_th10.get_player(process)
        self.power = auto_th10.get_power(process)
        self.resources = auto_th10.get_resources(process)
        self.score = auto_th10.get_score(process)

    def summarize(self) -> str:
        return f"hp: {self.hp}\n" \
            f"power: {self.power}\n" \
            f"score: {self.score}\n" \
            f"len enemy: {len(self.enemies)}\n" \
            f"len enemy_bullets: {len(self.enemy_bullets)}\n" \
            f"len enemy_lasers: {len(self.enemy_lasers)}\n" \
            f"len resources: {len(self.resources)}\n" \
            f"powers: {self.power}\n" \
            f"player: {self.player.x}, {self.player.y}\n"


class Th10Env:
    def __init__(self) -> None:
        self.controller = pynput.keyboard.Controller()
        self.hwnd = auto_th10.get_hwnd()
        self.pid, self.tid = auto_th10.get_pid_and_tid(self.hwnd)
        self.process = auto_th10.get_process_handle(self.pid)

    def reset(self) -> None:
        auto_th10.set_as_foreground(self.hwnd)
        if auto_th10.is_game_over(self.process):
            os.abort()
            # TODO
            # Th10 has two types of interface after game over?
            # 1. Select ...
            # 2. Whether store this history and select ...
            # self.controller.press(pynput.keyboard.Key.enter)
            # time.sleep(0.05)
            # self.controller.release(pynput.keyboard.Key.enter)
            # self.controller.press(pynput.keyboard.Key.down)
            # time.sleep(0.5)
            # self.controller.release(pynput.keyboard.Key.down)
            # self.controller.press(pynput.keyboard.Key.enter)
            # time.sleep(0.05)
            # self.controller.release(pynput.keyboard.Key.enter)
        else:
            self.controller.press(pynput.keyboard.Key.esc)
            time.sleep(0.05)
            self.controller.release(pynput.keyboard.Key.esc)
            self.controller.press(pynput.keyboard.Key.up)
            time.sleep(0.5)
            self.controller.release(pynput.keyboard.Key.up)
            self.controller.press(pynput.keyboard.Key.enter)
            time.sleep(0.1)
            self.controller.release(pynput.keyboard.Key.enter)
            self.controller.press(pynput.keyboard.Key.up)
            time.sleep(0.5)
            self.controller.release(pynput.keyboard.Key.up)
            self.controller.press(pynput.keyboard.Key.enter)
            time.sleep(0.05)
            self.controller.release(pynput.keyboard.Key.enter)

    def get_snapshot(self) -> Snapshot:
        return Snapshot(self.process)
