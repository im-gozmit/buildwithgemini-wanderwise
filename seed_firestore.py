"""Seed script to populate initial curated travel destinations and spots in Firestore."""

from google.cloud import firestore

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-04-7b122a8e9728"

SAMPLE_DESTINATIONS = [
    {
        "id": "kyoto_fushimi_inari",
        "name": "Fushimi Inari Shrine & Bamboo Trails",
        "city": "Kyoto",
        "country": "Japan",
        "category": "sightseeing",
        "budget_tier": "budget",
        "description": "Famous path lined with thousands of torii gates winding up sacred Mount Inari.",
        "duration_hours": 3.0,
        "tags": ["culture", "walking", "photography", "temples"],
        "cost_usd": 0.0,
        "rating": 4.9,
    },
    {
        "id": "kyoto_gion_tea_ceremony",
        "name": "Traditional Gion Tea House Experience",
        "city": "Kyoto",
        "country": "Japan",
        "category": "food_and_drink",
        "budget_tier": "moderate",
        "description": "Authentic matcha tea ceremony and seasonal wagashi sweets in the historic Gion district.",
        "duration_hours": 1.5,
        "tags": ["culture", "tea", "traditional", "historic"],
        "cost_usd": 45.0,
        "rating": 4.8,
    },
    {
        "id": "paris_louvre_highlights",
        "name": "Louvre Masterpieces Express Tour",
        "city": "Paris",
        "country": "France",
        "category": "museums",
        "budget_tier": "moderate",
        "description": "Curated walk hitting the Mona Lisa, Venus de Milo, and Winged Victory with timed entry.",
        "duration_hours": 2.5,
        "tags": ["art", "history", "landmarks", "indoor"],
        "cost_usd": 30.0,
        "rating": 4.7,
    },
    {
        "id": "paris_montmartre_food_walk",
        "name": "Montmartre Artisan Bakery & Cheese Walk",
        "city": "Paris",
        "country": "France",
        "category": "food_and_drink",
        "budget_tier": "moderate",
        "description": "Taste fresh baguettes, artisanal cheeses, and macarons while strolling cobblestone hills.",
        "duration_hours": 3.0,
        "tags": ["food", "walking", "pastry", "wine"],
        "cost_usd": 65.0,
        "rating": 4.9,
    },
    {
        "id": "sf_golden_gate_bridge_walk",
        "name": "Golden Gate Vista Point & Bridge Walk",
        "city": "San Francisco",
        "country": "United States",
        "category": "sightseeing",
        "budget_tier": "budget",
        "description": "Walk the iconic Golden Gate Bridge spanning the bay with views of Alcatraz and city skyline.",
        "duration_hours": 2.0,
        "tags": ["outdoors", "scenic", "iconic", "walking"],
        "cost_usd": 0.0,
        "rating": 4.8,
    },
    {
        "id": "sf_ferry_building_market",
        "name": "Ferry Building Gourmet Farmers Market",
        "city": "San Francisco",
        "country": "United States",
        "category": "food_and_drink",
        "budget_tier": "moderate",
        "description": "Historic transit hub offering oysters, artisanal sourdough, local coffee, and bay breezes.",
        "duration_hours": 2.0,
        "tags": ["food", "market", "coffee", "waterfront"],
        "cost_usd": 35.0,
        "rating": 4.7,
    },
]


def seed_firestore():
    print(f"Connecting to Firestore using project ID: {FIRESTORE_PROJECT_ID}")
    db = firestore.Client(project=FIRESTORE_PROJECT_ID)
    collection_ref = db.collection("travel_spots")

    for spot in SAMPLE_DESTINATIONS:
        doc_id = spot["id"]
        collection_ref.document(doc_id).set(spot)
        print(f"✓ Seeded spot: {doc_id} -> {spot['name']} ({spot['city']})")

    print("\nFirestore database seeding completed successfully!")


if __name__ == "__main__":
    seed_firestore()
