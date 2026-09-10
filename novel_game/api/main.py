"""FastAPI入口"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from api.route_novel import router as novel_router
from api.route_game import router as game_router
from api.route_graph import router as graph_router

app = FastAPI(title="小说互动游戏引擎")

app.include_router(novel_router)
app.include_router(game_router)
app.include_router(graph_router)

# 挂载静态文件（注意：graphic 必须在 "/" 之前挂载，否则被 static 通配拦截）
app.mount("/graphic", StaticFiles(directory="graphic", html=True), name="graphic")
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
