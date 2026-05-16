"""
로또 6/45 번호 생성기 (Android · Kivy)
- 역대 3등 이상 미당첨 + 최근 회차와 1개 중복 조건의 무작위 세트 생성
- 자동 업데이트: 앱 시작 시 dhlottery API에서 신규 회차 자동 다운로드
- 캐시는 앱 내부 저장소(user_data_dir)에 영구 보관
"""

import concurrent.futures
import json
import os
import random
import shutil
import sys
import threading
import urllib.request
from collections import Counter

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

API_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"
THRESHOLD = 5


# ---------- 경로 ----------

def _bundle_dir():
    """앱 패키지(=APK)에 함께 들어 있는 파일 경로 (읽기 전용)."""
    if hasattr(sys, "_MEIPASS"):  # 안 쓰지만 안전망
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _writable_dir():
    """캐시를 쓸 수 있는 영구 디렉토리."""
    if platform == "android":
        try:
            from android.storage import app_storage_path  # type: ignore
            d = app_storage_path()
        except Exception:
            d = os.path.join(os.environ.get("ANDROID_PRIVATE", "."), "lotto")
    else:
        d = os.path.expanduser("~/.lotto_picker")
    os.makedirs(d, exist_ok=True)
    return d


CACHE_PATH = os.path.join(_writable_dir(), "lotto_cache.json")
STARTER_CACHE = os.path.join(_bundle_dir(), "lotto_cache.json")


def ensure_starter_cache():
    """쓰기 가능 위치에 캐시가 없으면 번들된 초기 캐시 복사."""
    if not os.path.exists(CACHE_PATH) and os.path.exists(STARTER_CACHE):
        shutil.copy2(STARTER_CACHE, CACHE_PATH)


# ---------- API ----------

def fetch_draw(round_no, timeout=10, retries=3):
    for _ in range(retries):
        try:
            req = urllib.request.Request(
                API_URL.format(round_no),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("returnValue") != "success":
                return None
            return {
                "round": data["drwNo"],
                "date": data["drwNoDate"],
                "numbers": sorted(data[f"drwtNo{i}"] for i in range(1, 7)),
                "bonus": data["bnusNo"],
            }
        except Exception:
            continue
    return None


def find_latest_round(start_hint=1200):
    n = max(start_hint, 1)
    consecutive_none = 0
    last_valid = 0
    upper_limit = n + 200
    while n <= upper_limit and consecutive_none < 2:
        d = fetch_draw(n)
        if d is None:
            consecutive_none += 1
        else:
            consecutive_none = 0
            last_valid = n
        n += 1
    return last_valid


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in cache.items()}, f, ensure_ascii=False)


def fetch_missing(cache, latest, progress_cb=None):
    for pass_idx in range(3):
        missing = [r for r in range(1, latest + 1) if r not in cache]
        if not missing:
            break
        total = len(missing)
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(fetch_draw, r): r for r in missing}
            for fut in concurrent.futures.as_completed(futs):
                r = futs[fut]
                d = fut.result()
                if d is not None:
                    cache[r] = d
                done += 1
                if progress_cb and (done % 5 == 0 or done == total):
                    progress_cb(done, total, pass_idx + 1)
                if done % 100 == 0:
                    save_cache(cache)
        save_cache(cache)
    save_cache(cache)
    return cache


# ---------- 생성 / 검증 ----------

