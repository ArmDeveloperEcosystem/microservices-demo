#!/usr/bin/env python3
import json
import os
import re
import urllib.request

import grpc
from flask import Flask, jsonify, request

import demo_pb2
import demo_pb2_grpc

app = Flask(__name__)

PRODUCT_CATALOG_SERVICE_ADDR = os.getenv(
    "PRODUCT_CATALOG_SERVICE_ADDR", "productcatalogservice:3550"
)
CART_SERVICE_ADDR = os.getenv("CART_SERVICE_ADDR", "cartservice:7070")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
MODEL_NAME = os.getenv("MODEL_NAME", "gemma2:9b")
USER_ID = os.getenv("SHOPPING_ASSISTANT_USER_ID", "workshop-user")

catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(
    grpc.insecure_channel(PRODUCT_CATALOG_SERVICE_ADDR)
)
cart_stub = demo_pb2_grpc.CartServiceStub(grpc.insecure_channel(CART_SERVICE_ADDR))

pending_actions = {}
recent_results = {}


def money_to_string(money):
    units = int(money.units)
    cents = abs(int(money.nanos)) // 10_000_000
    return f"${units}.{cents:02d}"


def product_to_dict(product):
    return {
        "id": product.id,
        "name": product.name,
        "description": " ".join(product.description.split())[:160],
        "price": money_to_string(product.price_usd),
        "categories": list(product.categories),
    }


def search_catalog(query):
    try:
        response = catalog_stub.SearchProducts(
            demo_pb2.SearchProductsRequest(query=query)
        )
        products = list(response.results)
    except grpc.RpcError:
        products = []

    if not products:
        all_products = catalog_stub.ListProducts(demo_pb2.Empty()).products
        terms = re.findall(r"[a-z0-9]+", query.lower())
        scored = []
        for product in all_products:
            haystack = " ".join(
                [product.name, product.description, " ".join(product.categories)]
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score > 0:
                scored.append((score, product))
        scored.sort(key=lambda item: item[0], reverse=True)
        products = [product for _, product in scored[:3]]

    return [product_to_dict(product) for product in products[:3]]


def get_cart_contents():
    cart = cart_stub.GetCart(demo_pb2.GetCartRequest(user_id=USER_ID))
    summary = []
    for item in cart.items:
        product_name = item.product_id
        try:
            product = catalog_stub.GetProduct(
                demo_pb2.GetProductRequest(id=item.product_id)
            )
            product_name = product.name
        except grpc.RpcError:
            pass
        summary.append(
            {
                "product_id": item.product_id,
                "name": product_name,
                "quantity": item.quantity,
            }
        )
    return summary


def add_to_cart(product_id, quantity=1):
    cart_stub.AddItem(
        demo_pb2.AddItemRequest(
            user_id=USER_ID,
            item=demo_pb2.CartItem(product_id=product_id, quantity=quantity),
        )
    )
    return {"product_id": product_id, "quantity": quantity}


def call_model(user_prompt, tool_summary):
    prompt = f"""
You are a helpful shopping assistant for the Cymbal Shops storefront.
Use the tool results below to answer clearly and briefly.
Do not invent product IDs or prices.

User request:
{user_prompt}

Tool results:
{tool_summary}
""".strip()

    payload = json.dumps(
        {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
        }
    ).encode("utf-8")
    request_obj = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request_obj, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body.get("response", "").strip()


def current_session_id():
    return (
        request.cookies.get("shop_session-id")
        or request.headers.get("X-Session-Id")
        or "workshop-session"
    )


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "shoppingassistantservice"})


@app.post("/bot")
def bot():
    payload = request.get_json(force=True)
    user_input = payload.get("message", "").strip()
    session_id = current_session_id()

    if not user_input:
        return jsonify(
            {
                "message": "Please enter a shopping question.",
                "product_ids": [],
                "requires_confirmation": False,
            }
        )

    lowered = user_input.lower()

    if lowered in {"yes", "yes please", "confirm"} and session_id in pending_actions:
        action = pending_actions.pop(session_id)
        result = add_to_cart(action["product_id"], action["quantity"])
        return jsonify(
            {
                "message": f"Added {result['quantity']} x {action['name']} to your cart.",
                "product_ids": [result["product_id"]],
                "requires_confirmation": False,
            }
        )

    if any(
        phrase in lowered for phrase in ["what is in my cart", "show my cart", "cart now"]
    ):
        cart_items = get_cart_contents()
        if not cart_items:
            return jsonify(
                {
                    "message": "Your cart is empty right now.",
                    "product_ids": [],
                    "requires_confirmation": False,
                }
            )
        cart_message = "; ".join(
            f"{item['quantity']} x {item['name']}" for item in cart_items
        )
        return jsonify(
            {
                "message": f"Your cart currently contains: {cart_message}.",
                "product_ids": [item["product_id"] for item in cart_items],
                "requires_confirmation": False,
            }
        )

    if "add" in lowered and "cart" in lowered:
        candidates = recent_results.get(session_id) or search_catalog(user_input)
        if not candidates:
            return jsonify(
                {
                    "message": "I could not identify which product to add. Ask me to find an item first.",
                    "product_ids": [],
                    "requires_confirmation": False,
                }
            )
        selected = candidates[0]
        pending_actions[session_id] = {
            "product_id": selected["id"],
            "name": selected["name"],
            "quantity": 1,
        }
        return jsonify(
            {
                "message": (
                    f"I found {selected['name']} for {selected['price']}. "
                    "Reply yes to confirm adding it to your cart."
                ),
                "product_ids": [selected["id"]],
                "requires_confirmation": True,
            }
        )

    matches = search_catalog(user_input)
    recent_results[session_id] = matches

    if not matches:
        return jsonify(
            {
                "message": "I could not find a matching product in the live catalog.",
                "product_ids": [],
                "requires_confirmation": False,
            }
        )

    tool_summary = json.dumps(
        {
            "matching_products": matches,
            "cart": get_cart_contents(),
        },
        indent=2,
    )

    try:
        answer = call_model(user_input, tool_summary)
    except Exception:
        top = matches[0]
        answer = (
            f"I found {top['name']} for {top['price']}. "
            "Ask me to add it to your cart if you want it."
        )

    return jsonify(
        {
            "message": answer,
            "product_ids": [item["id"] for item in matches],
            "requires_confirmation": False,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
