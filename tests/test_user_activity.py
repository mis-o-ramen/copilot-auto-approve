from user_activity import MouseMoveIdle, UserActivity


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_active_when_recent_input():
    clock = Clock()
    idle = {"v": 0.5}
    ua = UserActivity(lambda: idle["v"], clock)
    assert ua.is_active(2.0)
    idle["v"] = 2.5
    assert not ua.is_active(2.0)


def test_own_click_is_not_user_activity():
    clock = Clock()
    idle = {"v": 10.0}
    ua = UserActivity(lambda: idle["v"], clock)
    ua.mark_self_input()          # t=100 で自動クリック
    clock.t = 101.0
    idle["v"] = 1.0               # 最後の入力 = 自分のクリック
    assert not ua.is_active(2.0)
    idle["v"] = 0.2               # クリック後にユーザーが操作した
    assert ua.is_active(2.0)


def test_mouse_move_idle():
    clock = Clock()
    pos = {"p": (0, 0)}
    idle = MouseMoveIdle(lambda: pos["p"], clock)
    clock.t = 103.0
    assert idle() == 3.0
    pos["p"] = (5, 5)
    assert idle() == 0.0
    clock.t = 104.5
    assert idle() == 1.5
