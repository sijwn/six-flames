import random
import asyncio
from contextlib import asynccontextmanager
from itertools import combinations, permutations
from collections import Counter
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

def is_same_tile(t1, t2):
    return (t1["top"] == t2["top"] and t1["bottom"] == t2["bottom"]) or \
           (t1["top"] == t2["bottom"] and t1["bottom"] == t2["top"])

def evaluate_hand(tiles, is_incidental=False):
    if len(tiles) != 6:
        return None

    star_count = sum(1 for t in tiles if t["is_double"])
    bottoms = [t["bottom"] for t in tiles]
    tops = [t["top"] for t in tiles]
    all_nums = set(tops + bottoms)

    best_name = None
    best_base = -1
    best_stars = 0

    # 1. 무쌍 (3점 + 별보너스 6개 = 총 9점)
    if star_count == 6 and sorted(tops) == [1, 2, 3, 4, 5, 6]:
        best_name = "무쌍"
        best_base = 3
        best_stars = 6
        return {
            "name": best_name, "base_score": best_base,
            "stars": best_stars, "total_score": best_base + best_stars
        }

    # 2. 개화 (8점 + 별보너스)
    if (sorted(tops) == [1, 2, 3, 4, 5, 6] and sorted(bottoms) == [1, 2, 3, 4, 5, 6]):
        if all(t["top"] + t["bottom"] == 7 for t in tiles):
            if 8 > best_base:
                best_name = "개화"
                best_base = 8
                best_stars = star_count

    # 3. 연쇄 (6점, 보너스 무시)
    chain_pairs = sorted([tuple(sorted((t["top"], t["bottom"]))) for t in tiles])
    if chain_pairs == [(1, 2), (1, 6), (2, 3), (3, 4), (4, 5), (5, 6)]:
        if 6 > best_base:
            best_name = "연쇄"
            best_base = 6
            best_stars = 0

    # 4. 육화 (6점 + 별보너스)
    if len(set(bottoms)) == 1 and sorted(tops) == [1, 2, 3, 4, 5, 6]:
        if 6 > best_base:
            best_name = "육화"
            best_base = 6
            best_stars = star_count

    # 5. 휘광 (5점, 보너스 무시)
    if star_count == 6:
        if 5 > best_base:
            best_name = "휘광"
            best_base = 5
            best_stars = 0

    # 6. 삼동 (5점 + 별보너스)
    for p in permutations(tiles):
        if is_same_tile(p[0], p[1]) and is_same_tile(p[2], p[3]) and is_same_tile(p[4], p[5]):
            if 5 > best_base:
                best_name = "삼동"
                best_base = 5
                best_stars = star_count
            break

    # 7. 삼색 (3점, 보너스 무시) - 겸사겸사
    if is_incidental and len(all_nums) <= 3:
        if 3 > best_base:
            best_name = "삼색"
            best_base = 3
            best_stars = 0

    # 8. 삼연 (3점 + 별보너스)
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
                best_name = "삼연"
                best_base = 3
                best_stars = star_count
            break

    # 9. 동형 (1점 + 별보너스)
    if sorted(tops) == [1, 2, 2, 3, 3, 3]:
        if 1 > best_base:
            best_name = "동형"
            best_base = 1
            best_stars = star_count

    # 10. 일색 (1점 + 별보너스)
    if len(set(bottoms)) == 1:
        if 1 > best_base:
            best_name = "일색"
            best_base = 1
            best_stars = star_count

    if best_name:
        return {
            "name": best_name, "base_score": best_base,
            "stars": best_stars, "total_score": best_base + best_stars
        }
    return None

def check_incidental_win(hand_5, discards):
    best = None
    for d in discards:
        for flipped in [False, True]:
            tile = dict(d)
            if flipped:
                tile["top"], tile["bottom"] = tile["bottom"], tile["top"]
            res = evaluate_hand(hand_5 + [tile], is_incidental=True)
            if res:
                if best is None or res["total_score"] > best["total_score"]:
                    best = res
                    best["winning_tile"] = tile
    return best

