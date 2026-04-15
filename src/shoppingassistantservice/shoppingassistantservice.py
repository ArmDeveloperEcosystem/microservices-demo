#!/usr/bin/env python3
import json
import os
import re
import urllib.error
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
OLLAMA_TAGS_URL = os.getenv("OLLAMA_TAGS_URL", "http://127.0.0.1:11434/api/tags")
MODEL_NAME = os.getenv("MODEL_NAME", "gemma3:1b-it-qat")
USER_ID = os.getenv("SHOPPING_ASSISTANT_USER_ID", "workshop-user")

catalog_stub = demo_pb2_grpc.ProductCatalogServiceStub(
    grpc.insecure_channel(PRODUCT_CATALOG_SERVICE_ADDR)
)
cart_stub = demo_pb2_grpc.CartServiceStub(grpc.insecure_channel(CART_SERVICE_ADDR))

pending_actions = {}
recent_results = {}
recent_actions = {}


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


def ids_suffix(product_ids):
    if not product_ids:
        return ""
    return " Relevant products: " + " ".join(f"[{product_id}]" for product_id in product_ids)


def respond(content, product_ids=None, requires_confirmation=False):
    product_ids = product_ids or []
    message = content + ids_suffix(product_ids)
    return jsonify(
        {
            "content": message,
            "details": {
                "product_ids": product_ids,
                "requires_confirmation": requires_confirmation,
            },
        }
    )


def record_action(session_id, action, detail):
    recent_actions[session_id] = {"action": action, "detail": detail}


def model_ready():
    request_obj = urllib.request.Request(OLLAMA_TAGS_URL, method="GET")
    with urllib.request.urlopen(request_obj, timeout=5) as response:
        body = json.loads(response.read().decode("utf-8"))
    names = {item.get("name", "") for item in body.get("models", [])}
    return MODEL_NAME in names


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


def get_product_details(product_id):
    try:
        product = catalog_stub.GetProduct(demo_pb2.GetProductRequest(id=product_id))
        return product_to_dict(product)
    except grpc.RpcError:
        return None


def get_cart():
    cart = cart_stub.GetCart(demo_pb2.GetCartRequest(user_id=USER_ID))
    summary = []
    for item in cart.items:
        product = get_product_details(item.product_id)
        summary.append(
            {
                "product_id": item.product_id,
                "name": product["name"] if product else item.product_id,
                "quantity": item.quantity,
                "price": product["price"] if product else None,
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


def normalize_message(payload):
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("message", "")).strip()


def get_tool_summary(matches):
    return json.dumps(
        {
            "matching_products": matches,
            "cart": get_cart(),
        },
        indent=2,
    )


def handle_confirmation(session_id, lowered):
    if lowered not in {"yes", "yes please", "confirm"}:
        return None
    if session_id not in pending_actions:
        return respond("I do not have a pending cart action to confirm right now.")

    action = pending_actions.pop(session_id)
    result = add_to_cart(action["product_id"], action["quantity"])
    record_action(session_id, "add_to_cart", action["product_id"])
    return respond(
        f"Added {result['quantity']} x {action['name']} to your cart.",
        [result["product_id"]],
        False,
    )


def handle_cart_query(session_id, lowered):
    if "add" in lowered and "cart" in lowered:
        return None

    if not any(
        phrase in lowered
        for phrase in {"what is in my cart", "show my cart", "cart now", "view my cart"}
    ):
        return None

    cart_items = get_cart()
    record_action(session_id, "get_cart", f"{len(cart_items)} items")
    if not cart_items:
        return respond("Your cart is empty right now.")

    cart_message = "; ".join(f"{item['quantity']} x {item['name']}" for item in cart_items)
    return respond(
        f"Your cart currently contains: {cart_message}.",
        [item["product_id"] for item in cart_items],
        False,
    )


def choose_catalog_candidate(user_input, session_id):
    candidates = recent_results.get(session_id)
    if candidates:
        return candidates[0], candidates

    matches = search_catalog(user_input)
    recent_results[session_id] = matches
    return (matches[0], matches) if matches else (None, [])


def handle_add_to_cart_intent(session_id, user_input, lowered):
    if "add" not in lowered or "cart" not in lowered:
        return None

    selected, candidates = choose_catalog_candidate(user_input, session_id)
    if not selected:
        return respond(
            "I could not identify which product to add. Ask me to find an item first."
        )

    details = get_product_details(selected["id"]) or selected
    pending_actions[session_id] = {
        "product_id": details["id"],
        "name": details["name"],
        "quantity": 1,
    }
    record_action(session_id, "prepare_add_to_cart", details["id"])
    return respond(
        (
            f"I found {details['name']} for {details['price']}. "
            "Reply yes to confirm adding it to your cart."
        ),
        [details["id"]],
        True,
    )


def handle_search_or_recommendation(session_id, user_input):
    matches = search_catalog(user_input)
    recent_results[session_id] = matches
    record_action(session_id, "search_catalog", user_input)

    if not matches:
        return respond("I could not find a matching product in the live catalog.")

    try:
        answer = call_model(user_input, get_tool_summary(matches))
        record_action(session_id, "call_model", MODEL_NAME)
    except Exception:
        top = matches[0]
        answer = (
            f"I found {top['name']} for {top['price']}. "
            "Ask me to add it to your cart if you want it."
        )

    return respond(answer, [item["id"] for item in matches], False)


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "shoppingassistantservice"})


@app.get("/readyz")
def readyz():
    try:
        if model_ready():
            return jsonify({"ok": True, "model": MODEL_NAME})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": False, "model": MODEL_NAME, "status": "loading"}), 503


@app.post("/")
@app.post("/bot")
def bot():
    payload = request.get_json(force=True)
    user_input = normalize_message(payload)
    session_id = current_session_id()

    if not user_input:
        return respond("Please enter a shopping question.")

    lowered = user_input.lower()
    confirmation_response = handle_confirmation(session_id, lowered)
    if confirmation_response is not None:
        return confirmation_response

    cart_response = handle_cart_query(session_id, lowered)
    if cart_response is not None:
        return cart_response

    add_response = handle_add_to_cart_intent(session_id, user_input, lowered)
    if add_response is not None:
        return add_response

    return handle_search_or_recommendation(session_id, user_input)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
