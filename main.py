import random
import asyncio
from contextlib import asynccontextmanager
from itertools import combinations, permutations
from collections import Counter
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketState

def is_same_tile(t1, t2):
    return (t1["top"] == t2["top"] and t1["bottom"] == t2["bottom"]) or \
           (t1["top"] == t2["bottom"] and t1["bottom"] == t2["top"])

def evaluate_fixed_hand(tiles, rule_level=4, is_incidental=False):
    if len(tiles) != 6:
        return None

    star_count = sum(1 for t in tiles if t["is_double"])
    bottoms = [t["bottom"] for t in tiles]
    tops = [t["top"] for t in tiles]
    all_nums = set(tops + bottoms)

    best_name = None
    best_base = -1
    best_stars = 0

    # 4단계 전용 족보
    if rule_level >= 4:
        if sorted(tops) == [1, 2, 3, 4, 5, 6] and sorted(bottoms) == [1, 2, 3, 4, 5, 6]:
            if all(t["top"] + t["bottom"] == 7 for t in tiles):
                if 8 > best_base:
                    best_name, best_base, best_stars = "개화", 8, star_count

        chain_pairs = sorted([tuple(sorted((t["top"], t["bottom"]))) for t in tiles])
        if chain_pairs == [(1, 2), (1, 6), (2, 3), (3, 4), (4, 5), (5, 6)]:
            if 6 > best_base:
                best_name, best_base, best_stars = "연쇄", 6, 0

    # 3단계 이상 족보
    if rule_level >= 3:
        if star_count == 6 and sorted(tops) == [1, 2, 3, 4, 5, 6]:
            if 3 > best_base:
                best_name, best_base, best_stars = "무쌍", 3, 6

    # 2단계 이상 족보
    if rule_level >= 2:
        if star_count == 6:
            if 5 > best_base:
                best_name, best_base, best_stars = "휘광", 5, 0

        for p in permutations(tiles):
            if is_same_tile(p[0], p[1]) and is_same_tile(p[2], p[3]) and is_same_tile(p[4], p[5]):
                if 5 > best_base:
                    best_name, best_base, best_stars = "삼동", 5, star_count
                break

        if is_incidental and len(all_nums) <= 3:
            if 3 > best_base:
                best_name, best_base, best_stars = "삼색", 3, 0

    # 1단계 기본 족보
    if rule_level >= 1:
        if len(set(bottoms)) == 1 and sorted(tops) == [1, 2, 3, 4, 5, 6]:
            if 6 > best_base:
                best_name, best_base, best_stars = "육화", 6, star_count

        for s1_idx in combinations(range(6), 3):
            s2_idx = [i for i in range(6) if i not in s1_idx]
            s1 = [tiles[i] for i in s1_idx]
            s2 = [tiles[i] for i in s2_idx]
            
            c1 = (len(set(t["bottom"] for t in s1)) == 1)
            st1 = sorted(t["top"] for t in s1)
            seq1 = (len(st1) == 3 and st1[0] + 1 == st1[1] and st1[1] + 1 == st1[2])

            c2 = (len(set(t["bottom"] for t in s2)) == 1)
            st2 = sorted(t["top"] for t in s2)
            seq2 = (len(st2) == 3 and st2[0] + 1 == st2[1] and st2[1] + 1 == st2[2])

            if c1 and seq1 and c2 and seq2:
                if 3 > best_base:
                    best_name, best_base, best_stars = "삼연", 3, star_count
                break

        if len(set(bottoms)) == 1:
            if 1 > best_base:
                best_name, best_base, best_stars = "일색", 1, star_count

    if best_name:
        return {
            "name": best_name, "base_score": best_base,
            "stars": best_stars, "total_score": best_base + best_stars
        }
    return None