class GameSession:
    def __init__(self, mode="single", ai_diff="mid"):
        self.mode = mode
        self.ai_diff = ai_diff
        self.target_score = 10
        self.time_limit = 60
        self.time_left = 60
        self.scores = {1: 0, 2: 0}
        self.starter = 1
        self.ready = {1: False, 2: False}
        self.game_started = False
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
        self.ready = {1: False, 2: False}
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

        if declare_riichi and not self.riichi[p_num]:
            self.riichi[p_num] = True

        hand.remove(target)
        self.discards.append(target)
        self.last_discard = target

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
            self.pass_turn()

    def declare_tsumo(self, p_num):
        if self.current_turn != p_num or self.turn_phase != "discard":
            return False
        res = evaluate_hand(self.players[p_num], is_incidental=False)
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

            self.round_settlement = [{
                "player": p_num, "type": "쯔모",
                "text": f"[{p_title} 쯔모 완성] {detail} = 총 {total}점 획득"
            }]
            self.check_round_end_incidentals(winner_num=p_num)
            self.end_round()
            return True
        return False

    def declare_ron(self, p_num, mode="steal"):
        if self.current_turn != p_num or self.turn_phase != "draw" or not self.last_discard:
            return False

        opp = 2 if p_num == 1 else 1
        winning_tile = self.last_discard

        res = evaluate_hand(self.players[p_num] + [winning_tile], is_incidental=False)
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

        self.round_winner = p_num
        self.round_settlement = [{"player": p_num, "type": "론", "text": desc}]
        self.end_round()
        return True

    def check_round_end_incidentals(self, winner_num):
        for p in [1, 2]:
            if p != winner_num and len(self.players[p]) == 5:
                res = check_incidental_win(self.players[p], self.discards)
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

    # --- 전략적 AI 평가 알고리즘 ---
    def calculate_hand_outs(self, hand_5):
        """남은 5장에 어떤 타일이 들어오면 역이 완성되는지 모든 유효타 반환"""
        valid_outs = []
        for top in range(1, 7):
            for btm in range(top, 7):
                t1 = {"id": "sim", "top": top, "bottom": btm, "is_double": (top == btm)}
                t2 = {"id": "sim", "top": btm, "bottom": top, "is_double": (top == btm)}
                r1 = evaluate_hand(hand_5 + [t1], is_incidental=False)
                r2 = evaluate_hand(hand_5 + [t2], is_incidental=False)
                if r1 or r2:
                    score = (r1["total_score"] if r1 else 0) or (r2["total_score"] if r2 else 0)
                    valid_outs.append((top, btm, score))
        return valid_outs

    def count_remaining_outs(self, p_num, valid_outs):
        """유효타 중 실제 게임(패산+상대패)에 살아있는 매수 및 점수 기댓값"""
        total_remaining = 0
        total_score_pot = 0
        known_tiles = self.players[p_num] + self.discards
        for (top, btm, score) in valid_outs:
            used = sum(1 for t in known_tiles if (t["top"] == top and t["bottom"] == btm) or (t["top"] == btm and t["bottom"] == top))
            remain = max(0, 2 - used)
            total_remaining += remain
            total_score_pot += (remain * score)
        return total_remaining, total_score_pot

    def evaluate_hand_potential(self, hand, opponent_riichi=False):
        """현재 패(5장 혹은 6장)의 역 지향성 및 잠재력 종합 평가"""
        if not hand: return 0

        # 최적 위/아래 플립 맞춤
        best_pot = 0
        star_count = sum(1 for t in hand if t["is_double"])
        bottom_counts = Counter(t["bottom"] for t in hand)
        top_counts = Counter(t["top"] for t in hand)

        # 1. 상대방이 리치 상태라면: 빠른 1점 완성(일색/동형)에 극단적 가중치
        if opponent_riichi:
            max_same_bottom = max(bottom_counts.values()) if bottom_counts else 0
            return max_same_bottom * 15 + star_count * 2

        # 2. 일반 상황: 고득점 역 잠재력 점수화
        # 휘광(별 6개) 가능성
        if star_count >= 3:
            best_pot += star_count * 12

        # 일색 및 육화(아래 동일) 가능성
        max_same_bottom = max(bottom_counts.values()) if bottom_counts else 0
        best_pot += max_same_bottom * 8

        # 삼동(페어) 가능성
        pairs = 0
        for i in range(len(hand)):
            for j in range(i + 1, len(hand)):
                if is_same_tile(hand[i], hand[j]):
                    pairs += 1
        best_pot += pairs * 10

        return best_pot

    async def run_ai_turn(self):
        await asyncio.sleep(0.7)
        if not self.game_started or self.current_turn != 2:
            return

        opp_riichi = self.riichi[1]

        # 1. DRAW 단계
        if self.turn_phase == "draw":
            # 1-1. 론(직격) 완성 체크
            if self.last_discard:
                for fl in [False, True]:
                    t_cand = dict(self.last_discard)
                    if fl: t_cand["top"], t_cand["bottom"] = t_cand["bottom"], t_cand["top"]
                    if evaluate_hand(self.players[2] + [t_cand], is_incidental=False):
                        self.declare_ron(2, mode="steal")
                        return

            # 1-2. 바닥 패 가져오기 탐색
            picked_from_floor = False
            if self.discards:
                current_pot = self.evaluate_hand_potential(self.players[2], opponent_riichi=opp_riichi)
                candidates = []

                # 난이도별 바닥 탐색 깊이
                search_depth = 2 if self.ai_diff == "low" else (5 if self.ai_diff == "mid" else len(self.discards))
                for disc in reversed(self.discards[-search_depth:]):
                    for fl in [False, True]:
                        cand = dict(disc)
                        if fl: cand["top"], cand["bottom"] = cand["bottom"], cand["top"]
                        # 가져왔을 때 즉시 역이 완성되거나 패의 질이 크게 개선되는지 평가
                        test_hand_6 = self.players[2] + [cand]
                        if evaluate_hand(test_hand_6, is_incidental=False):
                            candidates.append((999, disc["id"])) # 즉시 완성
                            break

                        # 완성은 아니어도 잠재력이 대폭 상승하는지 비교
                        pot_diff = self.evaluate_hand_potential(test_hand_6, opponent_riichi=opp_riichi) - current_pot
                        if pot_diff >= (10 if self.ai_diff == "high" else 14):
                            candidates.append((pot_diff, disc["id"]))
                            break

                if candidates:
                    # 초급은 40% 확률로만 주움, 중/고급은 최적의 바닥패 확정 수거
                    if self.ai_diff != "low" or random.random() < 0.4:
                        candidates.sort(key=lambda x: x[0], reverse=True)
                        best_discard_id = candidates[0][1]
                        self.draw_tile(2, discard_id=best_discard_id)
                        picked_from_floor = True

            if not picked_from_floor:
                self.draw_tile(2, discard_id=None)

        await asyncio.sleep(0.7)
        if not self.game_started or self.current_turn != 2:
            return

        # 2. DISCARD 단계
        if self.turn_phase == "discard":
            # 2-1. 쯔모 완성 체크
            if evaluate_hand(self.players[2], is_incidental=False):
                self.declare_tsumo(2)
                return

            hand = self.players[2]

            # 리치 상태면 뽑은 패 고정 버림
            if self.riichi[2]:
                chosen = next((t for t in hand if t["id"] == self.last_drawn_id[2]), hand[-1])
                self.discard_tile(2, chosen["id"], declare_riichi=False)
                return

            # 2-2. 6장 중 어떤 패를 버릴지 정밀 비교
            best_tile = None
            should_riichi = False

            if self.ai_diff == "low":
                # 초급: 완전 무작위 대신 손패의 고립패 위주 방출
                chosen = random.choice(hand)
                declare_r = False
            else:
                candidates = []
                for t in hand:
                    remain_5 = [x for x in hand if x["id"] != t["id"]]
                    outs = self.calculate_hand_outs(remain_5)
                    live_outs_count, score_pot = self.count_remaining_outs(2, outs)
                    potential = self.evaluate_hand_potential(remain_5, opponent_riichi=opp_riichi)

                    # 가중치 계산
                    eval_score = (live_outs_count * 20) + (score_pot * 2) + potential
                    
                    # 상대가 리치라면? 안전패(바닥에 이미 많이 버려진 숫자) 방출 가산점
                    if opp_riichi:
                        safety = sum(1 for d in self.discards if d["top"] == t["top"] or d["bottom"] == t["bottom"])
                        eval_score += safety * 8

                    if t["is_double"]:
                        eval_score -= 4 # 별 패 보존

                    candidates.append((eval_score, live_outs_count, t))

                candidates.sort(key=lambda x: x[0], reverse=True)
                best_score, best_live_outs, chosen = candidates[0]

                # 신중한 리치 조건 (상대 리치 시에는 확실할 때만)
                if not self.riichi[2]:
                    if opp_riichi:
                        should_riichi = (best_live_outs >= 3)
                    elif self.ai_diff == "mid":
                        should_riichi = (best_live_outs >= 2)
                    elif self.ai_diff == "high":
                        should_riichi = (best_live_outs >= 2 and best_score >= 35)

            self.discard_tile(2, chosen["id"], declare_riichi=should_riichi)

