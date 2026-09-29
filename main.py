import random
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI()

class SixFlamesGame:
    def __init__(self):
        self.reset()

    def reset(self):
        # 21종류 x 2장 = 총 42장 도미노 덱 생성
        unique_tiles = []
        for top in range(1, 7):
            for bottom in range(top, 7):  # (1,1)부터 (6,6)까지 21종
                unique_tiles.append((top, bottom))

        self.deck = []
        tile_seq = 0
        for _ in range(2):
            for top, bottom in unique_tiles:
                tile_seq += 1
                self.deck.append({
                    "id": f"tile_{tile_seq}",
                    "top": top,
                    "bottom": bottom,
                    "is_double": (top == bottom)
                })

        random.shuffle(self.deck)

        self.players = {1: [], 2: []}
        self.discards = []
        self.current_turn = 1
        self.turn_phase = "draw"

        # 시작 시 6장씩 배분
        for _ in range(6):
            if self.deck: self.players[1].append(self.deck.pop())
            if self.deck: self.players[2].append(self.deck.pop())

    def draw_tile(self, player_num):
        if self.current_turn == player_num and self.turn_phase == "draw" and self.deck:
            tile = self.deck.pop()
            self.players[player_num].append(tile)
            self.turn_phase = "discard"
            return True
        return False

    def discard_tile(self, player_num, tile_id):
        if self.current_turn == player_num and self.turn_phase == "discard":
            hand = self.players[player_num]
            target = next((t for t in hand if t["id"] == tile_id), None)
            if target:
                hand.remove(target)
                self.discards.append(target)
                self.current_turn = 2 if self.current_turn == 1 else 1
                self.turn_phase = "draw"
                return True
        return False

connections = {}
game = SixFlamesGame()

async def broadcast_state():
    for p_num, ws in list(connections.items()):
        opp_num = 2 if p_num == 1 else 1
        payload = {
            "my_turn": game.current_turn == p_num,
            "phase": game.turn_phase,
            "deck_count": len(game.deck),
            "my_hand": game.players.get(p_num, []),
            "opp_hand_count": len(game.players.get(opp_num, [])),
            "discards": game.discards[-6:],
            "current_turn": game.current_turn
        }
        try:
            await ws.send_json(payload)
        except Exception:
            pass

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
        await websocket.send_json({"type": "wait", "msg": "이미 2명이 플레이 중입니다."})
        await websocket.close()
        return

    connections[p_num] = websocket

    if len(connections) < 2:
        await websocket.send_json({"type": "wait", "msg": "친구 접속을 기다리는 중입니다..."})
    else:
        game.reset()
        await broadcast_state()

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action == "draw":
                if game.draw_tile(p_num):
                    await broadcast_state()
            elif action == "discard":
                if game.discard_tile(p_num, data.get("tile_id")):
                    await broadcast_state()
    except WebSocketDisconnect:
        if p_num in connections:
            del connections[p_num]
        game.reset()