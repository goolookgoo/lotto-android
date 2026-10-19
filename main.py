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
import ssl
import sys
import threading
import urllib.request
from collections import Counter

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.textinput import TextInput
from kivy.utils import platform

# 데이터 소스 (동행복권이 IP 차단할 수 있어 미러를 폴백으로 둠)
DHLOTTERY_URL = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"
SMOK_URL = "https://smok95.github.io/lotto/results/{}.json"       # 개별 회차
SMOK_LATEST_URL = "https://smok95.github.io/lotto/results/latest.json"  # 최신 회차
THRESHOLD = 5

# ── 한글 폰트 등록 ──────────────────────────────────────────
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NanumGothic.ttf")
if os.path.exists(_FONT_PATH):
    LabelBase.register(name="NanumGothic", fn_regular=_FONT_PATH)
    KR_FONT = "NanumGothic"
else:
    KR_FONT = "Roboto"        # 폰트 없으면 기본값 (깨질 수 있음)


# ---------- 경로 ----------

def _bundle_dir():
    """앱 패키지(=APK)에 함께 들어 있는 파일 경로 (읽기 전용)."""
    if hasattr(sys, "_MEIPASS"):
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
        try:
            shutil.copy(STARTER_CACHE, CACHE_PATH)
        except Exception:
            with open(STARTER_CACHE, "rb") as src, open(CACHE_PATH, "wb") as dst:
                dst.write(src.read())


# ---------- API ----------

def _make_ssl_context():
    """Android 파이썬은 시스템 CA 번들이 없어 HTTPS 검증이 실패함.
    certifi의 인증서를 쓰고, 없으면 검증 생략(공개 로또 데이터 전용)으로 폴백."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


_SSL_CTX = _make_ssl_context()


def _http_json(url, timeout=10):
    """URL에서 JSON을 받아 반환. HTML(차단/리다이렉트)이면 ValueError."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        body = r.read().decode("utf-8", "replace")
    if not body.lstrip().startswith(("{", "[")):
        raise ValueError("JSON이 아닌 응답 (차단/리다이렉트 추정)")
    return json.loads(body)


def _from_dhlottery(round_no, timeout=10):
    data = _http_json(DHLOTTERY_URL.format(round_no), timeout)
    if data.get("returnValue") != "success":
        return None
    return {
        "round": data["drwNo"],
        "date": data["drwNoDate"],
        "numbers": sorted(data[f"drwtNo{i}"] for i in range(1, 7)),
        "bonus": data["bnusNo"],
    }


def _from_smok(round_no, timeout=10):
    data = _http_json(SMOK_URL.format(round_no), timeout)
    if "numbers" not in data:
        return None
    return {
        "round": data["draw_no"],
        "date": str(data.get("date", ""))[:10],  # ISO 날짜에서 YYYY-MM-DD만
        "numbers": sorted(data["numbers"]),
        "bonus": data["bonus_no"],
    }


def fetch_draw(round_no, timeout=10, retries=2):
    # 우선순위: 동행복권(공식) → smok95 미러. 앞 소스가 막혀도 뒤 소스로 자동 폴백.
    for source in (_from_dhlottery, _from_smok):
        for _ in range(retries):
            try:
                d = source(round_no, timeout)
                if d is not None:
                    return d
                break  # 소스가 '이 회차 없음'을 명확히 응답 → 다음 소스로
            except Exception:
                continue
    return None


def find_latest_round(start_hint=1200):
    # 1차: smok95 latest.json으로 최신 회차를 한 번에 확인
    try:
        data = _http_json(SMOK_LATEST_URL)
        if "draw_no" in data:
            return int(data["draw_no"])
    except Exception:
        pass
    # 2차 폴백: start_hint부터 위로 한 칸씩. 연속 2개 None이면 종료.
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


# ---------- UI 헬퍼 ----------

def make_label(text, font_size="16sp", bold=False, height=None, halign="left", color=None):
    kw = dict(
        text=text,
        font_name=KR_FONT,
        font_size=font_size,
        bold=bold,
        halign=halign,
        valign="middle",
    )
    if height is not None:
        kw["size_hint_y"] = None
        kw["height"] = height
    if color:
        kw["color"] = color
    lbl = Label(**kw)
    lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
    return lbl


def make_button(text, font_size="18sp", width=None, **kwargs):
    kw = dict(font_name=KR_FONT, font_size=font_size, **kwargs)
    if width is not None:
        kw["size_hint_x"] = None
        kw["width"] = width
    return Button(text=text, **kw)


def make_input(hint="", font_size="16sp", **kwargs):
    return TextInput(
        hint_text=hint,
        font_name=KR_FONT,
        font_size=font_size,
        multiline=False,
        **kwargs,
    )


# ---------- UI ----------