def evaluate_hand(tiles, rule_level=4, is_incidental=False):
    if len(tiles) != 6:
        return None

    best = None
    for mask in range(64):
        variant = []
        for i in range(6):
            t = tiles[i]
            if (mask >> i) & 1:
                variant.append({"id": t["id"], "top": t["bottom"], "bottom": t["top"], "is_double": t["is_double"]})
            else:
                variant.append(t)
        res = evaluate_fixed_hand(variant, rule_level=rule_level, is_incidental=is_incidental)
        if res:
            if best is None or res["total_score"] > best["total_score"]:
                best = res
                if best["total_score"] >= 8:
                    break
    return best

def check_incidental_win(hand_5, discards, rule_level=4):
    best = None
    for d in discards:
        res = evaluate_hand(hand_5 + [d], rule_level=rule_level, is_incidental=True)
        if res:
            if best is None or res["total_score"] > best["total_score"]:
                best = res
                best["winning_tile"] = d
    return best

class GameSession:
    def __init__(self, mode="single", ai_diff="high"):
        self.mode = mode
        self.ai_diff = ai_diff
        self.rule_level = 4
        self.target_score = 10
        self.time_limit = 60
        self.ai_delay_setting = 0
        self.time_left = 60
        self.scores = {1: 0, 2: 0}
        self.starter = 1
        self.ready = {1: False, 2: False}
        self.game_started = False
        self.status_notice = None
        self.last_taken_discard = None
        self.last_discard_info = None
        self.ai_task = None
        self.event_banner = None
        self.reset_round()

    def reset_round(self):
        unique = [(top, bottom) for top in range(1, 7) for bottom in range(top, 7)]
        self.deck = []
        tid = 0
        for _ in range(2):
            for top, bottom in unique:
                tid += 1
                self.deck.append({
                    "id": f"t_{tid}", "top": top, "bottom": bottom, "is_double": (top == bottom)
                })
        random.shuffle(self.deck)

        self.players = {1: [], 2: []}
        self.discards = []
        self.riichi = {1: False, 2: False}
        self.current_turn = self.starter
        self.turn_phase = "lobby" if not self.game_started else "draw"
        self.round_winner = None
        self.round_settlement = []
        self.last_drawn_id = {1: None, 2: None}
        self.last_discard = None
        self.last_taken_discard = None
        self.last_discard_info = None
        self.ready = {1: False, 2: False}
        self.status_notice = None
        self.event_banner = None
        if self.ai_task and not self.ai_task.done():
            self.ai_task.cancel()
        self.ai_task = None
        self.time_left = self.time_limit

        for _ in range(5):
            self.players[1].append(self.deck.pop())
            self.players[2].append(self.deck.pop())

    def reset_timer(self):
        self.time_left = self.time_limit

    def flip_tile(self, p_num, tile_id):
        hand = self.players[p_num]
        for t in hand:
            if t["id"] == tile_id:
                t["top"], t["bottom"] = t["bottom"], t["top"]
                return True
        return False

    def reorder_tiles(self, p_num, order_ids):
        hand = self.players[p_num]
        id_map = {t["id"]: t for t in hand}
        new_hand = [id_map[tid] for tid in order_ids if tid in id_map]
        if len(new_hand) == len(hand):
            self.players[p_num] = new_hand
            return True
        return False

    def draw_tile(self, p_num, discard_id=None):
        if self.current_turn != p_num or self.turn_phase != "draw":
            return False

        if discard_id:
            target = next((t for t in self.discards if t["id"] == discard_id), None)
            if not target:
                return False
            self.discards.remove(target)
            tile = target
            self.last_taken_discard = {"by": p_num, "tile": target}
        else:
            if not self.deck:
                self.turn_phase = "round_end"
                self.round_settlement.append({"player": 0, "text": "유국 (패산 소진으로 무승부)"})
                self.check_round_end_incidentals(winner_num=None)
                return True
            tile = self.deck.pop()

        self.players[p_num].append(tile)
        self.last_drawn_id[p_num] = tile["id"]
        self.turn_phase = "discard"
        return True

    def discard_tile(self, p_num, tile_id, declare_riichi=False):
        if self.current_turn != p_num or self.turn_phase != "discard":
            return False

        if self.riichi[p_num] and tile_id != self.last_drawn_id[p_num]:
            return False

        hand = self.players[p_num]
        target = next((t for t in hand if t["id"] == tile_id), None)
        if not target:
            return False

        p_title = "1P" if p_num == 1 else ("2P" if self.mode == "multi" else "AI")

        if declare_riichi and not self.riichi[p_num] and self.rule_level >= 3:
            self.riichi[p_num] = True
            self.event_banner = {
                "type": "riichi",
                "title": "🔥 리치 (RIICHI)!",
                "yaku_name": f"{p_title} 리치 선언 (+1점)",
                "subtext": "텐파이 확정! 손패가 고정됩니다."
            }

        hand.remove(target)
        self.discards.append(target)
        self.last_discard = target
        self.last_discard_info = {"by": p_num, "tile": target}

        self.pass_turn()
        return True

    def pass_turn(self):
        self.current_turn = 2 if self.current_turn == 1 else 1
        self.turn_phase = "draw"
        self.reset_timer()

    def handle_timeout(self):
        p = self.current_turn
        if self.turn_phase == "draw":
            self.pass_turn()
        elif self.turn_phase == "discard":
            tid_to_discard = self.last_drawn_id.get(p)
            hand = self.players[p]
            target = next((t for t in hand if t["id"] == tid_to_discard), None)
            if not target and hand:
                target = hand[-1]
            if target:
                hand.remove(target)
                self.discards.append(target)
                self.last_discard = target
                self.last_discard_info = {"by": p, "tile": target}
            self.pass_turn()

    def declare_tsumo(self, p_num):
        if self.current_turn != p_num or self.turn_phase != "discard":
            return False
        res = evaluate_hand(self.players[p_num], rule_level=self.rule_level, is_incidental=False)
        if res:
            base = res["base_score"]
            stars = res["stars"]
            riichi_pt = 1 if self.riichi[p_num] else 0
            total = base + stars + riichi_pt
            self.scores[p_num] += total
            self.round_winner = p_num

            p_title = f"{p_num}P" if self.mode == "multi" or p_num == 1 else "AI(2P)"
            detail = f"{res['name']}({base}점)"
            if stars > 0: detail += f" + 별보너스({stars}점)"
            if riichi_pt > 0: detail += " + 리치(1점)"

            self.event_banner = {
                "type": "tsumo",
                "title": "🏆 역 완성 (쯔모)!",
                "yaku_name": f"[{res['name']}]",
                "subtext": f"{p_title} 완성 | 총 {total}점 획득"
            }
            self.round_settlement = [{
                "player": p_num, "type": "쯔모",
                "text": f"[{p_title} 쯔모 완성] {detail} = 총 {total}점 획득"
            }]
            self.check_round_end_incidentals(winner_num=p_num)
            self.end_round()
            return True
        return False

    def declare_ron(self, p_num, mode="steal"):
        if self.rule_level < 3:
            return False
        if self.current_turn != p_num or self.turn_phase != "draw" or not self.last_discard:
            return False

        opp = 2 if p_num == 1 else 1
        winning_tile = self.last_discard

        res = evaluate_hand(self.players[p_num] + [winning_tile], rule_level=self.rule_level, is_incidental=False)
        if not res:
            return False

        if winning_tile in self.discards:
            self.discards.remove(winning_tile)
        self.players[p_num].append(winning_tile)

        base = res["base_score"]
        stars = res["stars"]
        riichi_pt = 1 if self.riichi[p_num] else 0
        total = base + stars + riichi_pt

        p_title = f"{p_num}P" if self.mode == "multi" or p_num == 1 else "AI(2P)"
        opp_title = f"{opp}P" if self.mode == "multi" or opp == 1 else "AI(2P)"

        detail = f"{res['name']}({base}점)"
        if stars > 0: detail += f" + 별보너스({stars}점)"
        if riichi_pt > 0: detail += " + 리치(1점)"

        if mode == "steal":
            stolen = min(self.scores[opp], total)
            self.scores[opp] -= stolen
            self.scores[p_num] += stolen
            desc = f"[{p_title} 론(강탈)] {detail} = {opp_title}에게서 {stolen}점 강탈 (기본 점수: {total}점)"
        else:
            self.scores[p_num] += total
            desc = f"[{p_title} 완성] {detail} = 공급처로부터 총 {total}점 획득"

        self.event_banner = {
            "type": "ron",
            "title": "⚡ 론 (RON) 직격!",
            "yaku_name": f"[{res['name']}]",
            "subtext": f"{p_title}이(가) {opp_title}의 버림패로 완성!"
        }
        self.round_winner = p_num
        self.round_settlement = [{"player": p_num, "type": "론", "text": desc}]
        self.end_round()
        return True

    def check_round_end_incidentals(self, winner_num):
        for p in [1, 2]:
            if p != winner_num and len(self.players[p]) == 5:
                res = check_incidental_win(self.players[p], self.discards, rule_level=self.rule_level)
                if res:
                    wt = res["winning_tile"]
                    self.players[p].append(wt)
                    base = res["base_score"]
                    stars = res["stars"]
                    riichi_pt = 1 if self.riichi[p] else 0
                    total = base + stars + riichi_pt
                    self.scores[p] += total

                    p_title = f"{p}P" if self.mode == "multi" or p == 1 else "AI(2P)"
                    detail = f"{res['name']}({base}점)"
                    if stars > 0: detail += f" + 별보너스({stars}점)"
                    if riichi_pt > 0: detail += " + 리치(1점)"

                    self.round_settlement.append({
                        "player": p, "type": "겸사겸사",
                        "text": f"[{p_title} 겸사겸사 완성] {detail} = 총 {total}점 획득"
                    })

    def end_round(self):
        if self.scores[1] >= self.target_score or self.scores[2] >= self.target_score:
            self.turn_phase = "game_over"
        else:
            self.turn_phase = "round_end"
            self.starter = 2 if self.starter == 1 else 1

    def evaluate_hand_potential_fast(self, hand):
        if not hand: return 0
        star_count = sum(1 for t in hand if t["is_double"])
        num_freq = Counter()
        for t in hand:
            num_freq[t["top"]] += 1
            if t["top"] != t["bottom"]:
                num_freq[t["bottom"]] += 1
        max_color = max(num_freq.values()) if num_freq else 0

        pair_cnt = 0
        for i in range(len(hand)):
            for j in range(i + 1, len(hand)):
                if is_same_tile(hand[i], hand[j]):
                    pair_cnt += 1

        score = (max_color * 10) + (star_count * 8) + (pair_cnt * 12)
        return score

    async def execute_ai_step(self, ws: WebSocket):
        try:
            total_delay = 0.3 + float(self.ai_delay_setting)
            await asyncio.sleep(total_delay)
            if not self.game_started or self.current_turn != 2:
                return

            opp_score = self.scores[1]

            # 1. DRAW
            if self.turn_phase == "draw":
                if self.rule_level >= 3 and self.last_discard:
                    res_ron = evaluate_hand(self.players[2] + [self.last_discard], rule_level=self.rule_level, is_incidental=False)
                    if res_ron:
                        base = res_ron["base_score"]
                        stars = res_ron["stars"]
                        riichi_pt = 1 if self.riichi[2] else 0
                        hand_total = base + stars + riichi_pt
                        chosen_mode = "steal" if opp_score >= hand_total else "direct"
                        should_ron = True
                        if not self.riichi[2] and opp_score == 0 and hand_total <= 1 and len(self.deck) > 15:
                            should_ron = False

                        if should_ron:
                            self.declare_ron(2, mode=chosen_mode)
                            self.status_notice = None
                            await send_state_to_ws(ws, self, 1)
                            self.event_banner = None
                            return

                picked_from_floor = False
                picked_tile_info = None

                if self.discards:
                    base_pot = self.evaluate_hand_potential_fast(self.players[2])
                    best_gain = 0
                    best_target = None
                    for disc in reversed(self.discards[-4:]):
                        gain = self.evaluate_hand_potential_fast(self.players[2] + [disc]) - base_pot
                        if gain > best_gain:
                            best_gain = gain
                            best_target = disc

                    if best_target and best_gain >= 8:
                        if self.ai_diff != "low" or random.random() < 0.4:
                            picked_tile_info = f"[{best_target['top']}/{best_target['bottom']}]"
                            self.draw_tile(2, discard_id=best_target["id"])
                            picked_from_floor = True

                if not picked_from_floor:
                    self.draw_tile(2, discard_id=None)
                    draw_action_txt = "덱에서 패를 뽑고"
                else:
                    draw_action_txt = f"바닥에서 {picked_tile_info} 패를 가져오고"

            # 2. DISCARD
            if self.turn_phase == "discard":
                res_win = evaluate_hand(self.players[2], rule_level=self.rule_level, is_incidental=False)
                if res_win:
                    self.declare_tsumo(2)
                    self.status_notice = None
                    await send_state_to_ws(ws, self, 1)
                    self.event_banner = None
                    return

                hand = self.players[2]
                if self.riichi[2]:
                    chosen = next((t for t in hand if t["id"] == self.last_drawn_id[2]), hand[-1])
                    self.discard_tile(2, chosen["id"], declare_riichi=False)
                    self.status_notice = f"🤖 AI가 {draw_action_txt} [{chosen['top']}/{chosen['bottom']}]을(를) 버렸습니다."
                    await send_state_to_ws(ws, self, 1)
                    self.event_banner = None
                    return

                if self.ai_diff == "low":
                    chosen = random.choice(hand)
                    should_riichi = False
                else:
                    scored_candidates = []
                    for t in hand:
                        remain_5 = [x for x in hand if x["id"] != t["id"]]
                        pot = self.evaluate_hand_potential_fast(remain_5)
                        if t["is_double"]: pot -= 5
                        scored_candidates.append((pot, t))

                    scored_candidates.sort(key=lambda x: x[0], reverse=True)
                    chosen = scored_candidates[0][1]

                    should_riichi = False
                    if self.rule_level >= 3 and not self.riichi[2] and self.ai_diff in ["mid", "high"]:
                        remain_5 = [x for x in hand if x["id"] != chosen["id"]]
                        if self.evaluate_hand_potential_fast(remain_5) >= 42:
                            should_riichi = True

                self.discard_tile(2, chosen["id"], declare_riichi=should_riichi)
                riichi_txt = " (🔥리치 선언!)" if should_riichi else ""
                self.status_notice = f"🤖 AI가 {draw_action_txt} [{chosen['top']}/{chosen['bottom']}]을(를) 버렸습니다.{riichi_txt}"

                await send_state_to_ws(ws, self, 1)
                self.event_banner = None

        except asyncio.CancelledError:
            pass
        finally:
            self.ai_task = None

