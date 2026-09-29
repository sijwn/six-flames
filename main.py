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

    if star_count == 6 and sorted(tops) == [1, 2, 3, 4, 5, 6]:
        best_name = "무쌍"
        best_base = 3
        best_stars = 6
        return {
            "name": best_name,
            "base_score": best_base,
            "stars": best_stars,
            "total_score": best_base + best_stars
        }

    if star_count == 6:
        if 5 > best_base:
            best_name = "휘광"
            best_base = 5
            best_stars = 0

    if len(set(bottoms)) == 1 and sorted(tops) == [1, 2, 3, 4, 5, 6]:
        if 6 > best_base:
            best_name = "육화"
            best_base = 6
            best_stars = star_count

    for p in permutations(tiles):
        if is_same_tile(p[0], p[1]) and is_same_tile(p[2], p[3]) and is_same_tile(p[4], p[5]):
            if 5 > best_base:
                best_name = "삼동"
                best_base = 5
                best_stars = star_count
            break

    if is_incidental and len(all_nums) <= 3:
        if 3 > best_base:
            best_name = "삼색"
            best_base = 3
            best_stars = 0

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

    if len(set(bottoms)) == 1:
        if 1 > best_base:
            best_name = "일색"
            best_base = 1
            best_stars = star_count

    if best_name:
        return {
            "name": best_name,
            "base_score": best_base,
            "stars": best_stars,
            "total_score": best_base + best_stars
        }
    return None

def check_can_riichi(hand_5):
    for t in range(1, 7):
        for b in range(t, 7):
            fake1 = {"id": "fake", "top": t, "bottom": b, "is_double": (t == b)}
            fake2 = {"id": "fake", "top": b, "bottom": t, "is_double": (t == b)}
            if evaluate_hand(hand_5 + [fake1], is_incidental=False) or evaluate_hand(hand_5 + [fake2], is_incidental=False):
                return True
    return False

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
        self.target_score = 10
        self.time_limit = 60
        self.time_left = 60
        self.scores = {1: 0, 2: 0}
        self.starter = random.choice([1, 2])
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
        self.scores = {1: 0, 2: 0}
        self.game_started = False
        self.starter = random.choice([1, 2])
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
            temp_hand = [t for t in hand if t["id"] != tile_id]
            if check_can_riichi(temp_hand):
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

            detail = f"{res['name']}({base}점)"
            if stars > 0:
                detail += f" + 별보너스({stars}점)"
            if riichi_pt > 0:
                detail += " + 리치(1점)"

            self.round_settlement = [{
                "player": p_num,
                "type": "쯔모",
                "text": f"[{p_num}P 쯔모 완성] {detail} = 총 {total}점 획득"
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

        detail = f"{res['name']}({base}점)"
        if stars > 0:
            detail += f" + 별보너스({stars}점)"
        if riichi_pt > 0:
            detail += " + 리치(1점)"

        if mode == "steal":
            stolen = min(self.scores[opp], total)
            self.scores[opp] -= stolen
            self.scores[p_num] += stolen
            desc = f"[{p_num}P 론(강탈)] {detail} = {opp}P에게서 {stolen}점 강탈 (기본 점수: {total}점)"
        else:
            self.scores[p_num] += total
            desc = f"[{p_num}P 완성] {detail} = 공급처로부터 총 {total}점 획득"

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

                    detail = f"{res['name']}({base}점)"
                    if stars > 0:
                        detail += f" + 별보너스({stars}점)"
                    if riichi_pt > 0:
                        detail += " + 리치(1점)"

                    self.round_settlement.append({
                        "player": p,
                        "type": "겸사겸사",
                        "text": f"[{p}P 겸사겸사 완성] {detail} = 총 {total}점 획득"
                    })

    def end_round(self):
        if self.scores[1] >= self.target_score or self.scores[2] >= self.target_score:
            self.turn_phase = "game_over"
        else:
            self.turn_phase = "round_end"
            self.starter = 2 if self.starter == 1 else 1

connections = {}
game = SixFlamesGame()

async def broadcast_state():
    for p_num, ws in list(connections.items()):
        opp_num = 2 if p_num == 1 else 1
        my_hand = game.players.get(p_num, [])
        opp_hand = game.players.get(opp_num, [])

        current_yaku = evaluate_hand(my_hand, is_incidental=False) if len(my_hand) == 6 else None
        
        can_riichi = False
        if p_num == game.current_turn and game.turn_phase == "discard" and not game.riichi[p_num]:
            for t in my_hand:
                remain = [x for x in my_hand if x["id"] != t["id"]]
                if check_can_riichi(remain):
                    can_riichi = True
                    break

        can_ron = False
        if p_num == game.current_turn and game.turn_phase == "draw" and game.last_discard:
            if evaluate_hand(my_hand + [game.last_discard], is_incidental=False):
                can_ron = True

        can_tsumo = (p_num == game.current_turn and game.turn_phase == "discard" and current_yaku is not None)
        show_all = (game.turn_phase in ["round_end", "game_over"])

        payload = {
            "player_num": p_num,
            "target_score": game.target_score,
            "time_limit": game.time_limit,
            "time_left": game.time_left,
            "my_turn": game.current_turn == p_num,
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
    elif 2 not in connections:
        p_num = 2
    else:
        await websocket.send_json({"type": "full", "msg": "이미 방이 가득 찼습니다."})
        await websocket.close()
        return

    connections[p_num] = websocket

    if len(connections) < 2:
        await websocket.send_json({"type": "wait", "player_num": p_num})
    else:
        await broadcast_state()

    try:
        while True:
            data = await websocket.receive_json()
            act = data.get("action")
            
            if act == "set_settings":
                if p_num == 1 and not game.game_started:
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
            elif act == "tsumo":
                if game.declare_tsumo(p_num):
                    await broadcast_state()
            elif act == "ron":
                if game.declare_ron(p_num, mode=data.get("mode", "steal")):
                    await broadcast_state()
            elif act == "ready":
                game.ready[p_num] = True
                if game.ready[1] and game.ready[2]:
                    game.game_started = True
                    game.reset_round()
                await broadcast_state()
            elif act == "ready_next":
                game.ready[p_num] = True
                if game.ready[1] and game.ready[2]:
                    game.reset_round()
                await broadcast_state()
            elif act == "reset_game":
                game.reset_to_lobby()
                await broadcast_state()
    except WebSocketDisconnect:
        if p_num in connections:
            del connections[p_num]
        game.reset_to_lobby()
