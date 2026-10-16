import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from app.main import create_app


def run_milestone():
    print("Testing Milestone 1: Multi-turn search, verification, citations, and constraint carry-over...")
    app = create_app()
    client = TestClient(app)

    # 1. First turn: "Show me black shoes under ₹2,000."
    print("\nTurn 1: 'Show me black shoes under ₹2,000.'")
    res1 = client.post("/api/chat", json={"message": "Show me black shoes under ₹2,000."})
    assert res1.status_code == 200, f"Turn 1 failed: {res1.text}"
    data1 = res1.json()
    print("Decision:", data1["decision"])
    print("Answer Text:", data1["answer_text"])
    print("Active Constraints:", [c["label"] for c in data1["active_constraints"]])
    print(f"Products returned: {len(data1['products'])}")
    for p in data1["products"]:
        print(f"  - [{p['product_id']}] {p['title']} ({p['facts'][0]['display']}, {p['facts'][1]['display']})")
    
    session_id = data1["session_id"]
    assert data1["decision"] == "ANSWER"
    assert len(data1["products"]) > 0
    assert any("Price under" in c["label"] for c in data1["active_constraints"])
    assert any("black" in c["label"].lower() for c in data1["active_constraints"])

    # Verify every displayed product has price under 2000 and is black
    for p in data1["products"]:
        price_val = p["facts"][0]["value"]
        assert price_val < 200000, f"Price {price_val} is not under 200000"

    # 2. Second turn: "Only show products that are in stock."
    print("\nTurn 2: 'Only show products that are in stock.' (Multi-turn follow-up)")
    res2 = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "Only show products that are in stock."
    })
    assert res2.status_code == 200, f"Turn 2 failed: {res2.text}"
    data2 = res2.json()
    print("Decision:", data2["decision"])
    print("Answer Text:", data2["answer_text"])
    print("Active Constraints:", [c["label"] for c in data2["active_constraints"]])
    print("Warnings:", data2["warnings"])
    print(f"Products returned: {len(data2['products'])}")
    for p in data2["products"]:
        print(f"  - [{p['product_id']}] {p['title']} ({p['facts'][0]['display']}, {p['facts'][1]['display']})")

    assert data2["decision"] == "ANSWER"
    assert "CONSTRAINTS_CARRIED_OVER" in data2["warnings"]
    assert any("In stock only" in c["label"] for c in data2["active_constraints"])
    assert any("Price under" in c["label"] for c in data2["active_constraints"])
    assert any("black" in c["label"].lower() for c in data2["active_constraints"])

    # Ensure all products are in stock, under 2000, and black
    for p in data2["products"]:
        stock_val = p["facts"][1]["value"]
        price_val = p["facts"][0]["value"]
        assert stock_val == "IN_STOCK", f"Product {p['product_id']} is not IN_STOCK: {stock_val}"
        assert price_val < 200000, f"Product {p['product_id']} price {price_val} is not under 2000"

    # 3. Third turn: Ask policy question
    print("\nTurn 3: 'What is the return window?'")
    res3 = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "What is the return window?"
    })
    assert res3.status_code == 200, f"Turn 3 failed: {res3.text}"
    data3 = res3.json()
    print("Decision:", data3["decision"])
    print("Answer Text:", data3["answer_text"])
    assert data3["decision"] == "ANSWER"
    assert "30 days" in data3["answer_text"]
    assert "policy:1.1" in data3["answer_text"]

    # 4. Fourth turn: Product fact lookup: "What is the price of SH-BLK-001?"
    print("\nTurn 4: 'What is the price of SH-BLK-001?'")
    res4 = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "What is the price of SH-BLK-001?"
    })
    assert res4.status_code == 200, f"Turn 4 failed: {res4.text}"
    data4 = res4.json()
    print("Decision:", data4["decision"])
    print("Answer Text:", data4["answer_text"])
    assert data4["decision"] == "ANSWER"
    assert "₹1,499.00" in data4["answer_text"]
    assert "SH-BLK-001" in data4["answer_text"]

    # 5. Fifth turn: Product missing field: "What is the price of SH-NOP-001?"
    print("\nTurn 5: 'What is the price of SH-NOP-001?' (Missing price)")
    res5 = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "What is the price of SH-NOP-001?"
    })
    assert res5.status_code == 200, f"Turn 5 failed: {res5.text}"
    data5 = res5.json()
    print("Decision:", data5["decision"])
    print("Answer Text:", data5["answer_text"])
    assert data5["decision"] == "REFUSE"
    assert "not available" in data5["answer_text"]

    # 6. Sixth turn: Unknown product: "What is the price of P9999?"
    print("\nTurn 6: 'What is the price of P9999?' (Unknown product)")
    res6 = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "What is the price of P9999?"
    })
    assert res6.status_code == 200, f"Turn 6 failed: {res6.text}"
    data6 = res6.json()
    print("Decision:", data6["decision"])
    print("Answer Text:", data6["answer_text"])
    assert data6["decision"] == "REFUSE"
    assert "No product matching" in data6["answer_text"]

    print("\nALL MILESTONE 1 TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_milestone()
