"""Modo tienda (fork Maxima Suplementos): pedidos en vez de citas."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.profile import BusinessProfile
from app.prompt import build_system_prompt
from app.state import Conversation
from app.tools import ToolRuntime, tool_schemas


def _nombres(schemas):
    return {t["function"]["name"] for t in schemas}


def test_herramientas_de_pedido_solo_en_modo_tienda():
    assert "registrar_pedido" not in _nombres(tool_schemas(False))
    tienda = _nombres(tool_schemas(False, store=True))
    assert {"registrar_pedido", "consultar_pedidos"} <= tienda
    assert "book_session" not in tienda


def test_prompt_de_tienda_conserva_los_nunca():
    conv = Conversation(id=1, wa_identity="59891429069")
    p = build_system_prompt(profile=BusinessProfile(), context=None, conv=conv, agenda=False, store=True)
    assert "MODO TIENDA" in p
    assert "TOMAR SU PEDIDO" in p
    assert "NUNCA:" in p and "Inventes datos, precios" in p
    assert "NUNCA pidas números de tarjeta" in p
    assert "ESTE NEGOCIO NO AGENDA" not in p
    normal = build_system_prompt(profile=BusinessProfile(), context=None, conv=conv, agenda=False)
    assert "MODO TIENDA" not in normal


class _Settings:
    orders_url = "http://pedidos:4000"
    orders_api_key = "k"
    store_mode = True


class _Crm:
    def __init__(self):
        self.fichas = []

    async def put_ficha(self, conv_id, ficha):
        self.fichas.append(ficha)


class _Ctx:
    def __init__(self):
        self.settings = _Settings()
        self.crm = _Crm()


@pytest.mark.asyncio
@respx.mock
async def test_registrar_pedido_manda_telefono_y_conversacion():
    ruta = respx.post("http://pedidos:4000/api/pedidos").mock(
        return_value=httpx.Response(201, json={"numero": 7, "total": 3290, "estado": "nuevo"})
    )
    ctx = _Ctx()
    rt = ToolRuntime(ctx, Conversation(id=1, wa_identity="59891429069"), "cv_1")
    out = await rt.execute(
        "registrar_pedido",
        {
            "nombre_cliente": "Ana",
            "items": [{"producto": "Whey", "cantidad": 1, "precio_unitario": 3290}],
            "entrega": "retiro",
            "metodo_pago": "efectivo",
        },
    )
    assert out == {"ok": True, "numero": 7, "total": 3290, "estado": "nuevo"}
    enviado = ruta.calls.last.request
    assert enviado.headers["X-API-Key"] == "k"
    assert b'"telefono":"59891429069"' in enviado.content.replace(b" ", b"")
    assert b'"conversation_id":"cv_1"' in enviado.content.replace(b" ", b"")
    assert ctx.crm.fichas[0]["ultimo_pedido"].startswith("#7")


@pytest.mark.asyncio
@respx.mock
async def test_panel_caido_no_finge_registro():
    respx.post("http://pedidos:4000/api/pedidos").mock(side_effect=httpx.ConnectError("x"))
    rt = ToolRuntime(_Ctx(), Conversation(id=1, wa_identity="598"), "cv_1")
    out = await rt.execute(
        "registrar_pedido",
        {"nombre_cliente": "A", "items": [{"producto": "X", "cantidad": 1, "precio_unitario": 1}], "entrega": "retiro", "metodo_pago": "e"},
    )
    assert out["ok"] is False and out["error"] == "panel_caido"


@pytest.mark.asyncio
@respx.mock
async def test_buscar_productos_consulta_catalogo_en_vivo():
    ruta = respx.get("http://pedidos:4000/api/catalogo/buscar").mock(
        return_value=httpx.Response(200, json={"productos": [{"nombre": "Bolic Mass 3kg", "precio": 1190}]})
    )
    rt = ToolRuntime(_Ctx(), Conversation(id=1, wa_identity="598"), "cv_1")
    out = await rt.execute("buscar_productos", {"consulta": "hipercalorico"})
    assert out["ok"] and out["productos"][0]["precio"] == 1190
    assert ruta.calls.last.request.url.params["q"] == "hipercalorico"
    assert "buscar_productos" in _nombres(tool_schemas(False, store=True))