multi_game = GameSession(mode="multi")
multi_connections = {}
single_sessions = {}

async def send_state_to_ws(ws: WebSocket, game: GameSession, p_num: int):
    opp_num = 2 if p_num == 1 else 1
    my_hand = game.players.get(p_num, [])
    opp_hand = game.players.get(opp_num, [])

    current_yaku = evaluate_hand(my_hand, is_incidental=False) if len(my_hand) == 6 else None
    can_riichi = (p_num == game.current_turn and game.turn_phase == "discard" and not game.riichi[p_num])

    can_ron = False
    if p_num == game.current_turn and game.turn_phase == "draw" and game.last_discard:
        if evaluate_hand(my_hand + [game.last_discard], is_incidental=False):
            can_ron = True

    can_tsumo = (p_num == game.current_turn and game.turn_phase == "discard" and current_yaku is not None)
    show_all = (game.turn_phase in ["round_end", "game_over"])

    payload = {
        "mode": game.mode,
        "ai_diff": game.ai_diff,
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
        "game_started": game.game_started
    }
    try:
        await ws.send_json(payload)
    except Exception:
        pass

async def broadcast_multi():
    for p_num, ws in list(multi_connections.items()):
        await send_state_to_ws(ws, multi_game, p_num)

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

            # 1인 모드 타이머 및 AI 동작
            for ws, s_game in list(single_sessions.items()):
                if s_game.game_started and s_game.turn_phase in ["draw", "discard"] and s_game.time_limit > 0:
                    s_game.time_left -= 1
                    if s_game.time_left <= 0:
                        s_game.handle_timeout()
                    await send_state_to_ws(ws, s_game, 1)

                if s_game.game_started and s_game.current_turn == 2 and s_game.turn_phase in ["draw", "discard"]:
                    await s_game.run_ai_turn()
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

            if act == "select_mode":
                chosen = data.get("mode")
                if chosen == "single":
                    current_mode = "single"
                    p_num = 1
                    diff = data.get("diff", "mid")
                    single_sessions[websocket] = GameSession(mode="single", ai_diff=diff)
                    await send_state_to_ws(websocket, single_sessions[websocket], 1)
                elif chosen == "multi":
                    current_mode = "multi"
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
                        del single_sessions[websocket]
                elif current_mode == "multi":
                    if p_num in multi_connections:
                        del multi_connections[p_num]
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
                        active_game.reset_round()
                    elif active_game.ready[1] and active_game.ready[2]:
                        active_game.reset_round()
                elif act == "reset_game":
                    active_game.scores = {1: 0, 2: 0}
                    active_game.game_started = False
                    active_game.reset_round()

                if current_mode == "single":
                    await send_state_to_ws(websocket, active_game, 1)
                else:
                    await broadcast_multi()

    except WebSocketDisconnect:
        if websocket in single_sessions:
            del single_sessions[websocket]
        if current_mode == "multi" and p_num in multi_connections:
            del multi_connections[p_num]
            multi_game.game_started = False
            multi_game.scores = {1: 0, 2: 0}
            multi_game.ready = {1: False, 2: False}
            multi_game.reset_round()
            await broadcast_multi()
