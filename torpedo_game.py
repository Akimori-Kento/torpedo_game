"""魚雷ゲーム：OOPで作るターン制ASCII海戦ゲーム（Python標準ライブラリのみ）。

拡張ポイント：WEAPONS と Weapon.targets()/Weapon.scan_cells()、
             Ship のサブクラス、CPU の選択ロジック。

ゲーム全体を理解しなくても、新兵装なら既存の CrossTorpedo を参考に作れる。
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import dataclass, field
import random
import time

SIZE = 9
RADAR_LIFETIME = 2  # 索敵した後のプレイヤー行動2回まで表示
SHIPS_PER_FLEET = 3  # 編成可能な隻数。2隻編成でも開始できる
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
NEIGHBORS = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]

COLORS = {
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[96m",
    "gray": "\033[90m",
    "orange": "\033[38;5;208m",
    "white": "\033[1;97m",
    "Bold_text": "\033[1m"
}
RESET = "\033[0m"

color_tag = {
    "X": "red",
    "?": "yellow",
    "^": "green", "-": "white", ".":"gray",
    "A": "orange", "S": "orange", "N": "orange", "T": "orange",
    "C": "cyan" ,"P": "cyan"
}

# 盤面内か確認する関数
def in_bounds(p, size=SIZE):
    return 0 <= p[0] < size and 0 <= p[1] < size


# 四角形の座標を作る関数
def cells_in_rect(center, width, height):
    x, y = center
    return [(x + dx, y + dy)
            for dy in range(-(height // 2), height - height // 2)
            for dx in range(-(width // 2), width - width // 2)]


@dataclass
class Ship:
    name: str = '艦艇'
    symbol: str = 'S'
    width: int = 1
    height: int = 2
    weapons: tuple[str, ...] = ('torpedo',)
    positions: set[tuple[int, int]] = field(default_factory=set)
    hits: set[tuple[int, int]] = field(default_factory=set)
    ammo: dict[str, int | None] = field(default_factory=dict)

    # 撃墜されたか確認する
    def is_sunk(self):
        return bool(self.positions) and self.positions <= self.hits

    # 生きてるか確認する
    def is_alive(self):
        return bool(self.positions) and not self.is_sunk()

    # 座標を取る
    def place(self, x, y, rotated=False):
        w, h = (self.height, self.width) if rotated else (self.width, self.height)
        return {(x + dx, y + dy) for dy in range(h) for dx in range(w)}


class AttackSubmarine(Ship):
    def __init__(self):
        super().__init__('攻撃型潜水艦', 'A', 1, 2, ('torpedo', 'cross'))


class StandardSubmarine(Ship):
    def __init__(self):
        super().__init__('通常型潜水艦', 'S', 1, 3, ('torpedo', 'double'))


class NuclearSubmarine(Ship):
    def __init__(self):
        super().__init__('原子力潜水艦', 'N', 1, 4, ('torpedo',))


class StealthSubmarine(Ship):
    def __init__(self):
        super().__init__('ステルス潜水艦', 'T', 1, 1, ('torpedo',))


class Carrier(Ship):
    def __init__(self):
        super().__init__('空母', 'C', 2, 4, ('radar',))


class Scout(Ship):
    def __init__(self):
        super().__init__('偵察機', 'P', 1, 1, ('radar', 'wide_radar', 'directional_radar'))


# 戦艦の種類
SHIP_TYPES = {
    '1': AttackSubmarine, '2': StandardSubmarine, '3': NuclearSubmarine, '4':StealthSubmarine,
    '5': Carrier, '6': Scout,
}


class Weapon:
    key = ''
    name = ''
    limit = None  # None: 無制限
    kind = 'attack'

    def targets(self, center):
        return [center]

    def scan_cells(self, center, direction=None):
        return []


class Torpedo(Weapon):
    key, name, limit = 'torpedo', '通常魚雷', None


class DoubleTorpedo(Weapon):
    key, name, limit = 'double', '二連装魚雷', 3

    def targets(self, center):
        # 二連装は攻撃指定を2回行うため、Game側で別々の座標を受け取る
        return [center]


class CrossTorpedo(Weapon):
    key, name, limit = 'cross', '十字魚雷', 2

    def targets(self, center):
        x, y = center
        return [center] + [(x + dx, y + dy) for dx, dy in DIRECTIONS]


class BasicRadar(Weapon):
    key, name, limit, kind = 'radar', '通常レーダー(3x3)', None, 'scan'

    def scan_cells(self, center, direction=None):
        return cells_in_rect(center, 3, 3)


class WideRadar(Weapon):
    key, name, limit, kind = 'wide_radar', '広域レーダー(5x5)', 1, 'scan'

    def scan_cells(self, center, direction=None):
        return cells_in_rect(center, 5, 5)


class DirectionalRadar(Weapon):
    key, name, limit, kind = 'directional_radar', '方向レーダー(直線5)', 2, 'scan'

    def scan_cells(self, center, direction=None):
        dx, dy = direction or (0, -1)
        return [(center[0] + n * dx, center[1] + n * dy) for n in range(5)]


# TODOの例：新しい魚雷を作るには Weapon を継承して targets() を変更し、
# WEAPONS に登録し、Shipサブクラスの weapons に key を追加する。
WEAPONS = {w.key: w() for w in (Torpedo, DoubleTorpedo, CrossTorpedo,
                                BasicRadar, WideRadar, DirectionalRadar)}


@dataclass
class RadarContact:
    """座標ごとの一時的な探知情報。艦影の有無と残り寿命を保持する。"""
    detected: bool
    remaining: int = RADAR_LIFETIME


class Fleet:
    def __init__(self, ships, size=SIZE):
        self.ships = ships
        self.size = size
        for ship in self.ships:
            ship.ammo = {key: WEAPONS[key].limit for key in ship.weapons}
        self.radar_logs = []
        self.radar_contacts: dict[tuple[int, int], RadarContact] = {}
        self.radar_last_scan = None  # (兵装名, 探知した艦影マス数)

    def advance_radar_time(self):
        """プレイヤーの有効な行動が1回終わったら既存の探知情報を減衰させる。"""
        expired = []
        for p, contact in self.radar_contacts.items():
            contact.remaining -= 1
            if contact.remaining <= 0:
                expired.append(p)
        for p in expired:
            del self.radar_contacts[p]
        if not self.radar_contacts:
            self.radar_last_scan = None

    def record_radar_scan(self, cells, enemy, name):
        """既存情報の寿命を進めてから、今回の索敵結果を追加・上書きする。"""
        self.advance_radar_time()
        count = 0
        occupied = enemy.radar_visible_positions()
        for p in cells:
            detected = p in occupied
            count += detected
            self.radar_contacts[p] = RadarContact(detected)
        self.radar_last_scan = (name, count)
        return count

    def occupied(self):
        return {p for ship in self.ships for p in ship.positions}
    
    def radar_visible_positions(self):
        return {
            p
            for ship in self.ships
            if not isinstance(ship, StealthSubmarine)
            for p in ship.positions
        }


    # 設置可能か調べる
    def can_place(self, ship, positions):
        return all(in_bounds(p, self.size) for p in positions) and not (positions & self.occupied())


    # 設置する
    def place(self, ship, x, y, rotate=False):
        positions = ship.place(x, y, rotate)
        if not self.can_place(ship, positions):
            return False
        ship.positions = positions
        return True


    # ランダム配置
    def random_place_all(self, rng):
        for ship in self.ships:
            for _ in range(1000):
                if self.place(ship, rng.randrange(self.size), rng.randrange(self.size), rng.choice((True, False))):
                    break
            else:
                raise RuntimeError('艦隊を配置できません。艦数や盤面を確認してください。')

    def attack(self, p):
        """(結果, 撃沈した艦) を返す。外れの場合は近隣8マスの至近弾も検出。"""
        for ship in self.ships:
            if p in ship.positions:
                if p in ship.hits:
                    return 'repeat', None
                ship.hits.add(p)
                return ('sunk' if ship.is_sunk() else 'hit'), (ship if ship.is_sunk() else None)
        near = any((p[0] + dx, p[1] + dy) in self.occupied() for dx, dy in NEIGHBORS)
        return ('near' if near else 'miss'), None

# 攻撃できる艦が一隻でもあるか確認する関数
    def has_offense(self):
        return any(WEAPONS[key].kind == 'attack' for key in self.available())

    # 使える兵器一覧を返す
    def available(self):
        keys = set()  # レーダーも艦種に紐づく。空母・偵察機の生存中のみ使用可能
        for ship in self.ships:
            if ship.is_alive():
                for key, remaining in ship.ammo.items():
                    if remaining is None or remaining > 0:
                        keys.add(key)
        return [key for key in WEAPONS if key in keys]

    # 使用回数を減らす
    def use_ammo(self, key):
        for ship in self.ships:
            if not ship.is_alive() or key not in ship.ammo:
                continue
            remaining = ship.ammo[key]
            if remaining is None:
                return True
            if remaining > 0:
                ship.ammo[key] -= 1
                return True
        return False
    
    def remaining_ammo(self, key):
        total = 0
        for ship in self.ships:
            if not ship.is_alive() or key not in ship.ammo:
                continue
            remaining = ship.ammo[key]
            if remaining is None:
                return None
            total += remaining
        return total

    # 敗北したかを判断する
    def defeated(self):
        return all(s.is_sunk() for s in self.ships) or not self.has_offense()

    # 艦隊の構成を返す
    def summary(self):
        counts = Counter(s.name for s in self.ships)
        sunk = Counter(s.name for s in self.ships if s.is_sunk())
        return ' / '.join(f'{name} {n}隻 (撃沈{sunk[name]})' for name, n in counts.items())

@dataclass(frozen=True)
class TargetChoice:
    priority: int
    position: tuple[int, int]

class CPU:
    def __init__(self, rng, size=SIZE):
        self.rng, self.size = rng, size
        self.tried = set()
        self.detected_targets = deque()
        self.hit_targets = deque()
        self.near_targets = deque()
        self.low_priority = deque()
        self.not_targets = deque()



    def choose_target(self, enemy):
        self.detected_targets = deque(
            position
            for position, contact in enemy.radar_contacts.items()
            if contact.detected and position not in self.tried
        )
        self.not_targets = deque(
            position
            for position, contact in enemy.radar_contacts.items()
            if not contact.detected and position not in self.tried
        )
        self.hit_targets = deque(
            position
            for position in self.hit_targets
            if position not in self.not_targets 
        )
        self.near_targets = deque(
            position
            for position in self.near_targets
            if position not in self.not_targets
        )
        for priority, queue in enumerate((self.detected_targets, self.hit_targets, self.near_targets)):
            while queue:
                p = queue.popleft()
                if p not in self.tried and in_bounds(p, self.size):
                    self.tried.add(p)
                    return TargetChoice(priority ,position=p)
        
        remaining = [
            (x, y)
            for y in range(self.size)
            for x in range(self.size)
            if (x, y) not in self.tried
            and (x, y) not in self.low_priority
            and (x, y) not in self.not_targets
        ]
        if not remaining:
            remaining = [
                (x, y)
                for y in range(self.size)
                for x in range(self.size)
                if (x, y) not in self.tried
                and (x, y) not in self.not_targets
            ]
        if not remaining:
            return None
        p = self.rng.choice(remaining)
        self.tried.add(p)
        return TargetChoice(priority=3, position=p)

    def feedback(self, p, result):
        if result == 'hit':
            offsets = DIRECTIONS
            queue = self.hit_targets
        elif result == 'near':
            offsets = NEIGHBORS
            queue = self.near_targets
        else:
            offsets = NEIGHBORS
            queue = self.low_priority
        candidates = [
            (p[0] + dx, p[1] + dy)
            for dx, dy in offsets
        ]
        self.rng.shuffle(candidates)
        queue.extend(candidates)


DEPARTURE = rf'''
                      |\
                 _____|_\________
         _______/_______________  \____
    ____/  _  _   _   _  _     \______\____
   /_________________________________________\
   \_________________________________________/
 {COLORS["cyan"]}~~~~~~{RESET}\_________________________________/{COLORS["cyan"]}~~~~~~~~
 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                  {COLORS["green"]}FLEET DEPARTURE{RESET}
'''

SINKING = rf'''
                 |\       *
         ________|_\___  ***
     ___/______________\_***___
  {COLORS["cyan"]}~~~{RESET}\____________________/{COLORS["cyan"]}~~~~~~~~~~~~~~~~
  ~~~~~{RESET}\______________/{COLORS["cyan"]}~~~~~~~~~~~~~~~~~~~~
  ~~~~~~~~~~~{RESET}\____/{COLORS["cyan"]}~~~~~~~~~~~~~~~~~~~~~~~~
                 {COLORS["green"]}ENEMY SUNK!{RESET}
'''


# 少し待つ演出
def animation(ascii_art=None, enabled=True):
    if not enabled:
        return
    if ascii_art is not None:
        print('\n' + ascii_art)
    time.sleep(0.6)


def parse_coord(raw, size=SIZE):
    s = raw.strip().replace(',', ' ').split()
    if len(s) != 2:
        return None
    try:
        x, y = map(int, s)
    except ValueError:
        return None
    p = (x - 1, y - 1)
    return p if in_bounds(p, size) else None


def ask_coord(prompt, size=SIZE, allow_back=False):
    while True:
        s = input(prompt).strip().lower()
        if s == 'q':
            raise KeyboardInterrupt
        if allow_back and s == "b":
            return None
        p = parse_coord(s, size)
        if p is not None:
            return p
        print(f'座標は「列 行」で入力してください（例: 3 5、1〜{size}）。qで終了。')


# 盤面の攻撃結果や検索結果を文字列に変換する関数
def board_lines(fleet, shots=None, own=False, radar_contacts=None):
    shots = shots or {}
    occupied = {p: ship.symbol for ship in fleet.ships for p in ship.positions}
    radar_contacts = radar_contacts or {}
    rows = ['    ' + ' '.join(str(i + 1) for i in range(fleet.size))]
    for y in range(fleet.size):
        tiles = []
        for x in range(fleet.size):
            p = x, y
            if p in shots:
                tiles.append({'hit': 'X', 'sunk': 'X', 'miss': 'o', 'near': '?', 'repeat': 'X'}[shots[p]])
            elif own and p in occupied:
                tiles.append(occupied[p])
            elif p in radar_contacts:
                tiles.append('^' if radar_contacts[p].detected else '-')
            else:
                tiles.append('.')
        rows.append(f'{y + 1:>2}  ' + ' '.join(color_tile(tile) for tile in tiles))
    return rows


# 色を付ける関数
def color_tile(tile):
    color_name = color_tag.get(tile, "")
    color_code = COLORS.get(color_name, "")
    if color_code:
        return color_code + tile + RESET
    return tile


def show_boards(player, enemy, player_shots, cpu_shots, turn, reveal_enemy=False):
    # 敵の盤面情報を代入
    left = board_lines(enemy, player_shots, own=reveal_enemy,
                       radar_contacts=player.radar_contacts)
    # 自分の盤面情報を代入
    right = board_lines(player, cpu_shots, own=True,
                        radar_contacts=enemy.radar_contacts)
    
    print(f'\n{COLORS["cyan"]}========== TURN {turn} / 魚雷ゲーム =========={RESET}')
    print('敵艦隊: ' + enemy.summary())
    print('自艦隊: ' + player.summary())
    print('     敵の海域（' + ('DEBUG: 艦影公開' if reveal_enemy else '配置は秘密') + '）         自艦隊（被弾状況）')
    # 盤面の表示
    board_width = 4 + enemy.size + (enemy.size - 1)
    gap = " " * max(1, 27 -board_width)
    for a, b in zip(left, right):
        print(a + gap + b)
    print(f'\n表示: {COLORS["red"]}X{RESET} 命中  o 外れ  {COLORS["yellow"]}?{RESET} 至近弾  {COLORS["green"]}^{RESET} 艦影反応  - 反応なし  . 未調査')
    if player.radar_contacts:
        contact_counts = Counter(c.remaining for c in player.radar_contacts.values())
        time_info = ' / '.join(f'残り{n}行動:{count}マス' for n, count in sorted(contact_counts.items()))
        print(f'レーダー情報（探知位置を一時表示）: {time_info}')
        if player.radar_last_scan:
            name, count = player.radar_last_scan
            print(f'直近の索敵: {name} / 艦影反応 {count}マス')
    weapon_labels = []
    for key in player.available():
        remaining = player.remaining_ammo(key)
        count = "∞" if remaining is None else remaining
        weapon_labels.append(f"{WEAPONS[key].name}[{count}]")
    print('兵装: ' + ' / '.join(weapon_labels))
    
def show_result(outcome, turn, player, enemy):
    left = board_lines(enemy, own=True)
    right = board_lines(player, own=True)
    
    print(f'\n{COLORS["cyan"]}========== RESULT =========={RESET}')
    print('勝利！' if outcome == 'win' else '敗北……')
    print(f"ターン数: {turn}")
    
    board_width = 4 + enemy.size + (enemy.size - 1)
    gap = " " * max(1, 27 -board_width)
    for a, b in zip(left, right):
        print(a + gap + b)
    
    print(f'\n自艦隊: {player.summary()}')
    print(f'敵艦隊: {enemy.summary()}')
    print(f'{COLORS["cyan"]}============================{RESET}')
    
# 戦艦選択
def choose_fleet(size=SIZE):
    print(f'\n--- \033[1;100m艦隊編成{RESET} ---')
    print(f'艦種は全6種類。{size}×{size}の海域に2〜3隻を選択できます。')
    print(f'同じ艦種を複数選んでもOKです{COLORS["gray"]}（例: 2 2 5）{RESET}')
    for k, ctor in SHIP_TYPES.items():
        s = ctor()
        print(f' {k}: {s.name} ({s.width}x{s.height}) | {", ".join(WEAPONS[w].name for w in s.weapons)}')
    print(f'\n{COLORS["Bold_text"]}{COLORS["white"]}※空母・偵察機は攻撃能力なし。通常レーダーは両艦とも無制限です。')
    print(f'※支援艦だけの編成も選択できますが、\033[4m攻撃不能のため戦闘開始時に\033[31m敗北{COLORS["Bold_text"]}{COLORS["white"]}します。{RESET}')
    while True:
        s = input(f'6種類から2〜3隻の番号を空白区切りで入力 {COLORS["gray"]}(例: 1 4 5){RESET}: ').strip()
        if s.lower() == 'q':
            raise KeyboardInterrupt
        values = s.split()
        if 2 <= len(values) <= SHIPS_PER_FLEET and all(v in SHIP_TYPES for v in values):
            ships = [SHIP_TYPES[v]() for v in values]
            if try_place_all(Fleet(ships, size), random.Random(0)):
                return [SHIP_TYPES[v]() for v in values]
            print(f'この艦隊は{size}×{size}の海域に配置できません。別の編成を選んでください。')
            continue
        print('番号1〜6の中から、2〜3隻を選んでください。')


def try_place_all(fleet, rng):
    """狭い海域でも重複せず全艦配置できるか探索（成功時は配置する）。"""
    if sum(ship.width * ship.height for ship in fleet.ships) > fleet.size ** 2:
        return False
    ships = sorted(fleet.ships, key=lambda ship: ship.width * ship.height, reverse=True)
    occupied = set()

    def place_next(index):
        if index == len(ships):
            return True
        ship = ships[index]
        candidates = []
        for rotated in (False, True):
            for y in range(fleet.size):
                for x in range(fleet.size):
                    cells = ship.place(x, y, rotated)
                    if all(in_bounds(p, fleet.size) for p in cells) and not (cells & occupied):
                        if cells not in candidates:
                            candidates.append(cells)
        rng.shuffle(candidates)
        for cells in candidates:
            ship.positions = cells
            occupied.update(cells)
            if place_next(index + 1):
                return True
            occupied.difference_update(cells)
            ship.positions = set()
        return False

    if place_next(0):
        return True
    for ship in fleet.ships:
        ship.positions.clear()
    return False


def place_player_fleet(fleet, rng):
    s = input('\n配置方法 [a: 自動 / m: 手動] (Enterで自動): ').strip().lower()
    if s != 'm':
        if not try_place_all(fleet, rng):
            raise RuntimeError('艦船を配置できません。編成を変更してください。')
        return
    for ship in fleet.ships:
        while True:
            print('\n'.join(board_lines(fleet, own=True)))
            print(f'{ship.name} ({ship.width}x{ship.height}) を配置')
            p = ask_coord('左上の座標「列 行」: ', fleet.size)
            rotate = input('90度回転しますか？ [y/N] ').strip().lower() == 'y'
            if fleet.place(ship, *p, rotate):
                break
            print('そこには配置できません（盤外または艦船が重複）。')


def offer_weapon(fleet):
    keys = fleet.available()
    print('\n兵装選択: ' + ' / '.join(f'{i + 1}:{WEAPONS[k].name}' for i, k in enumerate(keys)))
    while True:
        s = input('番号を入力（q終了）: ').strip().lower()
        if s == 'q':
            raise KeyboardInterrupt
        if s.isdigit() and 1 <= int(s) <= len(keys):
            return keys[int(s) - 1]
        print('表示された番号を選んでください。')


# 行動をする関数
def perform_player_action(key, player, enemy, results, animations):
    weapon = WEAPONS[key]
    center = ask_coord('目標座標「列 行」(b戻る): ', enemy.size, allow_back=True)
    if center is None:
        return False
    direction = None
    if key == 'directional_radar':
        while True:
            d = input('探索方向 [w上/a左/s下/d右](b戻る): ').strip().lower()
            if d == "b":
                return False
            if d in {'w': (0, -1), 'a': (-1, 0), 's': (0, 1), 'd': (1, 0)}:
                direction = {'w': (0, -1), 'a': (-1, 0), 's': (0, 1), 'd': (1, 0)}[d]
                break
            print('w/a/s/d を入力してください。')
    extra = None
    if key == 'double':
        extra = ask_coord('2発目の目標座標「列 行」(b戻る): ', enemy.size, allow_back=True)
        if extra is None:
            return False
    # 重複や盤外を除いた「実際に処理できるマス」を先に確認する
    targets = ([p for p in weapon.scan_cells(center, direction) if in_bounds(p, enemy.size)]
               if weapon.kind == 'scan' else
               [p for p in weapon.targets(center) + ([extra] if extra else []) if in_bounds(p, enemy.size)])
    targets = list(dict.fromkeys(targets))
    if weapon.kind == 'attack' and all(p in results for p in targets):
        print('その範囲はすでに攻撃済み。弾薬を消費せず選び直せます。')
        return False
    if not player.use_ammo(key):
        print('この兵装は使用できません。')
        return False
    if weapon.kind == 'scan':
        count = player.record_radar_scan(targets, enemy, weapon.name)
        print(f'\n索敵結果: {len(targets)}マスを調査。艦影反応 {count} マス。')
        player.radar_logs.append((key, center, count))
        print(f'艦影あり {COLORS["green"]}^{RESET} / 反応なし - を表示（探索後の{RADAR_LIFETIME}行動まで有効）。')
        return True
    # 攻撃前の情報を使って座標を選べるよう、攻撃成立後に寿命を進める。
    player.advance_radar_time()
    for p in targets:
        label = f'({p[0] + 1},{p[1] + 1})'
        if p in results:
            print(f'{label}: 攻撃済みのためスキップ')
            continue
        outcome, sunk = enemy.attack(p)
        results[p] = outcome
        print(f'{label}: ' + {'hit': f'{COLORS["red"]}命中！{RESET}', 'sunk': f'\033[1;31m撃沈！{RESET}', 'miss': '外れ', 'near': f'{COLORS["yellow"]}至近弾！{RESET}'}[outcome])
        if sunk:
            print('撃沈艦:', sunk.name)
            animation(SINKING, animations)
    return True


def cpu_turn(rng, cpu, enemy, player, shots, animations):
    while True:
        choice = cpu.choose_target(enemy)
        if choice is None:
            return
        center = choice.position
        priority = choice.priority
        weapon_list = enemy.available()
        if priority <= 1:
            keys = [
                key
                for key in weapon_list
                if WEAPONS[key].kind == 'attack'
        ]
        else:
            keys = [
                key
                for key in weapon_list
                if WEAPONS[key].kind == 'scan'
            ]
            keys += weapon_list
        key = rng.choice(keys)
        weapon = WEAPONS[key]
        direction = (
            rng.choice(DIRECTIONS) 
            if key == 'directional_radar' 
            else None
            )
        extra = (
            extra_choice.position
            if key == 'double' and (extra_choice := cpu.choose_target(enemy)) is not None
            else None
        )
        targets = ([p for p in weapon.scan_cells(center, direction) if in_bounds(p, player.size)]
                    if weapon.kind == 'scan' else
                    [p for p in weapon.targets(center) + ([extra] if extra else []) if in_bounds(p, player.size)])
        targets = list(dict.fromkeys(targets))
        if (weapon.kind == 'attack' and all(p in shots for p in targets)) or not enemy.use_ammo(key):
            continue
        if weapon.kind == 'scan':
            cpu.tried.discard(center)
            count = enemy.record_radar_scan(targets, player, weapon.name)
            print(f'\n索敵結果: {len(targets)}マスを調査。艦影反応 {count} マス。')
            enemy.radar_logs.append((key, center, count))
            print(f'艦影あり {COLORS["green"]}^{RESET} / 反応なし - を表示（探索後の{RADAR_LIFETIME}行動まで有効）。')
            animation(enabled=animations)
            break
        enemy.advance_radar_time()
        print(f"\n敵は{COLORS['red']}{WEAPONS[key].name}{RESET}を使った")
        for p in targets:
            label = f'({p[0] + 1},{p[1] + 1})'
            if p in shots:
                print(f'{label}: 攻撃済みのためスキップ')
                continue
            outcome, sunk = player.attack(p)
            shots[p] = outcome
            cpu.tried.add(p)
            cpu.feedback(p, outcome)
            print(f'敵の攻撃 ({p[0] + 1},{p[1] + 1}): ' +
                {'hit': f'{COLORS["red"]}命中！{RESET}', 'sunk': f'\033[1;31m撃沈！{RESET}', 'miss': '外れ', 'near': f'{COLORS["yellow"]}至近弾！{RESET}'}[outcome])
            if sunk:
                surrounding = {
                    (x + dx, y + dy)
                    for x, y in sunk.positions
                    for dx, dy in NEIGHBORS
                    if in_bounds((x + dx, y + dy), player.size)
                } - sunk.positions
                cpu.hit_targets = deque(
                    position
                    for position in cpu.hit_targets
                    if position not in surrounding
                )
                cpu.near_targets = deque(
                    position
                    for position in cpu.near_targets
                    if position not in surrounding
                )
                print(f'こちらの', sunk.name, f'が{COLORS["red"]}撃沈{RESET}されました！')
                animation(SINKING, animations)
        animation(enabled=animations)
        break


# デモ
def make_debug_enemy_fleet(size):
    """4〜6マスの固定敵艦配置。命中・至近弾・撃沈を再現しやすくする。"""
    if size == 4:
        ships = [AttackSubmarine(), StandardSubmarine(), Scout()]
        placements = [(0, 0), (2, 1), (3, 0)]
    elif size == 5:
        ships = [AttackSubmarine(), StandardSubmarine(), NuclearSubmarine()]
        placements = [(0, 0), (2, 1), (4, 0)]
    elif size == 6:
        ships = [AttackSubmarine(), StandardSubmarine(), Carrier()]
        placements = [(0, 0), (2, 1), (4, 0)]
    else:
        raise ValueError('デバッグ海域は4×4、5×5、6×6から選択します。')
    fleet = Fleet(ships, size)
    for ship, (x, y) in zip(ships, placements):
        if not fleet.place(ship, x, y):
            raise AssertionError('デバッグ用の固定配置が不正です')
    return fleet


# CPU
def make_cpu_fleet(rng, size=SIZE):
    # 攻撃可能艦は必ず含める（プレイヤー側は空母だけの編成も許可）
    pool = [AttackSubmarine, StandardSubmarine, NuclearSubmarine, StealthSubmarine, Carrier, Scout]
    classes = [rng.choice(pool[:3]), rng.choice(pool), rng.choice(pool)]
    fleet = Fleet([c() for c in classes], size)
    fleet.random_place_all(rng)
    return fleet


def run_game(rng, animations=True, auto_demo=False, debug_size=None):
    # デモ用
    if auto_demo:
        player = Fleet([AttackSubmarine(), StandardSubmarine(), Carrier()])
        player.random_place_all(rng)
    # 通常ゲーム
    else:
        print(f'\n{COLORS["cyan"]}========== FLEET COMMAND / 魚雷ゲーム =========={RESET}')
        size = debug_size or SIZE
        if debug_size:
            print(f'【DEBUG {size}×{size}】敵の艦影を公開／固定配置／敵の反撃なし。')
            print('命中やレーダーの検証用です。通常モードとはルールが異なります。')
        else:
            print('6種類の艦種から2〜3隻を選んで9x9の海域に配置。敵艦隊の編成は公開、位置は秘密です。')
        print(f'命中{COLORS["red"]}X{RESET} / 外れo / 至近弾{COLORS["yellow"]}?{RESET} {COLORS["gray"]}（周囲8マス以内に艦影あり）{RESET}')
        # 戦艦選択
        player = Fleet(choose_fleet(size), size)
        # 戦艦の配置
        place_player_fleet(player, rng)
    # デモ用と通常の敵戦配置
    enemy = make_debug_enemy_fleet(debug_size) if debug_size else make_cpu_fleet(rng)
    animation(DEPARTURE, animations)
    player_shots, cpu_shots = {}, {}
    cpu = CPU(rng, player.size)
    turn = 0
    if not player.has_offense():
        print('この艦隊には攻撃手段がありません。出撃しましたが戦闘不能で敗北です。')
        return 'lose'
    while True:
        turn += 1
        if auto_demo and turn > player.size * player.size * 3 + 10:
            raise RuntimeError('終了できないゲーム状態を検出しました')
        if not auto_demo:
            # 盤面の初期設定
            show_boards(player, enemy, player_shots, cpu_shots, turn, reveal_enemy=bool(debug_size))
        # 行動選択
        action_finished = False
        while not action_finished:
            # デモ
            if auto_demo:
                keys = [k for k in player.available() if WEAPONS[k].kind == 'attack']
                key = 'torpedo' if 'torpedo' in keys else (keys[0] if keys else None)
                if key is None:
                    break
                pool = [(x, y) for y in range(enemy.size) for x in range(enemy.size) if (x, y) not in player_shots]
                if not pool:
                    break
                center = rng.choice(pool)
                outcome, sunk = enemy.attack(center)
                player_shots[center] = outcome
                if sunk and animations:
                    animation(SINKING, True)
                action_finished = True
            # 通常ゲーム
            else:
                key = offer_weapon(player)
                action_finished = perform_player_action(key, player, enemy, player_shots, animations)
        # チェック
        if enemy.defeated():
            print('\n*** VICTORY! 敵艦隊を制圧しました！ ***')
            show_result("win", turn, player, enemy)
            return 'win'
        if player.defeated():
            print('\n*** DEFEAT! 自艦隊は戦闘不能です。 ***')
            show_result("lose", turn, player, enemy)
            return 'lose'
        if debug_size:
            print('【DEBUG】敵の反撃はスキップ。次の検証へ進みます。')
            continue
        if not auto_demo:
            input('Enterで敵の反撃へ進む...')
        cpu_turn(rng, cpu, enemy, player, cpu_shots, animations and not auto_demo)
        if player.defeated():
            print('\n*** DEFEAT! 自艦隊は戦闘不能です。 ***')
            show_result("lose", turn, player, enemy)
            return 'lose'


def main():
    parser = argparse.ArgumentParser(description='ASCII魚雷ゲーム (艦隊編成＋CPU戦)')
    parser.add_argument('--seed', type=int, default=None, help='乱数シード（動作確認用）')
    parser.add_argument('--no-animations', action='store_true', help='演出の待ち時間を省く')
    parser.add_argument('--demo', action='store_true', help='自動対戦で動作確認する')
    parser.add_argument('--debug', type=int, choices=(4, 5, 6), metavar='{4,5,6}',
                        help='検証モード: 小さな海域・敵の固定配置を公開・敵の反撃なし')
    args = parser.parse_args()
    if args.debug and args.demo:
        parser.error('--debug と --demo は同時に指定できません')
    rng = random.Random(args.seed)
    try:
        while True:
            outcome = run_game(rng, animations=not args.no_animations and not args.demo,
                               auto_demo=args.demo, debug_size=args.debug)
            if args.demo:
                print('DEMO RESULT:', outcome)
                break
            if input('\nもう一度遊びますか？ [y/N] ').strip().lower() != 'y':
                break
    except (KeyboardInterrupt, EOFError):
        print('\nゲームを終了しました。')


if __name__ == '__main__':
    main()