def has_obvious_pattern(nums):
    sorted_nums = sorted(nums)
    run = 1
    for i in range(1, 6):
        if sorted_nums[i] == sorted_nums[i - 1] + 1:
            run += 1
            if run >= 3:
                return True
        else:
            run = 1
    if max(Counter(n // 10 for n in nums).values()) >= 5:
        return True
    odd = sum(n % 2 for n in nums)
    if odd == 0 or odd == 6:
        return True
    for div in (3, 4, 5, 6, 7):
        if all(n % div == 0 for n in nums):
            return True
    diffs = {sorted_nums[i + 1] - sorted_nums[i] for i in range(5)}
    if len(diffs) == 1:
        return True
    return False


def generate_sets(all_draws, latest_round, num_sets, no_pattern=True):
    latest_numbers = all_draws[latest_round]["numbers"]
    historical = [set(d["numbers"]) for d in all_draws.values()]

    results = []
    seen = set()
    attempts = 0
    max_attempts = 2_000_000

    while len(results) < num_sets and attempts < max_attempts:
        attempts += 1
        shared = random.choice(latest_numbers)
        pool = [n for n in range(1, 46) if n not in latest_numbers]
        others = random.sample(pool, 5)
        candidate = tuple(sorted([shared] + others))

        if candidate in seen:
            continue
        if no_pattern and has_obvious_pattern(candidate):
            continue

        cset = set(candidate)
        if any(len(cset & h) >= THRESHOLD for h in historical):
            continue

        seen.add(candidate)
        results.append(candidate)

    return results, latest_numbers


# ---------- UI ----------

class LottoApp(App):
    title = "로또 번호 생성기"

    def build(self):
        root = BoxLayout(orientation="vertical", padding=8, spacing=6)

        # 상단 정보
        self.info_lbl = Label(
            text="데이터 불러오는 중...",
            size_hint_y=None, height=44,
            halign="left", valign="middle",
            font_size="14sp", bold=True,
        )
        self.info_lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        root.add_widget(self.info_lbl)

        self.status_lbl = Label(
            text="",
            size_hint_y=None, height=24,
            halign="left", valign="middle",
            color=(0.4, 0.4, 0.4, 1),
            font_size="11sp",
        )
        self.status_lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        root.add_widget(self.status_lbl)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=18)
        root.add_widget(self.progress)

        # 버튼 줄
        btn_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=48, spacing=4)
        for n in (5, 10, 20, 30):
            b = Button(text=f"{n}세트", font_size="13sp")
            b.bind(on_release=lambda inst, c=n: self._on_generate(c))
            btn_row.add_widget(b)
        root.add_widget(btn_row)

        # 옵션 줄
        opt_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=36, spacing=4)
        self.no_pattern_cb = CheckBox(active=True, size_hint_x=None, width=36)
        opt_row.add_widget(self.no_pattern_cb)
        opt_row.add_widget(Label(text="패턴 제거", halign="left", valign="middle", size_hint_x=None, width=80,
                                  font_size="12sp"))
        clear_btn = Button(text="결과 지우기", font_size="12sp")
        clear_btn.bind(on_release=lambda inst: self._clear())
        opt_row.add_widget(clear_btn)
        root.add_widget(opt_row)

        # 당첨 이력 확인 줄
        check_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=44, spacing=4)
        self.check_input = TextInput(
            multiline=False, hint_text="6개 번호 (예: 1 7 13 22 30 41)",
            font_size="13sp",
        )
        check_row.add_widget(self.check_input)
        check_btn = Button(text="이력 확인", size_hint_x=None, width=96, font_size="12sp")
        check_btn.bind(on_release=lambda inst: self._on_check())
        check_row.add_widget(check_btn)
        root.add_widget(check_row)

        # 신규 회차 수동 입력 줄
        add_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=44, spacing=4)
        self.add_input = TextInput(
            multiline=False, hint_text="신규 회차 추가 (예: 1224 5 12 18 24 33 41 / 7)",
            font_size="12sp",
        )
        add_row.add_widget(self.add_input)
        add_btn = Button(text="회차 추가", size_hint_x=None, width=96, font_size="12sp")
        add_btn.bind(on_release=lambda inst: self._on_add_round())
        add_row.add_widget(add_btn)
        root.add_widget(add_row)

        # 출력 영역 (TextInput multiline=True 기본 → 자체 스크롤 가능)
        self.output = TextInput(
            text="",
            readonly=True,
            font_size="12sp",
            background_color=(0.97, 0.97, 0.97, 1),
        )
        root.add_widget(self.output)

        # 상태
        self.cache = {}
        self.latest_round = None

        self._set_buttons_enabled(False)
        threading.Thread(target=self._load_data, daemon=True).start()
        return root

    # ---------- 비동기 데이터 로딩 ----------
    def _load_data(self):
        try:
            ensure_starter_cache()
            Clock.schedule_once(lambda dt: self._set_status("캐시 확인 중..."))
            cache = load_cache()
            Clock.schedule_once(lambda dt: self._set_status(
                f"캐시 {len(cache)}회차 로드됨. 최신 회차 확인 중..."
            ))

            cache_max = max(cache.keys()) if cache else 0
            hint = cache_max + 1 if cache else 1200
            latest = find_latest_round(start_hint=hint)

            api_ok = latest > 0
            if not api_ok:
                if cache_max > 0:
                    latest = cache_max
                else:
                    Clock.schedule_once(lambda dt: self._set_status(
                        "오류: 캐시도 없고 네트워크도 안 됨"
                    ))
                    return
            else:
                Clock.schedule_once(lambda dt: self._set_status(
                    f"최신 회차: {latest}회 · 누락 회차 다운로드 중..."
                ))

                def on_progress(done, total, pass_idx=1):
                    pct = done / total * 100
                    Clock.schedule_once(lambda dt: self._set_progress(pct, f"다운로드 (pass {pass_idx}) {done}/{total}"))

                cache = fetch_missing(cache, latest, progress_cb=on_progress)

            if latest not in cache and cache:
                latest = max(cache.keys())

            self.cache = cache
            self.latest_round = latest
            info = cache[latest]

            nums = " ".join(f"{n:2d}" for n in info["numbers"])
            suffix = "" if api_ok else "  (오프라인)"
            Clock.schedule_once(lambda dt: self._on_ready(
                f"최근 {info['round']}회 ({info['date']})\n당첨: {nums}  + 보너스 {info['bonus']}",
                f"준비 완료 · 총 {len(cache)}회차{suffix}",
            ))
        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self._set_status(f"오류: {err}"))

    def _on_ready(self, info_text, status_text):
        self.info_lbl.text = info_text
        self.status_lbl.text = status_text
        self.progress.value = 100
        self._set_buttons_enabled(True)

    def _set_status(self, msg):
        self.status_lbl.text = msg

    def _set_progress(self, pct, msg):
        self.progress.value = pct
        self.status_lbl.text = msg

    def _set_buttons_enabled(self, enabled):
        def walk(w):
            if isinstance(w, Button):
                w.disabled = not enabled
            for c in w.children:
                walk(c)
        walk(self.root)

    # ---------- 액션 ----------
    def _on_generate(self, count):
        if not self.cache or self.latest_round is None:
            return
        self._set_buttons_enabled(False)
        self.output.text = f"{count}세트 생성 중..."
        threading.Thread(target=self._do_generate, args=(count,), daemon=True).start()

    def _do_generate(self, count):
        sets, latest_nums = generate_sets(
            self.cache, self.latest_round, count,
            no_pattern=self.no_pattern_cb.active,
        )
        lines = [f"=== {len(sets)}세트 (역대 3등 이상 미당첨 · 최근회차와 1개 중복) ==="]
        for i, s in enumerate(sets, 1):
            shared = next(n for n in s if n in latest_nums)
            nums = " ".join(f"{n:2d}" for n in s)
            lines.append(f"{i:2d}. {nums}  (공유: {shared})")
        if len(sets) < count:
            lines.append(f"\n주의: 조건을 만족하는 세트가 {len(sets)}개만 생성됨")
        text = "\n".join(lines)
        Clock.schedule_once(lambda dt: self._on_generate_done(text))

    def _on_generate_done(self, text):
        self.output.text = text
        self._set_buttons_enabled(True)

    def _on_check(self):
        if not self.cache:
            return
        raw = self.check_input.text
        try:
            tokens = [t for t in raw.replace(",", " ").replace("/", " ").split() if t]
            nums = [int(t) for t in tokens]
        except ValueError:
            self.output.text = "입력 오류: 숫자만 6개 (예: 1 7 13 22 30 41)"
            return
        if len(nums) != 6 or len(set(nums)) != 6 or not all(1 <= n <= 45 for n in nums):
            self.output.text = "입력 오류: 1~45 사이 서로 다른 숫자 6개"
            return

        ns = set(nums)
        ranks = {1: [], 2: [], 3: [], 4: [], 5: []}
        for d in self.cache.values():
            hist = set(d["numbers"])
            m = len(ns & hist)
            if m == 6:
                ranks[1].append(d)
            elif m == 5:
                if d["bonus"] in ns:
                    ranks[2].append(d)
                else:
                    ranks[3].append(d)
            elif m == 4:
                ranks[4].append(d)
            elif m == 3:
                ranks[5].append(d)

        nums_str = " ".join(f"{n:2d}" for n in sorted(nums))
        lines = [
            f"=== {nums_str} 역대 이력 ===",
            f"(검사 1~{max(self.cache)}회, 총 {len(self.cache)}회)",
            "",
        ]
        labels = {1: "1등 (6개)", 2: "2등 (5+보너스)", 3: "3등 (5개)", 4: "4등 (4개)", 5: "5등 (3개)"}
        for r in (1, 2, 3, 4, 5):
            lines.append(f"  {labels[r]:>14} : {len(ranks[r])}회")
        lines.append("")

        for r in (1, 2, 3):
            if not ranks[r]:
                continue
            lines.append(f"[{labels[r]} 당첨 회차]")
            for d in sorted(ranks[r], key=lambda x: x["round"]):
                wn = " ".join(f"{n:2d}" for n in d["numbers"])
                lines.append(f"  {d['round']}회 ({d['date']}) 당첨: {wn} +보너스 {d['bonus']}")
            lines.append("")

        self.output.text = "\n".join(lines)

    def _on_add_round(self):
        """수동으로 새 회차 추가. 형식: '1224 5 12 18 24 33 41 / 7' 또는 '1224 5 12 18 24 33 41 7'."""
        raw = self.add_input.text.strip()
        try:
            tokens = [t for t in raw.replace(",", " ").replace("/", " ").split() if t]
            vals = [int(t) for t in tokens]
        except ValueError:
            self.output.text = "입력 오류: 숫자만. 예: 1224 5 12 18 24 33 41 / 7"
            return
        if len(vals) != 8:
            self.output.text = "입력 오류: 회차 + 번호 6개 + 보너스 1개 = 8개 숫자"
            return
        round_no = vals[0]
        nums = vals[1:7]
        bonus = vals[7]
        if not (1 <= round_no <= 9999):
            self.output.text = "입력 오류: 회차 범위"
            return
        if len(set(nums)) != 6 or not all(1 <= n <= 45 for n in nums):
            self.output.text = "입력 오류: 1~45 서로 다른 6개"
            return
        if not (1 <= bonus <= 45) or bonus in nums:
            self.output.text = "입력 오류: 보너스 범위 또는 중복"
            return
        # 날짜는 1회차(2002-12-07) 기준 자동 계산
        from datetime import date, timedelta
        d = (date(2002, 12, 7) + timedelta(days=(round_no - 1) * 7)).isoformat()

        self.cache[round_no] = {
            "round": round_no,
            "date": d,
            "numbers": sorted(nums),
            "bonus": bonus,
        }
        if round_no > (self.latest_round or 0):
            self.latest_round = round_no
            info = self.cache[round_no]
            nstr = " ".join(f"{n:2d}" for n in info["numbers"])
            self.info_lbl.text = f"최근 {info['round']}회 ({info['date']})\n당첨: {nstr}  + 보너스 {info['bonus']}"

        save_cache(self.cache)
        self.add_input.text = ""
        self.output.text = f"{round_no}회 추가됨. 총 {len(self.cache)}회차 캐시."
        self.status_lbl.text = f"준비 완료 · 총 {len(self.cache)}회차"

    def _clear(self):
        self.output.text = ""


if __name__ == "__main__":
    LottoApp().run()