class LottoApp(App):
    title = "로또 번호 생성기"

    def build(self):
        root = BoxLayout(orientation="vertical", padding=10, spacing=8)

        # ── 상단 정보 ──────────────────────────────────────
        self.info_lbl = make_label(
            "데이터 불러오는 중...",
            font_size="18sp", bold=True, height=56,
        )
        root.add_widget(self.info_lbl)

        self.status_lbl = make_label(
            "", font_size="14sp", height=30,
            color=(0.4, 0.4, 0.4, 1),
        )
        root.add_widget(self.status_lbl)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=14)
        root.add_widget(self.progress)

        # ── 세트 생성 버튼 ─────────────────────────────────
        btn_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None, height=72,
            spacing=6,
        )
        for n in (5, 10, 20, 30):
            b = make_button(f"{n}세트", font_size="20sp")
            b.bind(on_release=lambda inst, c=n: self._on_generate(c))
            btn_row.add_widget(b)
        root.add_widget(btn_row)

        # ── 옵션 줄 ────────────────────────────────────────
        opt_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None, height=56,
            spacing=6,
        )
        self.no_pattern_cb = CheckBox(active=True, size_hint_x=None, width=44)
        opt_row.add_widget(self.no_pattern_cb)
        opt_row.add_widget(make_label("패턴 제거", font_size="16sp", height=56))
        clear_btn = make_button("결과 지우기", font_size="16sp")
        clear_btn.bind(on_release=lambda inst: self._clear())
        opt_row.add_widget(clear_btn)
        root.add_widget(opt_row)

        # ── 당첨 이력 확인 ─────────────────────────────────
        check_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None, height=56,
            spacing=6,
        )
        self.check_input = make_input(
            hint="6개 번호 입력 (예: 1 7 13 22 30 41)",
            font_size="15sp",
        )
        check_row.add_widget(self.check_input)
        check_btn = make_button("이력 확인", font_size="16sp", width=110)
        check_btn.bind(on_release=lambda inst: self._on_check())
        check_row.add_widget(check_btn)
        root.add_widget(check_row)

        # ── 수동 회차 추가 ─────────────────────────────────
        add_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None, height=56,
            spacing=6,
        )
        self.add_input = make_input(
            hint="회차 추가 (예: 1224 5 12 18 24 33 41 / 7)",
            font_size="14sp",
        )
        add_row.add_widget(self.add_input)
        add_btn = make_button("추가", font_size="16sp", width=80)
        add_btn.bind(on_release=lambda inst: self._on_add_round())
        add_row.add_widget(add_btn)
        root.add_widget(add_row)

        # ── 결과 출력 영역 ─────────────────────────────────
        self.output = TextInput(
            text="",
            readonly=True,
            font_name=KR_FONT,
            font_size="16sp",
            background_color=(0.97, 0.97, 0.97, 1),
        )
        root.add_widget(self.output)

        # ── 네트워크 업데이트 버튼 ─────────────────────────
        update_btn = make_button(
            "서버에서 최신 회차 받기",
            font_size="16sp",
        )
        update_btn.size_hint_y = None
        update_btn.height = 60
        update_btn.bind(on_release=lambda inst: self._on_network_update())
        root.add_widget(update_btn)
        self.update_btn = update_btn

        # ── 상태 초기화 ────────────────────────────────────
        self.cache = {}
        self.latest_round = None
        Clock.schedule_once(self._initial_load_safe, 0.5)

        return root

    def _initial_load_safe(self, dt):
        """앱 시작 시 캐시만 로드. 네트워크 호출 없음."""
        try:
            ensure_starter_cache()
            cache = load_cache()
            if not cache:
                self.info_lbl.text = "캐시 없음 — '서버에서 최신 회차 받기' 눌러주세요"
                self._set_buttons_enabled(True)
                return
            self.cache = cache
            self.latest_round = max(cache.keys())
            info = cache[self.latest_round]
            nums = " ".join(f"{n:2d}" for n in info["numbers"])
            self.info_lbl.text = (
                f"최신: {nums}  + 보너스 {info['bonus']}\n"
                f"제 {info['round']}회차 ({info['date']})"
            )
            self.status_lbl.text = f"준비 완료  ·  총 {len(cache)}회차 로드됨 (캐시)"
            self.progress.value = 100
            self._set_buttons_enabled(True)
            # 캐시 표시 후 백그라운드로 최신 회차 자동 업데이트
            self._on_network_update()
        except Exception:
            import traceback
            self.info_lbl.text = "초기화 오류"
            self.output.text = traceback.format_exc()

    def _on_network_update(self):
        self._set_buttons_enabled(False)
        self.status_lbl.text = "서버 업데이트 중..."
        threading.Thread(target=self._do_network_update, daemon=True).start()

    def _do_network_update(self):
        try:
            cache = self.cache or load_cache()
            cache_max = max(cache.keys()) if cache else 0
            hint = cache_max + 1 if cache else 1200
            latest = find_latest_round(start_hint=hint)
            if latest == 0:
                Clock.schedule_once(lambda dt: self._update_done("서버 응답 없음 — 캐시 사용 중"))
                return

            def on_progress(done, total, pass_idx=1):
                pct = done / total * 100
                Clock.schedule_once(lambda dt: self._set_progress(pct, f"다운로드 {done}/{total}"))

            cache = fetch_missing(cache, latest, progress_cb=on_progress)
            self.cache = cache
            self.latest_round = max(cache.keys())
            info = cache[self.latest_round]
            nums = " ".join(f"{n:2d}" for n in info["numbers"])
            Clock.schedule_once(lambda dt: self._update_done(
                f"업데이트 완료  ·  총 {len(cache)}회차",
                f"최신: {nums}  + 보너스 {info['bonus']}\n제 {info['round']}회차 ({info['date']})",
            ))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            Clock.schedule_once(lambda dt: self._update_done(f"오류: {e}", output=tb))

    def _update_done(self, status, info_text=None, output=None):
        self.status_lbl.text = status
        if info_text:
            self.info_lbl.text = info_text
        if output:
            self.output.text = output
        self.progress.value = 100
        self._set_buttons_enabled(True)

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
        lines = [f"=== {len(sets)}세트 (3등이상 미당첨 · 최근회차 1개 중복) ==="]
        for i, s in enumerate(sets, 1):
            shared = next(n for n in s if n in latest_nums)
            nums = "  ".join(f"{n:2d}" for n in s)
            lines.append(f"{i:2d}.  {nums}   (공유:{shared})")
        if len(sets) < count:
            lines.append(f"\n조건 만족 세트: {len(sets)}개")
        Clock.schedule_once(lambda dt: self._on_generate_done("\n".join(lines)))

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
            self.output.text = "입력 오류: 숫자 6개 (예: 1 7 13 22 30 41)"
            return
        if len(nums) != 6 or len(set(nums)) != 6 or not all(1 <= n <= 45 for n in nums):
            self.output.text = "입력 오류: 1~45 서로 다른 숫자 6개"
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
            f"(1~{max(self.cache)}회, 총 {len(self.cache)}회차)",
            "",
        ]
        labels = {1: "1등(6개)", 2: "2등(5+보너스)", 3: "3등(5개)", 4: "4등(4개)", 5: "5등(3개)"}
        for r in (1, 2, 3, 4, 5):
            lines.append(f"  {labels[r]:>12} : {len(ranks[r])}회")
        lines.append("")
        for r in (1, 2, 3):
            if not ranks[r]:
                continue
            lines.append(f"[{labels[r]} 당첨 회차]")
            for d in sorted(ranks[r], key=lambda x: x["round"]):
                wn = " ".join(f"{n:2d}" for n in d["numbers"])
                lines.append(f"  {d['round']}회 ({d['date']})  {wn} +{d['bonus']}")
            lines.append("")

        self.output.text = "\n".join(lines)

    def _on_add_round(self):
        raw = self.add_input.text.strip()
        try:
            tokens = [t for t in raw.replace(",", " ").replace("/", " ").split() if t]
            vals = [int(t) for t in tokens]
        except ValueError:
            self.output.text = "입력 오류: 예) 1224 5 12 18 24 33 41 / 7"
            return
        if len(vals) != 8:
            self.output.text = "입력 오류: 회차+번호6개+보너스 = 8개"
            return
        round_no, nums, bonus = vals[0], vals[1:7], vals[7]
        if not (1 <= round_no <= 9999):
            self.output.text = "입력 오류: 회차 범위 초과"
            return
        if len(set(nums)) != 6 or not all(1 <= n <= 45 for n in nums):
            self.output.text = "입력 오류: 1~45 서로 다른 6개"
            return
        if not (1 <= bonus <= 45) or bonus in nums:
            self.output.text = "입력 오류: 보너스 범위/중복"
            return
        from datetime import date, timedelta
        d = (date(2002, 12, 7) + timedelta(days=(round_no - 1) * 7)).isoformat()
        self.cache[round_no] = {
            "round": round_no, "date": d,
            "numbers": sorted(nums), "bonus": bonus,
        }
        if round_no > (self.latest_round or 0):
            self.latest_round = round_no
            info = self.cache[round_no]
            nstr = " ".join(f"{n:2d}" for n in info["numbers"])
            self.info_lbl.text = (
                f"최신: {nstr}  + 보너스 {info['bonus']}\n"
                f"제 {info['round']}회차 ({info['date']})"
            )
        save_cache(self.cache)
        self.add_input.text = ""
        self.output.text = f"{round_no}회 추가됨. 총 {len(self.cache)}회차."
        self.status_lbl.text = f"준비 완료  ·  총 {len(self.cache)}회차"

    def _clear(self):
        self.output.text = ""


if __name__ == "__main__":
    try:
        LottoApp().run()
    except Exception:
        import traceback
        try:
            log_path = os.path.join(_writable_dir(), "crash.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
