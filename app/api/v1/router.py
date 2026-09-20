from fastapi import APIRouter

from app.api.v1.endpoints import auth, clients, market, orders, portfolio

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(clients.router)
api_router.include_router(orders.router)
api_router.include_router(portfolio.router)
api_router.include_router(market.router)