multi_game = GameSession(mode="multi")
multi_connections = {}
single_sessions = {}

async def send_state_to_ws(ws: WebSocket, game: GameSession, p_num: int):
    opp_num = 2 if p_num == 1 else 1
    my_hand = game.players.get(p_num, [])
    opp_hand = game.players.get(opp_num, [])

    current_yaku = evaluate_hand(my_hand, rule_level=game.rule_level, is_incidental=False) if len(my_hand) == 6 else None
    can_riichi = (game.rule_level >= 3 and p_num == game.current_turn and game.turn_phase == "discard" and not game.riichi[p_num])

    can_ron = False
    if game.rule_level >= 3 and p_num == game.current_turn and game.turn_phase == "draw" and game.last_discard:
        if evaluate_hand(my_hand + [game.last_discard], rule_level=game.rule_level, is_incidental=False):
            can_ron = True

    can_tsumo = (p_num == game.current_turn and game.turn_phase == "discard" and current_yaku is not None)
    show_all = (game.turn_phase in ["round_end", "game_over"])

    # 2인 모드 상대방 연결 상태 판정
    opp_connected = True
    if game.mode == "multi":
        opp_connected = (opp_num in multi_connections and multi_connections[opp_num].client_state == WebSocketState.CONNECTED)

    # 실제 접속 중인 소켓 수 계산
    active_player_count = sum(1 for conn in multi_connections.values() if conn.client_state == WebSocketState.CONNECTED)

    payload = {
        "mode": game.mode,
        "ai_diff": game.ai_diff,
        "rule_level": game.rule_level,
        "ai_delay_setting": game.ai_delay_setting,
        "player_num": p_num,
        "target_score": game.target_score,
        "time_limit": game.time_limit,
        "time_left": game.time_left,
        "my_turn": game.current_turn == p_num,
        "current_turn": game.current_turn,
        "phase": game.turn_phase,
        "deck_count": len(game.deck),
        "my_hand": my_hand,
        "opp_hand_count": len(opp_hand),
        "opp_hand": opp_hand if show_all else None,
        "opp_connected": opp_connected,
        "multi_player_count": active_player_count,
        "discards": game.discards,
        "scores": game.scores,
        "riichi": game.riichi,
        "winner": game.round_winner,
        "settlements": game.round_settlement,
        "can_ron": can_ron,
        "can_tsumo": can_tsumo,
        "can_riichi": can_riichi,
        "current_yaku": current_yaku,
        "last_drawn_id": game.last_drawn_id[p_num],
        "ready": game.ready,
        "show_all": show_all,
        "game_started": game.game_started,
        "status_notice": game.status_notice,
        "last_taken_discard": game.last_taken_discard,
        "last_discard_info": game.last_discard_info,
        "event_banner": game.event_banner
    }
    try:
        await ws.send_json(payload)
    except Exception:
        pass

