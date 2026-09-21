from typing import List, Optional
from pydantic import BaseModel


class PlaceOrderRequest(BaseModel):
    exchange: str
    symboltoken: int
    symbol: str
    buyorsell: str
    ordertype: str
    producttype: str
    orderduration: str
    price: float = 0.0
    triggerprice: float = 0.0
    quantityinlot: int
    disclosedquantity: int = 0
    amoorder: str = "N"
    algoid: str = ""
    tag: str = ""
    client_ids: Optional[List[int]] = None


class ModifyOrderRequest(BaseModel):
    uniqueorderid: str
    ordertype: str
    price: float = 0.0
    triggerprice: float = 0.0
    quantityinlot: int
    disclosedquantity: int = 0
    client_id: int


class CancelOrderRequest(BaseModel):
    uniqueorderid: str
    exchange: str
    client_id: int


class CancelOrderItem(BaseModel):
    order_id: str
    client_id: int
    name: Optional[str] = None
    symbol: Optional[str] = None
    broker: Optional[str] = None
    exchange: Optional[str] = None


class BatchCancelRequest(BaseModel):
    orders: List[CancelOrderItem]
