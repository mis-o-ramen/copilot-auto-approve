import cv2
import numpy as np

import auto_approve as aa


def make_button() -> np.ndarray:
    btn = np.full((28, 72), 60, dtype=np.uint8)
    cv2.putText(btn, "Allow", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 1, cv2.LINE_AA)
    return btn


def make_screen(button: np.ndarray | None, at=(300, 150)) -> np.ndarray:
    rng = np.random.default_rng(0)
    screen = rng.integers(0, 40, size=(600, 800), dtype=np.uint8)
    cv2.putText(screen, "Deny", (100, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 1, cv2.LINE_AA)
    if button is not None:
        x, y = at
        screen[y:y + button.shape[0], x:x + button.shape[1]] = button
    return screen


def test_finds_button_center():
    btn = make_button()
    tpl = [aa.Template("allow.png", btn)]
    m = aa.find_best_match(make_screen(btn), tpl, 0.85, [1.0])
    assert m is not None
    assert (m.x, m.y) == (300, 150)
    assert m.center == (336, 164)


def test_no_match_when_absent():
    tpl = [aa.Template("allow.png", make_button())]
    assert aa.find_best_match(make_screen(None), tpl, 0.85, [1.0]) is None


def test_multiscale_finds_scaled_button():
    btn = make_button()
    big = cv2.resize(btn, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_CUBIC)
    tpl = [aa.Template("allow.png", btn)]
    assert aa.find_best_match(make_screen(big), tpl, 0.85, [1.0]) is None
    m = aa.find_best_match(make_screen(big), tpl, 0.85, [1.0, 1.25])
    assert m is not None and abs(m.x - 300) <= 1 and abs(m.y - 150) <= 1


def test_cli_test_image(tmp_path):
    btn = make_button()
    cv2.imwrite(str(tmp_path / "allow.png"), btn)
    cv2.imwrite(str(tmp_path / "screen.png"), make_screen(btn))
    assert aa.main([str(tmp_path / "allow.png"), "--test-image", str(tmp_path / "screen.png")]) == 0
    cv2.imwrite(str(tmp_path / "empty.png"), make_screen(None))
    assert aa.main([str(tmp_path / "allow.png"), "--test-image", str(tmp_path / "empty.png")]) == 1


def test_missing_templates(tmp_path):
    assert aa.main([str(tmp_path)]) == 2
