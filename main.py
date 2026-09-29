import random
import asyncio
from contextlib import asynccontextmanager
from itertools import combinations, permutations
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

class SixFlamesGame:
    def __init__(self):
        self.mode = "none" # "single" 또는 "multi"
        self.ai_diff = "mid" # "low", "mid", "high"
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

    def reset_to_lobby(self):
        self.mode = "none"
        self.scores = {1: 0, 2: 0}
        self.game_started = False
        self.starter = 1
        self.reset_round()
        self.turn_phase = "lobby"

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

    # --- AI 의사결정 알고리즘 ---
    async def run_ai_turn(self):
        await asyncio.sleep(0.9)
        if not self.game_started or self.current_turn != 2:
            return

        # 1. Draw 단계 (론 체크 및 패 뽑기)
        if self.turn_phase == "draw":
            # 론 검사
            if self.last_discard:
                for fl in [False, True]:
                    t_cand = dict(self.last_discard)
                    if fl: t_cand["top"], t_cand["bottom"] = t_cand["bottom"], t_cand["top"]
                    if evaluate_hand(self.players[2] + [t_cand], is_incidental=False):
                        self.declare_ron(2, mode="steal")
                        await broadcast_state()
                        return

            # 바닥 패 주워오기 판단
            picked_from_floor = False
            if self.ai_diff in ["mid", "high"] and self.discards:
                for disc in reversed(self.discards[-3:]):
                    for fl in [False, True]:
                        cand = dict(disc)
                        if fl: cand["top"], cand["bottom"] = cand["bottom"], cand["top"]
                        if evaluate_hand(self.players[2] + [cand], is_incidental=False):
                            self.draw_tile(2, discard_id=disc["id"])
                            picked_from_floor = True
                            break
                    if picked_from_floor: break

            if not picked_from_floor:
                self.draw_tile(2, discard_id=None)
            await broadcast_state()

        await asyncio.sleep(0.9)
        if not self.game_started or self.current_turn != 2:
            return

        # 2. Discard 단계 (쯔모 확인, 리치 선언, 버릴 패 선정)
        if self.turn_phase == "discard":
            # 쯔모 완성 체크
            if evaluate_hand(self.players[2], is_incidental=False):
                self.declare_tsumo(2)
                await broadcast_state()
                return

            hand = self.players[2]
            chosen_tile = None

            # 리치 상태인 경우 이번에 뽑은 패만 버려야 함
            if self.riichi[2]:
                chosen_tile = next((t for t in hand if t["id"] == self.last_drawn_id[2]), hand[-1])
                self.discard_tile(2, chosen_tile["id"], declare_riichi=False)
                await broadcast_state()
                return

            # 난이도별 패 버리기 전략
            if self.ai_diff == "low":
                chosen_tile = random.choice(hand)
                declare_r = False
            elif self.ai_diff == "mid":
                # 더블(별) 패 보존, 일반 패 우선 버림
                non_stars = [t for t in hand if not t["is_double"]]
                chosen_tile = random.choice(non_stars) if non_stars else hand[0]
                declare_r = (random.random() < 0.3)
            else: # high
                # 완성에 가장 방해되는 패 계산 & 적극적 리치
                cand_scores = []
                for t in hand:
                    remain = [x for x in hand if x["id"] != t["id"]]
                    # 남은 5장 기준 유효 타일 개수 모의 판정
                    valid_outs = 0
                    for num1 in range(1, 7):
                        for num2 in range(num1, 7):
                            f_tile = {"id": "fk", "top": num1, "bottom": num2, "is_double": (num1 == num2)}
                            if evaluate_hand(remain + [f_tile], is_incidental=False):
                                valid_outs += 1
                    cand_scores.append((valid_outs, t))
                
                # 남은 5장 기준 완성 대기패가 가장 많은 방향으로 버림
                cand_scores.sort(key=lambda x: x[0], reverse=True)
                best_valid_outs, chosen_tile = cand_scores[0]
                declare_r = (best_valid_outs >= 2 and not self.riichi[2])

            self.discard_tile(2, chosen_tile["id"], declare_riichi=declare_r)
            await broadcast_state()

connections = {}
game = SixFlamesGame()

async def broadcast_state():
    for p_num, ws in list(connections.items()):
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

async def timer_background_task():
    while True:
        try:
            await asyncio.sleep(1)
            if game.game_started and game.turn_phase in ["draw", "discard"] and game.time_limit > 0:
                game.time_left -= 1
                if game.time_left <= 0:
                    game.handle_timeout()
                await broadcast_state()

            # 1인 모드 시 AI 차례 실행
            if game.game_started and game.mode == "single" and game.current_turn == 2 and game.turn_phase in ["draw", "discard"]:
                await game.run_ai_turn()
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
    if 1 not in connections:
        p_num = 1
    elif 2 not in connections and game.mode != "single":
        p_num = 2
    else:
        # 단일 모드 진행 중이거나 풀방인 경우
        p_num = 1 if 1 not in connections else 2

    connections[p_num] = websocket
    await broadcast_state()

    try:
        while True:
            data = await websocket.receive_json()
            act = data.get("action")
            
            if act == "select_mode":
                game.mode = data.get("mode") # "single" 또는 "multi"
                game.ai_diff = data.get("diff", "mid")
                game.game_started = False
                game.reset_round()
                await broadcast_state()
            elif act == "set_settings":
                if not game.game_started:
                    game.target_score = int(data.get("score", 10))
                    game.time_limit = int(data.get("time", 60))
                    game.time_left = game.time_limit
                    await broadcast_state()
            elif act == "flip":
                if game.flip_tile(p_num, data.get("tile_id")):
                    await broadcast_state()
            elif act == "reorder":
                if game.reorder_tiles(p_num, data.get("order", [])):
                    await broadcast_state()
            elif act == "draw":
                if game.draw_tile(p_num, discard_id=data.get("discard_id")):
                    await broadcast_state()
            elif act == "discard":
                if game.discard_tile(p_num, data.get("tile_id"), data.get("riichi", False)):
                    await broadcast_state()
                    if game.mode == "single" and game.current_turn == 2:
                        asyncio.create_task(game.run_ai_turn())
            elif act == "tsumo":
                if game.declare_tsumo(p_num):
                    await broadcast_state()
            elif act == "ron":
                if game.declare_ron(p_num, mode=data.get("mode", "steal")):
                    await broadcast_state()
            elif act == "ready":
                game.ready[p_num] = True
                if game.mode == "single":
                    game.ready[2] = True
                    game.game_started = True
                    game.reset_round()
                elif game.ready[1] and game.ready[2]:
                    game.game_started = True
                    game.reset_round()
                await broadcast_state()
            elif act == "ready_next":
                game.ready[p_num] = True
                if game.mode == "single":
                    game.ready[2] = True
                    game.reset_round()
                elif game.ready[1] and game.ready[2]:
                    game.reset_round()
                await broadcast_state()
            elif act == "reset_game":
                game.reset_to_lobby()
                await broadcast_state()
            elif act == "go_home":
                game.reset_to_lobby()
                await broadcast_state()
    except WebSocketDisconnect:
        if p_num in connections:
            del connections[p_num]
        game.reset_to_lobby()