async def broadcast_multi():
    """양쪽 플레이어 모두에게 상태를 전송한 뒤 1회성 배너 리셋"""
    for p_num, ws in list(multi_connections.items()):
        await send_state_to_ws(ws, multi_game, p_num)
    multi_game.event_banner = None

async def timer_background_task():
    while True:
        try:
            await asyncio.sleep(1)
            # 2인 모드 타이머
            if multi_game.game_started and multi_game.turn_phase in ["draw", "discard"] and multi_game.time_limit > 0:
                multi_game.time_left -= 1
                if multi_game.time_left <= 0:
                    multi_game.handle_timeout()
                await broadcast_multi()

            # 1인 모드 타이머
            for ws, s_game in list(single_sessions.items()):
                if s_game.game_started and s_game.turn_phase in ["draw", "discard"] and s_game.time_limit > 0:
                    s_game.time_left -= 1
                    if s_game.time_left <= 0:
                        s_game.handle_timeout()
                    await send_state_to_ws(ws, s_game, 1)

        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(timer_background_task())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def get_index():
    return FileResponse("index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    current_mode = "none"
    p_num = None

    await websocket.send_json({"mode": "none"})

    try:
        while True:
            data = await websocket.receive_json()
            act = data.get("action")

            if act == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if act == "select_mode":
                chosen = data.get("mode")
                if chosen == "single":
                    current_mode = "single"
                    p_num = 1
                    diff = data.get("diff", "high")
                    s_game = GameSession(mode="single", ai_diff=diff)
                    single_sessions[websocket] = s_game
                    await send_state_to_ws(websocket, s_game, 1)
                elif chosen == "multi":
                    current_mode = "multi"

                    # 유령 연결(Zombie Connection) 및 재접속 정리
                    for p in [1, 2]:
                        if p in multi_connections:
                            ws_conn = multi_connections[p]
                            if ws_conn.client_state != WebSocketState.CONNECTED or ws_conn == websocket:
                                del multi_connections[p]

                    # 빈자리 배정
                    if 1 not in multi_connections:
                        p_num = 1
                    elif 2 not in multi_connections:
                        p_num = 2
                    else:
                        await websocket.send_json({"type": "full", "msg": "2인 대전 방이 이미 가득 찼습니다."})
                        continue

                    multi_connections[p_num] = websocket
                    await broadcast_multi()

            elif act == "go_home":
                if current_mode == "single":
                    if websocket in single_sessions:
                        s_game = single_sessions[websocket]
                        if s_game.ai_task and not s_game.ai_task.done():
                            s_game.ai_task.cancel()
                        del single_sessions[websocket]
                elif current_mode == "multi":
                    for p in [1, 2]:
                        if multi_connections.get(p) == websocket:
                            del multi_connections[p]
                    multi_game.game_started = False
                    multi_game.scores = {1: 0, 2: 0}
                    multi_game.ready = {1: False, 2: False}
                    multi_game.reset_round()
                    await broadcast_multi()

                current_mode = "none"
                p_num = None
                await websocket.send_json({"mode": "none"})

            else:
                active_game = single_sessions.get(websocket) if current_mode == "single" else multi_game
                curr_p = 1 if current_mode == "single" else p_num

                if not active_game or curr_p is None:
                    continue

                if act == "set_settings":
                    if not active_game.game_started:
                        active_game.target_score = int(data.get("score", 10))
                        active_game.rule_level = int(data.get("rule_level", 4))
                        if active_game.mode == "single":
                            active_game.ai_delay_setting = float(data.get("ai_delay", 0))
                        else:
                            active_game.time_limit = int(data.get("time", 60))
                            active_game.time_left = active_game.time_limit
                elif act == "flip":
                    active_game.flip_tile(curr_p, data.get("tile_id"))
                elif act == "reorder":
                    active_game.reorder_tiles(curr_p, data.get("order", []))
                elif act == "draw":
                    active_game.draw_tile(curr_p, discard_id=data.get("discard_id"))
                elif act == "discard":
                    active_game.discard_tile(curr_p, data.get("tile_id"), data.get("riichi", False))
                elif act == "tsumo":
                    active_game.declare_tsumo(curr_p)
                elif act == "ron":
                    active_game.declare_ron(curr_p, mode=data.get("mode", "steal"))
                elif act == "ready":
                    active_game.ready[curr_p] = True
                    if current_mode == "single":
                        active_game.ready[2] = True
                        active_game.game_started = True
                        active_game.reset_round()
                    elif active_game.ready[1] and active_game.ready[2]:
                        active_game.game_started = True
                        active_game.reset_round()
                elif act == "ready_next":
                    active_game.ready[curr_p] = True
                    if current_mode == "single":
                        active_game.ready[2] = True
                        active_game.game_started = True
                        active_game.reset_round()
                    elif active_game.ready[1] and active_game.ready[2]:
                        active_game.game_started = True
                        active_game.reset_round()
                elif act == "reset_game":
                    active_game.scores = {1: 0, 2: 0}
                    active_game.game_started = False
                    active_game.reset_round()

                # 화면 동기화
                if current_mode == "single":
                    await send_state_to_ws(websocket, active_game, 1)
                    active_game.event_banner = None
                    if active_game.game_started and active_game.current_turn == 2:
                        if active_game.ai_task is None or active_game.ai_task.done():
                            active_game.ai_task = asyncio.create_task(active_game.execute_ai_step(websocket))
                else:
                    await broadcast_multi()

    except WebSocketDisconnect:
        if websocket in single_sessions:
            s_game = single_sessions[websocket]
            if s_game.ai_task and not s_game.ai_task.done():
                s_game.ai_task.cancel()
            del single_sessions[websocket]

        for p in [1, 2]:
            if multi_connections.get(p) == websocket:
                del multi_connections[p]
                multi_game.game_started = False
                multi_game.scores = {1: 0, 2: 0}
                multi_game.ready = {1: False, 2: False}
                multi_game.reset_round()
                await broadcast_multi()
                break
